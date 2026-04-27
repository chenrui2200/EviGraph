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
        graph_id, query, limit, root_type (str, 兼容旧接口)
        或 root_types (list[str], 批量搜索多种根节点类型)

    当 root_types 提供时，一次性计算 embedding 并并行搜索所有类型。
    """
    data = request.get_json() or {}
    graph_id = data.get('graph_id')
    query = data.get('query', '')
    limit = int(data.get('limit', 10))
    root_type = data.get('root_type', 'Entity')
    root_types = data.get('root_types')  # 新参数: ['Entity', 'Term']
    similarity_threshold = float(data.get('similarity_threshold', 0))  # 前端阈值 50-100

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

        # 确定要搜索的根节点类型列表
        types_to_search = root_types if root_types else [root_type]

        result = tools.search_term_entity_to_clause_batch(
            graph_id=graph_id,
            query=query,
            limit=limit,
            root_types=types_to_search,
            similarity_threshold=similarity_threshold,
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
    相关性重排：接收检索结果 rows，调用 bge-reranker-v2-m3 打分 + 分数过滤 + top_k 截取。

    POST body:
        rows: List[ObjectFirstRow] (序列化后的 dict)
        query: str
        top_k: int (保留得分最高的 k 个样本，默认 10)
        rerank_min_score: int (0-100，最低分阈值，默认 0 不过滤)
    """
    from ..services.graph_tools import ObjectFirstRow, ObjectPathNode, ObjectPathEdge

    data = request.get_json() or {}
    rows_data = data.get('rows', [])
    query = data.get('query', '')
    top_k = int(data.get('top_k', 10))
    rerank_min_score = int(data.get('rerank_min_score', 0))

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
            top_k=top_k,
            rerank_min_score=rerank_min_score,
        )

        logger.info(f"Rerank complete: scored={len(rerank_result.scored_facts)}, filtered={len(rerank_result.filtered_facts)} (top_k={top_k}, min_score={rerank_min_score})")

        # 构建 fact_key -> topics 映射，用于为结果附加关联 Topic
        # 优先从 row.facts 中聚合所有 Topic 名称（比 traversal_paths 更完整）
        topic_map = {}
        for row in (getattr(rerank_result, 'rows_with_scored_facts', []) or []):
            all_topics = set()
            for fact in (row.facts or []):
                fact_topics = fact.get('topics', []) if isinstance(fact, dict) else getattr(fact, 'topics', [])
                all_topics.update(fact_topics)
            # fallback：从 traversal_paths 中提取 Topic 名称
            if not all_topics:
                for p in (row.traversal_paths or []):
                    if 'Topic' in (p.labels or []):
                        all_topics.add(p.name)
            topic_list = sorted(all_topics)
            for fact in (row.facts or []):
                ft = fact.get('text', '') if isinstance(fact, dict) else getattr(fact, 'text', '')
                fs = fact.get('source', '') if isinstance(fact, dict) else getattr(fact, 'source', '')
                topic_map[(ft, fs)] = topic_list

        scored_facts = []
        for f in (rerank_result.scored_facts or []):
            fc = dict(f) if not isinstance(f, dict) else dict(f)
            if not fc.get('topics'):
                key = (fc.get('text', ''), fc.get('source', ''))
                fc['topics'] = topic_map.get(key, [])
            scored_facts.append(fc)

        filtered_facts = []
        for f in (rerank_result.filtered_facts or []):
            fc = dict(f) if not isinstance(f, dict) else dict(f)
            if not fc.get('topics'):
                key = (fc.get('text', ''), fc.get('source', ''))
                fc['topics'] = topic_map.get(key, [])
            filtered_facts.append(fc)

        return jsonify({
            "success": True,
            "data": {
                "scored_facts": scored_facts,
                "filtered_facts": filtered_facts,
                "rows": [r.to_dict() for r in rerank_result.rows_with_scored_facts],
            }
        })
    except Exception as e:
        logger.error(f"Rerank failed: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


@report_bp.route('/tools/llm-answer', methods=['POST'])
def llm_answer():
    """
    LLM 推理问答：接收过滤后的 facts + query，构建 prompt 并调用 LLM 生成回答。

    POST body:
        facts: List[Dict] - 过滤后的 fact 列表（含 text, source, page, bbox, graph_id 等）
        query: str - 用户问题
        temperature: float (default 0.7)
    """
    data = request.get_json() or {}
    facts = data.get('facts', [])
    query = data.get('query', '')
    temperature = float(data.get('temperature', 0.7))

    if not query:
        return jsonify({"success": False, "error": "query is required"}), 400

    try:
        from ..utils.llm_client import LLMClient
        llm = LLMClient()

        # 构建 facts 文本（带来源标注）
        # 优先使用 original_text（Clause 完整内容），fallback 到 text
        facts_text_parts = []
        for i, f in enumerate(facts):
            text = f.get('original_text', '').strip() or f.get('text', '').strip()
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
        user_prompt = (
            f"### 多跳检索结果汇总 (Context from Knowledge Graph):\n{facts_text}\n\n"
            f"### 用户当前问题 (User Query):\n{query}\n\n"
            f"请进行深度推理并回答："
        )

        logger.info(f"LLM Answer: facts={len(facts)}, query={query[:50]}...")

        answer = llm.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
        )

        # 构建 facts 完整信息（含 PDF 可访问 URL）
        base_url = request.host_url.rstrip('/')
        enriched_facts = []
        for i, f in enumerate(facts):
            item = dict(f)
            # 如果有 PDF 来源，生成可访问的文档 URL
            if f.get('source') and f.get('graph_id'):
                page = f.get('page', 1)
                doc_url = f"{base_url}/api/graph/project/{f['graph_id']}/document/{f['source']}?page={page}"
                item['pdf_url'] = doc_url
            enriched_facts.append(item)

        return jsonify({
            "success": True,
            "data": {
                "answer": answer,
                "facts": enriched_facts,
                "prompts": {
                    "system": system_prompt,
                    "user": user_prompt,
                },
            },
        })
    except Exception as e:
        logger.error(f"LLM Answer failed: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


@report_bp.route('/public-query', methods=['POST'])
def public_query():
    """
    公共查询接口：无需认证，通过 app_id 获取应用配置并执行检索。

    POST body:
        app_id: str - 应用ID
        query: str - 查询问题

    返回格式：
        {
            "success": true,
            "data": {
                "query": "...",
                "results": [
                    {
                        "text": "条款内容",
                        "source": "GB50054.pdf",
                        "page": 12,
                        "pdf_bboxes": [[12, 100, 200, 300, 400]],
                        "bbox": [100, 200, 300, 400],
                        "page_width": 595,
                        "page_height": 842,
                        "relevance_score": 85,
                        "pdf_url": "http://.../api/graph/project/xxx/document/GB50054.pdf?page=12",
                        "source_link": "http://.../chat/app_xxx?source=GB50054.pdf&page=12&bbox=100,200,300,400"
                    }
                ]
            }
        }
    """
    data = request.get_json() or {}
    app_id = data.get('app_id', '')
    query = data.get('query', '')

    if not app_id:
        return jsonify({"success": False, "error": "app_id is required"}), 400
    if not query:
        return jsonify({"success": False, "error": "query is required"}), 400

    from ..models.ai_app import AiAppManager
    app = AiAppManager.get_app(app_id)
    if not app:
        return jsonify({"success": False, "error": "Application not found"}), 404
    if not app.is_published:
        return jsonify({"success": False, "error": "Application is not published"}), 403

    from flask import current_app
    storage = current_app.extensions.get('neo4j_storage')
    if not storage:
        return jsonify({"success": False, "error": "Storage not available"}), 503

    try:
        wf = app.workflow_data or {}
        saved_project_ids = wf.get('selectedProjectIds', [])

        # Resolve graph_ids from project_ids
        if saved_project_ids:
            from ..models.project import ProjectManager
            graph_ids = []
            for pid in saved_project_ids:
                proj = ProjectManager.get_project(pid)
                if proj and getattr(proj, 'graph_id', None):
                    graph_ids.append(proj.graph_id)
        else:
            graph_ids = wf.get('selectedGraphIds', [])

        if not graph_ids:
            return jsonify({"success": False, "error": "No knowledge base configured"}), 400

        root_types = wf.get('rootTypes', ['Entity', 'Term'])
        similarity_threshold = float(wf.get('similarityThreshold', 0))
        top_k = int(wf.get('topK', 5))
        rerank_min_score = int(wf.get('rerankMinScore', 0))

        tools = GraphToolsService(storage=storage)
        base_url = request.host_url.rstrip('/')

        # 前端地址：用于生成 /chat/ 等客户端路由链接
        from flask import current_app
        frontend_url = current_app.config.get('FRONTEND_URL')
        frontend_base_url = frontend_url.rstrip('/') if frontend_url else base_url

        # Stage 1: Search across all graph_ids
        all_rows = []
        seen_uuids = set()
        for gid in graph_ids:
            result = tools.search_term_entity_to_clause_batch(
                graph_id=gid,
                query=query,
                limit=15,
                root_types=root_types,
                similarity_threshold=similarity_threshold,
            )
            for row in result.rows:
                root_uuid = row.object_node.get('uuid', '')
                if root_uuid and root_uuid not in seen_uuids:
                    seen_uuids.add(root_uuid)
                    all_rows.append(row)

        if not all_rows:
            return jsonify({
                "success": True,
                "data": {"query": query, "results": []}
            })

        # Stage 2: Rerank
        try:
            from ..services.graph_tools import ObjectFirstRow, ObjectPathNode, ObjectPathEdge
            final_rows = []
            for r in all_rows:
                paths = [
                    ObjectPathNode(
                        uuid=p.uuid,
                        name=p.name,
                        labels=p.labels,
                        summary=p.summary,
                        depth=p.depth,
                    )
                    for p in r.traversal_paths
                ]
                edges = [
                    ObjectPathEdge(
                        uuid=e.uuid,
                        name=e.name,
                        fact=e.fact,
                        source_node_uuid=e.source_node_uuid,
                        target_node_uuid=e.target_node_uuid,
                        depth=e.depth,
                    )
                    for e in r.traversal_edges
                ]
                final_rows.append(ObjectFirstRow(
                    object_node=r.object_node,
                    traversal_paths=paths,
                    traversal_edges=edges,
                    facts=r.facts,
                    relevance_score=r.relevance_score,
                ))

            rerank_result = tools.run_retrieval_flow(
                final_rows=final_rows,
                query=query,
                similarity_threshold=0,
                top_k=top_k,
                rerank_min_score=rerank_min_score,
            )

            # 构建 fact_key -> topics 映射，用于为结果附加关联 Topic
            # 优先从 row.facts 中聚合所有 Topic 名称（比 traversal_paths 更完整）
            topic_map = {}
            for row in (getattr(rerank_result, 'rows_with_scored_facts', []) or []):
                all_topics = set()
                for fact in (row.facts or []):
                    fact_topics = fact.get('topics', []) if isinstance(fact, dict) else getattr(fact, 'topics', [])
                    all_topics.update(fact_topics)
                # fallback：从 traversal_paths 中提取 Topic 名称
                if not all_topics:
                    for p in (row.traversal_paths or []):
                        if 'Topic' in (p.labels or []):
                            all_topics.add(p.name)
                topic_list = sorted(all_topics)
                for fact in (row.facts or []):
                    ft = fact.get('text', '') if isinstance(fact, dict) else getattr(fact, 'text', '')
                    fs = fact.get('source', '') if isinstance(fact, dict) else getattr(fact, 'source', '')
                    topic_map[(ft, fs)] = topic_list

            raw_facts = rerank_result.filtered_facts or rerank_result.scored_facts or []
            facts = []
            for f in raw_facts:
                fc = dict(f) if not isinstance(f, dict) else f
                if not fc.get('topics'):
                    key = (fc.get('text', ''), fc.get('source', ''))
                    fc['topics'] = topic_map.get(key, [])
                facts.append(fc)
        except Exception as e:
            logger.warning(f"Rerank failed in public-query, fallback to raw facts: {e}")
            facts = []
            for row in all_rows:
                topics = [p.name for p in (row.traversal_paths or []) if 'Topic' in (p.labels or [])]
                for fact in (row.facts or []):
                    fact_copy = dict(fact) if not isinstance(fact, dict) else dict(fact)
                    if fact_copy.get('relevance_score') is None:
                        fact_copy['relevance_score'] = 0.0
                    fact_copy['topics'] = topics
                    facts.append(fact_copy)

        # Build results array
        results = []
        for f in facts:
            item = {
                "text": f.get('original_text', '').strip() or f.get('text', '').strip(),
                "source": f.get('source', ''),
                "page": f.get('page'),
                "pdf_bboxes": f.get('pdf_bboxes', []),
                "bbox": f.get('bbox'),
                "page_width": f.get('page_width'),
                "page_height": f.get('page_height'),
                "relevance_score": f.get('relevance_score') if f.get('relevance_score') is not None else 0,
                "graph_id": f.get('graph_id'),
                "topics": f.get('topics', []),
            }
            # PDF download URL
            if f.get('source') and f.get('graph_id'):
                page = f.get('page', 1)
                item['pdf_url'] = f"{base_url}/api/graph/project/{f['graph_id']}/document/{f['source']}?page={page}"
            # Source link: click to open PDF preview page with bbox highlight
            # 传递完整 pdf_bboxes，支持多段高亮（同一 clause 跨页或多区域）
            pdf_bboxes = f.get('pdf_bboxes')
            if f.get('source') and pdf_bboxes and len(pdf_bboxes) > 0:
                import urllib.parse
                bboxes_str = urllib.parse.quote(json.dumps(pdf_bboxes))
                item['source_link'] = f"{frontend_base_url}/preview/{app_id}?source={f['source']}&page={f.get('page', 1)}&pdf_bboxes={bboxes_str}&graph_id={f['graph_id']}"
            results.append(item)

        return jsonify({
            "success": True,
            "data": {
                "query": query,
                "results": results,
            }
        })
    except Exception as e:
        logger.error(f"public_query failed: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


