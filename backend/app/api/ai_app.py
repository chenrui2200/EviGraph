from flask import request, jsonify, current_app
from . import ai_app_bp
from ..models.ai_app import AiAppManager
from ..services.graph_tools import GraphToolsService
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
import traceback

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

@ai_app_bp.route('/execute/<app_id>', methods=['POST'])
def execute_app(app_id: str):
    """Execute a published AI application via API"""
    try:
        app = AiAppManager.get_app(app_id)
        if not app:
            return jsonify({"success": False, "error": "Application not found"}), 404

        if not app.is_published:
            return jsonify({"success": False, "error": "This application is not published"}), 403

        data = request.get_json() or {}
        query = data.get('query')
        if not query:
            return jsonify({"success": False, "error": "Query is required"}), 400

        # Get configuration from app workflow data
        graph_ids = app.workflow_data.get('selectedGraphIds', [])
        temperature = app.workflow_data.get('temperature', 0.7)

        if not graph_ids:
            return jsonify({"success": False, "error": "App has no knowledge base configured"}), 400

        storage = _get_storage()
        tools = GraphToolsService(storage=storage)

        # Core logic (replicated from graph.py for independence)
        logger.info(f"API Exec App {app_id}: {query[:50]}...")
        search_result = tools.search_multi_graphs(graph_ids=graph_ids, query=query, limit=20)
        facts_text = search_result.to_text()

        system_prompt = "你是一个专业的知识库问答助手。你的任务是基于提供的【检索到的知识参考详情】回答用户的问题。\n\n回答要求：\n1. 请先在 <thought> 标签内写下你的思考过程（分析检索到的证据，核核对条款编号，理清逻辑关系）。\n2. 在思考过程之后，给出最终的结论性回答。\n3. 如果知识库中没有相关信息，请明确告知：'根据目前的知识库，无法回答该问题'。\n4. 回答时必须引用来源（如：'根据[文档名, 页码]显示...'）。\n5. 保持专业、准确和简洁。"
        user_prompt = f"### 检索到的知识参考详情 (Knowledge Base Context):\n{facts_text}\n\n### 用户当前问题 (User Query):\n{query}\n\n请按照上述要求（思考过程 + 最终结论）进行回答："

        llm = LLMClient()
        answer = llm.chat(messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ], temperature=temperature)

        return jsonify({
            "success": True,
            "data": {
                "answer": answer,
                "retrieved_facts_count": len(search_result.facts),
                "app_name": app.name
            }
        })

    except Exception as e:
        logger.error(f"Failed to execute AI app: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500

