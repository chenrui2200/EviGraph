"""
Graph-related API Routes
通过 ProjectManager 管理项目状态，服务端 JSON 持久化存储。
"""

import os
import re
import json
import queue
import shutil
import traceback
import threading
import requests
from typing import Dict, Optional, List, Any, Union
from flask import request, jsonify, current_app, send_from_directory

from . import graph_bp
from .project_routes import _backfill_page_info
from ..config import Config
from ..services.ontology_generator import OntologyGenerator
from ..services.graph_builder import GraphBuilderService
from ..services.graph_tools import GraphToolsService
from ..services.text_processor import TextProcessor
from ..services.llm_driven_chunker import clause_to_dict, element_to_dict
from ..utils.file_parser import FileParser, TextChunk
from ..utils.logger import get_logger
from ..utils.api_utils import api_handler, success_response, error_response
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
                            # 路径安全验证：确保最终路径在允许目录内
                            if os.path.isfile(full):
                                real_path = os.path.realpath(full)
                                real_dir = os.path.realpath(files_dir)
                                if real_path.startswith(real_dir + os.sep):
                                    logger.info(f"[PDF路径解析] ✅ filename匹配: {full}")
                                    return full
                                else:
                                    logger.warning(f"[PDF路径解析] ⚠️ 路径穿越检测: {full}")
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
    """
    从 Flask app extensions 获取 Neo4jStorage 实例。

    Raises:
        ValueError: 如果 storage 未初始化（Neo4j 连接异常）
    """
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

                # DEBUG: 打印数据加载详情
                tree_path = ProjectManager._get_intelligent_chunks_tree_path(project_id)
                jsonl_path = ProjectManager._get_intelligent_chunks_jsonl_path(project_id)
                build_logger.info(f"[{task_id}] DEBUG: tree_file={os.path.exists(tree_path)}, jsonl={os.path.exists(jsonl_path)}")
                build_logger.info(f"[{task_id}] DEBUG: intelligent_chunks_data={bool(intelligent_chunks_data)}, "
                    f"sections={len(intelligent_chunks_data.get('sections', [])) if intelligent_chunks_data else 0}, "
                    f"clauses={len(intelligent_chunks_data.get('clauses', [])) if intelligent_chunks_data else 0}, "
                    f"elements={len(intelligent_chunks_data.get('elements', [])) if intelligent_chunks_data else 0}")
                if intelligent_chunks_data and intelligent_chunks_data.get('clauses'):
                    sample = intelligent_chunks_data['clauses'][0]
                    build_logger.info(f"[{task_id}] DEBUG: first clause: id={sample.get('clause_id')} "
                        f"topic={str(sample.get('topic',''))[:30]} entities={sample.get('entities', [])}")

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
                        elements=[]  # 不传 elements！Topic-Entity 关系已由 add_topic_and_entity_nodes 创建
                    )
                    total_chunks = hierarchical_result.total_chunks
                    build_logger.info(f"Using intelligent chunks: {len(sections)} sections, {len(clauses)} clauses, 0 elements (Topic-Entity via add_topic_and_entity_nodes)")
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
                        # 尝试获取 MinerU md_content 用于条款引用识别
                        mineru_data = ProjectManager.get_mineru_parsed(project_id)
                        md_content = None
                        if mineru_data and mineru_data.get('files'):
                            # 取第一个文件的 md_content
                            first_file = next(iter(mineru_data['files'].values()), None)
                            md_content = first_file.get('md_content') if first_file else None
                        hierarchical_result = TextProcessor.hierarchical_chunk(
                            initial_chunks,
                            md_content=md_content,
                            chunks_data=chunks_data,
                            pdf_path=_resolve_pdf_path(project_id, '')
                        )
                        total_chunks = hierarchical_result.total_chunks
                        build_logger.info(f"Hierarchical chunking complete: {total_chunks} chunks")
                    else:
                        # 最终降级：纯文本分块
                        build_logger.warning("chunks.json not found, falling back to plain text splitting")
                        text = ProjectManager.get_extracted_text(project_id)
                        mineru_data = ProjectManager.get_mineru_parsed(project_id)
                        md_content = None
                        if mineru_data and mineru_data.get('files'):
                            first_file = next(iter(mineru_data['files'].values()), None)
                            md_content = first_file.get('md_content') if first_file else None
                        hierarchical_result = TextProcessor.hierarchical_chunk_text(
                            text,
                            md_content=md_content,
                            pdf_path=_resolve_pdf_path(project_id, '')
                        )
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
                        message="Creating Neo4j graph...",
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
                    build_logger.info(f"[{task_id}] 开始写入Neo4j episodes (共 {hierarchical_result.total_chunks} 个)...")
                    episode_uuids = builder.add_hierarchical_chunks(
                        graph_id,
                        hierarchical_result,
                        progress_callback=add_progress_callback
                    )
                    build_logger.info(f"[{task_id}] ✅ Neo4j episodes写入完成: {len(episode_uuids)} 个")

                # Topic/Entity 节点已在 clause 写入时 inline 创建（_create_clause_entity_only 中），无需单独处理

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
                try:
                    graph_data = builder.get_graph_data(graph_id)
                    if graph_data.get("query_error"):
                        build_logger.warning(f"[{task_id}] ⚠️ get_graph_data返回部分数据: {graph_data.get('query_error')}")
                    else:
                        build_logger.info(f"[{task_id}] ✅ get_graph_data成功: nodes={graph_data.get('node_count')}, edges={graph_data.get('edge_count')}")
                except Exception as gd_err:
                    build_logger.error(f"[{task_id}] ❌ get_graph_data异常: {gd_err}")
                    graph_data = {"node_count": 0, "edge_count": 0, "nodes": [], "edges": []}

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
                # 从 clauses.metadata["topics"] 提取 elements（新结构：entities 在 topics 数组中）
                extracted_elements = []
                for c in result.clauses:
                    for tp in c.metadata.get("topics", []):
                        if isinstance(tp, dict):
                            for ent in tp.get("entities", []):
                                if isinstance(ent, str):
                                    extracted_elements.append({
                                        "element_type": "unknown",
                                        "key": ent,
                                        "value": "",
                                        "unit": "",
                                        "abbreviation": "",
                                        "definition": "",
                                        "source_clause_id": c.clause_id,
                                        "metadata": {}
                                    })
                                elif isinstance(ent, dict):
                                    extracted_elements.append({
                                        "element_type": ent.get("entity_type", "unknown"),
                                        "key": ent.get("name", ""),
                                        "value": ent.get("value", ""),
                                        "unit": ent.get("unit", ""),
                                        "abbreviation": ent.get("abbreviation", ""),
                                        "definition": ent.get("definition", ""),
                                        "source_clause_id": ent.get("clause_id", c.clause_id),
                                        "metadata": ent
                                    })
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
                            "scope_prefix": c.metadata.get("scope_prefix") if c.metadata else None,
                            "chapter": c.metadata.get("chapter") if c.metadata else None,
                            "metadata": c.metadata
                        }
                        for c in result.clauses
                    ],
                    "elements": extracted_elements,
                    "edges": getattr(result, 'edges', []) or []
                }
                ProjectManager.save_chunks_result(project_id, chunks_result)
                build_logger.info(f"[{recovery_task_id}] ✅ intelligent_chunks.jsonl 保存成功")
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
                            "elements": [
                                {
                                    "element_type": "unknown",
                                    "key": ent,
                                    "value": "",
                                    "unit": "",
                                    "source_clause_id": clause.get("clause_id"),
                                    "scope_prefix": clause.get("scope_prefix") or clause.get("metadata", {}).get("scope_prefix"),
                                    "chapter": clause.get("chapter") or clause.get("metadata", {}).get("chapter"),
                                    "metadata": {}
                                }
                                for clause in (fallback_checkpoint.completed_clauses or [])
                                for tp in (clause.get("metadata") or {}).get("topics", [])
                                for ent in (tp.get("entities") if isinstance(tp, dict) else [])
                                if isinstance(ent, str)
                            ] if fallback_checkpoint.completed_clauses else [],
                            "edges": [],
                        }
                        ProjectManager.save_chunks_result(project_id, chunks_result)
                        build_logger.info(f"[{recovery_task_id}] ✅ 检查点兜底保存成功（JSONL）")
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

                # 完成任务（统计从 clauses.metadata["topics"] 提取的实体数量）
                entity_count = sum(
                    sum(len(tp.get('entities', [])) for tp in c.metadata.get('topics', []) if isinstance(tp, dict))
                    for c in result.clauses
                )
                summary = f"✅ 标注分析完成: {len(result.sections)} 章节, {len(result.clauses)} 条文, {entity_count} 实体"
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
                        "entities": entity_count
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
    MinerU PDF 解析接口（异步任务，通过 SSE 推送进度）

    请求: JSON { "project_id": "xxx", "filename": "xxx.pdf", "parse_method": "auto|ocr" }
    流程:
      1. 创建任务，立即返回 task_id
      2. 后台线程执行：逐页调用 MinerU API → 解析 → 保存 chunks.json
      3. 通过 SSE 将每一步日志推送到前端

    Response:
        {
            "success": true,
            "data": { "project_id": "xxx", "task_id": "xxx" }
        }
    """
    data = request.get_json() or {}
    project_id = data.get('project_id')
    filename = data.get('filename')
    parse_method = data.get('parse_method', 'auto')  # 默认用 auto 以支持 return_images

    if not project_id or not filename:
        return jsonify({"success": False, "error": "请提供 project_id 和 filename"}), 400

    # 创建任务
    task_manager = TaskManager()
    task_id = task_manager.create_task("mineru_parse", metadata={"project_id": project_id, "filename": filename})
    task_manager.update_task(task_id, status=TaskStatus.PROCESSING, message="🚀 准备 MinerU 解析...")

    # 异步执行
    def do_mineru_parse():
        try:
            _do_mineru_parse_work(task_id, project_id, filename, parse_method)
        except Exception as e:
            task_manager.fail_task(task_id, str(e))

    threading.Thread(target=do_mineru_parse, daemon=True).start()

    return jsonify({
        "success": True,
        "data": {"project_id": project_id, "task_id": task_id}
    })


def _sanitize_response_bytes(obj: Any) -> Any:
    """
    递归将 dict/list/str 中的 bytes 对象转换为字符串。
    解决 MinerU API 返回 bytes 类型路径导致的 "Can't mix strings and bytes in path components" 错误。
    """
    if isinstance(obj, dict):
        return {k: _sanitize_response_bytes(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_response_bytes(item) for item in obj]
    elif isinstance(obj, bytes):
        try:
            return obj.decode('utf-8')
        except UnicodeDecodeError:
            return obj.decode('latin-1')
    return obj


def _parse_single_page(pdf_path: str, filename: str, api_page_num: int, parse_method: str = 'ocr') -> tuple[int, Optional[Dict[str, Any]], Optional[str]]:
    """
    并发解析单个页面，返回 (页码, 结果数据, 错误信息)

    Args:
        pdf_path: PDF 文件路径
        filename: 文件名
        api_page_num: 页码（0-based）
        parse_method: 解析方法，'auto' 或 'ocr'

    Returns:
        (api_page_num, page_result_dict, error_msg)
        - page_result_dict 包含: md_content, middle_json, page_num
        - error_msg 为 None 表示成功
    """
    with open(pdf_path, 'rb') as f:
        pdf_bytes = f.read()

    page_result, error_msg = _call_mineru_api(pdf_bytes, api_page_num, parse_method=parse_method)
    return api_page_num, page_result, error_msg


def _call_mineru_api(pdf_bytes: bytes, page_num: int, max_retries: int = 3, timeout: int = 600, parse_method: str = 'auto') -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    统一的 MinerU API 调用方法，带错误重试机制。

    Args:
        pdf_bytes: PDF 文件字节内容
        page_num: 页码（1-based，用于 start_page_id 和 end_page_id）
        max_retries: 最大重试次数
        timeout: 请求超时时间（秒）

    Returns:
        (page_result_dict, error_msg)
        - page_result_dict: MinerU 返回的解析结果
        - error_msg: 错误信息（成功时为 None）
    """
    import uuid
    temp_filename = f"temp_{uuid.uuid4().hex[:8]}.pdf"

    data = {
        'return_middle_json': 'true',
        'return_model_output': 'false',
        'return_md': 'true',
        'return_images': 'true',
        'return_content_list': 'false',
        'parse_method': parse_method,
        'lang_list': 'ch',
        'table_enable': 'true',
        'formula_enable': 'true',
        'backend': 'pipeline',
        'start_page_id': str(page_num),
        'end_page_id': str(page_num),
        'server_url': 'string',
    }

    last_error = None

    for attempt in range(max_retries):
        try:
            mineru_response = requests.post(
                Config.MINERU_API_URL,
                files={'files': (temp_filename, pdf_bytes, 'application/pdf')},
                data=data,
                timeout=timeout
            )

            if mineru_response.status_code != 200:
                last_error = f"HTTP {mineru_response.status_code}: {mineru_response.text[:200]}"
                logger.warning(f"[MinerU] 第 {page_num} 页请求失败 (尝试 {attempt + 1}/{max_retries}): {last_error}")
                continue  # 重试

            result = mineru_response.json()
            results = result.get('results', [])

            if not results:
                last_error = "Empty results"
                logger.warning(f"[MinerU] 第 {page_num} 页返回空结果 (尝试 {attempt + 1}/{max_retries})")
                continue  # 重试

            page_result = results[0]
            md_content = page_result.get('md_content', '')
            raw_images = page_result.get('images', {})
            middle_json = page_result.get('middle_json', {})

            # 如果内容为空，返回错误（不重试）
            if not md_content and not middle_json.get('pdf_info'):
                return None, "Empty page content"

            # 清理 bytes 字段，防止 "Can't mix strings and bytes in path components" 错误
            page_result = _sanitize_response_bytes(page_result)

            return {
                'page_num': page_num,
                'md_content': md_content,
                'middle_json': middle_json,
                'images': raw_images
            }, None

        except requests.exceptions.Timeout:
            last_error = f"Timeout after {timeout}s"
            logger.warning(f"[MinerU] 第 {page_num} 页请求超时 (尝试 {attempt + 1}/{max_retries})")
            continue  # 重试

        except requests.exceptions.ConnectionError as e:
            last_error = f"Connection error: {str(e)}"
            logger.warning(f"[MinerU] 第 {page_num} 页连接失败 (尝试 {attempt + 1}/{max_retries}): {last_error}")
            continue  # 重试

        except Exception as e:
            import traceback as tb
            error_traceback = tb.format_exc()
            last_error = str(e)
            logger.error(f"[MinerU] 第 {page_num} 页异常 (尝试 {attempt + 1}/{max_retries}): {last_error}\n{error_traceback}")
            break  # 不重试，其他异常

    return None, f"Max retries exceeded. Last error: {last_error}"


def _do_mineru_parse_work(task_id: str, project_id: str, filename: str, parse_method: str = 'ocr'):
    """MinerU 解析的后台执行逻辑（并发写入 jsonl）

    Args:
        task_id: 任务 ID
        project_id: 项目 ID
        filename: 文件名
        parse_method: 解析方法，'auto' 或 'ocr'
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    task_manager = TaskManager()

    # === 1. 查找 PDF 文件 ===
    project = ProjectManager.get_project(project_id)
    project_dir = ProjectManager._get_project_dir(project_id)
    pdf_path = None

    # 方式1: 优先使用 project.files 中的 path（如果存在且是有效的 PDF）
    if project and project.files:
        pf_path = project.files[0].get('path', '')
        if pf_path and os.path.isfile(pf_path):
            with open(pf_path, 'rb') as f:
                if f.read(5) == b'%PDF-':
                    pdf_path = pf_path
                    logger.info(f"[MinerU] 使用 project.files 中的 PDF 路径: {pdf_path}")

    # 方式2: 使用文件名匹配
    if not pdf_path:
        for root, dirs, f_list in os.walk(project_dir):
            for f in f_list:
                if filename.lower() in f.lower() or f.lower() in filename.lower():
                    pdf_path = os.path.join(root, f)
                    break
            if pdf_path:
                break

    # 方式3: 使用 magic bytes 查找
    if not pdf_path:
        for root, dirs, files in os.walk(project_dir):
            for f in files:
                fpath = os.path.join(root, f)
                if os.path.isfile(fpath):
                    try:
                        with open(fpath, 'rb') as fh:
                            if fh.read(5) == b'%PDF-':
                                pdf_path = fpath
                                logger.info(f"[MinerU] 通过 magic bytes 找到 PDF: {pdf_path}")
                                break
                    except Exception:
                        pass
            if pdf_path:
                break

    if not pdf_path or not os.path.exists(pdf_path):
        task_manager.fail_task(task_id, f"PDF 文件未找到: {filename}")
        return

    import fitz
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    doc.close()

    task_manager.update_task(
        task_id,
        message=f"📄 PDF 总页数: {total_pages}，开始并发解析 (方法: {parse_method})...",
        progress=0,
        log=f"PDF 总页数: {total_pages}，并发数: {min(8, total_pages)}"
    )

    # === 2. 并发调用 MinerU API 并写入 jsonl ===
    jsonl_path = os.path.join(project_dir, 'mineru_parsed.jsonl')
    successful_pages = 0
    completed_count = 0
    all_md_contents = []
    all_pdf_info = []
    results_map = {}  # 按页码排序

    # 使用 ThreadPoolExecutor 并发处理
    max_workers = min(8, total_pages)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有页面的解析任务
        # MinerU API 期望 0-based 索引：start_page_id=0 表示第一页
        future_to_page = {
            executor.submit(_parse_single_page, pdf_path, filename, page_idx, parse_method): page_idx
            for page_idx in range(total_pages)
        }

        for future in as_completed(future_to_page):
            api_page_num = future_to_page[future]
            completed_count += 1

            try:
                api_page_num, page_result, error_msg = future.result()

                if error_msg:
                    task_manager.update_task(
                        task_id,
                        message=f"⚠️ 第 {api_page_num} 页解析失败: {error_msg}",
                        progress=int((completed_count / total_pages) * 80),
                        log=f"第 {api_page_num} 页失败: {error_msg}"
                    )
                else:
                    # 不信任 MinerU 返回的 page_num（MinerU 的 page_idx 可能与实际 PDF 页码不符）
                    # 直接使用任务索引 api_page_num 作为确定性 0-based 页码（0, 1, 2, ..., total_pages-1）
                    page_result['page_num'] = api_page_num
                    # 写入 jsonl（每行一条记录）
                    with open(jsonl_path, 'a', encoding='utf-8') as jf:
                        jf.write(json.dumps(page_result, ensure_ascii=False) + '\n')

                    results_map[api_page_num] = page_result
                    if page_result.get('md_content'):
                        all_md_contents.append((api_page_num, page_result['md_content']))
                    all_pdf_info.extend(page_result.get('middle_json', {}).get('pdf_info', []))
                    successful_pages += 1

                    task_manager.update_task(
                        task_id,
                        message=f"✅ 第 {api_page_num}/{total_pages} 页解析成功 ({completed_count}/{total_pages})",
                        progress=int((completed_count / total_pages) * 80),
                        log=f"第 {api_page_num} 页成功，md_content 长度: {len(page_result.get('md_content', ''))}"
                    )

            except Exception as e:
                import traceback as tb
                error_traceback = tb.format_exc()
                logger.error(f"[MinerU] 第 {api_page_num} 页解析异常: {e}\n{error_traceback}")
                task_manager.update_task(
                    task_id,
                    message=f"⚠️ 第 {api_page_num} 页解析异常: {e}",
                    progress=int((completed_count / total_pages) * 80),
                    log=f"第 {api_page_num} 页异常: {e}\n{error_traceback}"
                )

    task_manager.update_task(
        task_id,
        message=f"📝 MinerU API 调用完成，成功 {successful_pages}/{total_pages} 页",
        progress=80,
        log=f"MinerU API 调用完成: {successful_pages}/{total_pages} 页，jsonl 已写入"
    )

    # 按页码排序拼接 md_content
    all_md_contents.sort(key=lambda x: x[0])
    sorted_md_content = '\n'.join([mc for _, mc in all_md_contents])

    # 构建合并后的 mineru_data
    mineru_data = {
        'md_content': sorted_md_content,
        'info': {
            'pdf_info': all_pdf_info,
            '_version_name': '2.1.10 (并发解析)',
            '_parse_type': 'pipeline',
        },
        'files': {
            filename: {
                'md_content': sorted_md_content,
                'info': {
                    'pdf_info': all_pdf_info,
                    '_version_name': '2.1.10 (并发解析)',
                    '_parse_type': 'pipeline',
                }
            }
        }
    }

    # === 3. 解析 MinerU 返回结果 ===
    task_manager.update_task(
        task_id,
        message="🔍 解析 MinerU 返回结果...",
        progress=90,
        log="开始解析 chunks"
    )

    # 使用 JSONL 路径（已按 page_num 排序），而非 mineru_data dict（并发乱序）
    chunks = _parse_mineru_to_chunks(jsonl_path, filename)

    # === 4. 保存 raw_text.txt ===
    md_content = mineru_data.get('md_content', '')
    if md_content:
        raw_text_path = os.path.join(ProjectManager._get_project_dir(project_id), 'raw_text.txt')
        with open(raw_text_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        task_manager.update_task(
            task_id,
            message="📄 raw_text.txt 已保存",
            progress=95,
            log=f"raw_text.txt 已保存: {len(md_content)} 字符"
        )

    # === 5. 保存到 chunks.json ===
    ProjectManager.save_chunks(project_id, chunks)

    text_count = len([c for c in chunks if c['category_id'] in (0, 1)])
    table_count = len([c for c in chunks if c['category_id'] == 2])

    # 完成任务
    result_data = {
        "project_id": project_id,
        "filename": filename,
        "total_pages": total_pages,
        "successful_pages": successful_pages,
        "text_chunks": text_count,
        "table_chunks": table_count,
        "mineru_version": mineru_data.get('info', {}).get('_version_name', 'unknown')
    }
    task_manager.complete_task(task_id, result_data)
    task_manager.update_task(
        task_id,
        message=f"✅ 解析完成！{text_count} 文本块 + {table_count} 表格块",
        progress=100,
        log=f"MinerU 解析完成: {text_count} 文本块 + {table_count} 表格块"
    )


@graph_bp.route('/pdf/re-annotate', methods=['POST'])
def re_annotate():
    """
    重新标注：从 mineru_parsed.json 重新生成 chunks.json（异步任务）

    请求: JSON { "project_id": "xxx", "parse_method": "auto|ocr" }
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
    parse_method = data.get('parse_method', 'auto')

    if parse_method not in ('auto', 'ocr'):
        return jsonify({"success": False, "error": "parse_method 必须是 'auto' 或 'ocr'"}), 400

    if not project_id:
        return jsonify({"success": False, "error": "请提供 project_id"}), 400

    # 创建任务
    task_manager = TaskManager()
    task_id = task_manager.create_task("re-annotate", metadata={"project_id": project_id, "parse_method": parse_method})
    task_manager.update_task(task_id, status=TaskStatus.PROCESSING, message=f"🚀 准备重新标注 (方法: {parse_method})...")

    # 异步执行
    def do_re_annotate():
        try:
            # 获取项目信息以获取文件名
            project = ProjectManager.get_project(project_id)
            if not project or not project.files:
                task_manager.fail_task(task_id, "项目不存在或没有文件")
                return

            filename = project.files[0].get('filename', '') or project.files[0].get('original_filename', '')
            if not filename:
                task_manager.fail_task(task_id, "无法获取文件名")
                return

            # 删除旧的解析文件，强制重新解析
            project_dir = ProjectManager._get_project_dir(project_id)
            for old_file in ['mineru_parsed.jsonl', 'chunks.json', 'raw_text.txt']:
                old_path = os.path.join(project_dir, old_file)
                if os.path.exists(old_path):
                    os.remove(old_path)
                    logger.info(f"[re-annotate] 已删除旧文件: {old_file}")

            # 调用完整的 MinerU 解析流程
            _do_mineru_parse_work(task_id, project_id, filename, parse_method)
        except Exception as e:
            logger.error(f"[re-annotate] 异常: {e}")
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
        # 没有 mineru_parsed.json 数据，无法重新生成 chunks.json
        task_manager.fail_task(task_id, "未找到 mineru_parsed.json，请先上传文档进行 MinerU 解析")
        return

    task_manager.update_task(task_id, message="✅ mineru_parsed.json 已读取",
                            log="mineru_parsed.json 已读取")

    project = ProjectManager.get_project(project_id)

    # 获取 pdf_path
    pdf_path = ''
    if project and project.files:
        pdf_path = project.files[0].get('path', '')
    if not pdf_path or not os.path.isfile(pdf_path):
        pdf_path = _resolve_pdf_path(project_id, project.files[0].get('filename', '') if project and project.files else '')

    # === 保存 raw_text.txt（新接口已直接返回包含表格的 md_content，无需 OCR） ===
    mineru_files = mineru_parsed.get('files', {})
    raw_text_path = os.path.join(ProjectManager._get_project_dir(project_id), 'raw_text.txt')
    raw_text_content = ''

    if mineru_files:
        # 多文件格式：取第一个文件的 md_content
        raw_text_content = list(mineru_files.values())[0].get('md_content', '')
    else:
        # 单文件格式：直接取 md_content
        raw_text_content = mineru_parsed.get('md_content', '')

    if raw_text_content:
        with open(raw_text_path, 'w', encoding='utf-8') as f:
            f.write(raw_text_content)
        task_manager.update_task(task_id, message="💾 raw_text.txt 已保存",
                                log=f"💾 raw_text.txt 已保存: {len(raw_text_content)} 字符")
        logger.info(f"[重新标注] raw_text.txt 已保存: {len(raw_text_content)} 字符")
    else:
        logger.warning("[重新标注] raw_text.txt 为空")

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
    构建单个表格 chunk（唯一入口）。

    Args:
        table_item: 表格项（含 page_idx, table_caption, img_path）
        table_bboxes_by_img: 图片名 → bbox 映射
        table_first_by_page: 页码 → 该页第一个表格信息
        pdf_path: PDF 文件路径
        filename: 文件名

    Returns:
        表格 chunk 字典（含 bbox, page_idx, chunk_id 等）
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

    # 表格内容已迁移至 MinerU table_html 字段，table_content 不再使用
    table_content = ''

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


def _merge_bboxes(bboxes: List[list]) -> list:
    """
    计算多个 bbox 的并集包围盒 [x0, y0, x1, y1] → [x0_min, y0_min, x1_max, y1_max]
    MinerU 0.7.1 中 block.bbox 只有第一行位置，需要从所有 lines 中取并集。
    """
    if not bboxes:
        return []
    xs = [b[0] for b in bboxes if len(b) >= 4]
    ys = [b[1] for b in bboxes if len(b) >= 4]
    xe = [b[2] for b in bboxes if len(b) >= 4]
    ye = [b[3] for b in bboxes if len(b) >= 4]
    if not xs:
        return []
    return [min(xs), min(ys), max(xe), max(ye)]


def _merge_line_bboxes(lines: List[dict], page_idx: int, block_bbox: list = None) -> list:
    """
    合并 lines 的 bbox，遇到跨页行（cross_page: true）时分组合并。

    MinerU 0.7.1 中 block.bbox 只有第一行位置，
    但跨页块中不同 page 的行 y 坐标完全不同，不能直接并集。
    策略：
    1. 优先检查 spans 中是否有 cross_page: true 的 span
    2. 如果有 cross_page span，只取当前页（无 cross_page 标记）的行
    3. 如果没有 cross_page span，用 block_bbox 高度判断是否跨页
    4. 真正跨页时，取当前页所在行（y0 >= median_y - 50）

    Args:
        lines: MinerU preproc_blocks 中的 lines 列表
        page_idx: 当前 page_idx
        block_bbox: MinerU block 的外层 bbox [x0, y0, x1, y1]，用于判断是否真正跨页

    Returns:
        合并后的 bbox [x0, y0, x1, y1]，跨页则取当前页所在行
    """
    if not lines:
        return []

    # 检查 spans 中是否有 cross_page: true（MinerU 0.7.1 中 cross_page 在 span 级别）
    def _has_cross_page_span(line: dict) -> bool:
        for span in line.get('spans', []):
            if span.get('cross_page'):
                return True
        return False

    has_cross_page = any(_has_cross_page_span(line) for line in lines)
    if has_cross_page:
        # 有跨页行，只取同一页的行（没有 cross_page 标记的）
        same_page_lines = []
        for line in lines:
            if _has_cross_page_span(line):
                continue  # 跳过跨页行
            if len(line.get('bbox', [])) >= 4:
                same_page_lines.append(line)
        if same_page_lines:
            return _merge_bboxes([line.get('bbox', []) for line in same_page_lines])
        # 全部都是 cross_page（异常），fallthrough 到后面处理

    # 用 block_bbox（块外层 bbox）判断是否真正跨页
    # MinerU PDF 一页高度约 841 像素
    if block_bbox and len(block_bbox) >= 4:
        bbox_height = block_bbox[3] - block_bbox[1]
        if bbox_height <= 841:
            # 块高度在一页以内，不跨页，直接并集
            return _merge_bboxes([line.get('bbox', []) for line in lines if len(line.get('bbox', [])) >= 4])

    # 跨页情况：按 y 坐标中位数分组
    # MinerU PDF 坐标：y 从上到下递增（约 0-841 为一页）
    # 跨页时，高 y 行在当前页，低 y 行在下一页
    all_y0 = [line.get('bbox', [0, 0])[1] for line in lines if len(line.get('bbox', [])) >= 4]
    if not all_y0:
        return []

    median_y = sorted(all_y0)[len(all_y0) // 2]

    # 找当前页的行（y0 >= median_y - 50 表示在当前页下方/附近）
    same_page_lines = [
        line for line in lines
        if len(line.get('bbox', [])) >= 4 and line['bbox'][1] >= median_y - 50
    ]
    if same_page_lines:
        return _merge_bboxes([line.get('bbox', []) for line in same_page_lines])

    # fallback：全部并集
    return _merge_bboxes([line.get('bbox', []) for line in lines if len(line.get('bbox', [])) >= 4])


def _parse_mineru_jsonl(jsonl_path: str, filename: str) -> List[Dict[str, Any]]:
    """
    直接从 JSONL 文件读取并解析为 chunks 结构（一次遍历 + 排序）。

    JSONL 每行格式: {"page_num": int, "md_content": str, "middle_json": {"pdf_info": [...]}}
    每条记录对应一页，按 page_num 升序排列。

    Args:
        jsonl_path: JSONL 文件路径
        filename: 源文件名

    Returns:
        chunks 列表
    """
    chunks: List[Dict[str, Any]] = []

    if not os.path.exists(jsonl_path):
        return chunks

    # 一次性读取所有页面数据，按 page_num 排序
    sorted_pages: List[tuple] = []  # (page_num, page_data)

    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                page_data = json.loads(line)
            except json.JSONDecodeError:
                continue
            page_num = page_data.get('page_num', 0)
            sorted_pages.append((page_num, page_data))

    # 按 page_num 排序（确保按阅读顺序处理）
    sorted_pages.sort(key=lambda x: x[0])

    # page_size 映射（page_num -> [width, height]）
    # 统一使用外层 page_num（0-based），不依赖 MinerU 的 pdf_info.page_idx
    page_sizes: dict[int, list] = {}
    for page_num, page_data in sorted_pages:
        middle_json = page_data.get('middle_json', {})
        for info in middle_json.get('pdf_info', []):
            page_sizes[page_num] = info.get('page_size', [595, 842])

    # 解析 chunks
    for page_num, page_data in sorted_pages:
        middle_json = page_data.get('middle_json', {})
        pdf_info_list = middle_json.get('pdf_info', [])
        # 该页所有图片的 base64（key: image_path, value: base64 data URI）
        images_base64: dict = page_data.get('images', {})

        for info in pdf_info_list:
            # 统一使用外层 page_num（0-based），不依赖 MinerU 的 pdf_info.page_idx
            page_w, page_h = page_sizes.get(page_num, [595, 842])

            for block in info.get('preproc_blocks', []):
                bt = block.get('type', 'text')

                # ---- 标题/正文块 ----
                if bt in ('title', 'text'):
                    lines = block.get('lines', [])
                    lines_text = '\n'.join(
                        ''.join(s.get('content', '') for s in line.get('spans', []))
                        for line in lines
                    ).strip()
                    if not lines_text:
                        continue
                    bbox = _merge_line_bboxes(lines, page_num, block.get('bbox', []))
                    chunks.append({
                        "chunk_id": f"chunk_{len(chunks)}",
                        "page_idx": page_num,
                        "type": bt,
                        "content": lines_text,
                        "bbox_pdf": bbox,
                        "bbox_viewport": bbox,
                        "page_width": page_w,
                        "page_height": page_h,
                        "category_id": {'title': 0, 'Title': 0, 'text': 1, 'Text': 1}.get(bt, 1),
                        "block_type": bt,
                        "source": filename
                    })

                # ---- 表格块 ----
                elif bt == 'table':
                    caption = ''
                    img_path = ''
                    table_html = ''
                    table_footnote = ''
                    all_sub_lines = list(block.get('lines', []))
                    outer_bbox = _merge_line_bboxes(block.get('lines', []), page_num, block.get('bbox', []))

                    for sub in block.get('blocks', []):
                        sub_type = sub.get('type', '')
                        if sub_type == 'table_caption':
                            for line in sub.get('lines', []):
                                for span in line.get('spans', []):
                                    content = span.get('content', '')
                                    if content:
                                        caption += content
                                all_sub_lines.append(line)
                        elif sub_type == 'table_body':
                            for line in sub.get('lines', []):
                                all_sub_lines.append(line)
                            for line in sub.get('lines', []):
                                for span in line.get('spans', []):
                                    if span.get('image_path'):
                                        img_path = span['image_path']
                                    if span.get('html') and not table_html:
                                        table_html = span['html']
                        elif sub_type == 'table_footnote':
                            for line in sub.get('lines', []):
                                all_sub_lines.append(line)
                                for span in line.get('spans', []):
                                    content = span.get('content', '')
                                    if content:
                                        table_footnote += content + '\n'

                    if len(all_sub_lines) > len(block.get('lines', [])):
                        merged_sub_bbox = _merge_line_bboxes(all_sub_lines, page_num, block.get('bbox', []))
                    else:
                        merged_sub_bbox = outer_bbox

                    use_bbox = merged_sub_bbox if merged_sub_bbox and len(merged_sub_bbox) >= 4 else outer_bbox

                    final_bbox = list(use_bbox)
                    if len(final_bbox) >= 4 and final_bbox[1] > final_bbox[3]:
                        final_bbox = [final_bbox[0], final_bbox[3], final_bbox[2], final_bbox[1]]

                    chunks.append({
                        "chunk_id": f"chunk_{len(chunks)}",
                        "page_idx": page_num,
                        "type": "table",
                        "content": caption or '[表格]',
                        "bbox_pdf": final_bbox,
                        "bbox_viewport": final_bbox,
                        "page_width": page_w,
                        "page_height": page_h,
                        "category_id": 2,
                        "block_type": "table",
                        "source": filename,
                        "table_caption": caption,
                        "table_img_path": img_path,
                        "table_content": table_html,
                        "table_footnote": table_footnote.rstrip('\n') if table_footnote else '',
                    })

                # ---- 图片块 ----
                elif bt == 'image':
                    caption = ''
                    img_path = ''
                    img_content = ''
                    all_sub_lines = list(block.get('lines', []))
                    outer_bbox = _merge_line_bboxes(block.get('lines', []), page_num, block.get('bbox', []))

                    for sub in block.get('blocks', []):
                        sub_type = sub.get('type', '')
                        if sub_type == 'image_caption':
                            # 图片标题
                            for line in sub.get('lines', []):
                                for span in line.get('spans', []):
                                    content = span.get('content', '')
                                    if content:
                                        caption += content
                                all_sub_lines.append(line)
                        elif sub_type == 'image_body':
                            # 图片本体：提取 image_path（用于匹配 base64）
                            for line in sub.get('lines', []):
                                for span in line.get('spans', []):
                                    if span.get('image_path'):
                                        img_path = span['image_path']
                                all_sub_lines.append(line)

                    # 用 image_path 查找 base64 作为 image_content
                    if img_path and img_path in images_base64:
                        img_content = images_base64[img_path]

                    if len(all_sub_lines) > len(block.get('lines', [])):
                        merged_sub_bbox = _merge_line_bboxes(all_sub_lines, page_num, block.get('bbox', []))
                    else:
                        merged_sub_bbox = outer_bbox

                    use_bbox = merged_sub_bbox if merged_sub_bbox and len(merged_sub_bbox) >= 4 else outer_bbox

                    final_bbox = list(use_bbox)
                    if len(final_bbox) >= 4 and final_bbox[1] > final_bbox[3]:
                        final_bbox = [final_bbox[0], final_bbox[3], final_bbox[2], final_bbox[1]]

                    chunks.append({
                        "chunk_id": f"chunk_{len(chunks)}",
                        "page_idx": page_num,
                        "type": "image",
                        "content": caption or '[图片]',
                        "bbox_pdf": final_bbox,
                        "bbox_viewport": final_bbox,
                        "page_width": page_w,
                        "page_height": page_h,
                        "category_id": 3,
                        "block_type": "image",
                        "source": filename,
                        "image_caption": caption,
                        "image_img_path": img_path,
                        "image_content": img_content,
                    })

    return chunks


def _parse_mineru_to_chunks(mineru_data_or_jsonl_path: Union[dict, str], filename: str, pdf_path: str = '') -> List[Dict[str, Any]]:
    """
    将 MinerU 数据解析为 chunks 结构。

    支持两种输入：
    - dict: mineru_data 字典（向后兼容）
    - str: JSONL 文件路径（新增路径，直接流式读取）

    数据源（MinerU 0.7.1 格式）：
    - 正文/标题：preproc_blocks 中 type=title/text，提取 lines/spans 的 content
    - 表格：preproc_blocks 中 type=table，从 blocks.table_caption 提取标题，
            从 blocks.table_body 提取 img_path 和 bbox，
            表格内容从 MinerU 的 table_html 字段直接提取
    """
    # 新路径：直接读取 JSONL 文件
    if isinstance(mineru_data_or_jsonl_path, str):
        return _parse_mineru_jsonl(mineru_data_or_jsonl_path, filename)

    # 旧路径：向后兼容 mineru_data 字典
    mineru_data = mineru_data_or_jsonl_path
    pdf_info_list = mineru_data.get('info', {}).get('pdf_info', [])

    # page_size 映射
    page_sizes: dict[int, list] = {}
    for info in pdf_info_list:
        page_sizes[info.get('page_idx', 0)] = info.get('page_size', [595, 842])

    chunks: List[Dict[str, Any]] = []

    # ---- 按阅读顺序遍历：每页内块按 MinerU 返回顺序处理 ----
    # 注意：MinerU API 逐页调用时返回的 pdf_info 中 page_idx 可能全为 0，
    # 因此使用 enumerate 索引作为真实页码
    for page_idx, page_info in enumerate(pdf_info_list):
        mineru_page_idx = page_info.get('page_idx', 0)
        page_w, page_h = page_sizes.get(mineru_page_idx, [595, 842])

        for block in page_info.get('preproc_blocks', []):
            bt = block.get('type', 'text')

            # ---- 标题/正文块 ----
            if bt in ('title', 'text'):
                lines = block.get('lines', [])
                lines_text = '\n'.join(
                    ''.join(s.get('content', '') for s in line.get('spans', []))
                    for line in lines
                ).strip()
                if not lines_text:
                    continue
                bbox = _merge_line_bboxes(lines, page_idx, block.get('bbox', []))
                chunks.append({
                    "chunk_id": f"chunk_{len(chunks)}",
                    "page_idx": page_idx,
                    "type": bt,
                    "content": lines_text,
                    "bbox_pdf": bbox,
                    "bbox_viewport": bbox,
                    "page_width": page_w,
                    "page_height": page_h,
                    "category_id": {'title': 0, 'Title': 0, 'text': 1, 'Text': 1}.get(bt, 1),
                    "block_type": bt,
                    "source": filename
                })

            # ---- 表格块 ----
            elif bt == 'table':
                # 从 sub-blocks 中提取 caption、img_path、bbox、html、footnote
                caption = ''
                img_path = ''
                table_html = ''
                table_footnote = ''
                # 收集所有 sub-blocks 的 lines，用于合并 bbox
                all_sub_lines = list(block.get('lines', []))
                # outer_bbox 取 block 外层 bbox（跨页时分组处理）
                outer_bbox = _merge_line_bboxes(block.get('lines', []), page_idx, block.get('bbox', []))

                for sub in block.get('blocks', []):
                    sub_type = sub.get('type', '')
                    # 提取表格标题文字
                    if sub_type == 'table_caption':
                        for line in sub.get('lines', []):
                            for span in line.get('spans', []):
                                content = span.get('content', '')
                                if content:
                                    caption += content
                                all_sub_lines.append(line)
                    # 提取图片路径、body bbox 和 HTML 表格内容
                    elif sub_type == 'table_body':
                        for line in sub.get('lines', []):
                            all_sub_lines.append(line)
                        for line in sub.get('lines', []):
                            for span in line.get('spans', []):
                                if span.get('image_path'):
                                    img_path = span['image_path']
                                # 提取 MinerU 返回的 HTML 表格内容
                                if span.get('html') and not table_html:
                                    table_html = span['html']
                    # 提取表注文字
                    elif sub_type == 'table_footnote':
                        for line in sub.get('lines', []):
                            all_sub_lines.append(line)
                            for span in line.get('spans', []):
                                content = span.get('content', '')
                                if content:
                                    table_footnote += content + '\n'

                # 合并 caption + body + footnote 所有 lines 的 bbox
                if len(all_sub_lines) > len(block.get('lines', [])):
                    merged_sub_bbox = _merge_line_bboxes(all_sub_lines, page_idx, block.get('bbox', []))
                else:
                    merged_sub_bbox = outer_bbox

                # 优先用合并后的 sub-blocks bbox，fallback 到 outer bbox
                use_bbox = merged_sub_bbox if merged_sub_bbox and len(merged_sub_bbox) >= 4 else outer_bbox

                # 修正颠倒的 bbox
                final_bbox = list(use_bbox)
                if len(final_bbox) >= 4 and final_bbox[1] > final_bbox[3]:
                    final_bbox = [final_bbox[0], final_bbox[3], final_bbox[2], final_bbox[1]]

                chunks.append({
                    "chunk_id": f"chunk_{len(chunks)}",
                    "page_idx": page_idx,
                    "type": "table",
                    "content": caption or '[表格]',
                    "bbox_pdf": final_bbox,
                    "bbox_viewport": final_bbox,
                    "page_width": page_w,
                    "page_height": page_h,
                    "category_id": 2,
                    "block_type": "table",
                    "source": filename,
                    "table_caption": caption,
                    "table_img_path": img_path,
                    "table_content": table_html,
                    "table_footnote": table_footnote.rstrip('\n') if table_footnote else '',
                })

    return chunks


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
                # 保留原始字符串作为章节编号
                chapter_num = chapter_num_str

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

    chunks.json 由 mineru_parse 产生，
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

