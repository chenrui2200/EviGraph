"""
Graph Operations API Routes
Handles graph building, data retrieval, deletion, and knowledge supplementation
"""

import os
import traceback
from flask import request, jsonify

from . import graph_bp
from ..config import Config
from ..utils.logger import get_logger
from ..utils.api_utils import api_handler
from ..models.project import ProjectManager, ProjectStatus
from ..models.task import TaskManager, TaskStatus
from ..services.graph_builder import GraphBuilderService

logger = get_logger('mirofish.api')


@graph_bp.route('/build', methods=['POST'])
@api_handler
def build_graph():
    """
    Interface 2: Build graph based on project_id

    Request (JSON):
        {
            "project_id": "proj_xxxx",  // Required
            "graph_name": "Graph name",    // Optional
            "chunk_size": 500,          // Optional
            "chunk_overlap": 50,        // Optional
            "entity_label": "Term"       // Optional
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
    from .graph import _get_storage, _start_build_worker

    try:
        logger.info("=== Starting graph build ===")

        data = request.get_json() or {}
        project_id = data.get('project_id')
        logger.debug(f"Request parameters: project_id={project_id}")

        if not project_id:
            logger.warning("Build graph failed: missing project_id")
            return jsonify({"success": False, "error": "Please provide project_id"}), 400

        project = ProjectManager.get_project(project_id)
        if not project:
            logger.warning(f"Build graph failed: project {project_id} not found")
            return jsonify({"success": False, "error": f"Project does not exist: {project_id}"}), 404

        graph_name = data.get('graph_name', project.name or 'Knowledge EviGraph')
        chunk_size = data.get('chunk_size', project.chunk_size or Config.DEFAULT_CHUNK_SIZE)
        chunk_overlap = data.get('chunk_overlap', project.chunk_overlap or Config.DEFAULT_CHUNK_OVERLAP)
        use_semantic = data.get('semantic', False)
        entity_label = data.get('entity_label', project.entity_label)
        force = data.get('force', False)

        logger.info(f"Project {project_id} status: {project.status}, force={force}")

        has_ontology = (isinstance(project.ontology, dict) and len(project.ontology.get("entity_types", [])) > 0)

        if project.status == ProjectStatus.CREATED and has_ontology:
            logger.info(f"Project {project_id} has valid ontology despite CREATED status. Auto-fixing status to ONTOLOGY_GENERATED.")
            project.status = ProjectStatus.ONTOLOGY_GENERATED
            ProjectManager.save_project(project)

        task_id = project.graph_build_task_id
        if not force and project.status in [ProjectStatus.GRAPH_BUILDING, ProjectStatus.GRAPH_CHUNKING, ProjectStatus.GRAPH_EMBEDDING, ProjectStatus.GRAPH_INDEXING] and task_id:
            logger.info(f"Project {project_id} building status detected.")
        else:
            task_manager = TaskManager()
            task_id = task_manager.create_task(f"Build graph: {graph_name}")
            logger.info(f"New graph build task created: task_id={task_id}, project_id={project_id}")

            project.status = ProjectStatus.GRAPH_BUILDING
            project.graph_build_task_id = task_id
            ProjectManager.save_project(project)

        force_reset_statuses = [
            ProjectStatus.GRAPH_BUILDING, ProjectStatus.GRAPH_CHUNKING,
            ProjectStatus.GRAPH_EMBEDDING, ProjectStatus.GRAPH_INDEXING,
            ProjectStatus.FAILED, ProjectStatus.GRAPH_COMPLETED
        ]
        if force and project.status in force_reset_statuses:
            logger.info(f"Forcing rebuild for project {project_id}.")

            if project.graph_id:
                try:
                    storage = _get_storage()
                    builder = GraphBuilderService(storage=storage)
                    builder.delete_graph(project.graph_id)
                    logger.info(f"Old graph {project.graph_id} deleted successfully.")
                except Exception as de:
                    logger.warning(f"Failed to delete old graph {project.graph_id}: {de}")

            project.status = ProjectStatus.ONTOLOGY_GENERATED
            project.graph_id = None
            project.graph_build_task_id = task_id
            project.error = None
            ProjectManager.save_project(project)

        project.chunk_size = chunk_size
        project.chunk_overlap = chunk_overlap
        project.use_semantic = use_semantic
        project.entity_label = entity_label
        ProjectManager.save_project(project)

        text = ProjectManager.get_extracted_text(project_id)
        intelligent_chunks = ProjectManager.get_intelligent_chunks(project_id)
        has_text = text and len(text.strip()) > 0
        has_intelligent = intelligent_chunks is not None

        text_path = ProjectManager._get_project_text_path(project_id)
        chunks_path = ProjectManager._get_intelligent_chunks_path(project_id)
        logger.info(f"[build] project={project_id}")
        logger.info(f"[build]   extracted_text.txt exists={os.path.exists(text_path)}, size={os.path.getsize(text_path) if os.path.exists(text_path) else 0}")
        logger.info(f"[build]   intelligent_chunks.json exists={os.path.exists(chunks_path)}, size={os.path.getsize(chunks_path) if os.path.exists(chunks_path) else 0}")

        if not has_text and not has_intelligent:
            logger.warning(f"Build graph failed: no text data for project {project_id}")
            return jsonify({"success": False, "error": "No text data found. Please go to '智能Chunks标注分析' first."}), 400

        if has_intelligent:
            logger.info(f"Project {project_id} using intelligent_chunks.json (has {len(intelligent_chunks.get('clauses', []))} clauses)")
        else:
            logger.info(f"Project {project_id} using legacy extracted_text.txt")

        storage = _get_storage()
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
        return jsonify({"success": False, "error": str(e), "traceback": traceback.format_exc()}), 500


@graph_bp.route('/data/<graph_id>', methods=['GET'])
@api_handler
def get_graph_data(graph_id: str):
    """Get graph data (nodes and edges)"""
    from .graph import _get_storage

    try:
        storage = _get_storage()
        builder = GraphBuilderService(storage=storage)
        graph_data = builder.get_graph_data(graph_id)

        return jsonify({"success": True, "data": graph_data})
    except Exception as e:
        logger.error(f"API Error: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@graph_bp.route('/delete/<graph_id>', methods=['DELETE'])
@api_handler
def delete_graph(graph_id: str):
    """Delete graph"""
    from .graph import _get_storage

    try:
        storage = _get_storage()
        builder = GraphBuilderService(storage=storage)
        builder.delete_graph(graph_id)

        return jsonify({"success": True, "message": f"Graph deleted: {graph_id}"})
    except Exception as e:
        logger.error(f"API Error: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@graph_bp.route('/supplement', methods=['POST'])
@api_handler
def supplement_knowledge():
    """
    Supplement knowledge by processing specific regions of a PDF.
    Expects: { project_id, filename, regions: [{page, bbox: [x0, y0, x1, y1]}] }
    """
    from .graph import _get_storage

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

        project_dir = ProjectManager._get_project_dir(project_id)
        file_path = None
        found_filename = None

        search_name = filename.lower().strip()
        for root, dirs, files in os.walk(project_dir):
            for f in files:
                f_lower = f.lower().strip()
                if f_lower == search_name or search_name in f_lower or f_lower in search_name:
                    file_path = os.path.join(root, f)
                    found_filename = f
                    break
            if file_path:
                break

        if not file_path:
            all_files = []
            for root, dirs, files in os.walk(project_dir):
                all_files.extend(files)
            logger.warning(f"Supplement lookup failed. Searching for '{search_name}'. Present files: {all_files}")
            return jsonify({"success": False, "error": f"Source file {filename} not found in project directory."}), 404

        logger.info(f"Supplementing using file: {file_path}")

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

            page = doc[page_num - 1]
            page_rect = page.rect
            pw, ph = page_rect.width, page_rect.height

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

        storage = _get_storage()
        builder = GraphBuilderService(storage=storage)

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
