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


@report_bp.route('/tools/rerank', methods=['POST'])
def rerank_facts():
    """
    LLM 相关性重排：接收检索结果 rows，执行 LLM 打分 + 阈值过滤。

    POST body:
        rows: List[ObjectFirstRow] (序列化后的 dict)
        query: str
        filter_threshold: int (0-100, 默认 75)
    """
    from ..services.graph_tools import ObjectFirstRow, ObjectPathNode, ObjectPathEdge

    data = request.get_json() or {}
    rows_data = data.get('rows', [])
    query = data.get('query', '')
    filter_threshold = int(data.get('filter_threshold', 75))

    if not query:
        return jsonify({"success": False, "error": "query is required"}), 400
    if not rows_data:
        return jsonify({"success": True, "data": {"scored_facts": [], "filtered_facts": [], "rows": []}})

    try:
        from flask import current_app
        storage = current_app.extensions.get('neo4j_storage')
        if not storage:
            return jsonify({"success": False, "error": "Storage not available"}), 503

        # 重建 ObjectFirstRow 对象
        final_rows = []
        for r in rows_data:
            paths = [
                ObjectPathNode(
                    uuid=p.get('uuid', ''),
                    name=p.get('name', ''),
                    labels=p.get('labels', []),
                    summary=p.get('summary', ''),
                    depth=p.get('depth', 0),
                )
                for p in r.get('traversal_paths', [])
            ]
            edges = [
                ObjectPathEdge(
                    uuid=e.get('uuid', ''),
                    name=e.get('name', ''),
                    fact=e.get('fact', ''),
                    source_node_uuid=e.get('source_node_uuid', ''),
                    target_node_uuid=e.get('target_node_uuid', ''),
                    depth=e.get('depth', 0),
                )
                for e in r.get('traversal_edges', [])
            ]
            final_rows.append(ObjectFirstRow(
                object_node=r.get('object_node', {}),
                traversal_paths=paths,
                traversal_edges=edges,
                facts=r.get('facts', []),
                relevance_score=r.get('relevance_score', 0),
            ))

        tools = GraphToolsService(storage=storage)
        rerank_result = tools.run_retrieval_flow(
            final_rows=final_rows,
            query=query,
            similarity_threshold=0,
            filter_threshold=filter_threshold,
        )

        logger.info(f"Rerank complete: scored={len(rerank_result.scored_facts)}, filtered={len(rerank_result.filtered_facts)}")

        return jsonify({
            "success": True,
            "data": {
                "scored_facts": rerank_result.scored_facts,
                "filtered_facts": rerank_result.filtered_facts,
                "rows": [r.to_dict() for r in rerank_result.rows_with_scored_facts],
            }
        })
    except Exception as e:
        logger.error(f"Rerank failed: {str(e)}")
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
