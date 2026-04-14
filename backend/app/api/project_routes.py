"""
Project Management API Routes
Handles project CRUD operations and document serving
"""

import os
import re
from typing import Dict, List, Any, Optional
from flask import request, jsonify, current_app, send_file, make_response

from . import graph_bp
from ..utils.logger import get_logger
from ..utils.api_utils import api_handler, success_response
from ..models.project import ProjectManager, ProjectStatus
from ..models.task import TaskManager
from ..models.ai_app import AiAppManager

logger = get_logger('mirofish.api')


# ============== Project CRUD ==============

@graph_bp.route('/project/<project_id>', methods=['GET'])
@api_handler
def get_project(project_id: str):
    """
    Get project details with auto-recovery for lost tasks
    """
    project = ProjectManager.get_project(project_id)

    if not project:
        return jsonify({
            "success": False,
            "error": f"Project does not exist: {project_id}"
        }), 404

    # Check ontology task and AUTO-RECOVER if missing (智能Chunks标注分析)
    if project.status == ProjectStatus.ONTOLOGY_GENERATION and project.ontology_task_id:
        task = TaskManager().get_task(project.ontology_task_id)
        if not task:
            logger.warning(f"Project {project_id} lost its ontology task. Attempting auto-recovery...")

            checkpoint_v2 = ProjectManager.get_chunk_checkpoint_v2(project_id)
            has_checkpoint = checkpoint_v2 is not None

            old_checkpoint = ProjectManager.get_chunk_checkpoint(project_id)
            has_old_checkpoint = old_checkpoint is not None

            if has_checkpoint or has_old_checkpoint:
                try:
                    from .graph import _start_ontology_recovery_worker
                    _start_ontology_recovery_worker(project_id, project.ontology_task_id)
                    project.error = "检测到后台任务中断，系统已自动从检查点恢复进度。"
                    ProjectManager.save_project(project)
                    logger.info(f"Project {project_id} ontology recovery worker started.")
                except Exception as re:
                    logger.error(f"Auto-recovery failed for project {project_id}: {re}")
                    project.error = f"自动恢复失败: {str(re)}"
                    ProjectManager.save_project(project)
            else:
                project.error = "任务实例已过期，请点击按钮重新触发。"
                ProjectManager.save_project(project)

    # Check build task
    if project.status in [ProjectStatus.GRAPH_BUILDING, ProjectStatus.GRAPH_CHUNKING,
                          ProjectStatus.GRAPH_EMBEDDING, ProjectStatus.GRAPH_INDEXING]:
        task_id = project.graph_build_task_id
        from .graph import _get_storage, _start_build_worker
        storage = _get_storage()
        from ..services.graph_builder import GraphBuilderService
        builder = GraphBuilderService(storage=storage)

        if not task_id or not TaskManager().get_task(task_id):
            logger.warning(f"Project {project_id} is in building status but task record is missing.")
            new_task_id = TaskManager().create_task(f"Recover build: {project.name or project_id}")
            project.graph_build_task_id = new_task_id
            project.error = "检测到后台任务记录丢失，系统已自动创建新任务恢复进度。"
            ProjectManager.save_project(project)
            _start_build_worker(project_id, new_task_id, storage, force=False)

        elif not builder.is_worker_active(project_id):
            logger.info(f"🚀 Project {project_id} has active task {task_id} but NO active worker thread.")
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


@graph_bp.route('/project/list', methods=['GET'])
@api_handler
def list_projects():
    """
    List all projects with AI app reference info
    """
    limit = request.args.get('limit', 50, type=int)
    projects = ProjectManager.list_projects(limit=limit)

    apps = AiAppManager.list_apps(limit=100)
    app_graph_map = {}

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

    project_list = []
    for p in projects:
        p_dict = p.to_dict()
        if p.graph_id and p.graph_id in app_graph_map:
            p_dict["referencing_apps"] = app_graph_map[p.graph_id]
        else:
            p_dict["referencing_apps"] = []
        project_list.append(p_dict)

    return success_response(
        data={"projects": project_list, "count": len(projects)}
    )


@graph_bp.route('/project/<project_id>', methods=['DELETE'])
@api_handler
def delete_project(project_id: str):
    """
    Delete project with AI App reference check
    """
    project = ProjectManager.get_project(project_id)
    if not project:
        return jsonify({
            "success": False,
            "error": f"Project not found: {project_id}"
        }), 404

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
@api_handler
def update_project(project_id: str):
    """
    Update project details (e.g., name, current_step)
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
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid current_step value: {data['current_step']}, error: {e}")

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
@api_handler
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


# ============== Document Serving ==============

@graph_bp.route('/project/<project_id>/document/<path:filename>', methods=['GET'])
@api_handler
def get_project_document(project_id: str, filename: str):
    """
    Get a specific document (e.g. PDF) associated with a project for preview.
    Supports both project_id and graph_id as the first parameter.
    """
    project = ProjectManager.get_project(project_id)

    if not project:
        all_projects = ProjectManager.list_projects(limit=500)
        for p in all_projects:
            if p.graph_id == project_id:
                project = p
                break

    # 优先使用 project.files 中记录的 path（确保是有效的 PDF 文件）
    if project and project.files:
        for pf in project.files:
            pf_path = pf.get('path', '')
            if pf_path:
                # 标准化路径并验证
                pf_path = os.path.normpath(pf_path)
                if os.path.isfile(pf_path):
                    # 验证 magic bytes
                    with open(pf_path, 'rb') as f:
                        header = f.read(5)
                    if header == b'%PDF-':
                        logger.info(f"Using file from project.files: {pf_path}")
                        target_file_path = pf_path
                        target_dir = os.path.dirname(pf_path)
                        found_filename = os.path.basename(pf_path)
                    else:
                        logger.warning(f"File in project.files is not a PDF: {pf_path}, header: {header}")

    # Fallback: 如果 project.files 中没有有效 PDF，则从项目目录递归搜索
    if not target_file_path:
        actual_folder_id = project.project_id if project else project_id
        base_dir = os.path.abspath(os.path.join(current_app.root_path, '../uploads/projects', actual_folder_id))

        if not os.path.exists(base_dir):
            return jsonify({
                "success": False,
                "error": f"Project directory not found: {base_dir}"
            }), 404

        logger.info(f"Searching for PDF by magic bytes in {base_dir}...")
        all_files = []
        for root, dirs, files in os.walk(base_dir):
            for f in files:
                fpath = os.path.join(root, f)
                all_files.append(f)
                if os.path.isfile(fpath):
                    try:
                        with open(fpath, 'rb') as fh:
                            if fh.read(5) == b'%PDF-':
                                target_file_path = fpath
                                target_dir = root
                                found_filename = f
                                logger.info(f"Found PDF by magic bytes: {fpath}")
                                break
                    except Exception:
                        pass
            if target_file_path:
                break

        if not target_file_path:
            logger.warning(f"File lookup failed. Files present in project: {all_files[:10] if all_files else 'none'}")
            return jsonify({
                "success": False,
                "error": f"Document not found: {filename}. Searched {base_dir}. Found files: {all_files[:10] if all_files else 'none'}...",
                "searched_id": project_id,
                "mapped_id": actual_folder_id if 'actual_folder_id' in dir() else project_id
            }), 404

    logger.info(f"Serving document: {found_filename} from {target_dir}")

    response = make_response(send_file(target_file_path, mimetype='application/pdf'))
    response.headers['Content-Disposition'] = 'inline'
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'ALLOWALL'

    if 'Content-Transfer-Encoding' in response.headers:
        del response.headers['Content-Transfer-Encoding']

    return response


# ============== Chunk Page Info Backfill ==============

def _backfill_page_info(project_id: str, intelligent_chunks_data: dict, logger) -> None:
    """
    回填 intelligent_chunks.json 中缺失的 page 信息。

    从 chunks.json（MinerU 解析产物）中查找对应 clause_id 的 page_idx，
    填充到 clauses 的 page 字段中。确保旧数据也能支持定位到文档页面。

    支持多chunk合并的clause：同一个clause_id可能跨多个chunk/pages，
    需要收集所有相关的bbox并按页合并。
    """
    chunks_data = ProjectManager.get_chunks(project_id)
    if not chunks_data:
        logger.debug(f"[{project_id}] 回填 page: chunks.json 不存在，跳过")
        return

    # 改为收集所有匹配的bbox，支持多trunk/clause跨多页情况
    # clause_id -> list of {page_idx, bbox, page_width, page_height, source}
    clause_bbox_list_map: Dict[str, list] = {}
    clause_first_page_map: Dict[str, int] = {}

    for chunk in chunks_data:
        chunk_id = chunk.get('chunk_id', '')
        content = chunk.get('content', '')
        page_idx = chunk.get('page_idx')
        bbox = chunk.get('bbox_pdf') or chunk.get('bbox_viewport')
        source = chunk.get('source', '')
        page_width = chunk.get('page_width')
        page_height = chunk.get('page_height')

        if page_idx is not None and content:
            matches = re.findall(r'\b(\d+(?:\.\d+)+)\b', content)
            for m in matches:
                if m not in clause_bbox_list_map:
                    clause_bbox_list_map[m] = []
                    clause_first_page_map[m] = page_idx
                # 收集所有匹配的bbox（去重）
                bbox_entry = {
                    'page_idx': page_idx,
                    'bbox': bbox,
                    'page_width': page_width,
                    'page_height': page_height,
                    'source': source
                }
                # 避免重复添加同一page的同一bbox
                existing_pages = {b['page_idx'] for b in clause_bbox_list_map[m]}
                if page_idx not in existing_pages:
                    clause_bbox_list_map[m].append(bbox_entry)

    if not clause_bbox_list_map:
        logger.warning(f"[{project_id}] 回填 page: chunks.json 中无条款编号（正则匹配失败），跳过")
        return

    logger.info(f"[{project_id}] 回填 page: 从 chunks.json 匹配到 {len(clause_bbox_list_map)} 个条款编号")

    filled_count = 0
    skipped_already_filled = 0
    skipped_not_in_map = 0
    skipped_has_bboxs = 0  # 已有正确 bboxs 的跳过计数

    for clause in intelligent_chunks_data.get('clauses', []):
        clause_id = clause.get('clause_id', '')
        if clause_id and clause_id in clause_bbox_list_map:
            # 优先使用 intelligent_chunks.json 中已有的 bboxs（来自 LLM 合并结果，是正确的多trunk bbox）
            # 不覆盖已有的正确 bboxs
            existing_bboxs = clause.get('bboxs') or clause.get('metadata', {}).get('bboxs', [])
            existing_page = clause.get('metadata', {}).get('page') or clause.get('page')

            if existing_bboxs:
                # bboxs 已存在（来自 LLM 合并），跳过回填
                skipped_has_bboxs += 1
                continue

            # bboxs 不存在，需要回填
            if existing_page and not existing_bboxs:
                # page 已有但没有 bboxs，只回填 bbox
                pass  # 继续执行 bbox 回填逻辑
            elif existing_page:
                # page 和 bboxs 都已有，跳过
                skipped_already_filled += 1
                continue

            bbox_list = clause_bbox_list_map[clause_id]
            # 按页分组，合并每页的bbox取并集
            page_groups: Dict[int, list] = {}
            for bbox_info in bbox_list:
                p_idx = bbox_info['page_idx']
                if p_idx not in page_groups:
                    page_groups[p_idx] = []
                bbox_val = bbox_info.get('bbox')
                if bbox_val:
                    if isinstance(bbox_val, dict):
                        page_groups[p_idx].append([bbox_val.get('x0', 0), bbox_val.get('y0', 0), bbox_val.get('x1', 0), bbox_val.get('y1', 0)])
                    elif isinstance(bbox_val, list) and len(bbox_val) == 4:
                        page_groups[p_idx].append(bbox_val)

            # 合并每页bbox，生成 merged_bboxes
            merged_bboxes = []
            first_page = clause_first_page_map.get(clause_id, 1)
            first_source = bbox_list[0].get('source', '') if bbox_list else ''
            first_page_width = bbox_list[0].get('page_width') if bbox_list else None
            first_page_height = bbox_list[0].get('page_height') if bbox_list else None

            for p_idx in sorted(page_groups.keys()):
                bboxes = page_groups[p_idx]
                if not bboxes:
                    continue
                # 取并集
                x0 = min(b[0] for b in bboxes)
                y0 = min(b[1] for b in bboxes)
                x1 = max(b[2] for b in bboxes)
                y1 = max(b[3] for b in bboxes)
                merged_bboxes.append([p_idx, x0, y0, x1, y1])

            clause['metadata'] = clause.get('metadata', {})
            clause['page'] = first_page
            clause['source'] = first_source
            clause['metadata']['page'] = first_page
            clause['metadata']['source'] = first_source
            clause['metadata']['page_width'] = first_page_width
            clause['metadata']['page_height'] = first_page_height
            clause['metadata']['bboxs'] = merged_bboxes

            # 兼容：同时设置单 bbox 字段（取第一个页的bbox）
            if merged_bboxes:
                first_bbox = merged_bboxes[0]
                clause['bbox'] = first_bbox[1:]  # [x0, y0, x1, y1]
                clause['metadata']['bbox'] = first_bbox[1:]
            else:
                clause['bbox'] = None
                clause['metadata']['bbox'] = None

            filled_count += 1
            logger.debug(f"[{project_id}] 回填: clause={clause_id} → page={first_page}, bboxs_count={len(merged_bboxes)}")
        else:
            skipped_not_in_map += 1

    logger.info(f"[{project_id}] 回填 page 统计: 填充 {filled_count}, 已填充跳过 {skipped_already_filled}, "
                 f"已有bboxs跳过 {skipped_has_bboxs}, chunks中无匹配跳过 {skipped_not_in_map} / {len(intelligent_chunks_data.get('clauses', []))} 个条款")

    # 注意：不持久化到文件！intelligent_chunks.json 是 LLM 原始分析结果，保持不变
    # bbox 信息仅在内存中填充，供当前图谱构建流程使用
