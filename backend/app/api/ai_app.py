from flask import request, jsonify, current_app
from . import ai_app_bp
from ..models.ai_app import AiAppManager
from ..services.graph_tools import GraphToolsService
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
import traceback
import re

logger = get_logger('mirofish.api.ai_app')

def _get_storage():
    """Get Neo4jStorage from Flask app extensions."""
    storage = current_app.extensions.get('neo4j_storage')
    if not storage:
        raise ValueError("GraphStorage not initialized")
    return storage

@ai_app_bp.route('/save', methods=['POST'])
def save_app():
    """Save or update an AI application"""
    try:
        data = request.get_json() or {}
        if not data.get('name'):
            return jsonify({"success": False, "error": "Application name is required"}), 400

        app = AiAppManager.save_app(data)
        return jsonify({
            "success": True,
            "message": "AI Application saved successfully",
            "data": app.to_dict()
        })
    except Exception as e:
        logger.error(f"Failed to save AI app: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@ai_app_bp.route('/list', methods=['GET'])
def list_apps():
    """List all AI applications"""
    try:
        limit = request.args.get('limit', 50, type=int)
        apps = AiAppManager.list_apps(limit=limit)
        return jsonify({
            "success": True,
            "data": [app.to_dict() for app in apps],
            "count": len(apps)
        })
    except Exception as e:
        logger.error(f"Failed to list AI apps: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@ai_app_bp.route('/<app_id>', methods=['GET'])
def get_app(app_id: str):
    """Get a specific AI application"""
    app = AiAppManager.get_app(app_id)
    if not app:
        return jsonify({"success": False, "error": "Application not found"}), 404

    return jsonify({
        "success": True,
        "data": app.to_dict()
    })

@ai_app_bp.route('/<app_id>', methods=['DELETE'])
def delete_app(app_id: str):
    """Delete an AI application"""
    if AiAppManager.delete_app(app_id):
        return jsonify({"success": True, "message": "Application deleted"})
    return jsonify({"success": False, "error": "Application not found"}), 404

@ai_app_bp.route('/publish/<app_id>', methods=['POST'])
def publish_app(app_id: str):
    """Toggle publish status of an AI application"""
    try:
        data = request.get_json() or {}
        published = data.get('published', True)

        app = AiAppManager.publish_app(app_id, published)
        if not app:
            return jsonify({"success": False, "error": "Application not found"}), 404

        return jsonify({
            "success": True,
            "message": f"Application {'published' if published else 'unpublished'} successfully",
            "data": app.to_dict()
        })
    except Exception as e:
        logger.error(f"Failed to publish AI app: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


