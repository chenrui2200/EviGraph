"""
AI Q&A API Routes
Handles multi-hop knowledge graph QA with streaming responses
"""

import json
import time
from flask import request, Response, stream_with_context

from . import graph_bp
from ..utils.logger import get_logger
from ..utils.api_utils import api_handler
from ..services.graph_tools import GraphToolsService

logger = get_logger('mirofish.api')


@graph_bp.route('/ai-qa', methods=['POST'])
@api_handler
def ai_qa():
    """
    AI Q&A Interface: SSE streaming progress updates.
    """
    data = request.get_json() or {}
    query = data.get('query')
    graph_ids = data.get('graph_ids', [])

    try:
        top_k = int(data.get('top_k', 10))
    except (ValueError, TypeError):
        top_k = 10

    try:
        rerank_min_score = int(data.get('rerank_min_score', 0))
    except (ValueError, TypeError):
        rerank_min_score = 0

    try:
        max_depth = int(data.get('max_depth', 3))
    except (ValueError, TypeError):
        max_depth = 3

    root_types = data.get('root_types', ['Entity', 'Term'])
    if isinstance(root_types, str):
        root_types = [root_types]

    if not query:
        return jsonify({"success": False, "error": "Please provide query"}), 400
    if not graph_ids:
        return jsonify({"success": False, "error": "Please select at least one knowledge base"}), 400

    from .graph import _get_storage
    storage = _get_storage()

    @stream_with_context
    def generate():
        start_total = time.time()
        try:
            tools = GraphToolsService(storage=storage)
            from ..utils.llm_client import LLMClient
            llm = LLMClient()

            # ===== Stage 0: 意图解析 =====
            intent = None
            intent_data = None
            try:
                from ..services.query_intent_parser import QueryIntentParser
                parser = QueryIntentParser()
                intent = parser.parse(query)
                intent_data = {
                    'type': intent.type,
                    'component': intent.component,
                    'action': intent.action,
                    'obj': intent.obj,
                    'condition': intent.condition,
                    'requirement': intent.requirement,
                    'confidence': intent.confidence,
                }
                logger.info(f"[Stage 0] Intent parsed: component={intent.component}, "
                            f"action={intent.action}, obj={intent.obj}, "
                            f"confidence={intent.confidence:.2f}")
            except Exception as e:
                logger.warning(f"[Stage 0] Intent parsing failed: {e}")

            yield f"data: {json.dumps({'type': 'intent_parsed', 'data': intent_data}, ensure_ascii=False)}\n\n"

            # ===== Stage 1: 知识库检索 =====
            yield f"data: {json.dumps({'type': 'retrieval_start'})}\n\n"
            retrieval_start = time.time()

            use_intent = (
                intent is not None
                and not intent.is_empty()
                and intent.confidence >= 0.6
            )
            if use_intent:
                dfs_result = tools.search_with_intent_guided_dfs_flow(
                    graph_ids=graph_ids, query=query, intent=intent,
                    limit=20, max_depth=max_depth, root_types=root_types
                )
            else:
                dfs_result = tools.search_with_dfs_flow(
                    graph_ids=graph_ids, query=query, limit=20, max_depth=max_depth,
                    root_types=root_types
                )
            ret_dur = round(time.time() - retrieval_start, 2)

            similarity_threshold = int(data.get('similarity_threshold', 0))
            if similarity_threshold > 0:
                dfs_result.rows = [r for r in dfs_result.rows if (r.relevance_score or 0) >= similarity_threshold]

            all_facts = []
            for row in dfs_result.rows:
                all_facts.extend(row.facts)

            logger.info(f"[Stage 1] 知识库检索完成: rows:{len(dfs_result.rows)}, facts:{len(all_facts)}, duration={ret_dur}s")

            rows_data = [row.to_dict() for row in dfs_result.rows]

            msg_ret = {
                'type': 'retrieval_complete',
                'data': {
                    'rows': rows_data,
                    'duration': ret_dur,
                    'timings': {
                        'object_s': ret_dur,
                        'term_s': 0,
                        'total_s': ret_dur
                    },
                }
            }
            yield f"data: {json.dumps(msg_ret, ensure_ascii=False)}\n\n"

            # ===== Stage 2: bge-reranker-v2-m3 精排 =====
            yield f"data: {json.dumps({'type': 'rerank_start'})}\n\n"
            rerank_start = time.time()

            rerank_result = tools.run_retrieval_flow(
                final_rows=dfs_result.rows,
                query=query,
                similarity_threshold=similarity_threshold,
                top_k=top_k,
                rerank_min_score=rerank_min_score,
            )
            rerank_dur = round(time.time() - rerank_start, 2)

            if rerank_result.scored_facts:
                msg_rerank = {
                    'type': 'rerank_results',
                    'data': {
                        'facts': rerank_result.scored_facts,
                        'total': len(rerank_result.scored_facts),
                        'duration': f"{rerank_dur}s",
                    }
                }
                yield f"data: {json.dumps(msg_rerank, ensure_ascii=False)}\n\n"

            # ===== Stage 3: 阈值过滤 + LLM 推理 =====
            yield f"data: {json.dumps({'type': 'llm_start'})}\n\n"
            llm_start = time.time()

            rows_for_llm = rerank_result.rows_with_filtered_facts
            facts_text = "\n\n".join(r.to_text() for r in rows_for_llm) if rows_for_llm else "未找到高于阈值的相关事实。"
            system_prompt = "你是一个专业的工程标准知识助手。你的任务是基于提供的多跳检索到的【知识参考详情】深度回答用户问题。\n\n回答要求：\n1. 请先在 <thought> 标签内分析所有检索到的条文关联，确引用的完整性。\n2. 给出最终结论，必须引用具体的条款编号（如：根据 7.6.49 条规定...）。\n3. 如果知识涉及多个关联条款，请理清它们的逻辑先后关系。\n4. 若信息不足，请如实告知缺失的具体标准名称或编号。"
            user_prompt = f"### 多跳检索结果汇总 (Context from Knowledge Graph):\n{facts_text}\n\n### 用户当前问题 (User Query):\n{query}\n\n请进行深度推理并回答："

            logger.info(f"[Stage 3] LLM 推理: input=facts:{len(rerank_result.filtered_facts)}, output=rows:{len(rerank_result.rows_with_filtered_facts)}")

            msg_prompts = {
                'type': 'prompts_ready',
                'data': {
                    'system': system_prompt,
                    'user': user_prompt,
                    'filtered_facts': rerank_result.filtered_facts,
                }
            }
            yield f"data: {json.dumps(msg_prompts, ensure_ascii=False)}\n\n"

            answer = llm.chat(messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ], temperature=data.get('temperature', 0.7))

            llm_dur = round(time.time() - llm_start, 2)
            logger.info(f"[Stage 3] LLM 推理完成: duration={llm_dur}s")

            msg_final = {
                'type': 'llm_complete',
                'data': {
                    'answer': answer,
                    'duration': llm_dur,
                    'total_duration': round(time.time() - start_total, 2)
                }
            }
            yield f"data: {json.dumps(msg_final, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"AI Q&A stream failed: {str(e)}\n{traceback.format_exc()}")
            err_msg = {'type': 'error', 'message': str(e)}
            yield f"data: {json.dumps(err_msg, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype='text/event-stream')
