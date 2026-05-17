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
    """
    保存或更新 AI Application
    保存或更新一个 AI 应用配置。如果传入的 app_id 已存在则更新，
    否则自动生成新的 app_id 并创建。
    ---
    tags:
      - AI App / 应用管理
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            app_id:
              type: string
              description: 应用 ID（为空时自动创建）
            name:
              type: string
              description: 应用名称（必填）
            description:
              type: string
              description: 应用描述
            workflow_data:
              type: object
              description: 工作流配置
            nodes:
              type: array
              description: 节点配置列表
    responses:
      200:
        description: 保存成功
        schema:
          type: object
          properties:
            success:
              type: boolean
            data:
              type: object
      400:
        description: 缺少应用名称
      500:
        description: 服务器错误
    """
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
    """
    获取 AI Application 列表
    列出所有已创建的 AI 应用配置。
    ---
    tags:
      - AI App / 应用管理
    parameters:
      - name: limit
        in: query
        type: integer
        default: 50
        description: 返回数量上限
    responses:
      200:
        description: 列表获取成功
        schema:
          type: object
          properties:
            success:
              type: boolean
            data:
              type: array
            count:
              type: integer
      500:
        description: 服务器错误
    """
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
    """
    获取指定 AI Application
    根据 app_id 获取单个 AI 应用的完整配置。
    ---
    tags:
      - AI App / 应用管理
    parameters:
      - name: app_id
        in: path
        type: string
        required: true
        description: 应用 ID
    responses:
      200:
        description: 获取成功
        schema:
          type: object
          properties:
            success:
              type: boolean
            data:
              type: object
      404:
        description: 应用不存在
    """
    app = AiAppManager.get_app(app_id)
    if not app:
        return jsonify({"success": False, "error": "Application not found"}), 404

    return jsonify({
        "success": True,
        "data": app.to_dict()
    })

@ai_app_bp.route('/<app_id>', methods=['DELETE'])
def delete_app(app_id: str):
    """
    删除 AI Application
    根据 app_id 删除指定的 AI 应用。
    ---
    tags:
      - AI App / 应用管理
    parameters:
      - name: app_id
        in: path
        type: string
        required: true
        description: 应用 ID
    responses:
      200:
        description: 删除成功
        schema:
          type: object
          properties:
            success:
              type: boolean
            message:
              type: string
      404:
        description: 应用不存在
    """
    if AiAppManager.delete_app(app_id):
        return jsonify({"success": True, "message": "Application deleted"})
    return jsonify({"success": False, "error": "Application not found"}), 404

@ai_app_bp.route('/publish/<app_id>', methods=['POST'])
def publish_app(app_id: str):
    """
    发布/取消发布 AI Application
    切换 AI 应用的发布状态。
    ---
    tags:
      - AI App / 应用管理
    parameters:
      - name: app_id
        in: path
        type: string
        required: true
        description: 应用 ID
      - name: body
        in: body
        required: false
        schema:
          type: object
          properties:
            published:
              type: boolean
              default: true
              description: 是否发布（true=发布，false=取消发布）
    responses:
      200:
        description: 状态切换成功
        schema:
          type: object
          properties:
            success:
              type: boolean
            message:
              type: string
            data:
              type: object
      404:
        description: 应用不存在
      500:
        description: 服务器错误
    """
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
    """
    创建空的 AI Application
    创建一个新的空 AI 应用，自动填充默认工作流配置。
    包括 selectedGraphIds、temperature、topK、rerankMinScore、intentMatch 等参数。
    支持传入 workflow_data 自定义值进行深度合并。
    ---
    tags:
      - AI App / 应用管理
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - name
          properties:
            name:
              type: string
              description: 应用名称（必填）
            description:
              type: string
              description: 应用描述
            workflow_data:
              type: object
              description: 自定义工作流配置（会与默认值深度合并）
            nodes:
              type: array
              description: 节点配置列表
    responses:
      200:
        description: 创建成功
        schema:
          type: object
          properties:
            success:
              type: boolean
            message:
              type: string
            data:
              type: object
              properties:
                app_id:
                  type: string
                  description: 应用 ID
                name:
                  type: string
                  description: 应用名称
                description:
                  type: string
                  description: 应用描述
                nodes:
                  type: array
                  description: 节点配置列表
                workflow_data:
                  type: object
                  description: 工作流配置（含 selectedGraphIds、temperature、topK、intentMatch 等）
                created_at:
                  type: string
                  description: 创建时间
                updated_at:
                  type: string
                  description: 更新时间
                is_published:
                  type: boolean
                  description: 是否已发布
      400:
        description: 缺少应用名称
      500:
        description: 服务器错误
    """
    try:
        data = request.get_json() or {}
        name = data.get('name')
        if not name:
            return jsonify({"success": False, "error": "Application name is required"}), 400

        # 默认 workflow_data，支持请求体传入自定义值合并覆盖
        default_workflow = {
            "selectedGraphIds": [],
            "temperature": 0.7,
            "similarityThreshold": 50,
            "topK": 10,
            "rerankMinScore": 50,
            "maxDepth": 3,
            "rootTypes": ["Entity", "Term"],
            "intentMatch": {
                "topicLimit": 50,
                "entityLimit": 50,
                "rerankMinScore": 0
            }
        }
        user_workflow = data.get('workflow_data') or {}
        # 深度合并：保留默认值，用户传入的覆盖
        for key, value in user_workflow.items():
            if isinstance(value, dict) and key in default_workflow and isinstance(default_workflow[key], dict):
                default_workflow[key].update(value)
            else:
                default_workflow[key] = value

        app_data = {
            "name": name,
            "description": data.get('description', ''),
            "workflow_data": default_workflow,
            "nodes": data.get('nodes', [])
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
    """
    将项目关联到 AI Application
    将现有 Project 的 project_id 添加到指定 App 的 selectedProjectIds 中。
    自动去重，如果 project_id 已存在则不会重复添加。
    ---
    tags:
      - AI App / 应用管理
    parameters:
      - name: app_id
        in: path
        type: string
        required: true
        description: 应用 ID
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
              description: 项目 ID（必填）
    responses:
      200:
        description: 关联成功
        schema:
          type: object
          properties:
            success:
              type: boolean
            message:
              type: string
            data:
              type: object
      400:
        description: 缺少 project_id
      404:
        description: 应用或项目不存在
      500:
        description: 服务器错误
    """
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

        # 获取现有 selectedProjectIds，去重追加
        workflow_data = app.workflow_data or {}
        selected_project_ids = workflow_data.get("selectedProjectIds", [])
        if not isinstance(selected_project_ids, list):
            selected_project_ids = []

        if project_id not in selected_project_ids:
            selected_project_ids.append(project_id)
            workflow_data["selectedProjectIds"] = selected_project_ids

        # 保存更新后的 App
        update_data = app.to_dict()
        update_data["workflow_data"] = workflow_data
        updated_app = AiAppManager.save_app(update_data)

        return jsonify({
            "success": True,
            "message": f"Project {project_id} added to App {app_id}",
            "data": updated_app.to_dict()
        })
    except Exception as e:
        logger.error(f"Failed to add project to app: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


