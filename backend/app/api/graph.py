"""
Graph-related API Routes
Uses project context mechanism with server-side state persistence
"""

import os
import json
import queue
import traceback
import threading
import requests
from typing import Dict, Optional, List
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
                    graph_id = builder.create_graph(name=project.name or 'MiroFish Graph')
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

            # 保存分块结果
            chunks_result = {
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

            # 保存到项目
            ProjectManager.save_intelligent_chunks(project_id, chunks_result)

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
        # Debug: list what we DID find to help diagnose
        all_files = []
        for root, dirs, files in os.walk(base_dir):
            for f in files:
                all_files.append(f)

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

        # Create project first
        project = ProjectManager.create_project(name=project_name)
        project.simulation_requirement = simulation_requirement

        # Save files to disk immediately (cannot do this in background thread as request context will be gone)
        saved_files = []
        for file in uploaded_files:
            if file and file.filename and allowed_file(file.filename):
                file_info = ProjectManager.save_file_to_project(
                    project.project_id,
                    file,
                    file.filename
                )
                saved_files.append(file_info)
                project.files.append({
                    "filename": file_info["original_filename"],
                    "size": file_info["size"]
                })

        if not saved_files:
            ProjectManager.delete_project(project.project_id)
            return jsonify({"success": False, "error": "No valid files uploaded"}), 400

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

                    # 解析 MinerU 返回
                    pdf_info_list = mineru_data.get('info', {}).get('pdf_info', [])

                    # 从 pdf_info_list 精确构建 chunks（跟着 lines 走）
                    # 构建 page_size 映射
                    page_sizes_map: dict[int, list] = {}
                    for page_info in pdf_info_list:
                        page_idx = page_info.get('page_idx', 0)
                        page_sizes_map[page_idx] = page_info.get('page_size')

                    # 把 lines 里的 spans.content 拼在一起
                    line_metadata = []
                    for page_info in pdf_info_list:
                        page_idx = page_info.get('page_idx', 0)
                        # page_w, page_h = page_sizes_map.get(page_idx)
                        para_blocks = page_info.get('para_blocks', [])

                        for para_block in para_blocks:
                            block_type = para_block.get('type')
                            lines = para_block.get('lines', [])
                            line_bbox = para_block.get('bbox')

                            if not lines:
                                continue

                            line_content = ""
                            for line in lines:
                                #line_bbox = line.get('bbox')
                                span_content = "".join(span.get('content', '') for span in line.get('spans', []))
                                line_content += '\n' + span_content

                            if line_content.strip():
                                line_metadata.append((line_content, line_bbox, page_idx, block_type))


                    # 使用 line_metadata 构建 all_chunks
                    for line_content, line_bbox, page_idx, block_type in line_metadata:
                        page_w, page_h = page_sizes_map.get(page_idx)
                        all_chunks.append({
                            "chunk_id": f"chunk_{idx}_{len(all_chunks)}",
                            "page_idx": page_idx,
                            "type": "text",
                            "content": line_content,
                            "bbox_pdf": line_bbox,
                            "bbox_viewport": line_bbox,
                            "page_width": page_w,
                            "page_height": page_h,
                            "category_id": 1,
                            "block_type": block_type,
                            "source": orig_name
                        })
                    
                    build_logger.info(f"[{task_id}] 文件 {orig_name} 提取 {len(line_metadata)} 行文本")




                    msg = f"[{task_id}] ✅ 已提取 {len(all_chunks)} 个块"
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

        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()

        logger.info(f"MinerU 解析: project={project_id}, filename={filename}, size={len(pdf_bytes)} bytes")

        # === 2. 调用 MinerU API ===
        mineru_response = requests.post(
            Config.MINERU_API_URL,
            files={'pdf_file': (filename, pdf_bytes, 'application/pdf')},
            timeout=300
        )

        if mineru_response.status_code != 200:
            logger.error(f"MinerU API 返回错误: {mineru_response.status_code} - {mineru_response.text[:500]}")
            return jsonify({"success": False, "error": f"MinerU API 返回错误: {mineru_response.status_code}"}), 502

        mineru_data = mineru_response.json()

        # === 3. 解析 MinerU 返回结果 ===
        layout_pages = mineru_data.get('layout', [])
        pdf_info_list = mineru_data.get('info', {}).get('pdf_info', [])

        # 构建 page_size 映射 (page_no -> [width, height])
        page_sizes = {}
        for info in pdf_info_list:
            page_idx = info.get('page_idx', 0)
            page_size = info.get('page_size', [])
            if len(page_size) == 2:
                page_sizes[page_idx] = page_size

        chunks = []

        # 从 content 中提取带文本的 chunks（主数据源）
        content_list = mineru_data.get('content', [])
        for content_item in content_list:
            page_idx = content_item.get('page_idx', 0)
            text = content_item.get('text', '').strip()
            if not text:
                continue

            page_w, page_h = page_sizes.get(page_idx, [595.3, 841.9])
            content_type = content_item.get('type', 'text')
            if content_type == 'title':
                category_id = 0
            elif content_type == 'table':
                category_id = 2
            elif content_type == 'figure':
                category_id = 3
            else:
                category_id = 1

            # 尝试从 layout 中找到对应的 bbox
            bbox_viewport = [0, 0, page_w, page_h]
            for layout_page in layout_pages:
                if layout_page.get('page_info', {}).get('page_no', 0) != page_idx:
                    continue
                for det in layout_page.get('layout_dets', []):
                    # 简单匹配：category_id 相同即复用
                    if det.get('category_id', 1) == category_id:
                        bbox = det.get('bbox', [])
                        if bbox and len(bbox) >= 4:
                            x0, y0, x1, y1 = bbox[:4]
                            bbox_viewport = [x0, page_h - y1, x1, page_h - y0]
                        break

            chunks.append({
                "chunk_id": f"chunk_{page_idx}_{category_id}_{len(chunks)}",
                "page_idx": page_idx,
                "type": content_type,
                "content": text,
                "bbox_viewport": bbox_viewport,
                "page_width": page_w,
                "page_height": page_h,
                "category_id": category_id,
                "source": filename,
                "nouns": []
            })

        logger.info(f"MinerU 解析完成: {len(chunks)} 个文本块, {len(page_sizes)} 页")

        # === 4. 保存到 chunks.json ===
        ProjectManager.save_chunks(project_id, chunks)

        # === 5. 返回结果 ===
        return jsonify({
            "success": True,
            "data": {
                "project_id": project_id,
                "filename": filename,
                "total_pages": len(page_sizes),
                "total_layout_blocks": sum(len(lp.get('layout_dets', [])) for lp in layout_pages),
                "chunks": chunks,
                "mineru_version": mineru_data.get('info', {}).get('_version_name', 'unknown')
            }
        })

    except requests.exceptions.ConnectionError:
        return jsonify({"success": False, "error": "无法连接到 MinerU 服务"}), 503
    except Exception as e:
        logger.error(f"MinerU 解析失败: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


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
                    chunker_logger.info(message)
                    # 更新任务进度
                    task_mgr.update_task(
                        task_id,
                        status=TaskStatus.PROCESSING,
                        progress=int(progress * 100),
                        message=message,
                        log=message,
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

                # 保存分块结果
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

                # 保存到项目
                ProjectManager.save_intelligent_chunks(project_id, chunks_result)

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

    # 按章节号排序
    sorted_chapters = sorted(chapter_tree.items(), key=lambda x: x[0])

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
    if parent_chapter and chapters:
        # 按章节平均分配 PDF 页码（假设每个章节约 20 页）
        est_page = max(1, (parent_chapter - 1) * 20 + 1)
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
        graph_name = data.get('graph_name', project.name or 'MiroFish Graph')
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
        rerank_threshold = int(data.get('rerank_threshold', 60))
    except (ValueError, TypeError):
        rerank_threshold = 60

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

            # 1. Start Optimization & Retrieval
            yield f"data: {json.dumps({'type': 'retrieval_start'})}\n\n"
            retrieval_start = time.time()

            # Perform retrieval
            # 使用 scope='both' 搜索 edges 和 nodes，确保能找到相关事实
            search_result = tools.search_with_agentic_flow(graph_ids=graph_ids, query=query, limit=20, scope='both')
            ret_dur = round(time.time() - retrieval_start, 2)

            # 2. Retrieval Complete
            msg_ret = {
                'type': 'retrieval_complete',
                'data': {
                    'facts': search_result.facts,
                    'duration': ret_dur
                }
            }
            yield f"data: {json.dumps(msg_ret, ensure_ascii=False)}\n\n"

            # 3. Rerank & Filtering
            # Filter facts based on threshold
            all_facts = search_result.facts
            filtered_facts = [f for f in all_facts if f.get('relevance_score', 0) >= rerank_threshold]

            # If nothing passes threshold, keep top 1 as safety
            if not filtered_facts and all_facts:
                filtered_facts = all_facts[:1]

            msg_rerank = {
                'type': 'rerank_complete',
                'data': {
                    'results': search_result.rerank_details,
                    'duration': 'incl.',
                    'filtered_count': len(filtered_facts),
                    'total_count': len(all_facts)
                }
            }
            yield f"data: {json.dumps(msg_rerank, ensure_ascii=False)}\n\n"

            # 4. LLM Generation Start
            yield f"data: {json.dumps({'type': 'llm_start'})}\n\n"
            llm_start = time.time()

            # Use FILTERED facts for the prompt
            from ..services.graph_tools import SearchResult
            # Temporary SearchResult object to use its to_text method
            temp_result = SearchResult(
                facts=filtered_facts,
                edges=[],
                nodes=[],
                query=query,
                total_count=len(filtered_facts)
            )

            facts_text = temp_result.to_text()
            system_prompt = "你是一个专业的工程标准知识助手。你的任务是基于提供的多跳检索到的【知识参考详情】深度回答用户问题。\n\n回答要求：\n1. 请先在 <thought> 标签内分析所有检索到的条文关联，确引用的完整性。\n2. 给出最终结论，必须引用具体的条款编号（如：根据 7.6.49 条规定...）。\n3. 如果知识涉及多个关联条款，请理清它们的逻辑先后关系。\n4. 若信息不足，请如实告知缺失的具体标准名称或编号。"
            user_prompt = f"### 多跳检索结果汇总 (Context from Knowledge Graph):\n{facts_text}\n\n### 用户当前问题 (User Query):\n{query}\n\n请进行深度推理并回答："

            # Store prompts for debugging/visibility in UI
            msg_prompts = {
                'type': 'prompts_ready',
                'data': {
                    'system': system_prompt,
                    'user': user_prompt
                }
            }
            yield f"data: {json.dumps(msg_prompts, ensure_ascii=False)}\n\n"

            answer = llm.chat(messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ], temperature=data.get('temperature', 0.7))

            llm_dur = round(time.time() - llm_start, 2)

            # 5. Final LLM Complete
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

