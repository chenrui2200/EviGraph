"""
Entity Management API Routes
Handles entity label updates
"""

from flask import request, jsonify

from . import graph_bp
from ..utils.logger import get_logger
from ..utils.api_utils import api_handler, success_response

logger = get_logger('mirofish.api')


@graph_bp.route('/entity/<graph_id>/<node_uuid>/label', methods=['PUT'])
@api_handler
def update_node_label(graph_id: str, node_uuid: str):
    """
    更新节点的额外标签（如添加 Term 或 Entity label）

    Request (JSON):
        {
            "add_labels": ["Term"],     // 要添加的标签列表
            "remove_labels": ["Entity"]  // 要移除的标签列表（可选）
        }

    Response:
        {
            "success": true,
            "message": "Node label updated",
            "data": { "uuid": "...", "labels": ["Entity", "Term"] }
        }
    """
    try:
        data = request.get_json() or {}
        add_labels = data.get('add_labels', [])
        remove_labels = data.get('remove_labels', [])

        if not add_labels and not remove_labels:
            return jsonify({
                "success": False,
                "error": "请提供 add_labels 或 remove_labels"
            }), 400

        from .graph import _get_storage
        storage = _get_storage()
        success = storage.update_node_labels(graph_id, node_uuid, add_labels, remove_labels)

        if not success:
            return jsonify({
                "success": False,
                "error": f"节点 {node_uuid} 不存在或标签更新失败"
            }), 404

        # 获取更新后的节点信息
        node = storage.get_node(node_uuid)
        return jsonify({
            "success": True,
            "message": "Node label updated",
            "data": {
                "uuid": node.get("uuid"),
                "name": node.get("name"),
                "labels": node.get("labels", [])
            }
        })

    except Exception as e:
        logger.error(f"更新节点标签失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
