"""
Graph-related API Routes
Uses project context mechanism with server-side state persistence
"""

import os
import re
import json
import queue
import shutil
import traceback
import threading
import requests
from typing import Dict, Optional, List, Any
from flask import request, jsonify, current_app, send_from_directory

from . import graph_bp
from ..config import Config
from ..services.ontology_generator import OntologyGenerator
from ..services.graph_builder import GraphBuilderService
from ..services.graph_tools import GraphToolsService
from ..services.text_processor import TextProcessor
from ..services.llm_driven_chunker import clause_to_dict, element_to_dict
from ..utils.file_parser import FileParser, TextChunk
from ..utils.logger import get_logger
from ..models.task import TaskManager, TaskStatus
from ..models.project import ProjectManager, ProjectStatus
from ..models.ai_app import AiAppManager

# Get logger
logger = get_logger('mirofish.api')


def _resolve_pdf_path(project_id: str, filename: str = '') -> str:
    """
    解析项目 PDF 文件的真实路径。

    优先级：
    1. project.files[].path（有 path 字段时）
    2. 从项目 files/ 目录按文件名模糊匹配
    3. 从项目 files/ 目录按 magic bytes (%PDF-) 查找 PDF

    Returns:
        PDF 完整路径，找不到返回空字符串
    """
    if project_id:
        project = ProjectManager.get_project(project_id)
        if project and project.files:
            # 1. 优先用 project.files 中记录的 path（必须是文件）
            for pf in project.files:
                pf_path = pf.get('path', '')
                if pf_path:
                    logger.info(f"[PDF路径解析] files[].path={pf_path}, isfile={os.path.isfile(pf_path)}, isdir={os.path.isdir(pf_path)}")
                    if os.path.isfile(pf_path):
                        logger.info(f"[PDF路径解析] ✅ 直接命中: {pf_path}")
                        return pf_path
            # 2. 按 filename 模糊匹配（从 files/ 目录）
            if filename:
                files_dir = ProjectManager._get_project_files_dir(project_id)
                if os.path.isdir(files_dir):
                    logger.info(f"[PDF路径解析] 遍历目录: {files_dir}")
                    for f in os.listdir(files_dir):
                        if filename in f:
                            full = os.path.join(files_dir, f)
                            if os.path.isfile(full):
                                logger.info(f"[PDF路径解析] ✅ filename匹配: {full}")
                                return full
            # 3. 从 files/ 目录找任意 PDF（magic bytes）
            files_dir = ProjectManager._get_project_files_dir(project_id)
            if os.path.isdir(files_dir):
                for f in os.listdir(files_dir):
                    fpath = os.path.join(files_dir, f)
                    if os.path.isfile(fpath):
                        with open(fpath, 'rb') as fh:
                            if fh.read(5) == b'%PDF-':
                                logger.info(f"[PDF路径解析] ✅ magic匹配: {fpath}")
                                return fpath
        else:
            logger.info(f"[PDF路径解析] project={project}, files={project.files if project else 'N/A'}")
    logger.info(f"[PDF路径解析] ❌ 未找到PDF，返回空字符串")
    return ''


def _get_storage():
    """Get Neo4jStorage from Flask app extensions."""
    storage = current_app.extensions.get('neo4j_storage')
    if not storage:
        raise ValueError("GraphStorage not initialized — check Neo4j connection")
    return storage


def allowed_file(filename: str) -> bool:
    """Check if file extension is allowed"""
    if not filename or '.' not in filename:
        return False
    ext = os.path.splitext(filename)[1].lower().lstrip('.')
    return ext in Config.ALLOWED_EXTENSIONS


# ============== Internal Helper: Background Worker Trigger ==============

def _start_build_worker(project_id: str, task_id: str, storage, force: bool = False):
    """
    Start a background thread for graph building.
    Used for both initial build and recovery.
    """
    # Task manager for the thread
    task_manager = TaskManager()

    def build_task():
        build_logger = get_logger('mirofish.build')
        builder = GraphBuilderService(storage=storage)
        # Register worker as active
        builder.register_worker(project_id)

        try:
            build_logger.info(f"[{task_id}] Worker thread attempting to start for graph build...")

            # Reload project to ensure we have the latest metadata and config
            project = ProjectManager.get_project(project_id)
            if not project:
                build_logger.error(f"[{task_id}] Project {project_id} not found by worker.")
                return

            # Check for existing graph_id from project if not force
            active_graph_id = project.graph_id if (project.graph_id and not force) else None

            # Acquire build lock to prevent concurrent workers for the same graph
            lock_id = active_graph_id or task_id
            lock = builder._get_build_lock(lock_id)

            if not lock.acquire(blocking=False):
                build_logger.warning(f"[{task_id}] Build worker already active for {lock_id}. Exiting duplicate thread.")
                return

            try:
                msg = "🚀 WORKER START: Processing graph building..."
                build_logger.info(f"[{task_id}] {msg}")
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    message=msg,
                    log=msg # Persist to task.logs
                )

                # 优先使用已保存的智能分块结果（LLM分析结果），避免重复LLM调用
                intelligent_chunks_data = ProjectManager.get_intelligent_chunks(project_id)

                # 回填 page 信息（旧数据可能缺少 page，从 chunks.json 匹配）
                if intelligent_chunks_data:
                    _backfill_page_info(project_id, intelligent_chunks_data, build_logger)

                if intelligent_chunks_data:
                    # 直接使用 LLM 分析结果，不再重复调用 LLM
                    build_logger.info("Using saved intelligent_chunks.json (LLM analysis results)")
                    task_manager.update_task(
                        task_id,
                        message="Using pre-analyzed chunks from LLM...",
                        progress=5
                    )

                    # 将智能分块数据转换为 HierarchicalChunkResult 格式
                    from ..models.clause import HierarchicalChunkResult, SectionSegment, ClauseSegment, ElementSegment, ElementType, RequirementType, SemanticTriplet, ClauseItem

                    # 构建 sections
                    sections = []
                    for s in intelligent_chunks_data.get('sections', []):
                        sections.append(SectionSegment(
                            chapter_number=s.get('chapter_number'),
                            title=s.get('title', ''),
                            content=s.get('content', ''),
                            metadata=s
                        ))

                    # 构建 clauses
                    clauses = []
                    for c in intelligent_chunks_data.get('clauses', []):
                        req_type_str = c.get('requirement_type', 'recommended')
                        try:
                            req_type = RequirementType(req_type_str)
                        except ValueError:
                            req_type = RequirementType.RECOMMENDED

                        # 解析条文级语义三元组
                        clause_triplets = []
                        for t in c.get('triplets', []):
                            clause_triplets.append(SemanticTriplet(
                                component=t.get('component', ''),
                                action=t.get('action', ''),
                                obj=t.get('obj', ''),
                                condition=t.get('condition', ''),
                                requirement=t.get('requirement', 'mandatory')
                            ))

                        # 解析款/项及其三元组
                        clause_items = []
                        for ci_data in c.get('clause_items', []):
                            ci_triplets = []
                            for t in ci_data.get('triplets', []):
                                ci_triplets.append(SemanticTriplet(
                                    component=t.get('component', ''),
                                    action=t.get('action', ''),
                                    obj=t.get('obj', ''),
                                    condition=t.get('condition', ''),
                                    requirement=t.get('requirement', 'mandatory')
                                ))
                            clause_items.append(ClauseItem(
                                item_number=ci_data.get('item_number', ''),
                                item_content=ci_data.get('item_content', ''),
                                components=ci_data.get('components', []),
                                actions=ci_data.get('actions', []),
                                conditions=ci_data.get('conditions', []),
                                objects=ci_data.get('objects', []),
                                triplets=ci_triplets
                            ))

                        clauses.append(ClauseSegment(
                            clause_id=c.get('clause_id', ''),
                            clause_title=c.get('clause_title', ''),
                            content=c.get('content', ''),
                            source=c.get('source'),
                            page=c.get('page'),
                            requirement_type=req_type,
                            triplets=clause_triplets,
                            parent_chapter=c.get('parent_chapter'),
                            is_term_definition=c.get('is_term_definition', False),
                            terms=c.get('terms', []),
                            formula_content=c.get('formula_content'),
                            clause_items=clause_items,
                            metadata=c
                        ))

                    # 构建 elements
                    elements = []
                    for e in intelligent_chunks_data.get('elements', []):
                        elem_type_str = e.get('element_type', 'parameter')
                        try:
                            elem_type = ElementType(elem_type_str)
                        except ValueError:
                            elem_type = ElementType.PARAMETER

                        elements.append(ElementSegment(
                            element_type=elem_type,
                            source_id=e.get('source_clause_id', ''),
                            key=e.get('key', ''),
                            value=e.get('value', ''),
                            unit=e.get('unit', ''),
                            condition=e.get('condition', ''),
                            abbreviation=e.get('abbreviation', ''),
                            definition=e.get('definition', ''),
                            metadata=e
                        ))

                    hierarchical_result = HierarchicalChunkResult(
                        sections=sections,
                        clauses=clauses,
                        elements=elements
                    )
                    total_chunks = hierarchical_result.total_chunks
                    build_logger.info(f"Using intelligent chunks: {len(sections)} sections, {len(clauses)} clauses, {len(elements)} elements")
                else:
                    # 降级方案：使用普通 chunks 进行正则分块
                    build_logger.warning("intelligent_chunks.json not found, falling back to regular chunking")
                    chunks_data = ProjectManager.get_chunks(project_id)

                    if chunks_data:
                        # Convert dicts back to TextChunk objects（兼容 minerU 格式）
                        from ..utils.file_parser import TextChunk
                        def _fallback_to_text_chunk(c):
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
                        initial_chunks = [_fallback_to_text_chunk(c) for c in chunks_data]

                        # 使用多层级语义分块
                        build_logger.info(f"Using hierarchical chunking (Level-1/2/3)")
                        task_manager.update_task(
                            task_id,
                            message="Performing hierarchical semantic chunking...",
                            progress=5
                        )
                        hierarchical_result = TextProcessor.hierarchical_chunk(initial_chunks)
                        total_chunks = hierarchical_result.total_chunks
                        build_logger.info(f"Hierarchical chunking complete: {total_chunks} chunks")
                    else:
                        # 最终降级：纯文本分块
                        build_logger.warning("chunks.json not found, falling back to plain text splitting")
                        text = ProjectManager.get_extracted_text(project_id)
                        hierarchical_result = TextProcessor.hierarchical_chunk_text(text)
                        total_chunks = hierarchical_result.total_chunks

                # Create graph (OR RESUME EXISTING)
                if project.graph_id and not force:
                    graph_id = project.graph_id
                    build_logger.info(f"[{task_id}] Resuming graph build for existing graph_id: {graph_id}")
                    task_manager.update_task(
                        task_id,
                        message=f"Resuming build for graph: {graph_id}",
                        progress=10
                    )
                else:
                    task_manager.update_task(
                        task_id,
                        message="Creating Zep graph...",
                        progress=10
                    )
                    graph_id = builder.create_graph(name=project.name or 'Knowledge EviGraph')
                    # Update project graph_id
                    project.graph_id = graph_id
                    ProjectManager.save_project(project)

                # Update status to chunking and set ontology
                project.status = ProjectStatus.GRAPH_CHUNKING
                ProjectManager.save_project(project)

                task_manager.update_task(
                    task_id,
                    message="Setting ontology definition...",
                    progress=15
                )
                builder.set_ontology(graph_id, project.ontology)

                # Add text (progress_callback signature is (msg, progress_ratio, log=None))
                def add_progress_callback(msg, progress_ratio, log=None):
                    progress = 15 + int(progress_ratio * 75)  # 15% - 90%
                    task_manager.update_task(
                        task_id,
                        message=msg,
                        progress=progress,
                        log=log
                    )

                task_manager.update_task(
                    task_id,
                    message=f"Starting to add {total_chunks} text chunks...",
                    progress=15
                )

                if 'hierarchical_result' in dir() and hierarchical_result:
                    # 使用多层级分块存储
                    episode_uuids = builder.add_hierarchical_chunks(
                        graph_id,
                        hierarchical_result,
                        progress_callback=add_progress_callback
                    )

                # Update status to embedding generation
                project.status = ProjectStatus.GRAPH_EMBEDDING
                ProjectManager.save_project(project)

                # Neo4j processing is synchronous, no need to wait
                task_manager.update_task(
                    task_id,
                    message="Text processing completed, generating graph data...",
                    progress=90
                )

                # Update status to indexing
                project.status = ProjectStatus.GRAPH_INDEXING
                ProjectManager.save_project(project)

                # Get graph data
                task_manager.update_task(
                    task_id,
                    message="Retrieving graph data...",
                    progress=95
                )
                graph_data = builder.get_graph_data(graph_id)

                # Update project status
                project.status = ProjectStatus.GRAPH_COMPLETED
                ProjectManager.save_project(project)

                node_count = graph_data.get("node_count", 0)
                edge_count = graph_data.get("edge_count", 0)
                build_logger.info(f"[{task_id}] Graph build completed: graph_id={graph_id}, nodes={node_count}, edges={edge_count}")

                # Complete
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.COMPLETED,
                    message="Graph build completed",
                    progress=100,
                    result={
                        "project_id": project_id,
                        "graph_id": graph_id,
                        "node_count": node_count,
                        "edge_count": edge_count,
                        "chunk_count": total_chunks
                    }
                )

            except Exception as e:
                # Update project status to failed
                build_logger.error(f"[{task_id}] Graph build failed: {str(e)}")
                build_logger.debug(f"[{task_id}] Traceback: {traceback.format_exc()}")

                # 详细错误诊断日志
                build_logger.error(f"[{task_id}] === 错误诊断信息 ===")
                build_logger.error(f"[{task_id}] - 错误类型: {type(e).__name__}")
                build_logger.error(f"[{task_id}] - 错误消息: {str(e)}")
                build_logger.error(f"[{task_id}] - 项目ID: {project_id}")
                build_logger.error(f"[{task_id}] - 图谱ID: {project.graph_id if 'project' in dir() and hasattr(project, 'graph_id') else 'N/A'}")

                build_logger.error(f"[{task_id}] ==========================")

                try:
                    project.status = ProjectStatus.FAILED
                    project.error = str(e)
                    ProjectManager.save_project(project)
                except Exception as save_e:
                    build_logger.error(f"[{task_id}] - 保存项目状态失败: {save_e}")

                task_manager.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    message=f"Build failed: {str(e)}",
                    error=traceback.format_exc()
                )
            finally:
                # Release lock when done (success or fail)
                if 'lock' in locals() and lock.locked():
                    lock.release()
                    build_logger.info(f"[{task_id}] Worker lock released.")

        except Exception as outer_e:
            build_logger.error(f"[{task_id}] Outer build worker error: {str(outer_e)}")
            build_logger.debug(traceback.format_exc())
        finally:
            # Unregister worker so it can be auto-recovered if needed
            builder.unregister_worker(project_id)
            build_logger.info(f"[{task_id}] Worker thread unregistered for project {project_id}.")

    # Start thread
    thread = threading.Thread(target=build_task, daemon=True)
    thread.start()
    return thread


# ============== Project Management Interface ==============

@graph_bp.route('/project/<project_id>', methods=['GET'])
def get_project(project_id: str):
    """
    Get project details
    """
    project = ProjectManager.get_project(project_id)

    if not project:
        return jsonify({
            "success": False,
            "error": f"Project does not exist: {project_id}"
        }), 404

    # Check if project is building but task is lost (e.g. server restart)
    from ..models.project import ProjectStatus
    from ..models.task import TaskManager

    # Check ontology task and AUTO-RECOVER if missing (智能Chunks标注分析)
    if project.status == ProjectStatus.ONTOLOGY_GENERATION and project.ontology_task_id:
        task = TaskManager().get_task(project.ontology_task_id)
        if not task:
            logger.warning(f"Project {project_id} lost its ontology task. Attempting auto-recovery...")

            # 检查是否有增强版检查点
            checkpoint_v2 = ProjectManager.get_chunk_checkpoint_v2(project_id)
            has_checkpoint = checkpoint_v2 is not None

            # 检查是否有旧版检查点
            old_checkpoint = ProjectManager.get_chunk_checkpoint(project_id)
            has_old_checkpoint = old_checkpoint is not None

            if has_checkpoint or has_old_checkpoint:
                # 有检查点，触发自动恢复
                try:
                    _start_ontology_recovery_worker(project_id, project.ontology_task_id)
                    project.error = "检测到后台任务中断，系统已自动从检查点恢复进度。"
                    ProjectManager.save_project(project)
                    logger.info(f"Project {project_id} ontology recovery worker started.")
                except Exception as re:
                    logger.error(f"Auto-recovery failed for project {project_id}: {re}")
                    project.error = f"自动恢复失败: {str(re)}"
                    ProjectManager.save_project(project)
            else:
                # 无检查点，标记为可重新触发
                project.error = "任务实例已过期，请点击按钮重新触发。"
                ProjectManager.save_project(project)

    # Check build task
    if project.status in [ProjectStatus.GRAPH_BUILDING, ProjectStatus.GRAPH_CHUNKING, ProjectStatus.GRAPH_EMBEDDING, ProjectStatus.GRAPH_INDEXING]:
        task_id = project.graph_build_task_id
        storage = _get_storage()
        builder = GraphBuilderService(storage=storage)

        # 1. If task ID is missing or task record is gone
        if not task_id or not TaskManager().get_task(task_id):
            logger.warning(f"Project {project_id} is in building status but task record is missing. Starting new recovery task...")
            new_task_id = TaskManager().create_task(f"Recover build: {project.name or project_id}")
            project.graph_build_task_id = new_task_id
            project.error = "检测到后台任务记录丢失，系统已自动创建新任务恢复进度。"
            ProjectManager.save_project(project)
            _start_build_worker(project_id, new_task_id, storage, force=False)

        # 2. Task exists but worker thread is not active
        elif not builder.is_worker_active(project_id):
            logger.info(f"🚀 Project {project_id} has active task {task_id} but NO active worker thread. Auto-starting worker...")
            try:
                _start_build_worker(project_id, task_id, storage, force=False)
                project.error = "检测到后台任务中断，系统已尝试自动恢复进度。"
                ProjectManager.save_project(project)
            except Exception as e:
                logger.error(f"Failed to auto-recover project {project_id}: {e}")

    return jsonify({
        "success": True,
        "data": project.to_dict()
    })


def _start_ontology_recovery_worker(project_id: str, original_task_id: str):
    """
    启动智能Chunks标注分析的恢复工作线程

    从检查点恢复并继续处理
    """
    from ..models.task import TaskManager, TaskStatus

    # 创建新的恢复任务
    task_manager = TaskManager()
    recovery_task_id = task_manager.create_task(
        task_type="ontology_recovery",
        metadata={"project_id": project_id, "original_task_id": original_task_id}
    )

    # 更新项目的 ontology_task_id 为新的恢复任务
    project = ProjectManager.get_project(project_id)
    if project:
        project.ontology_task_id = recovery_task_id
        ProjectManager.save_project(project)

    def recovery_task():
        try:
            from ..services.llm_driven_chunker import LLMDrivenChunker
            from ..services.llm_driven_chunker import LLMChunkerError
            from ..utils.file_parser import TextChunk

            build_logger = get_logger('mirofish.ontology')

            # 获取文本块
            chunks_data = ProjectManager.get_chunks(project_id)
            if not chunks_data:
                text = ProjectManager.get_extracted_text(project_id)
                if text:
                    chunks_data = [{"text": text, "metadata": {"source": project.name or "document"}}]

            if not chunks_data:
                build_logger.error(f"[{recovery_task_id}] 未找到文本数据")
                task_manager.fail_task(recovery_task_id, "未找到文本数据")
                return

            def _recover_to_text_chunk(c):
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

            text_chunks = [_recover_to_text_chunk(c) for c in chunks_data]
            build_logger.info(f"[{recovery_task_id}] 开始从检查点恢复，文本块数量: {len(text_chunks)}")

            task_manager.update_task(
                recovery_task_id,
                status=TaskStatus.PROCESSING,
                progress=0,
                message="🚀 从检查点恢复智能标注分析..."
            )

            def progress_callback(progress, message, checkpoint_info=None):
                """进度回调"""
                build_logger.info(message)
                task_manager.update_task(
                    recovery_task_id,
                    status=TaskStatus.PROCESSING,
                    progress=int(progress * 100),
                    message=message,
                    log=message,
                    progress_detail=checkpoint_info or {}
                )

            # 获取增强版检查点
            checkpoint = ProjectManager.get_chunk_checkpoint_v2(project_id)
            if checkpoint:
                build_logger.info(f"[{recovery_task_id}] 从增强版检查点恢复，已处理 {len(checkpoint.completed_clauses)} 条文, {len(checkpoint.completed_elements)} 要素")
            else:
                # 尝试旧版检查点
                old_checkpoint = ProjectManager.get_chunk_checkpoint(project_id)
                if old_checkpoint:
                    build_logger.info(f"[{recovery_task_id}] 从旧版检查点恢复")
                    checkpoint = None  # 旧版检查点不支持自动恢复，从头开始
                else:
                    build_logger.info(f"[{recovery_task_id}] 无检查点，从头开始")
                    checkpoint = None

            # 执行 LLM 标注分析
            chunker = LLMDrivenChunker(progress_callback=progress_callback)
            result = chunker.chunk(
                text_chunks,
                progress_callback,
                checkpoint=checkpoint,
                project_id=project_id
            )

            # 保存分块结果（尝试序列化，失败时用检查点兜底）
            save_success = False
            try:
                chunks_result = {
                    "source": "llm",
                    "sections": [
                        {
                            "chapter_number": s.chapter_number,
                            "title": s.title,
                            "content": s.content
                        }
                        for s in result.sections
                    ],
                    "clauses": [
                        {
                            "clause_id": c.clause_id,
                            "clause_title": c.clause_title,
                            "content": c.content,
                            "requirement_type": c.requirement_type.value,
                            "conditions": c.conditions,
                            "actions": c.actions,
                            "components": c.components,
                            "objects": c.objects,
                            "parent_chapter": c.parent_chapter,
                            "metadata": c.metadata
                        }
                        for c in result.clauses
                    ],
                    "elements": [
                        {
                            "element_type": e.element_type.value,
                            "key": e.key,
                            "value": str(e.value) if e.value else "",
                            "unit": e.unit,
                            "condition": e.condition,
                            "abbreviation": e.abbreviation,
                            "definition": e.definition,
                            "keywords": e.keywords,
                            "source_clause_id": e.source_id,
                            "metadata": e.metadata
                        }
                        for e in result.elements
                    ]
                }
                ProjectManager.save_intelligent_chunks(project_id, chunks_result)
                build_logger.info(f"[{recovery_task_id}] ✅ intelligent_chunks.json 保存成功")
                save_success = True
            except Exception as save_err:
                build_logger.error(f"[{recovery_task_id}] 序列化 intelligent_chunks.json 失败: {save_err}，尝试检查点兜底...")
                try:
                    fallback_checkpoint = ProjectManager.get_chunk_checkpoint_v2(project_id)
                    if fallback_checkpoint:
                        chunks_result = {
                            "source": "llm_checkpoint_fallback",
                            "sections": [
                                {"chapter_number": cp.chapter_number, "title": cp.title, "content": ""}
                                for cp in fallback_checkpoint.chapter_plan
                                if cp.status.value in ("completed", "processing")
                            ],
                            "clauses": fallback_checkpoint.completed_clauses or [],
                            "elements": fallback_checkpoint.completed_elements or [],
                        }
                        ProjectManager.save_intelligent_chunks(project_id, chunks_result)
                        build_logger.info(f"[{recovery_task_id}] ✅ 检查点兜底保存成功")
                        save_success = True
                    else:
                        build_logger.error(f"[{recovery_task_id}] 无法兜底：检查点数据也不存在")
                except Exception as fb_err:
                    build_logger.error(f"[{recovery_task_id}] 检查点兜底也失败: {fb_err}")

            if save_success:
                # 删除检查点
                ProjectManager.delete_chunk_checkpoint_v2(project_id)
                ProjectManager.delete_chunk_checkpoint(project_id)

                # 更新项目状态
                project = ProjectManager.get_project(project_id)
                project.status = ProjectStatus.GRAPH_CHUNKED
                ProjectManager.save_project(project)

                # 完成任务
                summary = f"✅ 标注分析完成: {len(result.sections)} 章节, {len(result.clauses)} 条文, {len(result.elements)} 要素"
                build_logger.info(f"[{recovery_task_id}] {summary}")

                task_manager.update_task(
                    recovery_task_id,
                    status=TaskStatus.COMPLETED,
                    progress=100,
                    message=summary,
                    log=summary,
                    result={
                        "sections": len(result.sections),
                        "clauses": len(result.clauses),
                        "elements": len(result.elements)
                    }
                )

        except LLMChunkerError as e:
            build_logger.error(f"[{recovery_task_id}] LLM 分块失败: {e}")
            task_manager.update_task(
                recovery_task_id,
                status=TaskStatus.FAILED,
                message=f"LLM 分块失败: {e}",
                error=str(e)
            )
            project = ProjectManager.get_project(project_id)
            project.status = ProjectStatus.FAILED
            project.error = str(e)
            ProjectManager.save_project(project)

        except Exception as e:
            build_logger.error(f"[{recovery_task_id}] 恢复任务异常: {e}\n{traceback.format_exc()}")
            task_manager.update_task(
                recovery_task_id,
                status=TaskStatus.FAILED,
                message=f"恢复任务异常: {e}",
                error=str(e)
            )

    # 启动恢复线程
    thread = threading.Thread(target=recovery_task, daemon=True)
    thread.start()
    return thread


@graph_bp.route('/project/list', methods=['GET'])
def list_projects():
    """
    List all projects with AI app reference info
    """
    limit = request.args.get('limit', 50, type=int)
    projects = ProjectManager.list_projects(limit=limit)

    # Get all AI apps for reference check
    apps = AiAppManager.list_apps(limit=100)
    app_graph_map: Dict[str, List[Dict]] = {}  # graph_id -> list of {app_id, name, is_published}

    for app in apps:
        selected_graph_ids = app.workflow_data.get('selectedGraphIds', [])
        for gid in selected_graph_ids:
            if gid not in app_graph_map:
                app_graph_map[gid] = []
            app_graph_map[gid].append({
                "app_id": app.app_id,
                "name": app.name,
                "is_published": app.is_published
            })

    # Attach referencing apps to each project
    project_list = []
    for p in projects:
        p_dict = p.to_dict()
        if p.graph_id and p.graph_id in app_graph_map:
            p_dict["referencing_apps"] = app_graph_map[p.graph_id]
        else:
            p_dict["referencing_apps"] = []
        project_list.append(p_dict)

    return jsonify({
        "success": True,
        "data": project_list,
        "count": len(projects)
    })


@graph_bp.route('/project/<project_id>', methods=['DELETE'])
def delete_project(project_id: str):
    """
    Delete project with AI App reference check
    """
    # Get project to check graph_id
    project = ProjectManager.get_project(project_id)
    if not project:
        return jsonify({
            "success": False,
            "error": f"Project not found: {project_id}"
        }), 404

    # Check if any AI App references this project's graph
    if project.graph_id:
        apps = AiAppManager.list_apps(limit=100)
        referencing_apps = []
        for app in apps:
            selected_graph_ids = app.workflow_data.get('selectedGraphIds', [])
            if project.graph_id in selected_graph_ids:
                referencing_apps.append({
                    "app_id": app.app_id,
                    "name": app.name,
                    "is_published": app.is_published
                })

        if referencing_apps:
            return jsonify({
                "success": False,
                "error": "Cannot delete project: AI App is using this knowledge base",
                "referencing_apps": referencing_apps
            }), 409

    # Proceed with deletion
    success = ProjectManager.delete_project(project_id)

    if not success:
        return jsonify({
            "success": False,
            "error": f"Project deletion failed: {project_id}"
        }), 500

    return jsonify({
        "success": True,
        "message": f"Project deleted: {project_id}"
    })


@graph_bp.route('/project/<project_id>', methods=['PATCH'])
def update_project(project_id: str):
    """
    Update project details (e.g., name)
    """
    try:
        data = request.get_json() or {}
        project = ProjectManager.get_project(project_id)

        if not project:
            return jsonify({
                "success": False,
                "error": f"Project does not exist: {project_id}"
            }), 404

        if 'name' in data:
            project.name = data['name']

        if 'current_step' in data:
            try:
                project.current_step = int(data['current_step'])
            except:
                pass

        ProjectManager.save_project(project)

        return jsonify({
            "success": True,
            "message": "Project updated",
            "data": project.to_dict()
        })
    except Exception as e:
        logger.error(f"Failed to update project: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


@graph_bp.route('/project/<project_id>/reset', methods=['POST'])
def reset_project(project_id: str):
    """
    Reset project status (for rebuilding graph)
    """
    project = ProjectManager.get_project(project_id)

    if not project:
        return jsonify({
            "success": False,
            "error": f"Project does not exist: {project_id}"
        }), 404

    # Reset to ontology generated state
    if project.ontology:
        project.status = ProjectStatus.ONTOLOGY_GENERATED
    else:
        project.status = ProjectStatus.CREATED

    project.graph_id = None
    project.graph_build_task_id = None
    project.error = None
    ProjectManager.save_project(project)

    return jsonify({
        "success": True,
        "message": f"Project reset: {project_id}",
        "data": project.to_dict()
    })


@graph_bp.route('/project/<project_id>/document/<path:filename>', methods=['GET'])
def get_project_document(project_id: str, filename: str):
    """
    Get a specific document (e.g. PDF) associated with a project for preview.
    Supports both project_id and graph_id as the first parameter.
    """
    # 1. Try to find the actual project object to map IDs
    project = ProjectManager.get_project(project_id)

    # 2. If not found by ID, search all projects for a matching graph_id
    if not project:
        all_projects = ProjectManager.list_projects(limit=500) # Increase limit to be safe
        for p in all_projects:
            if p.graph_id == project_id:
                project = p
                break

    # Determine the actual folder name on disk
    actual_folder_id = project.project_id if project else project_id
    base_dir = os.path.abspath(os.path.join(current_app.root_path, '../uploads/projects', actual_folder_id))

    logger.info(f"Looking for document '{filename}' in project folder: {base_dir}")

    if not os.path.exists(base_dir):
        return jsonify({
            "success": False,
            "error": f"Project directory not found: {base_dir}"
        }), 404

    # 3. Robust search for the file (recursive and fuzzy)
    target_file_path = None
    target_dir = None
    found_filename = None

    # Normalize search filename for comparison
    search_name = filename.lower().strip()

    logger.info(f"Searching for '{search_name}' in {base_dir}...")

    for root, dirs, files in os.walk(base_dir):
        for f in files:
            # Try multiple matching strategies
            f_lower = f.lower().strip()

            # Strategy A: Exact match
            # Strategy B: Search name is part of found file (or vice versa)
            # Strategy C: Ignore common issues with Chinese punctuation
            if f_lower == search_name or search_name in f_lower or f_lower in search_name:
                target_file_path = os.path.join(root, f)
                target_dir = root
                found_filename = f
                break
        if target_file_path:
            break

    if not target_file_path:
        # Fallback: if filename is garbled (e.g. from MinerU returning corrupted Chinese filenames),
        # try to find the first PDF file in the directory
        logger.warning(f"File lookup failed for '{search_name}'. Trying fallback: finding first PDF file...")
        for root, dirs, files in os.walk(base_dir):
            for f in files:
                f_lower = f.lower().strip()
                if f_lower.endswith('.pdf') or f == 'pdf':
                    target_file_path = os.path.join(root, f)
                    target_dir = root
                    found_filename = f
                    logger.info(f"Fallback found PDF by extension/heuristic: {f}")
                    break
            if target_file_path:
                break

        # Last resort: try magic bytes (PDF files start with %PDF)
        if not target_file_path:
            all_files = []
            for root, dirs, files in os.walk(base_dir):
                for f in files:
                    fpath = os.path.join(root, f)
                    try:
                        with open(fpath, 'rb') as fh:
                            header = fh.read(5)
                            if header == b'%PDF-':
                                target_file_path = fpath
                                target_dir = root
                                found_filename = f
                                logger.info(f"Fallback found PDF by magic bytes: {f}")
                                break
                    except Exception:
                        pass
                    all_files.append(f)
                if target_file_path:
                    break

        if not target_file_path:
            logger.warning(f"File lookup failed. Files present in project: {all_files}")
            return jsonify({
                "success": False,
                "error": f"Document not found: {filename}. Searched {base_dir}. Found files: {all_files[:10]}...",
                "searched_id": project_id,
                "mapped_id": actual_folder_id
            }), 404

    logger.info(f"Serving document: {found_filename} from {target_dir}")

    from flask import send_file, make_response

    # Send file without any attachment/filename metadata to prevent download triggers
    response = make_response(send_file(target_file_path, mimetype='application/pdf'))

    # Force pure inline mode
    response.headers['Content-Disposition'] = 'inline'
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'ALLOWALL'

    # Remove any headers that might suggest a file download
    if 'Content-Transfer-Encoding' in response.headers:
        del response.headers['Content-Transfer-Encoding']

    return response


# ============================================================================
# 辅助函数
# ============================================================================

def _backfill_page_info(project_id: str, intelligent_chunks_data: Dict, logger) -> None:
    """
    回填 intelligent_chunks.json 中缺失的 page 信息。

    从 chunks.json（MinerU 解析产物）中查找对应 clause_id 的 page_idx，
    填充到 clauses 的 page 字段中。确保旧数据也能支持定位到文档页面。
    """
    chunks_data = ProjectManager.get_chunks(project_id)
    if not chunks_data:
        logger.debug(f"[{project_id}] 回填 page: chunks.json 不存在，跳过")
        return

    # 构建 clause_id → page 的映射
    # MinerU chunks 中的 page_idx 字段对应页码
    clause_page_map: Dict[str, int] = {}
    clause_bbox_map: Dict[str, Dict] = {}

    for chunk in chunks_data:
        chunk_id = chunk.get('chunk_id', '')
        content = chunk.get('content', '')

        # 尝试从 chunk_id 中提取 clause_id（格式: chunk_0_123）
        # 同时也通过内容匹配来找 clause
        page_idx = chunk.get('page_idx')
        bbox = chunk.get('bbox_pdf') or chunk.get('bbox_viewport')
        source = chunk.get('source', '')

        if page_idx is not None and content:
            # 提取条款编号（匹配形如 "2.0.36"、"5.2.8" 等）
            matches = re.findall(r'\b(\d+(?:\.\d+)+)\b', content)
            for m in matches:
                if m not in clause_page_map:
                    clause_page_map[m] = page_idx
                    clause_bbox_map[m] = {
                        'bbox': bbox,
                        'page_idx': page_idx,
                        'page_width': chunk.get('page_width'),
                        'page_height': chunk.get('page_height'),
                        'source': source
                    }

    if not clause_page_map:
        logger.warning(f"[{project_id}] 回填 page: chunks.json 中无条款编号（正则匹配失败），跳过")
        return

    logger.info(f"[{project_id}] 回填 page: 从 chunks.json 匹配到 {len(clause_page_map)} 个条款编号 "
                 f"(页码范围 {min(clause_page_map.values())}-{max(clause_page_map.values())})")

    # 回填到 clauses
    filled_count = 0
    skipped_already_filled = 0
    skipped_not_in_map = 0
    for clause in intelligent_chunks_data.get('clauses', []):
        clause_id = clause.get('clause_id', '')
        if clause_id and clause_id in clause_page_map:
            if clause.get('metadata', {}).get('page') is not None:
                skipped_already_filled += 1
                continue
            bbox_info = clause_bbox_map[clause_id]
            page_val = clause_page_map[clause_id]
            source_val = bbox_info.get('source')
            bbox_val = bbox_info.get('bbox')
            page_width_val = bbox_info.get('page_width')
            page_height_val = bbox_info.get('page_height')

            # 同时写入顶层字段（ClauseSegment.page/source 读取）和 metadata（_extract_semantic_elements 读取）
            clause['metadata'] = clause.get('metadata', {})
            clause['page'] = page_val
            clause['source'] = source_val
            clause['metadata']['page'] = page_val
            clause['metadata']['bbox'] = bbox_val
            clause['metadata']['source'] = source_val
            clause['metadata']['page_width'] = page_width_val
            clause['metadata']['page_height'] = page_height_val
            filled_count += 1
            logger.debug(f"[{project_id}] 回填: clause={clause_id} → page={page_val}, source={source_val}, bbox={bbox_val}")
        else:
            skipped_not_in_map += 1

    logger.info(f"[{project_id}] 回填 page 统计: 填充 {filled_count}, 已填充跳过 {skipped_already_filled}, "
                 f"chunks中无匹配跳过 {skipped_not_in_map} / {len(intelligent_chunks_data.get('clauses', []))} 个条款")

    if filled_count > 0:
        # 保存回填后的数据
        ProjectManager.save_intelligent_chunks(project_id, intelligent_chunks_data)
        logger.info(f"[{project_id}] 回填 page: 成功保存 {filled_count} 个条款的 page 信息到 intelligent_chunks.json")


# ============== Interface 1: Upload Files and Generate Ontology ==============

@graph_bp.route('/ontology/generate', methods=['POST'])
def generate_ontology():
    """
    Interface 1: Upload files and extract text (Asynchronous)

    流程（与手册一致）：
    1. 提取 PDF 文本块（chunks.json）
    2. 保存原始文本
    3. 设置状态为 ontology_generated

    注意：MinerU 解析（Phase 2.1）和智能分析（Phase 2.2）是独立的用户操作步骤。
    - 🧠 MinerU 解析 → POST /api/graph/pdf/mineru-parse（可选，用户手动触发）
    - 🚀 开始智能分析 → POST /api/graph/chunk/intelligent（可选，用户手动触发）
    """
    try:
        logger.info("=== Starting 文件上传与文本提取流程 ===")

        # Get parameters
        simulation_requirement = request.form.get('simulation_requirement', '')
        project_name = request.form.get('project_name', 'Unnamed Project')
        additional_context = request.form.get('additional_context', '')

        # Get uploaded files
        uploaded_files = request.files.getlist('files')
        if not uploaded_files or all(not f.filename for f in uploaded_files):
            return jsonify({
                "success": False,
                "error": "Please upload at least one document file"
            }), 400

        # 1. 创建项目对象（仅内存中，不创建目录）
        project = ProjectManager.create_project(name=project_name)
        project.simulation_requirement = simulation_requirement

        # 2. 先验证所有文件，全部有效后才写入磁盘（避免留下空目录）
        valid_files = [
            f for f in uploaded_files
            if f and f.filename and allowed_file(f.filename)
        ]
        if not valid_files:
            return jsonify({
                "success": False,
                "error": "No valid files uploaded"
            }), 400

        # 3. 创建项目目录结构（此时才开始写磁盘）
        ProjectManager.init_project_dirs(project.project_id)

        # 4. 保存文件到磁盘
        saved_files = []
        file_save_errors = []
        for file in valid_files:
            try:
                file_info = ProjectManager.save_file_to_project(
                    project.project_id,
                    file,
                    file.filename
                )
                saved_files.append(file_info)
                project.files.append({
                    "filename": file_info["original_filename"],
                    "path": file_info["path"],
                    "size": file_info["size"]
                })
            except Exception as file_err:
                logger.warning(f"Failed to save file {file.filename}: {file_err}")
                file_save_errors.append(f"{file.filename}: {file_err}")

        if not saved_files:
            # 清理刚创建的目录
            shutil.rmtree(ProjectManager._get_project_dir(project.project_id), ignore_errors=True)
            return jsonify({"success": False, "error": "No valid files uploaded"}), 400

        # 5. 保存项目元数据
        ProjectManager.save_project(project)

        # Create task
        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type="text_extraction",
            metadata={"project_id": project.project_id}
        )

        # Update project status and task ID
        project.status = ProjectStatus.ONTOLOGY_GENERATION
        project.ontology_task_id = task_id
        ProjectManager.save_project(project)

        # Start background thread
        def ontology_task():
            try:
                build_logger = get_logger('mirofish.ontology')

                # ========== 阶段 1: MinerU PDF 解析 → chunks.json ==========
                build_logger.info(f"[{task_id}] 阶段 1: MinerU PDF 解析...")
                task_manager.update_task(task_id, status=TaskStatus.PROCESSING, progress=5, message="🚀 开始 MinerU PDF 解析...")

                all_chunks = []       # 所有文件的 chunks（合并到 chunks.json）
                all_text_parts = []   # 所有文件的纯文本（拼接到 all_text）

                for idx, file_info in enumerate(saved_files):
                    orig_name = file_info["original_filename"]
                    current_progress = 5 + int((idx / len(saved_files)) * 40)
                    build_logger.info(f"[{task_id}] 处理文件: {orig_name} ({idx + 1}/{len(saved_files)})")

                    # 只对 PDF 调用 MinerU
                    if not orig_name.lower().endswith('.pdf'):
                        build_logger.info(f"[{task_id}] 非 PDF 文件，跳过 MinerU: {orig_name}")
                        # 降级：使用 FileParser 提取文本
                        try:
                            chunks = FileParser.extract_chunks(file_info["path"], override_filename=orig_name)
                            for c in chunks:
                                all_chunks.append({
                                    "chunk_id": f"chunk_{idx}_{len(all_chunks)}",
                                    "page_idx": c.metadata.get("page", 0),
                                    "type": c.metadata.get("type", "text"),
                                    "content": c.text,
                                    "bbox_pdf": c.metadata.get("bbox"),
                                    "bbox_viewport": c.metadata.get("bbox"),
                                    "page_width": c.metadata.get("page_width"),
                                    "page_height": c.metadata.get("page_height"),
                                    "category_id": 1,
                                    "source": orig_name
                                })
                                all_text_parts.append(c.text)
                            build_logger.info(f"[{task_id}] FileParser 提取 {len(chunks)} 个文本块: {orig_name}")
                        except Exception as fe:
                            build_logger.warning(f"[{task_id}] FileParser 失败: {fe}")
                        continue

                    # 调用 MinerU API
                    pdf_path = file_info["path"]
                    if not os.path.exists(pdf_path):
                        build_logger.warning(f"[{task_id}] PDF 文件不存在: {pdf_path}")
                        continue

                    mineru_url = Config.MINERU_API_URL
                    build_logger.info(f"[{task_id}] 调用 MinerU API: {mineru_url} for {orig_name}")

                    try:
                        with open(pdf_path, 'rb') as pdf_file:
                            mineru_response = requests.post(
                                mineru_url,
                                files={'pdf_file': (orig_name, pdf_file.read(), 'application/pdf')},
                                timeout=600
                            )
                    except Exception as req_err:
                        build_logger.error(f"[{task_id}] MinerU 请求失败: {req_err}")
                        task_manager.update_task(task_id, log=f"❌ {orig_name} MinerU 请求失败: {str(req_err)}")
                        continue

                    if mineru_response.status_code != 200:
                        build_logger.error(f"[{task_id}] MinerU API 返回错误: {mineru_response.status_code}")
                        task_manager.update_task(task_id, log=f"❌ MinerU API 错误: {mineru_response.status_code}")
                        continue

                    mineru_data = mineru_response.json()
                    build_logger.info(f"[{task_id}] ✅ MinerU API 成功: {orig_name}")

                    # 保存原始 MinerU 解析结果
                    mineru_parsed = ProjectManager.get_mineru_parsed(project.project_id) or {"files": {}}
                    mineru_parsed["files"][orig_name] = mineru_data
                    ProjectManager.save_mineru_parsed(project.project_id, mineru_parsed)

                    # 统一解析 MinerU 返回（正文/标题 + 表格）
                    file_chunks = _parse_mineru_to_chunks(mineru_data, orig_name, pdf_path)
                    for c in file_chunks:
                        c["chunk_id"] = f"chunk_{idx}_{c['chunk_id'].split('_', 1)[-1]}"
                    all_chunks.extend(file_chunks)

                    text_count = len([c for c in file_chunks if c['category_id'] in (0, 1)])
                    table_count = len([c for c in file_chunks if c['category_id'] == 2])
                    build_logger.info(f"[{task_id}] 文件 {orig_name} 提取 {text_count} 文本块 + {table_count} 表格块")
                    msg = f"[{task_id}] ✅ 已提取 {text_count} 文本块 + {table_count} 表格块"
                    task_manager.update_task(task_id, progress=current_progress, message=msg, log=msg)

                if not all_chunks:
                    build_logger.error(f"[{task_id}] 未提取到任何 chunks")
                    task_manager.fail_task(task_id, "未提取到任何文本 chunks")
                    return

                # ========== 阶段 1.2: 保存 chunks.json ==========
                build_logger.info(f"[{task_id}] 阶段 1.2: 保存 chunks.json...")
                task_manager.update_task(task_id, progress=60, message="💾 保存 chunks.json...", log="保存 chunks.json")

                ProjectManager.save_chunks(project.project_id, all_chunks)
                build_logger.info(f"[{task_id}] ✅ chunks.json 已保存，共 {len(all_chunks)} 个块")

                # ========== 阶段 1.3: 提取名词实体（可选，轻量） ==========
                task_manager.update_task(task_id, progress=75, message="🧠 提取名词实体...", log="提取名词实体")
                content_chunks = [c for c in all_chunks if c.get('content') and not c.get('is_layout_bbox')]
                total_nouns = _extract_nouns_from_chunks(content_chunks, project.project_id, task_id, build_logger)
                build_logger.info(f"[{task_id}] ✅ 名词提取完成: {total_nouns} 个名词")

                # ========== 阶段 1 完成: 保存本体 + 设置状态 ==========
                task_manager.update_task(task_id, progress=85, message="💾 保存本体定义...", log="保存本体定义")

                from ..services.normative_ontology import NORMATIVE_ONTOLOGY
                ontology = NORMATIVE_ONTOLOGY
                if ontology:
                    project.ontology = {
                        "entity_types": ontology.get("entity_types", []),
                        "edge_types": ontology.get("edge_types", [])
                    }
                    project.analysis_summary = f"MinerU 解析完成：{len(all_chunks)} 块，{total_nouns} 名词"
                else:
                    project.ontology = {"entity_types": [], "edge_types": []}
                    project.analysis_summary = ""

                # 状态设为 ontology_generated（MinerU解析完成，可进入 Phase 2.2 智能分析）
                project.status = ProjectStatus.ONTOLOGY_GENERATED
                ProjectManager.save_project(project)

                summary = f"✅ MinerU 解析完成！共 {len(all_chunks)} 块（含 layout），{total_nouns} 名词"
                build_logger.info(f"[{task_id}] {summary}")

                task_manager.complete_task(task_id, {
                    "project_id": project.project_id,
                    "ontology": project.ontology,
                    "analysis_summary": project.analysis_summary,
                    "total_text_length": project.total_text_length,
                    "chunks_count": len(all_chunks),
                    "content_chunks_count": len(content_chunks),
                    "total_nouns": total_nouns
                })
                build_logger.info(f"[{task_id}] 任务完成.")

            except Exception as e:
                build_logger.error(f"[{task_id}] ❌ 任务失败: {str(e)}\n{traceback.format_exc()}")
                project.status = ProjectStatus.FAILED
                project.error = str(e)
                ProjectManager.save_project(project)
                task_manager.fail_task(task_id, str(e))

        thread = threading.Thread(target=ontology_task, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "data": {
                "project_id": project.project_id,
                "task_id": task_id,
                "message": "文件上传与 MinerU PDF 解析任务已启动，请稍候"
            }
        })

    except Exception as e:
        logger.error(f"API Error: {str(e)}\n{traceback.format_exc()}")
        # 清理已创建的项目目录，避免留下空文件夹
        if 'project' in dir() and project and project.project_id:
            try:
                ProjectManager.delete_project(project.project_id)
                logger.info(f"Cleaned up empty project directory: {project.project_id}")
            except Exception as del_err:
                logger.warning(f"Failed to clean up project directory {project.project_id}: {del_err}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


def _convert_analysis_to_chunks(chunk_result, text_chunks, project_id):
    """
    将章节分析结果转换为 chunks

    基于条文和要素，在原始文本中定位并创建 chunks

    Args:
        chunk_result: HierarchicalChunkResult（包含章节、条文、要素）
        text_chunks: 原始 TextChunk 列表
        project_id: 项目ID

    Returns:
        chunks 列表
    """
    from ..models.clause import ClauseSegment, ElementSegment

    chunks = []

    # 1. 为每个条文创建一个 chunk
    for clause in chunk_result.clauses:
        # 在原始文本中查找条文位置
        clause_text = clause.content
        source_file = clause.source or "unknown"

        # 查找所属章节
        parent_chapter = None
        for section in chunk_result.sections:
            if section.chapter_number == clause.parent_chapter:
                parent_chapter = section.title
                break

        chunks.append({
            "text": clause_text,
            "metadata": {
                "chunk_type": "clause",
                "level": 2,
                "level_name": "Level2",
                "clause_id": clause.clause_id,
                "clause_title": clause.clause_title,
                "requirement_type": clause.requirement_type.value if hasattr(clause.requirement_type, 'value') else str(clause.requirement_type),
                "source": source_file,
                "parent_chapter": clause.parent_chapter,
                "parent_chapter_title": parent_chapter,
                "conditions": clause.conditions or [],
                "actions": clause.actions or [],
                "components": clause.components or [],
                "objects": clause.objects or [],
                "project_id": project_id,
                **clause.metadata
            }
        })

    # 2. 为每个要素创建一个 chunk
    for element in chunk_result.elements:
        source_clause_id = element.source_id or ""

        chunks.append({
            "text": f"{element.key}: {element.content}" if element.key else element.content,
            "metadata": {
                "chunk_type": "element",
                "level": 3,
                "level_name": "Level3",
                "element_type": element.element_type.value if hasattr(element.element_type, 'value') else str(element.element_type),
                "key": element.key,
                "value": str(element.value) if element.value else "",
                "unit": element.unit,
                "condition": element.condition,
                "abbreviation": element.abbreviation,
                "definition": element.definition,
                "source_clause_id": source_clause_id,
                "project_id": project_id,
                **element.metadata
            }
        })

    # 3. 为每个章节创建一个 chunk（包含章节描述）
    for section in chunk_result.sections:
        if section.content:  # 只添加有内容的章节
            chunks.append({
                "text": f"{section.title}\n\n{section.content}",
                "metadata": {
                    "chunk_type": "section",
                    "level": 1,
                    "level_name": "Level1",
                    "chapter_number": section.chapter_number,
                    "title": section.title,
                    "project_id": project_id
                }
            })

    return chunks


# ============== MinerU PDF 解析接口（简化版） ==============

@graph_bp.route('/pdf/mineru-parse', methods=['POST'])
def mineru_parse():
    """
    MinerU PDF 解析接口

    请求: JSON { "project_id": "xxx", "filename": "xxx.pdf" }
    流程:
      1. 从项目目录读取 PDF
      2. 调用 MinerU API
      3. 解析返回的 layout 数据，提取 bbox
      4. 保存到 chunks.json
      5. 返回结构化结果

    Response:
        {
            "success": true,
            "data": {
                "project_id": "xxx",
                "filename": "xxx.pdf",
                "total_pages": 3,
                "total_layout_blocks": 50,
                "chunks": [
                    {
                        "chunk_id": "layout_0_1_50_0",
                        "page_idx": 0,
                        "type": "text|title|table|figure",
                        "content": "...",
                        "bbox_pdf": [x0, y0, x1, y1],
                        "bbox_viewport": [x0, page_h-y1, x1, page_h-y0],
                        "page_width": 595,
                        "page_height": 842,
                        "category_id": 0|1|2|3|4|5|6,
                        "is_layout_bbox": true,
                        "score": 0.99
                    },
                    ...
                ],
                "mineru_version": "xxx"
            }
        }
    """
    try:
        data = request.get_json() or {}
        project_id = data.get('project_id')
        filename = data.get('filename')

        if not project_id or not filename:
            return jsonify({"success": False, "error": "请提供 project_id 和 filename"}), 400

        # === 1. 查找 PDF 文件 ===
        project_dir = ProjectManager._get_project_dir(project_id)
        pdf_path = None
        for root, dirs, f_list in os.walk(project_dir):
            for f in f_list:
                if filename.lower() in f.lower() or f.lower() in filename.lower():
                    pdf_path = os.path.join(root, f)
                    break
            if pdf_path:
                break

        if not pdf_path or not os.path.exists(pdf_path):
            return jsonify({"success": False, "error": f"PDF 文件未找到: {filename}"}), 404

        # === 2. 调用 MinerU API ===
        try:
            mineru_data, pdf_path = _call_mineru_api(project_id, filename)
        except FileNotFoundError as e:
            return jsonify({"success": False, "error": str(e)}), 404
        except Exception as e:
            if "连接" in str(e) or "Connection" in str(e):
                return jsonify({"success": False, "error": "无法连接到 MinerU 服务"}), 503
            logger.error(f"MinerU API 返回错误: {e}")
            return jsonify({"success": False, "error": str(e)}), 502

        # === 3. 解析 MinerU 返回结果 ===
        pdf_info_list = mineru_data.get('info', {}).get('pdf_info', [])
        page_sizes = {}
        for info in pdf_info_list:
            page_idx = info.get('page_idx', 0)
            page_size = info.get('page_size', [])
            if len(page_size) == 2:
                page_sizes[page_idx] = page_size

        chunks = _parse_mineru_to_chunks(mineru_data, filename, pdf_path)

        text_count = len([c for c in chunks if c['category_id'] in (0, 1)])
        table_count = len([c for c in chunks if c['category_id'] == 2])
        logger.info(f"MinerU 解析完成: {text_count} 文本块 + {table_count} 表格块, {len(page_sizes)} 页")

        # === 4. 保存到 chunks.json ===
        ProjectManager.save_chunks(project_id, chunks)

        # === 5. 返回结果 ===
        return jsonify({
            "success": True,
            "data": {
                "project_id": project_id,
                "filename": filename,
                "total_pages": len(page_sizes),
                "total_layout_blocks": sum(len(lp.get('layout_dets', [])) for lp in mineru_data.get('layout', [])),
                "chunks": chunks,
                "mineru_version": mineru_data.get('info', {}).get('_version_name', 'unknown')
            }
        })

    except requests.exceptions.ConnectionError:
        return jsonify({"success": False, "error": "无法连接到 MinerU 服务"}), 503
    except Exception as e:
        logger.error(f"MinerU 解析失败: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


def _call_mineru_api(project_id: str, filename: str) -> tuple[Dict[str, Any], str]:
    """
    调用 MinerU API 解析 PDF，返回原始数据并保存到 mineru_parsed.json。
    供 mineru_parse 和 re_annotate 共用。

    Returns:
        mineru_data: MinerU API 返回的原始 JSON 数据
    Raises:
        FileNotFoundError: PDF 文件未找到
        Exception: MinerU API 调用失败
    """
    # 查找 PDF 文件
    project_dir = ProjectManager._get_project_dir(project_id)
    pdf_path = None
    for root, dirs, f_list in os.walk(project_dir):
        for f in f_list:
            if filename.lower() in f.lower() or f.lower() in filename.lower():
                pdf_path = os.path.join(root, f)
                break
        if pdf_path:
            break

    if not pdf_path or not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF 文件未找到: {filename}")

    with open(pdf_path, 'rb') as f:
        pdf_bytes = f.read()

    logger.info(f"MinerU 解析: project={project_id}, filename={filename}, size={len(pdf_bytes)} bytes")

    # 调用 MinerU API
    mineru_response = requests.post(
        Config.MINERU_API_URL,
        files={'pdf_file': (filename, pdf_bytes, 'application/pdf')},
        timeout=300
    )

    if mineru_response.status_code != 200:
        raise Exception(f"MinerU API 返回错误: {mineru_response.status_code}")

    mineru_data = mineru_response.json()

    # 保存原始 MinerU 解析结果
    ProjectManager.save_mineru_parsed(project_id, mineru_data)
    logger.info(f"MinerU 原始结果已保存: mineru_parsed.json")

    return mineru_data, pdf_path


@graph_bp.route('/pdf/re-annotate', methods=['POST'])
def re_annotate():
    """
    重新标注：从 mineru_parsed.json 重新生成 chunks.json（异步任务）

    请求: JSON { "project_id": "xxx" }
    流程:
      1. 创建任务，立即返回 task_id
      2. 后台线程执行：读取/调用 MinerU → 解析 → 保存 chunks.json
      3. 通过 SSE 将每一步日志推送到前端

    Response:
        {
            "success": true,
            "data": { "project_id": "xxx", "task_id": "xxx" }
        }
    """
    data = request.get_json() or {}
    project_id = data.get('project_id')

    if not project_id:
        return jsonify({"success": False, "error": "请提供 project_id"}), 400

    # 创建任务
    task_manager = TaskManager()
    task_id = task_manager.create_task("re-annotate", metadata={"project_id": project_id})
    task_manager.update_task(task_id, status=TaskStatus.PROCESSING, message="🚀 准备重新标注...")

    # 异步执行
    def do_re_annotate():
        try:
            _do_re_annotate_work(task_id, project_id)
        except Exception as e:
            task_manager.fail_task(task_id, str(e))

    threading.Thread(target=do_re_annotate, daemon=True).start()

    return jsonify({
        "success": True,
        "data": {"project_id": project_id, "task_id": task_id}
    })


def _do_re_annotate_work(task_id: str, project_id: str):
    """重新标注的后台执行逻辑"""
    task_manager = TaskManager()

    task_manager.update_task(task_id, status=TaskStatus.PROCESSING,
                             message="📖 读取 mineru_parsed.json...",
                             log="读取 mineru_parsed.json")

    mineru_parsed = ProjectManager.get_mineru_parsed(project_id)

    if not mineru_parsed:
        # 没有原始数据，自动调用 MinerU API
        task_manager.update_task(task_id, message="🔍 未找到 mineru_parsed.json，自动调用 MinerU API...",
                                log="未找到 mineru_parsed.json，自动调用 MinerU API")
        project = ProjectManager.get_project(project_id)
        if not project or not project.files:
            task_manager.fail_task(task_id, "项目无文件记录，无法自动解析")
            return
        filename = project.files[0]["filename"]
        try:
            mineru_parsed, pdf_path = _call_mineru_api(project_id, filename)
            task_manager.update_task(task_id, message="✅ MinerU API 调用成功",
                                    log=f"✅ MinerU API 调用成功: {filename}")
        except FileNotFoundError as e:
            task_manager.fail_task(task_id, str(e))
            return
        except Exception as e:
            if "连接" in str(e) or "Connection" in str(e):
                task_manager.fail_task(task_id, "无法连接到 MinerU 服务")
            else:
                task_manager.fail_task(task_id, str(e))
            return
    else:
        task_manager.update_task(task_id, message="✅ mineru_parsed.json 已读取",
                                log="mineru_parsed.json 已读取")

    project = ProjectManager.get_project(project_id)

    if "files" in mineru_parsed:
        # 多文件格式
        task_manager.update_task(task_id, message="📄 解析多文件 MinerU 数据...",
                                log="解析多文件 MinerU 数据")
        all_chunks = []
        file_count = len(mineru_parsed["files"])
        for i, (orig_name, mineru_data) in enumerate(mineru_parsed["files"].items()):
            task_manager.update_task(task_id, message=f"📝 解析文件 {i+1}/{file_count}: {orig_name}",
                                    log=f"解析文件 {i+1}/{file_count}: {orig_name}")
            file_path = ''
            if project and project.files:
                for pf in project.files:
                    if orig_name in pf.get('path', '') or pf.get('filename', '') == orig_name:
                        file_path = pf.get('path', '')
                        break
            # 兜底：从项目 files/ 目录自动查找 PDF
            if not file_path:
                file_path = _resolve_pdf_path(project_id, orig_name)
            chunks = _parse_mineru_to_chunks(mineru_data, orig_name, file_path)
            all_chunks.extend(chunks)

            text_count = len([c for c in chunks if c['category_id'] in (0, 1)])
            table_count = len([c for c in chunks if c['category_id'] == 2])
            task_manager.update_task(task_id, message=f"✅ {orig_name}: {text_count} 文本块 + {table_count} 表格块",
                                    log=f"  ✅ {orig_name}: {text_count} 文本块 + {table_count} 表格块")

        ProjectManager.save_chunks(project_id, all_chunks)
        task_manager.update_task(task_id, message="💾 chunks.json 已保存",
                                log=f"💾 chunks.json 已保存，共 {len(all_chunks)} 块")
        task_manager.complete_task(task_id, result={
            "project_id": project_id,
            "filename": list(mineru_parsed["files"].keys())[0] if mineru_parsed["files"] else "",
            "total_chunks": len(all_chunks)
        })
    else:
        # 单文件格式
        filename = 'unknown.pdf'
        file_path = ''
        if project and project.files:
            filename = project.files[0].get('filename', filename)
            file_path = project.files[0].get('path', '')
            logger.info(f"[重新标注] project.files[0].path={file_path}, isfile={os.path.isfile(file_path) if file_path else 'N/A'}")
        # 兜底：从项目 files/ 目录自动查找 PDF
        if not file_path or not os.path.isfile(file_path):
            logger.info(f"[重新标注] 触发 _resolve_pdf_path (current path={file_path})")
            file_path = _resolve_pdf_path(project_id, filename)
            logger.info(f"[重新标注] _resolve_pdf_path 返回: {file_path}")

        task_manager.update_task(task_id, message=f"📝 解析 MinerU 数据: {filename}",
                                log=f"解析 MinerU 数据: {filename}")
        chunks = _parse_mineru_to_chunks(mineru_parsed, filename, file_path)
        ProjectManager.save_chunks(project_id, chunks)

        text_count = len([c for c in chunks if c['category_id'] in (0, 1)])
        table_count = len([c for c in chunks if c['category_id'] == 2])
        task_manager.update_task(task_id, message=f"✅ 解析完成: {text_count} 文本块 + {table_count} 表格块",
                                log=f"✅ 解析完成: {text_count} 文本块 + {table_count} 表格块")
        task_manager.update_task(task_id, message="💾 chunks.json 已保存",
                                log=f"💾 chunks.json 已保存，共 {len(chunks)} 块")
        task_manager.complete_task(task_id, result={
            "project_id": project_id,
            "filename": filename,
            "total_chunks": len(chunks)
        })


def _build_table_chunk(
    table_item: dict,
    table_bboxes_by_img: dict,
    table_first_by_page: dict,
    pdf_path: str,
    filename: str
) -> dict:
    """
    构建单个表格 chunk。

    这是构建表格 chunk 的唯一入口，所有解析路径都调用此函数。
    """
    page_idx = table_item.get('page_idx', 0)
    caption = table_item.get('table_caption', '') or ''
    img_path = table_item.get('img_path', '') or ''

    # 通过 img_path 精确匹配 bbox
    bbox = []
    img_key = img_path.split('/')[-1].split('.')[0]
    page_keys = [k for (pi, k) in table_bboxes_by_img if pi == page_idx]
    matched_key = None
    for (pi, k), b in table_bboxes_by_img.items():
        if pi == page_idx and img_key in k:
            bbox = list(b)
            matched_key = k
            break
    logger.info(
        f"[表格BBox] caption={caption[:30] if caption else '(空)'}, "
        f"img_key={img_key}, matched_key={matched_key}, bbox={bbox}"
    )
    # fallback：该页第一个 table bbox
    if not bbox and page_idx in table_first_by_page:
        bbox = list(table_first_by_page[page_idx])
        logger.warning(
            f"[表格BBox] 匹配失败fallback: caption={caption[:30] if caption else '(空)'}, "
            f"img_path={img_path}, img_key={img_key}, 该页keys={page_keys}"
        )
    elif not bbox:
        logger.warning(
            f"[表格BBox] 无bbox: caption={caption[:30] if caption else '(空)'}, img_path={img_path}"
        )

    # 修正颠倒的 bbox（y0 > y1 的情况）
    if bbox and len(bbox) >= 4 and bbox[1] > bbox[3]:
        logger.info(f"[表格提取] 修正颠倒bbox: {bbox} -> [{bbox[0]},{bbox[3]},{bbox[2]},{bbox[1]}]")
        bbox = [bbox[0], bbox[3], bbox[2], bbox[1]]

    # 用 PaddleOCR 提取表格内容
    table_content = ''
    logger.info(f"[表格提取] 开始: caption={caption[:30] if caption else '(空)'}, bbox={bbox}")
    if pdf_path and bbox:
        table_content = _extract_table_text_from_pdf(pdf_path, page_idx, bbox, caption)
        if not table_content:
            logger.warning(
                f"[表格提取] 失败: page={page_idx}, bbox={bbox}, "
                f"pdf={pdf_path}, caption={caption[:30] if caption else '(空)'}"
            )
    elif not pdf_path:
        logger.warning(
            f"[表格提取] 跳过（无PDF路径）: caption={caption[:30]}"
        )
    elif not bbox:
        logger.warning(
            f"[表格提取] 跳过（无BBox）: page={page_idx}, caption={caption[:30]}"
        )

    return {
        "chunk_id": "",  # caller 负责生成
        "page_idx": page_idx,
        "type": "table",
        "content": caption or '[表格]',
        "bbox_pdf": bbox,
        "bbox_viewport": bbox,
        "page_width": 595.3,
        "page_height": 841.9,
        "category_id": 2,
        "block_type": "table",
        "source": filename,
        "table_caption": caption,
        "table_img_path": img_path,
        "table_content": table_content
    }


def _parse_mineru_to_chunks(mineru_data: dict, filename: str, pdf_path: str = '') -> List[Dict[str, Any]]:
    """
    将 MinerU 原始数据解析为 chunks 结构。

    数据源：
    - 正文/标题：preproc_blocks 中 type=title/text，提取 lines/spans 的 content
    - 表格：content[] 中 type=table，取 table_caption + img_path
    """
    pdf_info_list = mineru_data.get('info', {}).get('pdf_info', [])

    # page_size 映射
    page_sizes: dict[int, list] = {}
    for info in pdf_info_list:
        page_sizes[info.get('page_idx', 0)] = info.get('page_size', [595, 842])

    chunks: List[Dict[str, Any]] = []

    # ---- 1. preproc_blocks → 正文/标题 chunks ----
    for page_info in pdf_info_list:
        page_idx = page_info.get('page_idx', 0)
        page_w, page_h = page_sizes.get(page_idx, [595, 842])
        for block in page_info.get('preproc_blocks', []):
            bt = block.get('type', 'text')
            if bt not in ('title', 'text'):
                continue
            bbox = block.get('bbox', [])
            lines_text = '\n'.join(
                ''.join(s.get('content', '') for s in line.get('spans', []))
                for line in block.get('lines', [])
            ).strip()
            if not lines_text:
                continue
            chunks.append({
                "chunk_id": f"chunk_{len(chunks)}",
                "page_idx": page_idx,
                "type": bt,
                "content": lines_text,
                "bbox_pdf": bbox,
                "bbox_viewport": bbox,
                "page_width": page_w,
                "page_height": page_h,
                "category_id": {'title': 0, 'text': 1}.get(bt, 1),
                "block_type": bt,
                "source": filename
            })

    # ---- 2. 表格：content[] + preproc_blocks 联合 ----
    # preproc_blocks.type=table 有 bbox，通过 img_path 精确匹配
    # 优先用 table_body 的 bbox（不含 caption），fallback 到 outer bbox
    table_bboxes_by_img: dict[tuple, list] = {}
    table_first_by_page: dict[int, list] = {}
    for page_info in pdf_info_list:
        page_idx = page_info.get('page_idx', 0)
        first_body_bbox = None
        for block in page_info.get('preproc_blocks', []):
            if block.get('type') != 'table':
                continue
            outer_bbox = block.get('bbox', [])
            if not outer_bbox or len(outer_bbox) < 4:
                continue
            # 收集 img_path 作为匹配 key
            img_keys = set()
            table_body_bbox = None
            for sub in block.get('blocks', []):
                if sub.get('type') == 'table_body':
                    table_body_bbox = sub.get('bbox', [])
                for line in sub.get('lines', []):
                    for span in line.get('spans', []):
                        if span.get('image_path'):
                            img_keys.add(span['image_path'])
            # 优先用 table_body bbox（不含 caption），无则用 outer bbox
            use_bbox = table_body_bbox if table_body_bbox and len(table_body_bbox) >= 4 else outer_bbox
            for k in img_keys:
                table_bboxes_by_img[(page_idx, k)] = use_bbox
            if first_body_bbox is None:
                first_body_bbox = use_bbox
        if first_body_bbox is not None:
            table_first_by_page[page_idx] = first_body_bbox

    for item in mineru_data.get('content', []):
        if item.get('type') != 'table':
            continue
        page_w, page_h = page_sizes.get(item.get('page_idx', 0), [595, 842])
        chunk = _build_table_chunk(
            item, table_bboxes_by_img, table_first_by_page, pdf_path, filename
        )
        chunk["chunk_id"] = f"chunk_{len(chunks)}"
        chunk["page_width"] = page_w
        chunk["page_height"] = page_h
        chunks.append(chunk)

    return chunks


_pps_table_engine = None
_pps_table_engine_lock = threading.Lock()


def _get_pps_table_engine():
    """
    获取全局 PPStructure 表格识别引擎（线程安全单例）。

    使用本地 paddle_model 目录下的模型，GPU/CPU 自适应。
    """
    global _pps_table_engine
    if _pps_table_engine is not None:
        return _pps_table_engine

    with _pps_table_engine_lock:
        if _pps_table_engine is not None:
            return _pps_table_engine

        import os as _os
        import sys as _sys
        import io as _io

        # 强制 UTF-8 stdout
        if _sys.stdout.encoding != 'utf-8':
            _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding='utf-8')

        # 禁用 OneDNN / MKLDNN 避免 Filter 错误
        _os.environ['FLAGS_use_mkldnn'] = '0'
        _os.environ['FLAGS_fused_conv_bn_pass'] = '0'
        _os.environ['FLAGS_fused_conv_add_act_pass'] = '0'
        _os.environ['FLAGS_cudnn_exhaustive_search'] = '0'
        _os.environ['FLAGS_max_inplace_grad_add'] = '0'
        _os.environ['noavx'] = 'true'

        import paddle
        paddle.set_flags({'FLAGS_use_mkldnn': False})

        _use_gpu = paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0
        paddle.set_device('gpu:0' if _use_gpu else 'cpu')
        paddle.disable_static()

        from paddleocr import PPStructure

        # 本地模型路径
        _base = _os.path.join(_os.path.dirname(_sys.modules[__name__].__file__), '..', 'paddle_model')
        _det_model = _os.path.join(_base, 'det', 'ch', 'ch_PP-OCRv4_det_infer')
        _rec_model = _os.path.join(_base, 'rec', 'ch', 'ch_PP-OCRv4_rec_infer')
        _table_model = _os.path.join(_base, 'table', 'ch_ppstructure_mobile_v2.0_SLANet_infer')
        _cls_model = _os.path.join(_base, 'cls', 'ch_ppocr_mobile_v2.0_cls_infer')

        def _model_exists(p):
            """检查模型目录是否存在且包含 .pdmodel 文件"""
            if _os.path.exists(p):
                return bool(_os.listdir(p))
            return False

        _pps_table_engine = PPStructure(
            layout=False,           # 关闭版面分析，只用表格识别
            table=True,
            lang='ch',
            show_log=False,
            return_ocr_result_in_table=True,
            use_gpu=_use_gpu,
            use_angle_cls=False,    # cls 模型可能导致 OneDNN 错误，关闭
            enable_mkldnn=False,
            cpu_threads=4,
            det_db_thresh=0.3,
            det_db_box_thresh=0.5,
            det_db_unclip_ratio=1.6,
            det_model_dir=_det_model if _os.path.exists(_det_model) else None,
            rec_model_dir=_rec_model if _os.path.exists(_rec_model) else None,
            table_model_dir=_table_model if _os.path.exists(_table_model) else None,
            cls_model_dir=_cls_model if _os.path.exists(_cls_model) else None,
        )

        logger.info(f"[PPStructure] 引擎初始化完成 (GPU={_use_gpu})")
        return _pps_table_engine


def _html_table_to_markdown(html_table: str) -> str:
    """
    将 PPStructure 返回的 HTML 表格转换为 Markdown 格式。

    HTML 结构示例:
    <table><thead><tr><th>xx</th>...</tr></thead><tbody><tr><td>xx</td>...</tr>...</tbody></table>
    """
    import re as _re

    rows = []
    # 提取所有 <tr>...</tr>
    tr_pattern = _re.compile(r'<tr[^>]*>(.*?)</tr>', _re.DOTALL)
    # 提取单元格内容，支持 <th> 和 <td>
    cell_pattern = _re.compile(r'<t[hd][^>]*>(.*?)</t[hd]>', _re.DOTALL)
    # 清理标签内残留的换行和多余空格
    clean = _re.compile(r'\s+')

    for tr in tr_pattern.findall(html_table):
        cells = cell_pattern.findall(tr)
        clean_cells = []
        for cell in cells:
            text = clean.sub(' ', cell).strip()
            # 保留 | 符号本身
            text = text.replace('|', '｜')
            clean_cells.append(text)
        if clean_cells:
            rows.append('| ' + ' | '.join(clean_cells) + ' |')

    if not rows:
        return ''

    # 生成表头分隔行
    col_count = rows[0].count('|') - 1
    sep = '| ' + ' | '.join(['---'] * col_count) + ' |'
    return '\n'.join([rows[0], sep] + rows[1:])


def _extract_table_text_from_pdf(
    pdf_path: str,
    page_idx: int,
    bbox: list,
    caption: str = ''
) -> str:
    """
    用 PaddleOCR PPStructure 从 PDF 指定区域提取表格内容。

    Args:
        pdf_path: PDF 文件路径
        page_idx: 0-based 页码
        bbox: [x0, y0, x1, y1] PDF 坐标系

    Returns:
        Markdown 格式的表格文字，失败时返回空字符串
    """
    try:
        import fitz
        import tempfile
        import os
        import cv2
        import numpy as np
    except ImportError as e:
        logger.warning(f"[PPStructure] 依赖缺失: {e}")
        return ''

    try:
        doc = fitz.open(pdf_path)
        if page_idx < 0 or page_idx >= len(doc):
            doc.close()
            return ''

        if not bbox or len(bbox) < 4:
            doc.close()
            return ''

        x0, y0_pdf, x1, y1_pdf = bbox

        # bbox 是 top-left 坐标系，直接作为 PyMuPDF clip
        clip = fitz.Rect(x0, y0_pdf, x1, y1_pdf)
        if clip.width <= 0 or clip.height <= 0:
            logger.warning(
                f"[PPStructure] clip 无效: bbox=[{x0},{y0_pdf},{x1},{y1_pdf}]"
            )
            doc.close()
            return ''

        logger.info(
            f"[PPStructure] 裁剪: caption={caption[:30] if caption else '(空)'}, "
            f"bbox=[{x0},{y0_pdf},{x1},{y1_pdf}], clip=[{clip.x0:.1f},{clip.y0:.1f},{clip.x1:.1f},{clip.y1:.1f}]"
        )

        # 3x 渲染
        pix = doc[page_idx].get_pixmap(matrix=fitz.Matrix(3.0, 3.0), clip=clip)
        doc.close()

        # 转 numpy BGR 图像
        img_bytes = pix.samples
        img = np.frombuffer(img_bytes, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

        # 调用 PPStructure 表格识别
        engine = _get_pps_table_engine()
        result = engine(img)
        logger.info(f"[PPStructure] caption={caption[:30] if caption else '(空)'}, 返回 {len(result)} 个结果: {[r.get('type') for r in result]}")

        # 解析结果：取 type=table 的条目
        for item in result:
            if item.get('type') == 'table':
                html = item.get('res', {}).get('html', '')
                if html:
                    logger.info(f"[PPStructure] caption={caption[:30] if caption else '(空)'}, 识别到表格 HTML，长度={len(html)}")
                    return _html_table_to_markdown(html)

        # 备选：取文本结果
        texts = []
        for item in result:
            if item.get('type') in ('text', 'table'):
                bbox_val = item.get('bbox', [])
                text = item.get('res', {}).get('text', '')
                if text and bbox_val:
                    texts.append(text)
                    logger.info(f"[PPStructure] caption={caption[:30] if caption else '(空)'}, 备选文本: {text[:100]}")
        if texts:
            return '\n'.join(texts)

        return ''

    except Exception as e:
        logger.warning(f"[PPStructure] caption={caption[:30] if caption else '(空)'}, 表格提取失败 page={page_idx} bbox={bbox}: {e}")
        return ''


def _mineru_cat_to_type(cat_id: int) -> str:
    """MinerU category_id -> 类型字符串"""
    return {0: 'title', 1: 'text', 2: 'table', 3: 'figure', 4: 'table', 5: 'figure', 6: 'math'}.get(cat_id, 'text')


def _extract_nouns_from_chunks(
    content_chunks: list,
    project_id: str,
    task_id: str,
    logger
) -> int:
    """
    使用 LLM 从 MinerU content chunks 中提取名词实体。

    直接修改 chunks 列表中的 nouns 字段。

    Args:
        content_chunks: content chunks 列表（会被原地修改）
        project_id: 项目ID（用于日志）
        task_id: 任务ID（用于日志）
        logger: 日志记录器

    Returns:
        总名词数
    """
    from ..utils.llm_client import LLMClient

    if not content_chunks:
        return 0

    llm = LLMClient()
    BATCH_SIZE = 5
    total_nouns = 0

    for i in range(0, len(content_chunks), BATCH_SIZE):
        batch = content_chunks[i:i + BATCH_SIZE]
        batch_texts = []
        batch_ids = []

        for c in batch:
            text_preview = c.get('content', '')[:500]
            batch_texts.append(f"[{c.get('chunk_id', '?')}] {text_preview}")
            batch_ids.append(c.get('chunk_id'))

        prompt = f"""你是一个专业的工程标准文档分析助手。请从以下文档片段中提取所有名词实体（专业术语、定义的概念、设备名称、系统名称、材料名称、符号等）。

请以 JSON 格式返回：
{{
  "nouns": [
    {{"term": "术语名称", "definition": "简要定义（可选）"}},
    ...
  ]
}}

要求：
1. 只提取名词性实体，不要动词、形容词
2. 重点提取：导体、设备、系统、材料、符号、概念名称等
3. 每个片段最多返回 15 个名词
4. 如果没有明显名词，返回空列表
5. 只返回 JSON，不要其他文字

文档片段：
{{'='*60}}
{chr(10).join(batch_texts)}
{{'='*60}}"""

        messages = [
            {"role": "system", "content": "你是一个专业的工程标准文档分析助手。"},
            {"role": "user", "content": prompt}
        ]

        try:
            response = llm.chat(messages, temperature=0.3, max_tokens=2048)
            import re as regex_module
            json_match = regex_module.search(r'\{[\s\S]*\}', response)
            noun_terms = []
            if json_match:
                noun_data = json.loads(json_match.group())
                nouns_list = noun_data.get('nouns', [])
                noun_terms = [n.get('term', '') for n in nouns_list if n.get('term')]

            # 更新对应 chunks 的 nouns 字段
            for chunk in content_chunks:
                for nid in batch_ids:
                    if chunk.get('chunk_id') == nid:
                        chunk['nouns'] = noun_terms
                        break
            total_nouns += len(noun_terms)
            logger.info(f"[{task_id}] 名词提取批次 {i // BATCH_SIZE + 1}: {len(noun_terms)} 个名词")
        except Exception as llm_err:
            logger.warning(f"[{task_id}] LLM 名词提取批次 {i // BATCH_SIZE + 1} 失败: {llm_err}")

    return total_nouns


def _build_sections_from_chunks(chunks: list) -> list:
    """从 chunks 中提取章节信息（基于标题类型）"""
    sections = []
    seen_numbers = set()

    for c in chunks:
        if c.get('type') == 'title' and c.get('content'):
            content = c['content']
            # 匹配章节编号，如 "2 术语" 或 "3.1 电器的选择"
            import re
            m = re.match(r'^(\d+(?:\.\d+)?)\s+(.+)', content)
            if m:
                chapter_num_str = m.group(1)
                # 转换为浮点数确定章节层级
                parts = chapter_num_str.split('.')
                if len(parts) == 1:
                    chapter_num = int(parts[0])
                else:
                    chapter_num = float(chapter_num_str)

                if chapter_num not in seen_numbers:
                    sections.append({
                        "chapter_number": chapter_num,
                        "title": m.group(2),
                        "content": "",
                        "page_idx": c.get('page_idx', 0),
                        "bbox_viewport": c.get('bbox_viewport', [])
                    })
                    seen_numbers.add(chapter_num)

    return sections


def _build_clauses_from_chunks(chunks: list) -> list:
    """从 chunks 中提取条文信息"""
    clauses = []
    import re

    for c in chunks:
        if not c.get('content') or c.get('is_layout_bbox'):
            continue

        content = c['content']
        # 匹配条文编号，如 "2.0.1", "3.6.2", "1.0.1" 等
        m = re.match(r'^(\d+\.\d+(?:\.\d+)?)\s*(.*)', content)
        if m:
            clause_id = m.group(1)
            clause_title = m.group(2).strip()[:80] if m.group(2) else ''

            # 尝试判断 requirement_type（基于关键词）
            req_type = 'recommended'
            if any(kw in content for kw in ['应', '必须', '严禁', '不得', '应不', '不应', '不宜']):
                req_type = 'mandatory'
            elif any(kw in content for kw in ['宜', '可', '建议', '推荐']):
                req_type = 'recommended'
            elif any(kw in content for kw in ['禁止', '不应', '不得']):
                req_type = 'prohibited'

            clauses.append({
                "clause_id": clause_id,
                "clause_title": clause_title,
                "content": content,
                "requirement_type": req_type,
                "terms": c.get('nouns', []),
                "conditions": [],
                "actions": [],
                "components": [],
                "objects": [],
                "parent_chapter": None,
                "page_idx": c.get('page_idx', 0),
                "bbox_viewport": c.get('bbox_viewport', []),
                "nouns": c.get('nouns', []),
                "metadata": {
                    "category_id": c.get('category_id', 1),
                    "type": c.get('type', 'text'),
                    "source": "mineru"
                }
            })

    return clauses


@graph_bp.route('/pdf/mineru-parse/<project_id>', methods=['GET'])
def get_mineru_chunks(project_id: str):
    """
    获取 MinerU 解析结果（从 chunks.json）

    chunks.json 由 ontology/generate 或 mineru_parse 产生，
    包含 MinerU 的 layout + content + bbox + nouns 数据。

    Returns:
        {
            "success": true,
            "data": {
                "project_id": "xxx",
                "chunks": [...],
                "summary": {...}
            }
        }
    """
    try:
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({"success": False, "error": f"项目不存在: {project_id}"}), 404

        # 从 chunks.json 读取 MinerU 数据
        chunks_data = ProjectManager.get_chunks(project_id)
        if not chunks_data:
            return jsonify({"success": False, "error": "尚未执行 MinerU 解析，请先上传文档"}), 404

        # 检查是否包含 minerU 特征字段
        first_chunk = chunks_data[0] if chunks_data else {}
        has_mineru_fields = bool(first_chunk.get('chunk_id') and first_chunk.get('bbox_viewport'))

        if not has_mineru_fields:
            return jsonify({"success": False, "error": "chunks 不是由 MinerU 生成的"}), 400

        all_chunks = chunks_data
        content_chunks = [c for c in all_chunks if c.get('content') and not c.get('is_layout_bbox')]
        layout_bboxes = [c for c in all_chunks if c.get('is_layout_bbox')]

        # 从 chunks 中提取章节（基于标题类型）
        sections = _build_sections_from_chunks(all_chunks)
        # 从 chunks 中提取条文（基于条文编号）
        clauses = _build_clauses_from_chunks(all_chunks)

        # 收集所有名词
        all_nouns = set()
        for c in content_chunks:
            for n in c.get('nouns', []):
                all_nouns.add(n)

        return jsonify({
            "success": True,
            "data": {
                "project_id": project_id,
                "source": "mineru",
                "sections": sections,
                "clauses": clauses,
                "chunks": all_chunks,
                "summary": {
                    "total_pages": max((c.get('page_idx', 0) for c in all_chunks), default=0) + 1,
                    "total_chunks": len(all_chunks),
                    "content_chunks": len(content_chunks),
                    "layout_bboxes": len(layout_bboxes),
                    "total_nouns": len(all_nouns),
                    "unique_nouns": list(all_nouns)[:100]
                }
            }
        })
    except Exception as e:
        logger.error(f"获取 MinerU 解析结果失败: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============== Interface 1.5: 智能Chunks标注分析 ==============

@graph_bp.route('/chunk/intelligent', methods=['POST'])
def intelligent_chunk():
    """
    Phase 2.2: LLM 驱动的智能语义分析

    读取 MinerU 解析产生的 chunks.json，在其基础上：
    1. LLM 提取目录（章节结构）
    2. 逐章 LLM 提取条文 + 语义三元组（Component → Action → Obj @ Condition）
    3. 逐章 LLM 提取技术要素（Term/Component/Parameter/Formula）
    4. 建立关联关系：(Term + Situation) - Chunk - Page info
    5. 保存到 intelligent_chunks.json

    输入: chunks.json（MinerU 产生，含有 chunk_id/page_idx/bbox_viewport/category_id/nouns）
    输出: intelligent_chunks.json（语义三元组 + 章节 + 要素）

    Request (JSON):
        {
            "project_id": "proj_xxxx",      // Required
            "reset": false                  // Optional, 是否重置并重新分析
        }

    Response:
        {
            "success": true,
            "data": {
                "project_id": "proj_xxxx",
                "task_id": "task_xxxx",
                "message": "智能Chunks标注分析任务已启动"
            }
        }
    """
    try:
        logger.info("=== Starting 智能Chunks标注分析（增强版）===")

        data = request.get_json() or {}
        project_id = data.get('project_id')
        reset = data.get('reset', False)

        if not project_id:
            return jsonify({
                "success": False,
                "error": "请提供 project_id"
            }), 400

        # 获取项目
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"项目不存在: {project_id}"
            }), 404

        # 获取已解析的 chunks
        chunks_data = ProjectManager.get_chunks(project_id)
        if not chunks_data:
            # 尝试直接解析文本
            text = ProjectManager.get_extracted_text(project_id)
            if not text:
                return jsonify({
                    "success": False,
                    "error": "未找到 MinerU 解析产生的 chunks，请先上传文档触发 MinerU 解析"
                }), 400
            # 将文本转换为 chunks
            chunks_data = [{"text": text, "metadata": {"source": project.name or "document"}}]

        # 转换为 TextChunk 对象（兼容 minerU 格式：content 字段 vs 旧格式：text 字段）
        def _to_text_chunk(c):
            text = c.get("content") or c.get("text", "")
            metadata = c.get("metadata", {})
            # minerU 格式没有 metadata，把 chunk 字段透传到 metadata
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

        # 检查增强版检查点数据
        checkpoint_v2 = ProjectManager.get_chunk_checkpoint_v2(project_id)
        existing_task_id = project.graph_build_task_id

        # 如果需要重置，删除所有检查点和旧的智能分块结果
        if reset:
            # 删除旧的 intelligent_chunks.json（确保下次重新分析时生成新数据）
            existing_chunks = ProjectManager.get_intelligent_chunks(project_id)
            if existing_chunks:
                chunks_path = ProjectManager._get_intelligent_chunks_path(project_id)
                if os.path.exists(chunks_path):
                    os.remove(chunks_path)
                    logger.info(f"[{project_id}] 旧的 intelligent_chunks.json 已删除（重置）")

            if checkpoint_v2:
                ProjectManager.delete_chunk_checkpoint_v2(project_id)
                logger.info(f"[{project_id}] 增强版检查点数据已删除（重置）")
            # 同时删除旧版检查点（兼容性）
            old_checkpoint = ProjectManager.get_chunk_checkpoint(project_id)
            if old_checkpoint:
                ProjectManager.delete_chunk_checkpoint(project_id)
                logger.info(f"[{project_id}] 旧版检查点数据已删除（重置）")
            if existing_task_id:
                # 删除旧任务
                task_manager = TaskManager()
                task_manager._tasks.pop(existing_task_id, None)
                task_file = os.path.join(task_manager.TASKS_DIR, f"{existing_task_id}.json")
                if os.path.exists(task_file):
                    os.remove(task_file)
                logger.info(f"[{project_id}] 旧任务已删除: {existing_task_id}")
            existing_task_id = None

        # 检查是否已完成
        if not reset and project.status == ProjectStatus.GRAPH_CHUNKED:
            task = TaskManager().get_task(existing_task_id) if existing_task_id else None
            return jsonify({
                "success": True,
                "data": {
                    "project_id": project_id,
                    "task_id": existing_task_id,
                    "status": project.status.value,
                    "message": "LLM 分块已完成",
                    "task": task.to_dict() if task else None
                }
            })

        # 创建或恢复任务
        task_manager = TaskManager()
        if existing_task_id:
            # 恢复已有任务
            task_id = existing_task_id
            task = task_manager.get_task(task_id)
            if task and task.status == TaskStatus.PROCESSING:
                logger.info(f"[{project_id}] 恢复已有任务: {task_id}")
        else:
            # 创建新任务
            task_id = task_manager.create_task(
                task_type="llm_semantic_analysis",
                metadata={"project_id": project_id}
            )

        # 更新项目状态
        project.status = ProjectStatus.GRAPH_CHUNKING
        project.graph_build_task_id = task_id
        project.error = None
        ProjectManager.save_project(project)

        # 启动后台线程
        def chunking_task():
            try:
                from ..services.llm_driven_chunker import LLMDrivenChunker
                from ..services.llm_driven_chunker import LLMChunkerError

                chunker_logger = get_logger('mirofish.chunker')
                task_mgr = TaskManager()

                def progress_callback(progress, message, checkpoint_info=None):
                    """进度回调，支持检查点信息"""
                    # 更新任务进度
                    task_mgr.update_task(
                        task_id,
                        status=TaskStatus.PROCESSING,
                        progress=int(progress * 100),
                        message=message,
                        progress_detail=checkpoint_info or {}
                    )

                chunker_logger.info(f"[{task_id}] LLM 智能语义分析开始（基于 MinerU chunks）...")
                chunker_logger.info(f"[{task_id}] 待分析 chunks 数量: {len(text_chunks)}")

                task_mgr.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    progress=0,
                    message="🚀 开始智能Chunks标注分析..."
                )

                # 获取增强版检查点（用于恢复）
                checkpoint = ProjectManager.get_chunk_checkpoint_v2(project_id)

                # 执行 LLM 标注分析（支持章节级断点恢复）
                chunker = LLMDrivenChunker(progress_callback=progress_callback)
                result = chunker.chunk(
                    text_chunks,
                    progress_callback,
                    checkpoint=checkpoint,
                    project_id=project_id
                )

                # 保存分块结果（尝试序列化，失败时用检查点兜底）
                chunks_result = None
                save_success = False
                try:
                    chunks_result = {
                        "source": "llm",  # LLM 语义分析（基于 MinerU chunks）
                        "sections": [
                            {
                                "chapter_number": s.chapter_number,
                                "title": s.title,
                                "content": s.content
                            }
                            for s in result.sections
                        ],
                        # 使用 clause_to_dict 完整序列化（含 triplets、terms、clause_items 等核心语义字段）
                        # 同时按 clause_id 去重，保留第一条（内容最完整）
                        "clauses": (lambda seen_ids: [
                            c for c in (
                                clause_to_dict(c) for c in result.clauses
                            ) if c["clause_id"] not in seen_ids and not seen_ids.add(c["clause_id"])
                        ])(set()),
                        # 使用 element_to_dict 完整序列化
                        # 同时按 key + source_id 去重
                        "elements": (lambda seen_keys: [
                            e for e in (
                                element_to_dict(e) for e in result.elements
                            ) if (e.get("key") or "") + "|" + (e.get("source_id") or "") not in seen_keys
                            and not seen_keys.add((e.get("key") or "") + "|" + (e.get("source_id") or ""))
                        ])(set())
                    }
                    ProjectManager.save_intelligent_chunks(project_id, chunks_result)
                    chunker_logger.info(f"[{task_id}] ✅ intelligent_chunks.json 保存成功: "
                                        f"{len(chunks_result['sections'])} 章节, "
                                        f"{len(chunks_result['clauses'])} 条文, "
                                        f"{len(chunks_result['elements'])} 要素")
                    save_success = True
                except Exception as save_err:
                    chunker_logger.error(f"[{task_id}] 序列化 intelligent_chunks.json 失败: {save_err}，尝试从检查点兜底...")
                    # 兜底方案：从检查点数据直接构建（确保已完成的工作不丢失）
                    try:
                        fallback_checkpoint = ProjectManager.get_chunk_checkpoint_v2(project_id)
                        if fallback_checkpoint:
                            chunks_result = {
                                "source": "llm_checkpoint_fallback",
                                "sections": [
                                    {"chapter_number": cp.chapter_number, "title": cp.title, "content": ""}
                                    for cp in fallback_checkpoint.chapter_plan
                                    if cp.status.value in ("completed", "processing")
                                ],
                                "clauses": fallback_checkpoint.completed_clauses or [],
                                "elements": fallback_checkpoint.completed_elements or [],
                            }
                            ProjectManager.save_intelligent_chunks(project_id, chunks_result)
                            chunker_logger.info(f"[{task_id}] ✅ 检查点兜底保存成功: "
                                                f"{len(chunks_result['sections'])} 章节, "
                                                f"{len(chunks_result['clauses'])} 条文, "
                                                f"{len(chunks_result['elements'])} 要素")
                            save_success = True
                        else:
                            chunker_logger.error(f"[{task_id}] 无法兜底：检查点数据也不存在")
                    except Exception as fallback_err:
                        chunker_logger.error(f"[{task_id}] 检查点兜底也失败: {fallback_err}")

                if save_success and chunks_result:
                    # 删除增强版检查点（任务完成）
                    ProjectManager.delete_chunk_checkpoint_v2(project_id)

                    # 更新项目状态
                    project = ProjectManager.get_project(project_id)
                    project.status = ProjectStatus.GRAPH_CHUNKED
                    ProjectManager.save_project(project)

                    # 完成任务
                    summary = (f"✅ 标注分析完成: {len(result.sections)} 章节, "
                               f"{len(chunks_result['clauses'])} 条文（去重后）, "
                               f"{len(chunks_result['elements'])} 要素（去重后）")
                    chunker_logger.info(f"[{task_id}] {summary}")

                    task_mgr.update_task(
                        task_id,
                        status=TaskStatus.COMPLETED,
                        progress=100,
                        message=summary,
                        log=summary,
                        result={
                            "sections": len(result.sections),
                            "clauses": len(result.clauses),
                            "elements": len(result.elements)
                        }
                    )
                else:
                    # 序列化完全失败，但 LLM 分析本身已完成
                    chunker_logger.warning(f"[{task_id}] intelligent_chunks.json 保存失败，但 LLM 分析已完成，记录结果")
                    task_mgr.update_task(
                        task_id,
                        status=TaskStatus.COMPLETED,
                        progress=100,
                        message=f"LLM 分析完成但文件保存失败: {len(result.sections)} 章节, "
                                f"{len(result.clauses)} 条文, {len(result.elements)} 要素",
                        result={
                            "sections": len(result.sections),
                            "clauses": len(result.clauses),
                            "elements": len(result.elements),
                            "save_failed": True
                        }
                    )

            except LLMChunkerError as e:
                chunker_logger.error(f"[{task_id}] LLM 分块失败: {e}")
                task_mgr.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    message=f"LLM 分块失败: {e}",
                    error=str(e)
                )
                # 更新项目状态
                project = ProjectManager.get_project(project_id)
                project.status = ProjectStatus.FAILED
                project.error = str(e)
                ProjectManager.save_project(project)

            except Exception as e:
                chunker_logger.error(f"[{task_id}] 分块异常: {e}\n{traceback.format_exc()}")
                task_mgr.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    message=f"分块异常: {e}",
                    error=str(e)
                )

        thread = threading.Thread(target=chunking_task, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "data": {
                "project_id": project_id,
                "task_id": task_id,
                "message": "LLM 智能语义分析任务已启动（基于 MinerU chunks，支持章节级断点恢复）",
                "checkpoint_v2": checkpoint_v2 is not None,
                "has_checkpoint": checkpoint_v2 is not None or ProjectManager.get_chunk_checkpoint(project_id) is not None
            }
        })

    except Exception as e:
        logger.error(f"API Error: {str(e)}\n{traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@graph_bp.route('/chunk/<project_id>/progress', methods=['GET'])
def get_chunk_progress(project_id: str):
    """
    获取章节处理进度详情（增强版）

    Returns:
        {
            "success": true,
            "data": {
                "total_chapters": 10,
                "completed_chapters": 3,
                "processing_chapters": 1,
                "failed_chapters": 0,
                "pending_chapters": 6,
                "progress_ratio": 0.3,
                "current_chapter_index": 3,
                "current_chapter": {
                    "chapter_number": 4,
                    "title": "术语与符号",
                    "status": "processing",
                    ...
                },
                "chapter_plan": [...],
                "completed_clauses_count": 25,
                "completed_elements_count": 45,
                ...
            }
        }
    """
    try:
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"项目不存在: {project_id}"
            }), 404

        progress = ProjectManager.get_chapter_progress(project_id)
        if not progress:
            # 检查旧版检查点
            old_checkpoint = ProjectManager.get_chunk_checkpoint(project_id)
            if old_checkpoint:
                return jsonify({
                    "success": True,
                    "data": {
                        "legacy_checkpoint": True,
                        "message": "使用旧版检查点格式",
                        "checkpoint_data": old_checkpoint
                    }
                })

            return jsonify({
                "success": False,
                "error": "尚未开始分块处理或无检查点数据"
            }), 404

        return jsonify({
            "success": True,
            "data": progress
        })

    except Exception as e:
        logger.error(f"获取章节进度失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@graph_bp.route('/chunk/<project_id>', methods=['GET'])
def get_intelligent_chunks(project_id: str):
    """
    获取项目的 LLM 智能分块结果
    """
    project = ProjectManager.get_project(project_id)
    if not project:
        return jsonify({
            "success": False,
            "error": f"项目不存在: {project_id}"
        }), 404

    chunks = ProjectManager.get_intelligent_chunks(project_id)
    if not chunks:
        return jsonify({
            "success": False,
            "error": "尚未执行 LLM 分块"
        }), 200

    return jsonify({
        "success": True,
        "data": chunks
    })


@graph_bp.route('/chunk/<project_id>/has_intelligent_chunks', methods=['GET'])
def check_has_intelligent_chunks(project_id: str):
    """
    检查项目是否存在 intelligent_chunks.json 文件
    用于前端路由守卫：无文件时重定向到 chunk_analysis 页面
    """
    project = ProjectManager.get_project(project_id)
    if not project:
        return jsonify({
            "success": False,
            "error": f"项目不存在: {project_id}"
        }), 404

    chunks = ProjectManager.get_intelligent_chunks(project_id)
    return jsonify({
        "success": True,
        "data": {
            "has_intelligent_chunks": chunks is not None
        }
    })


@graph_bp.route('/chunk/<project_id>/analysis', methods=['GET'])
def get_chunk_analysis(project_id: str):
    """
    获取智能Chunks标注分析的格式化展示数据

    返回包含：
    - 统计摘要（章节/条文/要素数量）
    - 章节树形结构（带所属条文和要素）
    - 条文详情（含条件、动作、组件等语义信息）
    - 要素详情（含类型、值、单位等）
    """
    project = ProjectManager.get_project(project_id)
    if not project:
        return jsonify({
            "success": False,
            "error": f"项目不存在: {project_id}"
        }), 404

    chunks = ProjectManager.get_intelligent_chunks(project_id)
    if not chunks:
        return jsonify({
            "success": False,
            "error": "尚未执行 LLM 分块"
        }), 200

    sections = chunks.get('sections', [])
    clauses = chunks.get('clauses', [])
    elements = chunks.get('elements', [])

    # 构建章节树形结构
    chapter_tree = {}
    for section in sections:
        chapter_num = section.get('chapter_number')
        if chapter_num:
            # 获取该章节下的条文
            chapter_clauses = [
                c for c in clauses
                if c.get('parent_chapter') == chapter_num
            ]
            # 获取该章节下的要素
            chapter_elements = [
                e for e in elements
                if _get_element_parent_chapter(e) == chapter_num
            ]
            chapter_tree[chapter_num] = {
                "chapter_number": chapter_num,
                "title": section.get('title', ''),
                "content": section.get('content', ''),
                "clauses": chapter_clauses,
                "elements": chapter_elements,
                "clause_count": len(chapter_clauses),
                "element_count": len(chapter_elements)
            }

    # 按章节号排序（数字章节按数值排，appendix/other 等非数字章节排在末尾）
    def _chapter_sort_key(item):
        key = item[0]
        if str(key).isdigit():
            return (0, int(key))
        return (1, str(key))
    sorted_chapters = sorted(chapter_tree.items(), key=_chapter_sort_key)

    # 条文统计
    requirement_stats = {"mandatory": 0, "recommended": 0, "prohibited": 0}
    for clause in clauses:
        req_type = clause.get('requirement_type', 'recommended')
        if req_type in requirement_stats:
            requirement_stats[req_type] += 1

    # 要素统计（支持 LLM 和 MinerU 两种格式）
    element_stats = {}
    for element in elements:
        # LLM 格式: element_type 字段
        elem_type = element.get('element_type', 'unknown')
        # MinerU 格式: type 字段（category_id）
        if elem_type == 'unknown':
            elem_type = element.get('type', 'noun_entity')
        element_stats[elem_type] = element_stats.get(elem_type, 0) + 1

    # 构建完整的条文详情列表（带要素关联 + PDF定位）
    storage = _get_storage()
    chapters = [v for _, v in sorted_chapters]  # 转为列表供 _get_clause_pdf_location 使用
    clause_details = []
    for clause in clauses:
        clause_id = clause.get('clause_id', '')
        # 获取该条文关联的要素（支持两种格式）
        related_elements = [
            e for e in elements
            if e.get('source_clause_id') == clause_id or e.get('chunk_id', '').startswith(f"chunk_{clause.get('page_idx', '')}")
        ]
        # 尝试从 Neo4j 获取 PDF 定位信息（episode 的 page/bbox/source）
        pdf_location = _get_clause_pdf_location(
            storage, project_id, clause_id,
            clause.get('parent_chapter'), chapters
        )
        clause_details.append({
            "clause_id": clause_id,
            "clause_title": clause.get('clause_title', ''),
            "content": clause.get('content', ''),
            "requirement_type": clause.get('requirement_type', 'recommended'),
            "parent_chapter": clause.get('parent_chapter'),
            "parent_chapter_title": _get_chapter_title(chapter_tree, clause.get('parent_chapter')),
            # 语义信息
            "conditions": clause.get('conditions', []),
            "actions": clause.get('actions', []),
            "components": clause.get('components', []),
            "objects": clause.get('objects', []),
            # 关联要素
            "related_elements": related_elements,
            "metadata": clause.get('metadata', {}),
            # PDF 定位信息
            "pdf_location": pdf_location
        })

    # 要素详情列表（支持 LLM 和 MinerU 两种格式）
    element_details = []
    for element in elements:
        # LLM 格式要素（element_type, key, value 等）
        if element.get('element_type'):
            element_details.append({
                "element_type": element.get('element_type', 'unknown'),
                "key": element.get('key', ''),
                "value": element.get('value', ''),
                "unit": element.get('unit', ''),
                "condition": element.get('condition', ''),
                "abbreviation": element.get('abbreviation', ''),
                "definition": element.get('definition', ''),
                "source_clause_id": element.get('source_clause_id', ''),
                "metadata": element.get('metadata', {})
            })
        else:
            # MinerU 格式要素（chunk_id, nouns, bbox_viewport 等）
            element_details.append({
                "element_type": "noun_entity",
                "chunk_id": element.get('chunk_id', ''),
                "page_idx": element.get('page_idx', 0),
                "content_preview": element.get('content_preview', ''),
                "nouns": element.get('nouns', []),
                "bbox_viewport": element.get('bbox_viewport', []),
                "category_id": element.get('category_id', 1),
                "type": element.get('type', 'text'),
                "source": "mineru"
            })

    # 获取 pdf_file: 查找项目中第一个 PDF 文件
    pdf_file = _get_project_pdf_filename(project)

    # 获取当前任务状态
    task_id = project.graph_build_task_id
    current_status = project.status.value
    task_status_value = None
    if task_id:
        task = TaskManager().get_task(task_id)
        if task:
            task_status_value = task.status.value

    return jsonify({
        "success": True,
        "data": {
            "pdf_file": pdf_file,
            "status": current_status,
            "task_id": task_id,
            "task_status": task_status_value,
            "summary": {
                "total_sections": len(sections),
                "total_clauses": len(clauses),
                "total_elements": len(elements),
                "requirement_stats": requirement_stats,
                "element_stats": element_stats,
                "source": chunks.get('source', 'llm')
            },
            "chapter_tree": [
                {"chapter": chapter, "clauses": clauses, "elements": elements}
                for chapter_num, (chapter, clauses, elements) in [
                    (num, (data, data.get('clauses', []), data.get('elements', [])))
                    for num, data in sorted_chapters
                ]
            ],
            "chapters": [
                {
                    "chapter_number": chapter_num,
                    "title": data.get('title', ''),
                    "clause_count": data.get('clause_count', 0),
                    "element_count": data.get('element_count', 0)
                }
                for chapter_num, data in sorted_chapters
            ],
            "clauses": clause_details,
            "elements": element_details
        }
    })


def _get_element_parent_chapter(element: Dict) -> Optional[int]:
    """从要素metadata中获取所属章节号"""
    metadata = element.get('metadata', {})
    return metadata.get('parent_chapter') or element.get('parent_chapter')


def _get_chapter_title(chapter_tree: Dict, chapter_num: Optional[int]) -> str:
    """获取章节标题"""
    if chapter_num and chapter_num in chapter_tree:
        return chapter_tree[chapter_num].get('title', '')
    return ''


def _get_project_pdf_filename(project) -> Optional[str]:
    """获取项目中第一个 PDF 文件名"""
    files = project.files or []
    for f in files:
        fname = f.get('saved_filename', '') or f.get('filename', '')
        if fname.lower().endswith('.pdf'):
            return fname
    return None


def _get_clause_pdf_location(
    storage, project_id: str, clause_id: str,
    parent_chapter: Optional[int], chapters: list
) -> Optional[Dict]:
    """
    尝试从 Neo4j 获取 clause 对应的 PDF 定位信息（page, bbox, source）。
    如果 graph 尚未构建，则基于章节结构估算位置。
    """
    try:
        graph_id = ProjectManager.get_project(project_id).graph_id
        if graph_id:
            # 从 Neo4j 查找该 clause 对应的 episode
            episodes = storage.get_all_clauses_with_metadata(graph_id, limit=1000)
            for ep in episodes:
                if ep.get('clause_id') == clause_id:
                    metadata = ep.get('metadata', {})
                    bbox = metadata.get('bbox') or ep.get('bbox')
                    page = metadata.get('page') or ep.get('page')
                    source = metadata.get('source') or ep.get('source') or ep.get('doc_name', '')
                    if page or bbox:
                        return {
                            "page": page or 1,
                            "bbox": bbox,
                            "source": source
                        }
    except Exception:
        pass

    # 回退：基于章节估算 PDF 位置
    if parent_chapter and chapters and str(parent_chapter).isdigit():
        # 按章节平均分配 PDF 页码（假设每个章节约 20 页）
        est_page = max(1, (int(parent_chapter) - 1) * 20 + 1)
        for ch in chapters:
            if ch.get('chapter_number') == parent_chapter:
                return {
                    "page": est_page,
                    "bbox": None,
                    "source": _get_project_pdf_filename(
                        ProjectManager.get_project(project_id)
                    ) or ''
                }
    return None


@graph_bp.route('/chunk/<project_id>/entity', methods=['PATCH'])
def update_clause_entity(project_id: str):
    """
    更新单个 clause 的知识实体（Term / Condition / Action / Component）。

    Request (JSON):
        {
            "clause_id": "3.2.1",
            "terms": ["导体", "截面积"],
            "conditions": ["短路条件下", "过负荷时"],
            "actions": ["承受", "选用"],
            "components": ["线路保护", "配电线路"]
        }

    Response:
        {
            "success": true,
            "message": "Clause entity updated",
            "data": { ... updated clause ... }
        }
    """
    try:
        data = request.get_json() or {}
        clause_id = data.get('clause_id')
        if not clause_id:
            return jsonify({
                "success": False,
                "error": "缺少 clause_id"
            }), 400

        # 加载 intelligent_chunks
        chunks = ProjectManager.get_intelligent_chunks(project_id)
        if not chunks:
            return jsonify({
                "success": False,
                "error": "尚未执行 LLM 分块，无可更新的数据"
            }), 404

        # 查找目标 clause
        clauses = chunks.get('clauses', [])
        target_idx = None
        for i, c in enumerate(clauses):
            if c.get('clause_id') == clause_id:
                target_idx = i
                break

        if target_idx is None:
            return jsonify({
                "success": False,
                "error": f"未找到 clause: {clause_id}"
            }), 404

        # 更新字段（只更新提供的字段）
        updated_clause = clauses[target_idx]
        if 'terms' in data:
            # 合并: 前端传字符串 → 保留原有 definition；前端传 dict → 用新的
            existing_terms = {t.get('term_name', '') if isinstance(t, dict) else t: t
                              for t in (updated_clause.get('terms') or [])}
            merged_terms = []
            for t in data['terms']:
                if isinstance(t, dict):
                    merged_terms.append(t)
                elif t in existing_terms:
                    merged_terms.append(existing_terms[t])
                else:
                    merged_terms.append({'term_name': t, 'definition': ''})
            updated_clause['terms'] = merged_terms
            updated_clause['conditions'] = data.get('conditions', updated_clause.get('conditions', []))
            updated_clause['actions'] = data.get('actions', updated_clause.get('actions', []))
            updated_clause['components'] = data.get('components', updated_clause.get('components', []))
            updated_clause['objects'] = data.get('objects', updated_clause.get('objects', []))
        else:
            for field in ('conditions', 'actions', 'components', 'objects', 'terms'):
                if field in data:
                    updated_clause[field] = data[field]

        # 保存 JSON 更新
        ProjectManager.save_intelligent_chunks(project_id, chunks)

        # 同步 Term 实体到 Neo4j
        if 'terms' in data and data['terms']:
            project = ProjectManager.get_project(project_id)
            if project and project.graph_id:
                storage = _get_storage()
                storage.sync_term_entities(project.graph_id, clause_id, updated_clause['terms'])

        return jsonify({
            "success": True,
            "message": "Clause entity updated",
            "data": updated_clause
        })

    except Exception as e:
        logger.error(f"更新 clause 实体失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============== Interface 2: Build Graph ==============

@graph_bp.route('/build', methods=['POST'])
def build_graph():
    """
    Interface 2: Build graph based on project_id

    Request (JSON):
        {
            "project_id": "proj_xxxx",  // Required: from interface 1
            "graph_name": "Graph name",    // Optional
            "chunk_size": 500,          // Optional, default 500
            "chunk_overlap": 50         // Optional, default 50
        }

    Response:
        {
            "success": true,
            "data": {
                "project_id": "proj_xxxx",
                "task_id": "task_xxxx",
                "message": "Graph build task started"
            }
        }
    """
    try:
        logger.info("=== Starting graph build ===")

        # Parse request
        data = request.get_json() or {}
        project_id = data.get('project_id')
        logger.debug(f"Request parameters: project_id={project_id}")

        if not project_id:
            logger.warning("Build graph failed: missing project_id")
            return jsonify({
                "success": False,
                "error": "Please provide project_id"
            }), 400

        # Get project (Force reload from disk to avoid stale state in memory)
        project = ProjectManager.get_project(project_id)
        if not project:
            logger.warning(f"Build graph failed: project {project_id} not found")
            return jsonify({
                "success": False,
                "error": f"Project does not exist: {project_id}"
            }), 404

        # Get configuration
        graph_name = data.get('graph_name', project.name or 'Knowledge EviGraph')
        chunk_size = data.get('chunk_size', project.chunk_size or Config.DEFAULT_CHUNK_SIZE)
        chunk_overlap = data.get('chunk_overlap', project.chunk_overlap or Config.DEFAULT_CHUNK_OVERLAP)
        use_semantic = data.get('semantic', False) # New: option for semantic chunking
        force = data.get('force', False)  # Force rebuild

        # Check project status
        logger.info(f"Project {project_id} status: {project.status}, force={force}")

        # Intelligent status fix: check if ontology data exists even if status is CREATED
        # We check if ontology is a dict and has at least one entity type
        has_ontology = (isinstance(project.ontology, dict) and
                       len(project.ontology.get("entity_types", [])) > 0)

        if project.status == ProjectStatus.CREATED and has_ontology:
            logger.info(f"Project {project_id} has valid ontology despite CREATED status. Auto-fixing status to ONTOLOGY_GENERATED.")
            project.status = ProjectStatus.ONTOLOGY_GENERATED
            ProjectManager.save_project(project)

        # Handle Existing Task vs New Task
        task_id = project.graph_build_task_id
        if not force and project.status in [ProjectStatus.GRAPH_BUILDING, ProjectStatus.GRAPH_CHUNKING, ProjectStatus.GRAPH_EMBEDDING, ProjectStatus.GRAPH_INDEXING] and task_id:
            logger.info(f"Project {project_id} building status detected. Attempting to ensure worker thread is active for task {task_id}.")
        else:
            # Create a brand new task
            task_manager = TaskManager()
            task_id = task_manager.create_task(f"Build graph: {graph_name}")
            logger.info(f"New graph build task created: task_id={task_id}, project_id={project_id}")

            # Update project status
            project.status = ProjectStatus.GRAPH_BUILDING
            project.graph_build_task_id = task_id
            ProjectManager.save_project(project)

        # If force rebuild, reset status and DELETE old graph data
        force_reset_statuses = [
            ProjectStatus.GRAPH_BUILDING,
            ProjectStatus.GRAPH_CHUNKING,
            ProjectStatus.GRAPH_EMBEDDING,
            ProjectStatus.GRAPH_INDEXING,
            ProjectStatus.FAILED,
            ProjectStatus.GRAPH_COMPLETED
        ]
        if force and project.status in force_reset_statuses:
            logger.info(f"Forcing rebuild for project {project_id}. Cleaning up old data...")

            # Physical cleanup of old graph in Neo4j if it exists
            if project.graph_id:
                try:
                    storage = _get_storage()
                    builder = GraphBuilderService(storage=storage)
                    builder.delete_graph(project.graph_id)
                    logger.info(f"Old graph {project.graph_id} deleted successfully.")
                except Exception as de:
                    logger.warning(f"Failed to delete old graph {project.graph_id}: {de}")

            # Reset project metadata for a clean start
            project.status = ProjectStatus.ONTOLOGY_GENERATED
            project.graph_id = None
            project.graph_build_task_id = task_id # Use the task we just created or recovered
            project.error = None
            ProjectManager.save_project(project)

        # Update project configuration
        project.chunk_size = chunk_size
        project.chunk_overlap = chunk_overlap
        project.use_semantic = use_semantic
        ProjectManager.save_project(project)

        # Get extracted text (legacy) or intelligent chunks (preferred)
        text = ProjectManager.get_extracted_text(project_id)
        intelligent_chunks = ProjectManager.get_intelligent_chunks(project_id)
        has_text = text and len(text.strip()) > 0
        has_intelligent = intelligent_chunks is not None

        # Debug: 检查文件是否存在
        text_path = ProjectManager._get_project_text_path(project_id)
        chunks_path = ProjectManager._get_intelligent_chunks_path(project_id)
        logger.info(f"[build] project={project_id}")
        logger.info(f"[build]   extracted_text.txt exists={os.path.exists(text_path)}, size={os.path.getsize(text_path) if os.path.exists(text_path) else 0}")
        logger.info(f"[build]   intelligent_chunks.json exists={os.path.exists(chunks_path)}, size={os.path.getsize(chunks_path) if os.path.exists(chunks_path) else 0}")
        logger.info(f"[build]   has_text={has_text} has_intelligent_chunks={has_intelligent}")

        if not has_text and not has_intelligent:
            logger.warning(f"Build graph failed: no text data for project {project_id}")
            return jsonify({
                "success": False,
                "error": "No text data found. Please go to '智能Chunks标注分析' first."
            }), 400

        # Log which data source will be used
        if has_intelligent:
            logger.info(f"Project {project_id} using intelligent_chunks.json (has {len(intelligent_chunks.get('clauses', []))} clauses)")
        else:
            logger.info(f"Project {project_id} using legacy extracted_text.txt")

        # Get storage in request context (background thread cannot access current_app)
        storage = _get_storage()

        # Start background task via helper
        _start_build_worker(project_id, task_id, storage, force=force)
        return jsonify({
            "success": True,
            "data": {
                "project_id": project_id,
                "task_id": task_id,
                "message": "Graph build task started. Query progress via /task/{task_id}"
            }
        })
        
    except Exception as e:
        logger.error(f"API Error: {str(e)}\n{traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Task Query Interface ==============

@graph_bp.route('/task/<task_id>', methods=['GET'])
def get_task(task_id: str):
    """
    Query task status
    """
    task = TaskManager().get_task(task_id)

    if not task:
        return jsonify({
            "success": False,
            "error": f"Task does not exist: {task_id}"
        }), 404

    return jsonify({
        "success": True,
        "data": task.to_dict()
    })


@graph_bp.route('/task/<task_id>/events', methods=['GET'])
def task_events(task_id: str):
    """
    Server-Sent Events (SSE) for real-time task updates.
    Provides incremental logs and status changes without polling full JSON.
    """
    from flask import Response
    import time

    task_manager = TaskManager()
    task = task_manager.get_task(task_id)

    if not task:
        return jsonify({"success": False, "error": "Task not found"}), 404

    def event_stream():
        # 1. First, send current full state once
        yield f"data: {json.dumps({'type': 'init', 'data': task.to_dict()})}\n\n"

        # 2. Subscribe to new events
        q = task_manager.subscribe(task_id)
        try:
            while True:
                # Wait for next event with timeout to prevent ghost connections
                try:
                    event_data = q.get(timeout=30.0)
                    yield f"data: {json.dumps({'type': 'update', 'data': event_data}, ensure_ascii=False)}\n\n"

                    # Close connection if task is finished
                    if event_data.get('status') in ['completed', 'failed']:
                        break
                except queue.Empty:
                    # Send keep-alive ping
                    yield ": ping\n\n"
        finally:
            task_manager.unsubscribe(task_id, q)

    return Response(event_stream(), mimetype='text/event-stream')


@graph_bp.route('/tasks', methods=['GET'])
def list_tasks():
    """
    List all tasks
    """
    tasks = TaskManager().list_tasks()
    
    return jsonify({
        "success": True,
        "data": [t.to_dict() for t in tasks],
        "count": len(tasks)
    })


# ============== Graph Data Interface ==============

@graph_bp.route('/data/<graph_id>', methods=['GET'])
def get_graph_data(graph_id: str):
    """
    Get graph data (nodes and edges)
    """
    try:
        storage = _get_storage()
        builder = GraphBuilderService(storage=storage)
        graph_data = builder.get_graph_data(graph_id)

        return jsonify({
            "success": True,
            "data": graph_data
        })

    except Exception as e:
        logger.error(f"API Error: {str(e)}\n{traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@graph_bp.route('/delete/<graph_id>', methods=['DELETE'])
def delete_graph(graph_id: str):
    """
    Delete graph
    """
    try:
        storage = _get_storage()
        builder = GraphBuilderService(storage=storage)
        builder.delete_graph(graph_id)

        return jsonify({
            "success": True,
            "message": f"Graph deleted: {graph_id}"
        })

    except Exception as e:
        logger.error(f"API Error: {str(e)}\n{traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500



@graph_bp.route('/supplement', methods=['POST'])
def supplement_knowledge():
    """
    Supplement knowledge by processing specific regions of a PDF.
    Expects: { project_id, filename, regions: [{page, bbox: [x0, y0, x1, y1]}] }
    """
    try:
        data = request.get_json() or {}
        project_id = data.get('project_id')
        filename = data.get('filename')
        regions = data.get('regions', [])

        if not project_id or not filename or not regions:
            return jsonify({"success": False, "error": "Missing parameters"}), 400

        project = ProjectManager.get_project(project_id)
        if not project or not project.graph_id:
            return jsonify({"success": False, "error": "Project or Graph not found"}), 404

        # 1. Find the physical file
        project_dir = ProjectManager._get_project_dir(project_id)
        file_path = None
        found_filename = None

        # Robust recursive fuzzy search (same as get_project_document logic)
        search_name = filename.lower().strip()
        for root, dirs, files in os.walk(project_dir):
            for f in files:
                f_lower = f.lower().strip()
                # Strategy: Exact, substring or contains
                if f_lower == search_name or search_name in f_lower or f_lower in search_name:
                    file_path = os.path.join(root, f)
                    found_filename = f
                    break
            if file_path:
                break

        if not file_path:
            # Debug info: see what's actually there
            all_files = []
            for root, dirs, files in os.walk(project_dir):
                all_files.extend(files)
            logger.warning(f"Supplement lookup failed. Searching for '{search_name}'. Present files: {all_files}")
            return jsonify({
                "success": False,
                "error": f"Source file {filename} not found in project directory."
            }), 404

        logger.info(f"Supplementing using file: {file_path}")


        # 2. Extract text from specific regions using PyMuPDF
        import fitz
        from ..utils.file_parser import TextChunk

        supplementary_chunks = []
        doc = fitz.open(file_path)
        total_pages = len(doc)

        for i, reg in enumerate(regions):
            page_num = reg.get('page')
            bbox = reg.get('bbox')
            if not page_num or not bbox or len(bbox) != 4:
                continue

            # fitz pages are 0-indexed
            page = doc[page_num - 1]

            # Get page dimensions
            page_rect = page.rect
            pw, ph = page_rect.width, page_rect.height

            # Extract text from the specific rectangle
            text = page.get_textbox(fitz.Rect(bbox))

            if text.strip():
                supplementary_chunks.append(TextChunk(
                    text=text.strip(),
                    metadata={
                        "source": filename,
                        "page": page_num,
                        "total_pages": total_pages,
                        "bbox": bbox,
                        "page_width": pw,
                        "page_height": ph,
                        "type": "supplementary",
                        "supplement_index": i
                    }
                ))

        doc.close()

        if not supplementary_chunks:
            return jsonify({"success": False, "error": "No text found in selected regions"}), 400

        # 3. Trigger incremental graph building
        storage = _get_storage()
        builder = GraphBuilderService(storage=storage)

        # We process these chunks synchronously for the supplement feature to give immediate feedback
        episode_ids = builder.add_text_batches(
            project.graph_id,
            supplementary_chunks,
            batch_size=1
        )

        return jsonify({
            "success": True,
            "message": f"Successfully supplemented {len(supplementary_chunks)} knowledge fragments.",
            "episode_ids": episode_ids
        })

    except Exception as e:
        logger.error(f"Supplement failed: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@graph_bp.route('/ai-qa', methods=['POST'])
def ai_qa():
    """
    AI Q&A Interface: SSE streaming progress updates.
    """
    import time
    from flask import Response

    data = request.get_json() or {}
    query = data.get('query')
    graph_ids = data.get('graph_ids', [])
    try:
        filter_threshold = int(data.get('filter_threshold', 75))
    except (ValueError, TypeError):
        filter_threshold = 75

    try:
        max_depth = int(data.get('max_depth', 3))
    except (ValueError, TypeError):
        max_depth = 3

    root_types = data.get('root_types', ['Object', 'Term'])
    if isinstance(root_types, str):
        root_types = [root_types]

    if not query:
        return jsonify({"success": False, "error": "Please provide query"}), 400
    if not graph_ids:
        return jsonify({"success": False, "error": "Please select at least one knowledge base"}), 400

    from flask import stream_with_context
    storage = _get_storage()

    @stream_with_context
    def generate():
        start_total = time.time()
        try:
            tools = GraphToolsService(storage=storage)
            from ..utils.llm_client import LLMClient
            llm = LLMClient()

            # ===== Stage 0: 意图解析 =====
            intent = None
            intent_data = None
            try:
                from ..services.query_intent_parser import QueryIntentParser
                parser = QueryIntentParser()
                intent = parser.parse(query)
                intent_data = {
                    'type': intent.type,
                    'component': intent.component,
                    'action': intent.action,
                    'obj': intent.obj,
                    'condition': intent.condition,
                    'requirement': intent.requirement,
                    'confidence': intent.confidence,
                }
                logger.info(f"[Stage 0] Intent parsed: component={intent.component}, "
                            f"action={intent.action}, obj={intent.obj}, "
                            f"confidence={intent.confidence:.2f}")
            except Exception as e:
                logger.warning(f"[Stage 0] Intent parsing failed: {e}")

            # SSE 推送 intent 解析结果（供前端调试展示）
            yield f"data: {json.dumps({'type': 'intent_parsed', 'data': intent_data}, ensure_ascii=False)}\n\n"

            # ===== Stage 1: 知识库检索 =====
            yield f"data: {json.dumps({'type': 'retrieval_start'})}\n\n"
            retrieval_start = time.time()

            # 3-tier fallback: 意图有效（置信度>=0.6 且非空）→ 引导搜索；否则 → 原 DFS
            use_intent = (
                intent is not None
                and not intent.is_empty()
                and intent.confidence >= 0.6
            )
            if use_intent:
                dfs_result = tools.search_with_intent_guided_dfs_flow(
                    graph_ids=graph_ids, query=query, intent=intent,
                    limit=20, max_depth=max_depth, root_types=root_types
                )
            else:
                dfs_result = tools.search_with_dfs_flow(
                    graph_ids=graph_ids, query=query, limit=20, max_depth=max_depth,
                    root_types=root_types
                )
            ret_dur = round(time.time() - retrieval_start, 2)

            # 相似度阈值过滤 rows（参考 HitTest：按 relevance_score 过滤）
            similarity_threshold = int(data.get('similarity_threshold', 0))
            if similarity_threshold > 0:
                dfs_result.rows = [r for r in dfs_result.rows if (r.relevance_score or 0) >= similarity_threshold]

            # 收集所有 facts（供 LLM 打分用）
            all_facts = []
            for row in dfs_result.rows:
                all_facts.extend(row.facts)

            logger.info(f"[Stage 1] 知识库检索完成: rows:{len(dfs_result.rows)}, facts:{len(all_facts)}, duration={ret_dur}s, sim_thresh={similarity_threshold}")

            # Add rows with traversal path info to the response
            rows_data = [row.to_dict() for row in dfs_result.rows]

            # Stage 1 Complete
            msg_ret = {
                'type': 'retrieval_complete',
                'data': {
                    'rows': rows_data,
                    'duration': ret_dur,
                    'timings': {
                        'object_s': ret_dur,
                        'term_s': 0,
                        'total_s': ret_dur
                    },
                }
            }
            yield f"data: {json.dumps(msg_ret, ensure_ascii=False)}\n\n"

            # ===== Stage 2: LLM 相关性重排 =====
            yield f"data: {json.dumps({'type': 'rerank_start'})}\n\n"
            rerank_start = time.time()

            # 调用共享方法（返回 RerankResult）
            rerank_result = tools.run_retrieval_flow(
                final_rows=dfs_result.rows,
                query=query,
                similarity_threshold=similarity_threshold,
                filter_threshold=filter_threshold,
            )
            rerank_dur = round(time.time() - rerank_start, 2)

            # SSE 发送所有打分 facts（供前端展示）
            if rerank_result.scored_facts:
                msg_rerank = {
                    'type': 'rerank_results',
                    'data': {
                        'facts': rerank_result.scored_facts,
                        'total': len(rerank_result.scored_facts),
                        'duration': f"{rerank_dur}s",
                    }
                }
                yield f"data: {json.dumps(msg_rerank, ensure_ascii=False)}\n\n"

            # ===== Stage 3: 阈值过滤 + LLM 推理 =====
            yield f"data: {json.dumps({'type': 'llm_start'})}\n\n"
            llm_start = time.time()

            # 构建 LLM prompt（使用过滤后的 rows）
            rows_for_llm = rerank_result.rows_with_filtered_facts
            facts_text = "\n\n".join(r.to_text() for r in rows_for_llm) if rows_for_llm else "未找到高于阈值的相关事实。"
            system_prompt = "你是一个专业的工程标准知识助手。你的任务是基于提供的多跳检索到的【知识参考详情】深度回答用户问题。\n\n回答要求：\n1. 请先在 <thought> 标签内分析所有检索到的条文关联，确引用的完整性。\n2. 给出最终结论，必须引用具体的条款编号（如：根据 7.6.49 条规定...）。\n3. 如果知识涉及多个关联条款，请理清它们的逻辑先后关系。\n4. 若信息不足，请如实告知缺失的具体标准名称或编号。"
            user_prompt = f"### 多跳检索结果汇总 (Context from Knowledge Graph):\n{facts_text}\n\n### 用户当前问题 (User Query):\n{query}\n\n请进行深度推理并回答："

            logger.info(f"[Stage 3] LLM 推理: input=facts:{len(rerank_result.filtered_facts)}, output=rows:{len(rerank_result.rows_with_filtered_facts)}")

            msg_prompts = {
                'type': 'prompts_ready',
                'data': {
                    'system': system_prompt,
                    'user': user_prompt,
                    'filtered_facts': rerank_result.filtered_facts,
                }
            }
            yield f"data: {json.dumps(msg_prompts, ensure_ascii=False)}\n\n"

            answer = llm.chat(messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ], temperature=data.get('temperature', 0.7))

            llm_dur = round(time.time() - llm_start, 2)
            logger.info(f"[Stage 3] LLM 推理完成: input=facts:{len(rerank_result.filtered_facts)}, output=rows:{len(rerank_result.rows_with_filtered_facts)}, duration={llm_dur}s")

            msg_final = {
                'type': 'llm_complete',
                'data': {
                    'answer': answer,
                    'duration': llm_dur,
                    'total_duration': round(time.time() - start_total, 2)
                }
            }
            yield f"data: {json.dumps(msg_final, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"AI Q&A stream failed: {str(e)}\n{traceback.format_exc()}")
            err_msg = {'type': 'error', 'message': str(e)}
            yield f"data: {json.dumps(err_msg, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype='text/event-stream')

