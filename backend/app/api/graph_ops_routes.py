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
    ---
    post:
      summary: 构建知识图谱（异步任务）
      description: |
        基于项目 ID 启动图谱构建异步任务。
        系统会自动解析项目中的文档，提取实体和关系，写入 Neo4j。
      tags:
        - Graph / 图谱操作
      parameters:
        - name: body
          in: body
          required: true
          schema:
            type: object
            required:
              - project_id
            properties:
              project_id:
                type: string
                description: 项目ID（必填）
              graph_name:
                type: string
                description: 图谱名称（可选，默认使用项目名称）
              chunk_size:
                type: integer
                default: 500
                description: 文本块大小
              chunk_overlap:
                type: integer
                default: 50
                description: 文本块重叠大小
              entity_label:
                type: string
                description: 实体标签（可选）
              semantic:
                type: boolean
                default: false
                description: 是否启用语义分块
              force:
                type: boolean
                default: false
                description: 是否强制重新构建
      responses:
        200:
          description: 构建任务已启动
          schema:
            type: object
            properties:
              success:
                type: boolean
              data:
                type: object
                properties:
                  project_id:
                    type: string
                  task_id:
                    type: string
                  message:
                    type: string
        400:
          description: 缺少 project_id
        404:
          description: 项目不存在
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


@graph_bp.route('/ops/search-nodes', methods=['POST'])
@api_handler
def search_nodes():
    """
    按节点名称模糊搜索 + 类型过滤。

    Request:
        {
            "graph_id": "proj_xxx",
            "query": "导体",
            "node_types": ["Entity", "Term"],  // 可选: 多选类型数组
            "node_type": "Entity",              // 向后兼容: 单类型字符串
            "limit": 20
        }
    """
    from .graph import _get_storage

    data = request.get_json() or {}
    graph_id = data.get('graph_id')
    query = data.get('query', '').strip()
    node_types = data.get('node_types')
    node_type = data.get('node_type', 'All')
    limit = data.get('limit', 20)

    if not graph_id:
        return jsonify({"success": False, "error": "graph_id is required"}), 400
    if not query:
        return jsonify({"success": False, "error": "query is required"}), 400

    try:
        storage = _get_storage()
        if node_types and isinstance(node_types, list):
            nodes = storage.search_nodes_by_name(graph_id, query, node_type=None, node_types=node_types, limit=limit)
        else:
            nodes = storage.search_nodes_by_name(graph_id, query, node_type=node_type, limit=limit)
        return jsonify({"success": True, "data": {"nodes": nodes}})
    except Exception as e:
        logger.error(f"search_nodes error: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@graph_bp.route('/ops/node-neighborhood', methods=['POST'])
@api_handler
def node_neighborhood():
    """
    获取节点的 1 跳邻域（中心节点 + 邻接节点 + 邻边）。

    Request:
        {
            "graph_id": "proj_xxx",
            "node_uuid": "uuid"
        }
    """
    from .graph import _get_storage

    data = request.get_json() or {}
    graph_id = data.get('graph_id')
    node_uuid = data.get('node_uuid')

    if not graph_id:
        return jsonify({"success": False, "error": "graph_id is required"}), 400
    if not node_uuid:
        return jsonify({"success": False, "error": "node_uuid is required"}), 400

    try:
        storage = _get_storage()
        result = storage.get_node_neighborhood(node_uuid, graph_id)
        if not result:
            return jsonify({"success": False, "error": "Node not found"}), 404
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error(f"node_neighborhood error: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


