"""
Ontology Generation API Routes
Handles file upload, text extraction, and ontology generation
"""

import os
import shutil
import traceback
import threading
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import request, jsonify

from . import graph_bp
from ..utils.logger import get_logger
from ..utils.api_utils import api_handler
from ..utils.file_parser import FileParser
from ..models.project import ProjectManager, ProjectStatus
from ..models.task import TaskManager, TaskStatus

logger = get_logger('mirofish.api')


@graph_bp.route('/ontology/generate', methods=['POST'])
@api_handler
def generate_ontology():
    """
    Interface 1: Upload files and extract text (Asynchronous)

    流程：
    1. 提取 PDF 文本块（chunks.json）
    2. 保存原始文本
    3. 设置状态为 ontology_generated
    """
    from .graph import allowed_file, _call_mineru_api, _parse_mineru_jsonl

    try:
        logger.info("=== Starting 文件上传与文本提取流程 ===")

        # Get uploaded files first (needed for default project name)
        uploaded_files = request.files.getlist('files')

        # Get parameters
        project_name = request.form.get('project_name', '').strip()

        # 如果没有提供项目名称，默认使用第一个上传文件的文件名（去掉扩展名）
        if not project_name:
            first_filename = uploaded_files[0].filename if uploaded_files else ''
            base_name = os.path.splitext(first_filename)[0] if first_filename else 'Unnamed Project'
            project_name = ProjectManager.generate_unique_name(base_name or 'Unnamed Project')

        if not uploaded_files or all(not f.filename for f in uploaded_files):
            return jsonify({
                "success": False,
                "error": "Please upload at least one document file"
            }), 400

        # 1. 创建项目对象（仅内存中，不创建目录）
        project = ProjectManager.create_project(name=project_name)

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

        if not saved_files:
            shutil.rmtree(ProjectManager._get_project_dir(project.project_id), ignore_errors=True)
            return jsonify({"success": False, "error": "No valid files uploaded"}), 400

        # 5. Word 文件统一转换为 PDF，以便共享后续 MinerU 解析逻辑
        from ..utils.word_converter import convert_word_to_pdf
        _word_exts = {'.doc', '.docx'}
        converted_files = []
        for f in saved_files:
            orig_name = f["original_filename"]
            ext = os.path.splitext(orig_name)[1].lower()
            if ext in _word_exts:
                try:
                    pdf_path = convert_word_to_pdf(f["path"])
                    pdf_name = os.path.splitext(orig_name)[0] + ".pdf"
                    converted_files.append({
                        "original_filename": pdf_name,
                        "saved_filename": os.path.basename(pdf_path),
                        "path": pdf_path,
                        "size": os.path.getsize(pdf_path)
                    })
                    # 更新原始文件条目，path 指向 PDF，original_filename 也改为 .pdf
                    f["original_filename"] = pdf_name
                    f["path"] = pdf_path
                    f["saved_filename"] = os.path.basename(pdf_path)
                    f["size"] = os.path.getsize(pdf_path)
                    logger.info(f"[Word→PDF] 转换成功: {orig_name} → {pdf_name}")
                except Exception as conv_err:
                    logger.error(f"[Word→PDF] 转换失败: {orig_name}, error: {conv_err}")
                    raise RuntimeError(
                        f"Word 文件转换为 PDF 失败: {orig_name}。"
                        "请确保已安装 LibreOffice (soffice) 或上传 PDF 文件。"
                    ) from conv_err
            else:
                converted_files.append(f)

        # 同步 project.files，确保后台任务使用转换后的 PDF 路径
        project.files = [{
            "filename": fi["original_filename"],
            "path": fi["path"],
            "size": fi["size"]
        } for fi in converted_files]
        ProjectManager.save_project(project)
        saved_files = converted_files

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

                # ========== 阶段 1: MinerU PDF 解析 ==========
                build_logger.info(f"[{task_id}] 阶段 1: MinerU PDF 解析...")
                task_manager.update_task(task_id, status=TaskStatus.PROCESSING, progress=5, message="🚀 开始 MinerU PDF 解析...")

                all_chunks = []

                for idx, file_info in enumerate(saved_files):
                    orig_name = file_info["original_filename"]
                    current_progress = 5 + int((idx / len(saved_files)) * 40)
                    build_logger.info(f"[{task_id}] 处理文件: {orig_name} ({idx + 1}/{len(saved_files)})")

                    if not orig_name.lower().endswith('.pdf'):
                        # 非 PDF 文件，使用 FileParser 提取
                        build_logger.info(f"[{task_id}] 非 PDF 文件，使用 FileParser: {orig_name}")
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
                            build_logger.info(f"[{task_id}] FileParser 提取 {len(chunks)} 个文本块: {orig_name}")
                        except Exception as fe:
                            build_logger.warning(f"[{task_id}] FileParser 失败: {fe}")
                        continue

                    pdf_path = file_info["path"]
                    if not os.path.exists(pdf_path):
                        build_logger.warning(f"[{task_id}] PDF 文件不存在: {pdf_path}")
                        continue

                    # 使用 fitz 获取 PDF 页数
                    import fitz
                    doc = fitz.open(pdf_path)
                    total_pages = len(doc)
                    doc.close()
                    build_logger.info(f"[{task_id}] PDF 总页数: {total_pages} for {orig_name}")

                    # 构建 jsonl 路径
                    jsonl_path = os.path.join(ProjectManager._get_project_dir(project.project_id), 'mineru_parsed.jsonl')

                    # 并发调用 MinerU API（使用 graph._call_mineru_api，带重试）
                    max_workers = min(8, total_pages)
                    successful_pages = 0
                    completed_count = 0

                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        future_to_page = {
                            executor.submit(_call_mineru_api, open(pdf_path, 'rb').read(), page_idx): page_idx
                            for page_idx in range(total_pages)
                        }

                        for future in as_completed(future_to_page):
                            page_idx = future_to_page[future]
                            completed_count += 1

                            try:
                                page_result, error_msg = future.result()

                                if error_msg:
                                    build_logger.error(f"[{task_id}] 第 {page_idx} 页失败: {error_msg}")
                                    task_manager.update_task(
                                        task_id,
                                        message=f"⚠️ {orig_name}: 第 {page_idx}/{total_pages} 页失败",
                                        progress=current_progress,
                                        log=f"第 {page_idx} 页失败: {error_msg}"
                                    )
                                else:
                                    # 写入 jsonl
                                    with open(jsonl_path, 'a', encoding='utf-8') as jf:
                                        jf.write(json.dumps(page_result, ensure_ascii=False) + '\n')
                                    successful_pages += 1

                                    task_manager.update_task(
                                        task_id,
                                        message=f"✅ {orig_name}: 第 {page_idx}/{total_pages} 页成功 ({completed_count}/{total_pages})",
                                        progress=current_progress,
                                        log=f"第 {page_idx}/{total_pages} 页成功"
                                    )
                                    build_logger.info(f"[{task_id}] 第 {page_idx}/{total_pages} 页成功")

                            except Exception as req_err:
                                build_logger.error(f"[{task_id}] 第 {page_idx} 页异常: {req_err}")
                                continue

                    build_logger.info(f"[{task_id}] ✅ MinerU API 完成: {orig_name}, 成功 {successful_pages}/{total_pages} 页")

                    if successful_pages == 0:
                        build_logger.error(f"[{task_id}] MinerU API 调用失败: {orig_name}, 成功页数 0")
                        continue

                    # 使用 graph._parse_mineru_jsonl 解析 chunks
                    file_chunks = _parse_mineru_jsonl(jsonl_path, orig_name, pdf_path)
                    for c in file_chunks:
                        c["chunk_id"] = f"chunk_{idx}_{c['chunk_id'].split('_', 1)[-1]}"
                    all_chunks.extend(file_chunks)

                    text_count = len([c for c in file_chunks if c['category_id'] in (0, 1)])
                    table_count = len([c for c in file_chunks if c['category_id'] == 2])
                    build_logger.info(f"[{task_id}] 文件 {orig_name} 提取 {text_count} 文本块 + {table_count} 表格块")

                if not all_chunks:
                    build_logger.error(f"[{task_id}] 未提取到任何 chunks")
                    task_manager.fail_task(task_id, "未提取到任何文本 chunks")
                    return

                # ========== 阶段 1.2: 保存 chunks.json ==========
                build_logger.info(f"[{task_id}] 阶段 1.2: 保存 chunks.json...")
                task_manager.update_task(task_id, progress=60, message="💾 保存 chunks.json...", log="保存 chunks.json")

                ProjectManager.save_chunks(project.project_id, all_chunks)
                build_logger.info(f"[{task_id}] ✅ chunks.json 已保存，共 {len(all_chunks)} 个块")

                # ========== 阶段 1 完成: 保存本体 + 设置状态 ==========
                task_manager.update_task(task_id, progress=85, message="💾 保存本体定义...", log="保存本体定义")

                from ..services.normative_ontology import NORMATIVE_ONTOLOGY
                ontology = NORMATIVE_ONTOLOGY
                if ontology:
                    project.ontology = {
                        "entity_types": ontology.get("entity_types", []),
                        "edge_types": ontology.get("edge_types", [])
                    }
                    project.analysis_summary = f"MinerU 解析完成：{len(all_chunks)} 块"
                else:
                    project.ontology = {"entity_types": [], "edge_types": []}
                    project.analysis_summary = ""

                project.status = ProjectStatus.ONTOLOGY_GENERATED
                ProjectManager.save_project(project)

                content_chunks_count = len([c for c in all_chunks if c.get('content') and not c.get('is_layout_bbox')])
                summary = f"✅ MinerU 解析完成！共 {len(all_chunks)} 块（含 layout）"
                build_logger.info(f"[{task_id}] {summary}")

                task_manager.complete_task(task_id, {
                    "project_id": project.project_id,
                    "ontology": project.ontology,
                    "analysis_summary": project.analysis_summary,
                    "total_text_length": project.total_text_length,
                    "chunks_count": len(all_chunks),
                    "content_chunks_count": content_chunks_count
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
        import sys
        print(f"[EXCEPTION] generate_ontology FAILED: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
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
