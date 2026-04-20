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
        top_k = int(data.get('top_k', app.workflow_data.get('topK', 10)))
        rerank_min_score = int(data.get('rerank_min_score', app.workflow_data.get('rerankMinScore', 0)))
        max_depth = int(data.get('max_depth', app.workflow_data.get('maxDepth', 3)))
        root_types = data.get('root_types', app.workflow_data.get('rootTypes', ['Entity', 'Term']))

        if not graph_ids:
            return jsonify({"success": False, "error": "App has no knowledge base configured"}), 400

        storage = _get_storage()
        tools = GraphToolsService(storage=storage)

        # ===== Stage 1: 知识库检索（与 AI-QA 前端 hitTestSearch 一致）=====
        # 使用固定 2 跳路径检索：Entity/Term → Topic → Clause
        logger.info(f"API Exec App {app_id} [Stage 1] 知识库检索: query={query[:30]}, graphs={len(graph_ids)}")

        all_rows = []
        seen_uuids = set()
        for gid in graph_ids:
            for root_type in root_types:
                try:
                    search_result = tools.search_term_entity_to_clause(
                        graph_id=gid, query=query, limit=20, root_type=root_type,
                    )
                    for row in search_result.rows:
                        obj_uuid = row.object_node.uuid if row.object_node else None
                        if obj_uuid and obj_uuid not in seen_uuids:
                            seen_uuids.add(obj_uuid)
                            all_rows.append(row)
                except Exception as e:
                    logger.warning(f"API Exec App {app_id} search failed for graph={gid}, root_type={root_type}: {e}")

        # 相似度阈值过滤 rows
        if similarity_threshold > 0:
            all_rows = [r for r in all_rows if (r.relevance_score or 0) >= similarity_threshold]

        logger.info(f"API Exec App {app_id} [Stage 1] 完成: rows:{len(all_rows)}, facts:{sum(len(r.facts) for r in all_rows)}")

        # ===== Stage 2+3: bge-reranker-v2-m3 精排 + 分数过滤 + top_k 截取（共享方法）=====
        rerank_result = tools.run_retrieval_flow(
            final_rows=all_rows,
            query=query,
            similarity_threshold=similarity_threshold,
            top_k=top_k,
            rerank_min_score=rerank_min_score,
        )

        logger.info(f"API Exec App {app_id} [Stage 2+3] 完成: scored={len(rerank_result.scored_facts)}, filtered={len(rerank_result.filtered_facts)}")

        # 构建 LLM prompt（与 /tools/llm-answer 格式一致）
        # 使用扁平 facts 格式，带来源标注
        facts_text_parts = []
        for i, f in enumerate(rerank_result.filtered_facts):
            text = (f.get('original_text', '') or f.get('text', '') or '').strip()
            source = f.get('source', '')
            page = f.get('page', '')
            source_str = f"（来源: {source}" + (f", 页码: {page}" if page else "") + "）" if source else ""
            facts_text_parts.append(f"[{i + 1}] {text}{source_str}")
        facts_text = "\n".join(facts_text_parts) if facts_text_parts else "未找到高于阈值的相关事实。"
        system_prompt = (
            "你是一个专业的工程标准知识助手。你的任务是基于提供的多跳检索到的【知识参考详情】深度回答用户问题。\n\n"
            "【绝对约束】你无权使用预训练知识，回答必须100%基于下面提供的【知识参考详情】。\n"
            "禁止编造、推断、推测、补充或引用任何未出现在知识参考中的内容。\n"
            "禁止输出\"根据一般工程经验\"、\"通常情况下\"、\"一般来说\"等依赖预训练知识的表述。\n\n"
            "回答要求：\n"
            "1. 请先在 <thought> 标签内分析所有检索到的条文关联，确保引用的完整性。\n"
            "2. 给出最终结论需要详实，必须引用具体的条款编号（如：根据 7.6.49 条规定...）。\n"
            "3. 如果知识涉及多个关联条款，请理清它们的逻辑先后关系。\n"
            "4. 若【知识参考详情】中没有找到能够回答该问题的依据，必须明确回答："
            "\"目前缺少相关知识依据，无法准确回答该问题。建议换一种提问方式，或补充更具体的标准编号/术语。\""
            "禁止在缺乏知识依据时给出任何看似合理的推断性回答。"
        )
        user_prompt = f"### 多跳检索结果汇总 (Context from Knowledge Graph):\n{facts_text}\n\n### 用户当前问题 (User Query):\n{query}\n\n请进行深度推理并回答："

        llm = LLMClient()
        answer = llm.chat(messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ], temperature=temperature)

        logger.info(f"API Exec App {app_id} 完成: answer_length={len(answer)}")

        # 解析 answer 中的 thought/conclusion
        thought = ''
        conclusion = answer
        thought_match = re.match(r'<(?:thought|think)>([\s\S]*?)</(?:thought|think)>', answer, re.IGNORECASE)
        if thought_match:
            thought = thought_match.group(1).strip()
            conclusion = re.sub(r'<(?:thought|think)>[\s\S]*?</(?:thought|think)>', '', answer, flags=re.IGNORECASE).strip()

        # 构建来源 PDF 下载 URL 模板
        base_url = request.host_url.rstrip('/')

        # 构建结构化证据列表（含 PDF 定位信息）
        evidence_list = []
        for idx, fact in enumerate(rerank_result.filtered_facts):
            graph_id = fact.get('graph_id', '')
            source = fact.get('source', '')
            page = fact.get('page')
            bbox = fact.get('bbox')
            page_width = fact.get('page_width')
            page_height = fact.get('page_height')
            pdf_bboxes = fact.get('pdf_bboxes')

            evidence_item = {
                "index": idx + 1,
                "text": fact.get('text', ''),
                "clause_content": fact.get('original_text', ''),
                "source_file": source,
                "page": page,
                "relevance_score": fact.get('relevance_score', 0),
                "relevance_reasoning": fact.get('relevance_reasoning', ''),
                "clause_id": fact.get('uuid', ''),
            }

            # PDF 定位信息
            if source and source not in ('Unknown', 'Graph', 'Graph Knowledge', 'Local Search'):
                evidence_item["pdf"] = {
                    "download_url": f"{base_url}/api/graph/project/{graph_id}/document/{source}",
                    "page_index": page or 1,
                }
                if bbox and isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                    evidence_item["pdf"]["bbox"] = list(bbox[:4])  # [x0, y0, x1, y1]
                    if page_width:
                        evidence_item["pdf"]["page_width"] = page_width
                    if page_height:
                        evidence_item["pdf"]["page_height"] = page_height

            evidence_list.append(evidence_item)

        return jsonify({
            "success": True,
            "data": {
                "query": query,
                "app_name": app.name,
                "answer": {
                    "raw": answer,
                    "thought": thought,
                    "conclusion": conclusion,
                },
                "evidence": evidence_list,
                "retrieval_stats": {
                    "total_rows": len(all_rows),
                    "total_facts_retrieved": sum(len(r.facts) for r in all_rows),
                    "facts_scored": len(rerank_result.scored_facts),
                    "facts_filtered": len(rerank_result.filtered_facts),
                    "top_k": top_k,
                },
            }
        })

    except Exception as e:
        logger.error(f"Failed to execute AI app: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500

