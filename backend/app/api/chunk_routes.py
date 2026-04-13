"""
Chunk Processing API Routes
Handles intelligent chunking and analysis
"""

import os
import json
import traceback
import threading
from typing import Dict, Optional
from flask import request, jsonify

from . import graph_bp
from ..utils.logger import get_logger
from ..utils.api_utils import api_handler, success_response, error_response
from ..models.project import ProjectManager, ProjectStatus
from ..models.task import TaskManager, TaskStatus

logger = get_logger('mirofish.api')


# ============== Intelligent Chunking ==============

@graph_bp.route('/chunk/intelligent', methods=['POST'])
@api_handler
def intelligent_chunk():
    """
    Phase 2.2: LLM 驱动的智能语义分析

    读取 MinerU 解析产生的 chunks.json，在其基础上：
    1. LLM 提取目录（章节结构）
    2. 逐章 LLM 提取条文 + 语义三元组
    3. 逐章 LLM 提取技术要素
    4. 建立关联关系
    5. 保存到 intelligent_chunks.json
    """
    from .graph import _resolve_pdf_path

    try:
        logger.info("=== Starting 智能Chunks标注分析（增强版）===")

        data = request.get_json() or {}
        project_id = data.get('project_id')
        reset = data.get('reset', False)

        if not project_id:
            return jsonify({"success": False, "error": "请提供 project_id"}), 400

        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({"success": False, "error": f"项目不存在: {project_id}"}), 404

        chunks_data = ProjectManager.get_chunks(project_id)
        if not chunks_data:
            text = ProjectManager.get_extracted_text(project_id)
            if not text:
                return jsonify({"success": False, "error": "未找到 MinerU 解析产生的 chunks，请先上传文档"}), 400
            chunks_data = [{"text": text, "metadata": {"source": project.name or "document"}}]

        def _to_text_chunk(c):
            from ..utils.file_parser import TextChunk
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

        checkpoint_v2 = ProjectManager.get_chunk_checkpoint_v2(project_id)
        existing_task_id = project.graph_build_task_id

        if reset:
            for path_attr in ('_get_intelligent_chunks_path', '_get_intelligent_chunks_jsonl_path', '_get_intelligent_chunks_tree_path'):
                path = getattr(ProjectManager, path_attr)(project_id)
                if os.path.exists(path):
                    os.remove(path)
                    logger.info(f"[{project_id}] 旧文件已删除（重置）: {os.path.basename(path)}")

            if checkpoint_v2:
                ProjectManager.delete_chunk_checkpoint_v2(project_id)
            old_checkpoint = ProjectManager.get_chunk_checkpoint(project_id)
            if old_checkpoint:
                ProjectManager.delete_chunk_checkpoint(project_id)
            if existing_task_id:
                task_manager = TaskManager()
                task_manager._tasks.pop(existing_task_id, None)
                task_file = os.path.join(task_manager.TASKS_DIR, f"{existing_task_id}.json")
                if os.path.exists(task_file):
                    os.remove(task_file)
            existing_task_id = None

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

        task_manager = TaskManager()
        if existing_task_id:
            task_id = existing_task_id
            task = task_manager.get_task(task_id)
            if task and task.status == TaskStatus.PROCESSING:
                logger.info(f"[{project_id}] 恢复已有任务: {task_id}")
        else:
            task_id = task_manager.create_task(task_type="llm_semantic_analysis", metadata={"project_id": project_id})

        project.status = ProjectStatus.GRAPH_CHUNKING
        project.graph_build_task_id = task_id
        project.error = None
        ProjectManager.save_project(project)

        def chunking_task():
            from ..services.llm_driven_chunker import LLMDrivenChunker, LLMChunkerError
            from ..services.llm_driven_chunker import clause_to_dict

            try:
                chunker_logger = get_logger('mirofish.chunker')
                task_mgr = TaskManager()

                def progress_callback(progress, message, checkpoint_info=None):
                    # progress < 0 表示不需要更新进度百分比，只推送日志消息
                    if progress >= 0:
                        task_mgr.update_task(task_id, status=TaskStatus.PROCESSING, progress=int(progress * 100), message=message, progress_detail=checkpoint_info or {})
                    else:
                        # 只更新日志消息，不改变进度
                        task_mgr.update_task(task_id, log=message)

                mineru_data = ProjectManager.get_mineru_parsed(project_id)
                md_content = None
                if mineru_data and mineru_data.get('files'):
                    first_file = next(iter(mineru_data['files'].values()), None)
                    md_content = first_file.get('md_content') if first_file else None

                checkpoint = ProjectManager.get_chunk_checkpoint_v2(project_id)
                pdf_path = _resolve_pdf_path(project_id, '')

                task_mgr.update_task(task_id, status=TaskStatus.PROCESSING, progress=0, message="🚀 开始 LLM 语义分块...")

                chunker = LLMDrivenChunker(progress_callback=progress_callback)
                result = chunker.chunk(text_chunks, progress_callback, checkpoint=checkpoint, project_id=project_id, md_content=md_content, chunks_data=chunks_data, pdf_path=pdf_path)

                chunks_result = None
                save_success = False
                try:
                    chunks_result = {
                        "source": "llm",
                        "sections": [{"chapter_number": s.chapter_number, "title": s.title, "content": s.content} for s in result.sections],
                        "clauses": (lambda seen_ids: [c for c in (clause_to_dict(c) for c in result.clauses) if c["clause_id"] not in seen_ids and not seen_ids.add(c["clause_id"])])(set()),
                        "elements": (lambda seen_keys: [{"element_type": "noun_entity", "key": ent if isinstance(ent, str) else "", "value": "", "unit": "", "abbreviation": "", "definition": "", "source_clause_id": c["clause_id"], "scope_prefix": c.get("scope_prefix") or c.get("metadata", {}).get("scope_prefix"), "chapter": c.get("chapter") or c.get("metadata", {}).get("chapter"), "metadata": {"name": ent if isinstance(ent, str) else ""}} for c in (clause_to_dict(c) for c in result.clauses) for ent in c.get("entities", []) if ent and (str(ent) + "|" + c["clause_id"]) not in seen_keys and not seen_keys.add(str(ent) + "|" + c["clause_id"])])(set()),
                        "edges": getattr(result, 'edges', []) or []
                    }
                    ProjectManager.save_chunks_result(project_id, chunks_result)
                    save_success = True
                except Exception as save_err:
                    chunker_logger.error(f"[{task_id}] 序列化失败: {save_err}")
                    try:
                        fallback_checkpoint = ProjectManager.get_chunk_checkpoint_v2(project_id)
                        if fallback_checkpoint:
                            fallback_elements = []
                            for clause in (fallback_checkpoint.completed_clauses or []):
                                for ent in clause.get("metadata", {}).get("entities", []):
                                    fallback_elements.append({"element_type": ent.get("entity_type", "unknown"), "key": ent.get("name", ""), "value": ent.get("value", ""), "unit": ent.get("unit", ""), "abbreviation": ent.get("abbreviation", ""), "definition": ent.get("definition", ""), "source_clause_id": ent.get("clause_id", clause.get("clause_id")), "scope_prefix": clause.get("scope_prefix") or clause.get("metadata", {}).get("scope_prefix"), "chapter": clause.get("chapter") or clause.get("metadata", {}).get("chapter"), "metadata": ent})
                            chunks_result = {"source": "llm_checkpoint_fallback", "sections": [{"chapter_number": cp.chapter_number, "title": cp.title, "content": ""} for cp in fallback_checkpoint.chapter_plan if cp.status.value in ("completed", "processing")], "clauses": fallback_checkpoint.completed_clauses or [], "elements": fallback_elements, "edges": []}
                            ProjectManager.save_chunks_result(project_id, chunks_result)
                            save_success = True
                    except Exception as fb_err:
                        chunker_logger.error(f"[{task_id}] 兜底失败: {fb_err}")

                if save_success and chunks_result:
                    ProjectManager.delete_chunk_checkpoint_v2(project_id)
                    project = ProjectManager.get_project(project_id)
                    project.status = ProjectStatus.GRAPH_CHUNKED
                    ProjectManager.save_project(project)
                    summary = f"✅ 标注分析完成: {len(result.sections)} 章节, {len(chunks_result['clauses'])} 条文, {len(chunks_result['elements'])} 要素"
                    entity_count = sum(len(c.metadata.get("entities", [])) for c in result.clauses)
                    task_mgr.update_task(task_id, status=TaskStatus.COMPLETED, progress=100, message=summary, result={"sections": len(result.sections), "clauses": len(result.clauses), "entities": entity_count})
                else:
                    entity_count = sum(len(c.metadata.get("entities", [])) for c in result.clauses)
                    task_mgr.update_task(task_id, status=TaskStatus.COMPLETED, progress=100, message=f"LLM 分析完成但保存失败", result={"save_failed": True})

            except LLMChunkerError as e:
                chunker_logger.error(f"[{task_id}] LLM 分块失败: {e}")
                task_mgr.update_task(task_id, status=TaskStatus.FAILED, message=f"LLM 分块失败: {e}", error=str(e))
                project = ProjectManager.get_project(project_id)
                project.status = ProjectStatus.FAILED
                project.error = str(e)
                ProjectManager.save_project(project)
            except Exception as e:
                chunker_logger.error(f"[{task_id}] 分块异常: {e}\n{traceback.format_exc()}")
                task_mgr.update_task(task_id, status=TaskStatus.FAILED, message=f"分块异常: {e}", error=str(e))

        thread = threading.Thread(target=chunking_task, daemon=True)
        thread.start()

        return jsonify({
            "success": True,
            "data": {
                "project_id": project_id,
                "task_id": task_id,
                "message": "LLM 智能语义分析任务已启动",
                "checkpoint_v2": checkpoint_v2 is not None,
                "has_checkpoint": checkpoint_v2 is not None or ProjectManager.get_chunk_checkpoint(project_id) is not None
            }
        })

    except Exception as e:
        logger.error(f"API Error: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@graph_bp.route('/chunk/<project_id>/progress', methods=['GET'])
@api_handler
def get_chunk_progress(project_id: str):
    """获取章节处理进度详情"""
    try:
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({"success": False, "error": f"项目不存在: {project_id}"}), 404

        progress = ProjectManager.get_chapter_progress(project_id)
        if not progress:
            old_checkpoint = ProjectManager.get_chunk_checkpoint(project_id)
            if old_checkpoint:
                return jsonify({"success": True, "data": {"legacy_checkpoint": True, "message": "使用旧版检查点格式", "checkpoint_data": old_checkpoint}})
            return jsonify({"success": False, "error": "尚未开始分块处理或无检查点数据"}), 404

        return jsonify({"success": True, "data": progress})
    except Exception as e:
        logger.error(f"获取章节进度失败: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


@graph_bp.route('/chunk/<project_id>', methods=['GET'])
@api_handler
def get_intelligent_chunks(project_id: str):
    """获取项目的 LLM 智能分块结果"""
    project = ProjectManager.get_project(project_id)
    if not project:
        return jsonify({"success": False, "error": f"项目不存在: {project_id}"}), 404

    chunks = ProjectManager.get_intelligent_chunks(project_id)
    if not chunks:
        return jsonify({"success": False, "error": "尚未执行 LLM 分块"}), 200

    return jsonify({"success": True, "data": chunks})


@graph_bp.route('/chunk/<project_id>/has_intelligent_chunks', methods=['GET'])
@api_handler
def check_has_intelligent_chunks(project_id: str):
    """检查项目是否存在 intelligent_chunks.json 文件"""
    project = ProjectManager.get_project(project_id)
    if not project:
        return error_response(error="NOT_FOUND", message=f"项目不存在: {project_id}", status_code=404)

    chunks = ProjectManager.get_intelligent_chunks(project_id)
    return success_response(data={"has_intelligent_chunks": chunks is not None})


@graph_bp.route('/chunk/<project_id>/analysis', methods=['GET'])
@api_handler
def get_chunk_analysis(project_id: str):
    """获取智能Chunks标注分析的格式化展示数据"""
    project = ProjectManager.get_project(project_id)
    if not project:
        return jsonify({"success": False, "error": f"项目不存在: {project_id}"}), 404

    chunks = ProjectManager.get_intelligent_chunks(project_id)
    if not chunks:
        return jsonify({"success": False, "error": "尚未执行 LLM 分块"}), 200

    sections = chunks.get('sections', [])
    clauses = chunks.get('clauses', [])
    elements = chunks.get('elements', [])

    tree_path = ProjectManager._get_intelligent_chunks_tree_path(project_id)
    use_tree_file = os.path.exists(tree_path)
    if use_tree_file:
        with open(tree_path, 'r', encoding='utf-8') as f:
            tree_data = json.load(f)
        chapter_tree_raw = tree_data.get('chapter_tree', [])
    else:
        chapter_tree_raw = None

    chapter_tree = {}
    if use_tree_file and chapter_tree_raw is not None:
        for ch in chapter_tree_raw:
            chapter_info = ch.get('chapter', {})
            cn = chapter_info.get('chapter_number')
            if cn is not None:
                chapter_tree[cn] = {"chapter_number": cn, "title": chapter_info.get('title', ''), "content": chapter_info.get('content', ''), "clauses": ch.get('clauses', []), "clause_count": chapter_info.get('clause_count', 0), "element_count": 0}
    else:
        for section in sections:
            chapter_num = section.get('chapter_number')
            if chapter_num:
                chapter_clauses = [c for c in clauses if c.get('parent_chapter') == chapter_num]
                chapter_elements = [e for e in elements if _get_element_parent_chapter(e) == chapter_num]
                chapter_tree[chapter_num] = {"chapter_number": chapter_num, "title": section.get('title', ''), "content": section.get('content', ''), "clauses": chapter_clauses, "elements": chapter_elements, "clause_count": len(chapter_clauses), "element_count": len(chapter_elements)}

    def _chapter_sort_key(item):
        key = item[0]
        if str(key).isdigit():
            return (0, int(key))
        return (1, str(key))
    sorted_chapters = sorted(chapter_tree.items(), key=_chapter_sort_key)

    total_terms = sum(len(c.get('terms', [])) for c in clauses)
    total_entities = sum(len(c.get('entities', [])) for c in clauses)

    element_stats = {}
    for element in elements:
        elem_type = element.get('element_type', 'unknown')
        if elem_type == 'unknown':
            elem_type = element.get('type', 'noun_entity')
        element_stats[elem_type] = element_stats.get(elem_type, 0) + 1

    clause_details = []
    for clause in clauses:
        clause_id = clause.get('clause_id', '')
        related_elements = [e for e in elements if e.get('source_clause_id') == clause_id or e.get('chunk_id', '').startswith(f"chunk_{clause.get('page_idx', '')}")]
        clause_details.append({"clause_id": clause_id, "clause_title": clause.get('clause_title', ''), "content": clause.get('content', ''), "requirement_type": clause.get('requirement_type', 'recommended'), "parent_chapter": clause.get('parent_chapter'), "parent_chapter_title": _get_chapter_title(chapter_tree, clause.get('parent_chapter')), "conditions": clause.get('conditions', []), "actions": clause.get('actions', []), "components": clause.get('components', []), "objects": clause.get('objects', []), "entities": [e if isinstance(e, str) else {"element_type": e.get("entity_type", "unknown"), "key": e.get("name", ""), "value": e.get("value", ""), "unit": e.get("unit", "")} for e in clause.get('entities', [])], "related_elements": related_elements + [e if isinstance(e, str) else {"element_type": e.get("entity_type", "unknown"), "key": e.get("name", ""), "value": e.get("value", ""), "unit": e.get("unit", "")} for e in clause.get('entities', [])], "bboxs": clause.get('bboxs', []), "page": clause.get('page'), "page_idx": clause.get('page_idx')})

    element_details = []
    for element in elements:
        if element.get('element_type'):
            element_details.append({"element_type": element.get('element_type', 'unknown'), "key": element.get('key', ''), "value": element.get('value', ''), "unit": element.get('unit', ''), "condition": element.get('condition', ''), "abbreviation": element.get('abbreviation', ''), "definition": element.get('definition', ''), "source_clause_id": element.get('source_clause_id', ''), "metadata": element.get('metadata', {})})
        else:
            element_details.append({"element_type": "noun_entity", "chunk_id": element.get('chunk_id', ''), "page_idx": element.get('page_idx', 0), "content_preview": element.get('content_preview', ''), "nouns": element.get('nouns', []), "bbox_viewport": element.get('bbox_viewport', []), "category_id": element.get('category_id', 1), "type": element.get('type', 'text'), "source": "mineru"})

    pdf_file = _get_project_pdf_filename(project)

    return jsonify({
        "success": True,
        "data": {
            "pdf_file": pdf_file,
            "summary": {"total_sections": len(sections), "total_clauses": len(clauses), "total_terms": total_terms, "total_entities": total_entities, "total_elements": len(elements), "element_stats": element_stats, "source": chunks.get('source', 'llm')},
            "chapter_tree": [{"chapter": chapter, "clauses": clauses, "elements": elements} for chapter_num, (chapter, clauses, elements) in [(num, (data, data.get('clauses', []), data.get('elements', []))) for num, data in sorted_chapters]],
            "chapters": [{"chapter_number": chapter_num, "title": data.get('title', ''), "clause_count": data.get('clause_count', 0), "element_count": data.get('element_count', 0)} for chapter_num, data in sorted_chapters],
            "clauses": clause_details,
            "elements": element_details
        }
    })


@graph_bp.route('/chunk/<project_id>/entity', methods=['PATCH'])
@api_handler
def update_clause_entity(project_id: str):
    """更新单个 clause 的知识实体"""
    from .graph import _get_storage

    try:
        data = request.get_json() or {}
        clause_id = data.get('clause_id')
        if not clause_id:
            return jsonify({"success": False, "error": "缺少 clause_id"}), 400

        chunks = ProjectManager.get_intelligent_chunks(project_id)
        if not chunks:
            return jsonify({"success": False, "error": "尚未执行 LLM 分块，无可更新的数据"}), 404

        clauses = chunks.get('clauses', [])
        target_idx = None
        for i, c in enumerate(clauses):
            if c.get('clause_id') == clause_id:
                target_idx = i
                break

        if target_idx is None:
            return jsonify({"success": False, "error": f"未找到 clause: {clause_id}"}), 404

        updated_clause = clauses[target_idx]
        if 'terms' in data:
            existing_terms = {t.get('term_name', '') if isinstance(t, dict) else t: t for t in (updated_clause.get('terms') or [])}
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

        ProjectManager.save_chunks_result(project_id, chunks)

        if 'terms' in data and data['terms']:
            project = ProjectManager.get_project(project_id)
            if project and project.graph_id:
                storage = _get_storage()
                storage.sync_term_entities(project.graph_id, clause_id, updated_clause['terms'])

        return jsonify({"success": True, "message": "Clause entity updated", "data": updated_clause})
    except Exception as e:
        logger.error(f"更新 clause 实体失败: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============== Helper Functions ==============

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
