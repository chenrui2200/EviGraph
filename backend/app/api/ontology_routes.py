"""
Ontology Generation API Routes
Handles file upload, text extraction, and ontology generation
"""

import os
import json
import shutil
import traceback
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import request, jsonify

from . import graph_bp
from ..config import Config
from ..utils.logger import get_logger
from ..utils.api_utils import api_handler
from ..utils.file_parser import FileParser
from ..models.project import ProjectManager, ProjectStatus
from ..models.task import TaskManager, TaskStatus

logger = get_logger('mirofish.api')


def _parse_pdf_page(pdf_path: str, page_idx: int) -> tuple[int, dict, str]:
    """
    并发解析单个 PDF 页面，返回 (页码, 解析结果, 错误信息)
    """
    import uuid

    temp_filename = f"temp_{uuid.uuid4().hex[:8]}.pdf"
    api_page_num = page_idx + 1  # MinerU API 页码从 1 开始

    try:
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()

        data = {
            'return_middle_json': 'true',
            'return_model_output': 'false',
            'return_md': 'true',
            'return_images': 'false',
            'return_content_list': 'false',
            'parse_method': 'auto',
            'lang_list': 'ch',
            'table_enable': 'true',
            'formula_enable': 'true',
            'backend': 'pipeline',
            'start_page_id': str(api_page_num),
            'end_page_id': str(api_page_num),
            'output_dir': './output',
            'server_url': 'string',
        }

        response = requests.post(
            Config.MINERU_API_URL,
            files={'files': (temp_filename, pdf_bytes, 'application/pdf')},
            data=data,
            timeout=600
        )

        if response.status_code != 200:
            return api_page_num, None, f"HTTP {response.status_code}: {response.text[:200]}"

        result = response.json()
        results = result.get('results', [])

        if not results:
            return api_page_num, None, "Empty results"

        page_result = results[0]
        return api_page_num, {
            'page_num': api_page_num,
            'md_content': page_result.get('md_content', ''),
            'middle_json': page_result.get('middle_json', {})
        }, None

    except Exception as e:
        return api_page_num, None, str(e)


@graph_bp.route('/ontology/generate', methods=['POST'])
@api_handler
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
    from .graph import allowed_file

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
            from .graph import _parse_mineru_to_chunks, _extract_nouns_from_chunks

            try:
                build_logger = get_logger('mirofish.ontology')

                # ========== 阶段 1: MinerU PDF 解析 → chunks.json ==========
                build_logger.info(f"[{task_id}] 阶段 1: MinerU PDF 解析...")
                task_manager.update_task(task_id, status=TaskStatus.PROCESSING, progress=5, message="🚀 开始 MinerU PDF 解析...")

                all_chunks = []
                all_text_parts = []

                for idx, file_info in enumerate(saved_files):
                    orig_name = file_info["original_filename"]
                    current_progress = 5 + int((idx / len(saved_files)) * 40)
                    build_logger.info(f"[{task_id}] 处理文件: {orig_name} ({idx + 1}/{len(saved_files)})")

                    if not orig_name.lower().endswith('.pdf'):
                        build_logger.info(f"[{task_id}] 非 PDF 文件，跳过 MinerU: {orig_name}")
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

                    pdf_path = file_info["path"]
                    if not os.path.exists(pdf_path):
                        build_logger.warning(f"[{task_id}] PDF 文件不存在: {pdf_path}")
                        continue

                    import fitz
                    doc = fitz.open(pdf_path)
                    total_pdf_pages = len(doc)
                    doc.close()
                    build_logger.info(f"[{task_id}] PDF 总页数: {total_pdf_pages} for {orig_name}")

                    mineru_url = Config.MINERU_API_URL
                    build_logger.info(f"[{task_id}] 并发调用 MinerU API (max_workers=8): {mineru_url} for {orig_name}")

                    # 构建 jsonl 路径（每个文件一个 jsonl）
                    jsonl_path = os.path.join(ProjectManager._get_project_dir(project.project_id), f'mineru_{orig_name}.jsonl')
                    # 初始化/清空 jsonl 文件（每个 worker 追加写入）
                    with open(jsonl_path, 'w', encoding='utf-8') as f:
                        pass

                    successful_pages = 0
                    completed_count = 0

                    # 并发解析所有页面
                    max_workers = min(8, total_pdf_pages)
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        future_to_page = {
                            executor.submit(_parse_pdf_page, pdf_path, page_idx): page_idx
                            for page_idx in range(total_pdf_pages)
                        }

                        for future in as_completed(future_to_page):
                            page_idx = future_to_page[future]
                            completed_count += 1

                            try:
                                api_page_num, page_result, error_msg = future.result()

                                if error_msg:
                                    build_logger.error(f"[{task_id}] 第 {api_page_num} 页失败: {error_msg}")
                                    task_manager.update_task(
                                        task_id,
                                        message=f"⚠️ {orig_name}: 第 {api_page_num}/{total_pdf_pages} 页失败",
                                        progress=current_progress,
                                        log=f"第 {api_page_num} 页失败: {error_msg}"
                                    )
                                else:
                                    # 追加写入 jsonl（每条记录带 page_num，后续按 page_num 排序读取）
                                    with open(jsonl_path, 'a', encoding='utf-8') as jf:
                                        jf.write(json.dumps(page_result, ensure_ascii=False) + '\n')
                                    successful_pages += 1

                                    task_manager.update_task(
                                        task_id,
                                        message=f"✅ {orig_name}: 第 {api_page_num}/{total_pdf_pages} 页成功 ({completed_count}/{total_pdf_pages})",
                                        progress=current_progress,
                                        log=f"第 {api_page_num} 页成功，已写入 jsonl"
                                    )
                                    build_logger.info(f"[{task_id}] 第 {api_page_num}/{total_pdf_pages} 页成功 ({completed_count}/{total_pdf_pages})")

                            except Exception as req_err:
                                build_logger.error(f"[{task_id}] 第 {page_idx + 1} 页异常: {req_err}")
                                continue

                    build_logger.info(f"[{task_id}] ✅ MinerU API 完成: {orig_name}, 成功 {successful_pages}/{total_pdf_pages} 页")

                    if successful_pages == 0:
                        build_logger.error(f"[{task_id}] MinerU API 调用失败: {orig_name}, 成功页数 0")
                        continue

                    # 直接从 JSONL 文件解析 chunks（流式读取，无需构造 mineru_data 字典）
                    file_chunks = _parse_mineru_to_chunks(jsonl_path, orig_name, pdf_path)
                    for c in file_chunks:
                        c["chunk_id"] = f"chunk_{idx}_{c['chunk_id'].split('_', 1)[-1]}"
                    all_chunks.extend(file_chunks)

                    text_count = len([c for c in file_chunks if c['category_id'] in (0, 1)])
                    table_count = len([c for c in file_chunks if c['category_id'] == 2])
                    build_logger.info(f"[{task_id}] 文件 {orig_name} 提取 {text_count} 文本块 + {table_count} 表格块")
                    msg = f"[{task_id}] ✅ {orig_name}: {text_count} 文本块 + {table_count} 表格块 (成功 {successful_pages}/{total_pdf_pages} 页)"
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
