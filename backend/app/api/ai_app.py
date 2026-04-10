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

@ai_app_bp.route('/check-graph/<graph_id>', methods=['GET'])
def check_graph_references(graph_id: str):
    """Check which AI apps reference a specific graph_id (for project deletion constraint)"""
    try:
        apps = AiAppManager.list_apps(limit=100)
        referencing_apps = []

        for app in apps:
            selected_graph_ids = app.workflow_data.get('selectedGraphIds', [])
            if graph_id in selected_graph_ids:
                referencing_apps.append({
                    "app_id": app.app_id,
                    "name": app.name,
                    "is_published": app.is_published
                })

        return jsonify({
            "success": True,
            "data": {
                "graph_id": graph_id,
                "referencing_apps": referencing_apps,
                "can_delete": len(referencing_apps) == 0
            }
        })
    except Exception as e:
        logger.error(f"Failed to check graph references: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

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
    """Execute a published AI application via API - 使用与运行流程一致的逻辑"""
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

        # 从 app workflow_data 读取配置
        graph_ids = app.workflow_data.get('selectedGraphIds', [])
        temperature = app.workflow_data.get('temperature', 0.7)
        similarity_threshold = int(data.get('similarity_threshold', app.workflow_data.get('similarityThreshold', 0)))
        filter_threshold = int(data.get('filter_threshold', app.workflow_data.get('filterThreshold', 75)))
        max_depth = int(data.get('max_depth', app.workflow_data.get('maxDepth', 3)))
        root_types = data.get('root_types', app.workflow_data.get('rootTypes', ['Entity', 'Term']))

        if not graph_ids:
            return jsonify({"success": False, "error": "App has no knowledge base configured"}), 400

        storage = _get_storage()
        tools = GraphToolsService(storage=storage)

        # ===== Stage 1: 知识库检索（与运行流程一致）=====
        logger.info(f"API Exec App {app_id} [Stage 1] 知识库检索: query={query[:30]}, graphs={len(graph_ids)}")
        dfs_result = tools.search_with_dfs_flow(
            graph_ids=graph_ids, query=query, limit=20, max_depth=max_depth,
            root_types=root_types
        )

        # 相似度阈值过滤 rows
        if similarity_threshold > 0:
            dfs_result.rows = [r for r in dfs_result.rows if (r.relevance_score or 0) >= similarity_threshold]

        logger.info(f"API Exec App {app_id} [Stage 1] 完成: rows:{len(dfs_result.rows)}, facts:{sum(len(r.facts) for r in dfs_result.rows)}")

        # ===== Stage 2+3: LLM 重排 + 阈值过滤（共享方法）=====
        rerank_result = tools.run_retrieval_flow(
            final_rows=dfs_result.rows,
            query=query,
            similarity_threshold=similarity_threshold,
            filter_threshold=filter_threshold,
        )

        logger.info(f"API Exec App {app_id} [Stage 2+3] 完成: scored={len(rerank_result.scored_facts)}, filtered={len(rerank_result.filtered_facts)}")

        # 构建 LLM prompt（与运行流程一致）
        rows_for_llm = rerank_result.rows_with_filtered_facts
        facts_text = "\n\n".join(r.to_text() for r in rows_for_llm) if rows_for_llm else "未找到高于阈值的相关事实。"
        system_prompt = "你是一个专业的工程标准知识助手。你的任务是基于提供的多跳检索到的【知识参考详情】深度回答用户问题。\n\n回答要求：\n1. 请先在 <thought> 标签内分析所有检索到的条文关联，确引用的完整性。\n2. 给出最终结论，必须引用具体的条款编号（如：根据 7.6.49 条规定...）。\n3. 如果知识涉及多个关联条款，请理清它们的逻辑先后关系。\n4. 若信息不足，请如实告知缺失的具体标准名称或编号。"
        user_prompt = f"### 多跳检索结果汇总 (Context from Knowledge Graph):\n{facts_text}\n\n### 用户当前问题 (User Query):\n{query}\n\n请进行深度推理并回答："

        llm = LLMClient()
        answer = llm.chat(messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ], temperature=temperature)

        logger.info(f"API Exec App {app_id} 完成: answer_length={len(answer)}")

        return jsonify({
            "success": True,
            "data": {
                "answer": answer,
                "retrieved_facts": rerank_result.filtered_facts,
                "all_scored_facts": rerank_result.scored_facts,
                "rows": [r.to_dict() for r in rerank_result.rows_with_filtered_facts],
                "app_name": app.name,
            }
        })

    except Exception as e:
        logger.error(f"Failed to execute AI app: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500

