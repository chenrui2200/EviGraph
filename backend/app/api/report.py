"""
Report Tools Blueprint
Provides hit-test and object-first search endpoints.
"""

import json
from flask import Blueprint, jsonify, request
from ..services.graph_tools import GraphToolsService
from ..utils.logger import get_logger

logger = get_logger('mirofish.report')

report_bp = Blueprint('report', __name__)


@report_bp.route('/tools/search-object-first', methods=['POST'])
def search_object_first():
    """
    Root-node-first DFS search for hit-test view.

    POST body:
        graph_id, query, limit, max_depth, root_type
    """
    data = request.get_json() or {}
    graph_id = data.get('graph_id')
    query = data.get('query', '')
    limit = int(data.get('limit', 10))
    max_depth = int(data.get('max_depth', 3))
    root_type = data.get('root_type', 'Object')

    if not graph_id:
        return jsonify({"success": False, "error": "graph_id is required"}), 400
    if not query:
        return jsonify({"success": False, "error": "query is required"}), 400

    from flask import current_app
    storage = current_app.extensions.get('neo4j_storage')
    if not storage:
        return jsonify({"success": False, "error": "Storage not available"}), 503

    try:
        tools = GraphToolsService(storage=storage)
        result = tools.search_object_first(
            graph_id=graph_id,
            query=query,
            limit=limit,
            max_depth=max_depth,
            root_type=root_type,
        )
        return jsonify({
            "success": True,
            "data": result.to_dict() if hasattr(result, 'to_dict') else result
        })
    except Exception as e:
        logger.error(f"search_object_first failed: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


@report_bp.route('/tools/search-entity-topic-clause', methods=['POST'])
def search_entity_topic_clause():
    """
    专用路径检索：Entity/Term → Topic → Clause。
    用于 Hit-Test 视图，固定 2 跳路径，不走通用 DFS。

    POST body:
        graph_id, query, limit, root_type
    """
    data = request.get_json() or {}
    graph_id = data.get('graph_id')
    query = data.get('query', '')
    limit = int(data.get('limit', 10))
    root_type = data.get('root_type', 'Entity')

    if not graph_id:
        return jsonify({"success": False, "error": "graph_id is required"}), 400
    if not query:
        return jsonify({"success": False, "error": "query is required"}), 400

    from flask import current_app
    storage = current_app.extensions.get('neo4j_storage')
    if not storage:
        return jsonify({"success": False, "error": "Storage not available"}), 503

    try:
        tools = GraphToolsService(storage=storage)
        result = tools.search_term_entity_to_clause(
            graph_id=graph_id,
            query=query,
            limit=limit,
            root_type=root_type,
        )
        result_dict = result.to_dict() if hasattr(result, 'to_dict') else result
        logger.info(f"[DEBUG] search_entity_topic_clause result: rows={len(result_dict.get('rows', []))}, data={json.dumps(result_dict, ensure_ascii=False)[:500]}")
        return jsonify({
            "success": True,
            "data": result_dict
        })
    except Exception as e:
        logger.error(f"search_entity_topic_clause failed: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


@report_bp.route('/tasks/cleanup', methods=['POST'])
def cleanup_tasks():
    """
    Cleanup old completed/failed tasks.

    POST body (optional):
        max_age_hours: int (default 168 = 7 days)
    """
    data = request.get_json() or {}
    max_age_hours = int(data.get('max_age_hours', 168))

    try:
        from ..models.task import TaskManager
        tm = TaskManager()
        removed = tm.cleanup_old_tasks(max_age_hours=max_age_hours)
        return jsonify({"success": True, "removed": removed})
    except Exception as e:
        logger.error(f"cleanup_tasks failed: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500
