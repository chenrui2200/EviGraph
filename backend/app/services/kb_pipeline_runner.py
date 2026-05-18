"""
KB Pipeline 后台执行器

串行执行 6 个阶段，每个阶段完成后才进入下一个。
对于产生后台任务（Task）的阶段，采用轮询方式等待完成。
每阶段完成后触发外部回调通知。
"""

import os
import time
import json
import traceback
import threading
from typing import Optional, Dict, Any

from ..models.kb_pipeline import KbPipelineManager, KbPipeline, PipelineStageStatus
from ..models.project import ProjectManager, ProjectStatus
from ..models.task import TaskManager, TaskStatus
from ..models.ai_app import AiAppManager
from ..config import Config
from ..utils.minio_client import download_object
from ..utils.logger import get_logger
from . import kb_pipeline_callback

logger = get_logger('mirofish.kb_pipeline')

# 延迟导入 Flask app 以避免循环依赖
def _get_app():
    from app import create_app
    return create_app()


# 全局并发控制：最多同时运行 2 个 Pipeline，防止外部资源过载
_MAX_CONCURRENT_PIPELINES = 2
_pipeline_semaphore = threading.Semaphore(_MAX_CONCURRENT_PIPELINES)


class KbPipelineRunner:
    """KB Pipeline 运行器"""

    def __init__(self, pipeline: KbPipeline):
        self.pipeline = pipeline
        self.task_manager = TaskManager()

    def run(self):
        """启动执行（应在后台线程中调用）"""
        # 获取全局并发许可，避免同时运行过多 Pipeline 压垮外部服务
        acquired = _pipeline_semaphore.acquire(timeout=5)
        if not acquired:
            logger.error(f"Pipeline {self.pipeline.pipeline_id} 无法启动：并发 Pipeline 数量已达上限 ({_MAX_CONCURRENT_PIPELINES})")
            self.pipeline.status = PipelineStageStatus.PENDING
            self.pipeline.error = f"系统并发限制：最多同时运行 {_MAX_CONCURRENT_PIPELINES} 个 Pipeline"
            KbPipelineManager.save(self.pipeline)
            return

        try:
            stages = self.pipeline.stages

            for idx, stage in enumerate(stages):
                self.pipeline.current_stage_index = idx
                KbPipelineManager.save(self.pipeline)

                if stage.status.value.endswith("_completed"):
                    continue

                processing_status = getattr(PipelineStageStatus, f"{stage.name.upper()}_PROCESSING")
                completed_status = getattr(PipelineStageStatus, f"{stage.name.upper()}_COMPLETED")
                failed_status = getattr(PipelineStageStatus, f"{stage.name.upper()}_FAILED")

                stage.status = processing_status
                stage.processing_at = self._now()
                self.pipeline.status = processing_status
                KbPipelineManager.save(self.pipeline)

                # 阶段开始前回调
                self._invoke_stage_callback(stage, "started")

                try:
                    if stage.name == "project_creation":
                        self._run_project_creation()
                    elif stage.name == "mineru_annotation":
                        self._run_mineru_annotation()
                    elif stage.name == "chapter_analysis":
                        self._run_chapter_analysis()
                    elif stage.name == "intelligent_analysis":
                        self._run_intelligent_analysis()
                    elif stage.name == "graph_building":
                        self._run_graph_building()
                    elif stage.name == "app_creation":
                        self._run_app_creation()

                    stage.status = completed_status
                    stage.completed_at = self._now()
                    stage.message = stage.message or "完成"
                    self.pipeline.status = completed_status
                    # 阶段成功完成后回调
                    self._invoke_stage_callback(stage, "completed")
                except Exception as e:
                    stage.status = failed_status
                    stage.message = str(e)
                    self.pipeline.status = failed_status
                    self.pipeline.error = f"阶段 [{stage.label}] 失败: {str(e)}"
                    KbPipelineManager.save(self.pipeline)
                    # 阶段失败后回调
                    self._invoke_stage_callback(stage, "failed", message=str(e))
                    logger.error(f"Pipeline {self.pipeline.pipeline_id} failed at {stage.name}: {e}\n{traceback.format_exc()}")
                    return

                KbPipelineManager.save(self.pipeline)

            self.pipeline.total_completed = self._now()
            self.pipeline.status = PipelineStageStatus.TOTAL_COMPLETED
            KbPipelineManager.save(self.pipeline)
            logger.info(f"Pipeline {self.pipeline.pipeline_id} completed successfully.")
        except Exception as e:
            # fatal error：保持当前 stage 的 failed 状态，如果没有则设为 pending
            if self.pipeline.status == PipelineStageStatus.PENDING:
                self.pipeline.error = str(e)
                KbPipelineManager.save(self.pipeline)
            logger.error(f"Pipeline {self.pipeline.pipeline_id} fatal error: {e}\n{traceback.format_exc()}")
        finally:
            _pipeline_semaphore.release()

    def _invoke_stage_callback(self, stage, event_type: str, message: Optional[str] = None):
        """触发阶段回调"""
        try:
            payload = {
                "stage_label": stage.label,
                "event_type": event_type,
                "stage_index": self.pipeline.current_stage_index,
                "project_id": self.pipeline.project_id,
                "graph_id": self.pipeline.graph_id,
                "app_id": self.pipeline.app_id,
                "target_app_id": self.pipeline.target_app_id,
                "result": stage.result,
            }
            kb_pipeline_callback.invoke_callback(
                pipeline_id=self.pipeline.pipeline_id,
                stage_name=stage.name,
                stage_status=stage.status.value,
                message=message or stage.message or f"{stage.label} {event_type}",
                payload=payload
            )
        except Exception as e:
            logger.warning(f"Pipeline {self.pipeline.pipeline_id} 阶段回调异常: {e}")

    @staticmethod
    def _now() -> str:
        from datetime import datetime
        return datetime.now().isoformat()

    # ========================================================================
    # Stage 1: 项目创建 + MinIO 文件下载
    # ========================================================================
    def _run_project_creation(self):
        stage = self._get_stage("project_creation")
        object_name = self.pipeline.minio_object
        base_name = os.path.splitext(os.path.basename(object_name))[0]
        project_name = ProjectManager.generate_unique_name(f"Auto_{base_name}")

        project = ProjectManager.create_project(name=project_name)
        ProjectManager.init_project_dirs(project.project_id)

        files_dir = ProjectManager._get_project_files_dir(project.project_id)
        local_path = os.path.join(files_dir, os.path.basename(object_name))
        download_object(object_name, local_path)

        file_size = os.path.getsize(local_path)
        project.files.append({
            "filename": os.path.basename(object_name),
            "path": local_path,
            "size": file_size,
            "original_filename": os.path.basename(object_name),
            "saved_filename": os.path.basename(object_name),
        })
        ProjectManager.save_project(project)

        self.pipeline.project_id = project.project_id
        stage.result = {"project_id": project.project_id, "project_name": project_name}
        stage.link = f"/chunk-analysis/{project.project_id}"
        stage.message = f"项目 {project_name} 创建完成"

    # ========================================================================
    # Stage 2: MinerU 标注
    # ========================================================================
    def _run_mineru_annotation(self):
        stage = self._get_stage("mineru_annotation")
        project_id = self.pipeline.project_id
        project = ProjectManager.get_project(project_id)
        if not project or not project.files:
            raise ValueError("项目无文件")

        filename = project.files[0].get("filename", "")
        from ..api.graph import mineru_parse
        from flask import Flask

        # 直接调用 mineru_parse 的内部工作函数（避免走 HTTP）
        # 但 mineru_parse 依赖 request，因此直接复用 _do_mineru_parse_work
        task_id = self.task_manager.create_task(
            "mineru_parse",
            metadata={"project_id": project_id, "filename": filename}
        )
        self.task_manager.update_task(task_id, status=TaskStatus.PROCESSING, message="🚀 准备 MinerU 解析...")

        from ..api.graph import _do_mineru_parse_work
        _do_mineru_parse_work(task_id, project_id, filename, parse_method="ocr")

        # 等待任务完成
        self._wait_task(task_id, stage, timeout=1800)

        stage.result = {"task_id": task_id, "filename": filename}
        stage.link = f"/chunk-analysis/{project_id}"
        stage.message = "MinerU 标注完成"

    # ========================================================================
    # Stage 3: 章节模式分析
    # ========================================================================
    def _run_chapter_analysis(self):
        stage = self._get_stage("chapter_analysis")
        project_id = self.pipeline.project_id
        chunks_data = ProjectManager.get_chunks(project_id)
        if not chunks_data:
            raise ValueError("chunks.json 不存在，无法分析章节模式")

        from ..services.llm_driven_chunker import infer_anchor_patterns
        result = infer_anchor_patterns(chunks_data)

        stage.result = result
        stage.message = result.get("reason", "章节模式分析完成")

    # ========================================================================
    # Stage 4: 智能分析
    # ========================================================================
    def _run_intelligent_analysis(self):
        stage = self._get_stage("intelligent_analysis")
        project_id = self.pipeline.project_id
        project = ProjectManager.get_project(project_id)

        chunks_data = ProjectManager.get_chunks(project_id)
        if not chunks_data:
            raise ValueError("chunks.json 不存在")

        anchor_result = self._get_stage("chapter_analysis").result
        chapter_anchor = anchor_result.get("chapter_anchor", "x.x")
        clause_container = anchor_result.get("clause_container", "x.x.x")
        use_mineru_titles = anchor_result.get("use_mineru_titles", False)

        # 准备 text_chunks
        from ..utils.file_parser import TextChunk
        def _to_text_chunk(c):
            text = c.get("content") or c.get("text", "")
            metadata = c.get("metadata", {})
            if not metadata and c.get("chunk_id"):
                metadata = {
                    "chunk_id": c.get("chunk_id"),
                    "page_idx": c.get("page_idx"),
                    "bbox_viewport": c.get("bbox_viewport"),
                    "category_id": c.get("category_id"),
                    "type": c.get("type"),
                    "source": c.get("source")
                }
            return TextChunk(text, metadata)

        text_chunks = [_to_text_chunk(c) for c in chunks_data]

        task_id = self.task_manager.create_task(
            task_type="llm_semantic_analysis",
            metadata={"project_id": project_id}
        )
        project.status = ProjectStatus.GRAPH_CHUNKING
        project.graph_build_task_id = task_id
        project.error = None
        ProjectManager.save_project(project)

        from ..api.graph import _resolve_pdf_path
        pdf_path = _resolve_pdf_path(project_id, "")
        mineru_data = ProjectManager.get_mineru_parsed(project_id)
        md_content = None
        if mineru_data and mineru_data.get("files"):
            first_file = next(iter(mineru_data["files"].values()), None)
            md_content = first_file.get("md_content") if first_file else None

        def progress_callback(progress, message, checkpoint_info=None):
            if progress >= 0:
                self.task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    progress=int(progress * 100),
                    message=message,
                    progress_detail=checkpoint_info or {}
                )
            else:
                self.task_manager.update_task(task_id, log=message)

        from ..services.llm_driven_chunker import LLMDrivenChunker
        chunker = LLMDrivenChunker(progress_callback=progress_callback)
        result = chunker.chunk(
            text_chunks,
            progress_callback,
            checkpoint=None,
            project_id=project_id,
            md_content=md_content,
            chunks_data=chunks_data,
            pdf_path=pdf_path,
            chapter_anchor=chapter_anchor,
            clause_container=clause_container,
            use_mineru_titles=use_mineru_titles,
        )

        if not result:
            raise ValueError("chunker.chunk() 返回空结果")

        from ..services.llm_driven_chunker import clause_to_dict
        chunks_result = {
            "source": "llm",
            "sections": [{"chapter_number": s.chapter_number, "title": s.title, "content": s.content} for s in result.sections],
            "clauses": (lambda seen_ids: [c for c in (clause_to_dict(c) for c in result.clauses) if c["clause_id"] not in seen_ids and not seen_ids.add(c["clause_id"])])(set()),
            "elements": (lambda seen_keys: [{"element_type": "noun_entity", "key": ent if isinstance(ent, str) else "", "value": "", "unit": "", "abbreviation": "", "definition": "", "source_clause_id": c["clause_id"], "scope_prefix": c.get("scope_prefix") or c.get("metadata", {}).get("scope_prefix"), "chapter": c.get("chapter") or c.get("metadata", {}).get("chapter"), "metadata": {"name": ent if isinstance(ent, str) else ""}} for c in (clause_to_dict(c) for c in result.clauses) for ent in c.get("entities", []) if ent and (str(ent) + "|" + c["clause_id"]) not in seen_keys and not seen_keys.add(str(ent) + "|" + c["clause_id"])])(set()),
            "edges": getattr(result, 'edges', []) or []
        }
        ProjectManager.save_chunks_result(project_id, chunks_result)
        ProjectManager.delete_chunk_checkpoint_v2(project_id)
        project = ProjectManager.get_project(project_id)
        project.status = ProjectStatus.GRAPH_CHUNKED
        ProjectManager.save_project(project)

        entity_count = sum(
            sum(len(tp.get('entities', [])) for tp in c.metadata.get('topics', []) if isinstance(tp, dict))
            for c in result.clauses
        )
        self.task_manager.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            progress=100,
            message=f"✅ 标注分析完成: {len(result.sections)} 章节, {len(result.clauses)} 条文, {entity_count} 实体",
            result={"sections": len(result.sections), "clauses": len(result.clauses), "entities": entity_count}
        )

        stage.result = {"task_id": task_id, "sections": len(result.sections), "clauses": len(result.clauses)}
        stage.link = f"/chunk-analysis/{project_id}"
        stage.message = "智能分析完成"

    # ========================================================================
    # Stage 5: 图谱构建
    # ========================================================================
    def _run_graph_building(self):
        stage = self._get_stage("graph_building")
        project_id = self.pipeline.project_id
        project = ProjectManager.get_project(project_id)

        from ..api.graph import _get_storage, _start_build_worker
        from ..storage import GraphStorage
        storage = _get_storage()

        task_id = project.graph_build_task_id
        if task_id and self.task_manager.get_task(task_id):
            task = self.task_manager.get_task(task_id)
            if task.status == TaskStatus.PROCESSING:
                # 复用已有任务，等待完成
                self._wait_task(task_id, stage, timeout=3600)
                self.pipeline.graph_id = project.graph_id
                stage.link = f"/graph_build/{project_id}"
                stage.message = "图谱构建完成"
                return

        # 否则启动新 worker
        task_id = self.task_manager.create_task(
            task_type="graph_build",
            metadata={"project_id": project_id}
        )
        project.graph_build_task_id = task_id
        ProjectManager.save_project(project)
        _start_build_worker(project_id, task_id, storage, force=False)

        self._wait_task(task_id, stage, timeout=3600)
        self.pipeline.graph_id = project.graph_id
        stage.result = {"task_id": task_id, "graph_id": project.graph_id}
        stage.link = f"/graph_build/{project_id}"
        stage.message = "图谱构建完成"

    # ========================================================================
    # Stage 6: 应用创建 / 应用关联
    # ========================================================================
    def _run_app_creation(self):
        stage = self._get_stage("app_creation")
        project_id = self.pipeline.project_id
        project = ProjectManager.get_project(project_id)
        graph_id = project.graph_id
        if not graph_id:
            raise ValueError("图谱尚未构建，无法创建应用")

        target_app_id = self.pipeline.target_app_id

        if target_app_id:
            # 关联到已有 App：必须加锁防止多 Pipeline 同时修改同一 App 的竞态条件
            with AiAppManager._file_lock:
                app = AiAppManager.get_app(target_app_id)
                if not app:
                    raise ValueError(f"目标应用 {target_app_id} 不存在")

                workflow_data = app.workflow_data or {}
                selected_project_ids = workflow_data.get("selectedProjectIds", [])
                if not isinstance(selected_project_ids, list):
                    selected_project_ids = []

                if project_id not in selected_project_ids:
                    selected_project_ids.append(project_id)
                    workflow_data["selectedProjectIds"] = selected_project_ids

                update_data = app.to_dict()
                update_data["workflow_data"] = workflow_data
                updated_app = AiAppManager.save_app(update_data)

            self.pipeline.app_id = updated_app.app_id
            stage.result = {"app_id": updated_app.app_id, "app_name": updated_app.name, "mode": "attach"}
            stage.message = f"已关联到应用 {updated_app.name}"
        else:
            # 自动创建新 App
            app_name = f"Auto_{project.name}"
            app_data = {
                "name": app_name,
                "description": f"知识库应用 - {project.name}",
                "workflow_data": {
                    "selectedProjectIds": [project_id],
                    "temperature": 0.7,
                    "similarityThreshold": 0,
                    "topK": 10,
                    "rerankMinScore": 0,
                    "maxDepth": 3,
                    "rootTypes": ["Entity", "Term"],
                },
                "nodes": []
            }
            app = AiAppManager.save_app(app_data)
            self.pipeline.app_id = app.app_id
            stage.result = {"app_id": app.app_id, "app_name": app_name, "mode": "create"}
            stage.message = f"应用 {app_name} 创建完成"

    # ========================================================================
    # 工具方法
    # ========================================================================
    def _get_stage(self, name: str):
        for s in self.pipeline.stages:
            if s.name == name:
                return s
        raise ValueError(f"未知阶段: {name}")

    def _wait_task(self, task_id: str, stage, timeout: int = 1800, interval: int = 3):
        """轮询等待任务完成"""
        start = time.time()
        while time.time() - start < timeout:
            task = self.task_manager.get_task(task_id)
            if not task:
                raise ValueError(f"任务 {task_id} 不存在")
            if task.status == TaskStatus.COMPLETED:
                stage.message = task.message or "完成"
                return
            if task.status == TaskStatus.FAILED:
                raise ValueError(task.error or task.message or "任务失败")
            stage.message = task.message or "处理中..."
            KbPipelineManager.save(self.pipeline)
            time.sleep(interval)
        raise TimeoutError(f"任务 {task_id} 超时")


def start_pipeline_runner(pipeline: KbPipeline):
    """在后台线程启动 pipeline"""
    runner = KbPipelineRunner(pipeline)
    app = _get_app()

    def run_with_context():
        with app.app_context():
            runner.run()

    thread = threading.Thread(target=run_with_context, daemon=True)
    thread.start()
    return thread
