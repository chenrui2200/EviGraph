from flask import request, jsonify, current_app
from . import ai_app_bp
from ..models.ai_app import AiAppManager
from ..models.project import ProjectManager
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


@ai_app_bp.route('/create', methods=['POST'])
def create_app():
    """创建一个新的空 AI Application"""
    try:
        data = request.get_json() or {}
        name = data.get('name')
        if not name:
            return jsonify({"success": False, "error": "Application name is required"}), 400

        app_data = {
            "name": name,
            "description": data.get('description', ''),
            "workflow_data": {
                "selectedGraphIds": [],
                "temperature": 0.7,
                "similarityThreshold": 0,
                "topK": 10,
                "rerankMinScore": 0,
                "maxDepth": 3,
                "rootTypes": ["Entity", "Term"],
            },
            "nodes": []
        }
        app = AiAppManager.save_app(app_data)
        return jsonify({
            "success": True,
            "message": "AI Application created successfully",
            "data": app.to_dict()
        })
    except Exception as e:
        logger.error(f"Failed to create AI app: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


@ai_app_bp.route('/<app_id>/add-project', methods=['POST'])
def add_project_to_app(app_id: str):
    """将现有 Project 的 graph_id 添加到指定 App 的 selectedGraphIds 中"""
    try:
        data = request.get_json() or {}
        project_id = data.get('project_id')
        if not project_id:
            return jsonify({"success": False, "error": "project_id is required"}), 400

        app = AiAppManager.get_app(app_id)
        if not app:
            return jsonify({"success": False, "error": "Application not found"}), 404

        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({"success": False, "error": "Project not found"}), 404

        graph_id = project.graph_id
        if not graph_id:
            return jsonify({"success": False, "error": "Project has no graph_id"}), 400

        # 获取现有 selectedGraphIds，去重追加
        workflow_data = app.workflow_data or {}
        selected_graph_ids = workflow_data.get("selectedGraphIds", [])
        if not isinstance(selected_graph_ids, list):
            selected_graph_ids = []

        if graph_id not in selected_graph_ids:
            selected_graph_ids.append(graph_id)
            workflow_data["selectedGraphIds"] = selected_graph_ids

        # 保存更新后的 App
        update_data = app.to_dict()
        update_data["workflow_data"] = workflow_data
        updated_app = AiAppManager.save_app(update_data)

        return jsonify({
            "success": True,
            "message": f"Project {project_id} (graph {graph_id}) added to App {app_id}",
            "data": updated_app.to_dict()
        })
    except Exception as e:
        logger.error(f"Failed to add project to app: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


