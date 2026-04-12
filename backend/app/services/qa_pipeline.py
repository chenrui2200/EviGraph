"""
统一 QA Pipeline 服务

封装完整的 4 阶段 QA 流程：
  Stage 0: 意图解析 (QueryIntentParser)
  Stage 1: DFS 多跳检索 (search_with_dfs_flow / search_with_intent_guided_dfs_flow)
  Stage 2: bge-reranker-v2-m3 精排 (run_retrieval_flow)
  Stage 3: LLM 推理 (LLMClient)

被以下路由共用：
  - /api/graph/ai-qa          (SSE 流式, ai_qa_routes.py)
  - /api/report/tools/qa-pipeline (REST, report.py)
  - /api/ai-app/execute/:id   (REST, ai_app.py)
"""

import time
import traceback
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple, Generator

from ..utils.logger import get_logger
from ..utils.llm_client import LLMClient
from ..services.graph_tools import GraphToolsService
from ..storage import GraphStorage

logger = get_logger('mirofish.qa_pipeline')

# 统一的 LLM System Prompt（取自 report.py 的完整版本）
SYSTEM_PROMPT = (
    "你是一个专业的工程标准知识助手。你的任务是基于提供的多跳检索到的【知识参考详情】深度回答用户问题。\n\n"
    "回答要求：\n"
    "1. 请先在 <thought> 标签内分析所有检索到的条文关联，确保引用的完整性。\n"
    "2. 给出最终结论需要详实，必须引用具体的条款编号（如：根据 7.6.49 条规定...）。\n"
    "3. 如果知识涉及多个关联条款，请理清它们的逻辑先后关系。\n"
    "4. 若信息不足，请如实告知缺失的具体标准名称或编号。"
)


@dataclass
class QAPipelineConfig:
    """Pipeline 统一配置"""
    graph_ids: List[str]
    query: str
    top_k: int = 10
    rerank_min_score: int = 0
    max_depth: int = 3
    root_types: List[str] = None
    similarity_threshold: int = 0
    temperature: float = 0.7
    enable_intent: bool = True  # 是否启用意图解析

    def __post_init__(self):
        if self.root_types is None:
            self.root_types = ['Entity', 'Term']
        if isinstance(self.root_types, str):
            self.root_types = [self.root_types]


@dataclass
class QAPipelineResult:
    """Pipeline 统一输出"""
    intent_data: Optional[Dict] = None           # 意图解析结果
    retrieval_rows: List = field(default_factory=list)  # DFS 检索结果 (ObjectFirstRow)
    retrieval_duration: float = 0.0
    rerank_result: Optional[Any] = None          # RerankResult
    rerank_duration: float = 0.0
    llm_answer: str = ""
    llm_duration: float = 0.0
    prompts: Dict[str, str] = field(default_factory=dict)
    total_duration: float = 0.0


class QAPipelineService:
    """
    统一的 QA Pipeline 服务

    用法:
        # REST 一次性返回
        pipeline = QAPipelineService(storage)
        result = pipeline.run(config)

        # SSE 逐步 yield
        for event_type, event_data in pipeline.run_stages_iter(config):
            yield sse_format(event_type, event_data)
    """

    def __init__(self, storage: GraphStorage):
        self.storage = storage
        self.tools = GraphToolsService(storage=storage)
        self.llm = LLMClient()

    # ========================================================================
    # 完整 Pipeline (REST 路由用)
    # ========================================================================

    def run(self, config: QAPipelineConfig) -> QAPipelineResult:
        """执行完整 Pipeline，返回所有中间结果"""
        start = time.time()
        result = QAPipelineResult()

        # Stage 0: 意图解析
        intent, result.intent_data = self.run_intent_stage(config.query)

        # Stage 1: DFS 检索
        dfs_result, result.retrieval_duration = self.run_retrieval_stage(config, intent)
        result.retrieval_rows = dfs_result.rows

        # 相似度阈值过滤
        if config.similarity_threshold > 0:
            result.retrieval_rows = [
                r for r in result.retrieval_rows
                if (r.relevance_score or 0) >= config.similarity_threshold
            ]

        # Stage 2: 重排
        result.rerank_result, result.rerank_duration = self.run_rerank_stage(
            result.retrieval_rows, config.query, config
        )

        # Stage 3: LLM 推理
        result.llm_answer, result.prompts, result.llm_duration = self.run_llm_stage(
            result.rerank_result, config.query, config
        )

        result.total_duration = round(time.time() - start, 2)
        return result

    # ========================================================================
    # 生成器 Pipeline (SSE 路由用)
    # ========================================================================

    def run_stages_iter(self, config: QAPipelineConfig) -> Generator[Tuple[str, Dict], None, None]:
        """
        生成器模式，逐步 yield ('event_type', data_dict)。

        SSE 路由调用此方法，每个 yield 对应一个 SSE 事件。
        """
        start = time.time()

        # Stage 0: 意图解析
        intent, intent_data = self.run_intent_stage(config.query)
        yield ('intent_parsed', intent_data)

        # Stage 1: DFS 检索
        yield ('retrieval_start', {})
        dfs_result, ret_dur = self.run_retrieval_stage(config, intent)

        rows = dfs_result.rows
        if config.similarity_threshold > 0:
            rows = [r for r in rows if (r.relevance_score or 0) >= config.similarity_threshold]

        all_facts = []
        for row in rows:
            all_facts.extend(row.facts)
        logger.info(f"[Pipeline Stage 1] DFS 检索完成: rows:{len(rows)}, facts:{len(all_facts)}, duration={ret_dur}s")

        yield ('retrieval_complete', {
            'rows': [row.to_dict() for row in rows],
            'duration': ret_dur,
            'timings': {'object_s': ret_dur, 'term_s': 0, 'total_s': ret_dur},
        })

        # Stage 2: 重排
        yield ('rerank_start', {})
        rerank_result, rerank_dur = self.run_rerank_stage(rows, config.query, config)

        if rerank_result.scored_facts:
            yield ('rerank_results', {
                'facts': rerank_result.scored_facts,
                'total': len(rerank_result.scored_facts),
                'duration': f"{rerank_dur}s",
            })

        # Stage 3: LLM 推理
        yield ('llm_start', {})
        answer, prompts, llm_dur = self.run_llm_stage(rerank_result, config.query, config)

        yield ('prompts_ready', {
            'system': prompts['system'],
            'user': prompts['user'],
            'filtered_facts': rerank_result.filtered_facts,
        })

        yield ('llm_complete', {
            'answer': answer,
            'duration': llm_dur,
            'total_duration': round(time.time() - start, 2),
        })

    # ========================================================================
    # 各阶段独立方法
    # ========================================================================

    def run_intent_stage(self, query: str) -> Tuple[Any, Optional[Dict]]:
        """
        Stage 0: 意图解析

        Returns:
            (QueryIntent or None, intent_data dict or None)
        """
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
            logger.info(f"[Pipeline Stage 0] Intent parsed: component={intent.component}, "
                        f"action={intent.action}, obj={intent.obj}, "
                        f"confidence={intent.confidence:.2f}")
        except Exception as e:
            logger.warning(f"[Pipeline Stage 0] Intent parsing failed: {e}")

        return intent, intent_data

    def run_retrieval_stage(self, config: QAPipelineConfig, intent: Any = None):
        """
        Stage 1: DFS 多跳检索

        根据 intent 决定使用意图引导 DFS 还是通用 DFS。

        Returns:
            (ObjectFirstSearchResult, duration_seconds)
        """
        retrieval_start = time.time()

        use_intent = (
            config.enable_intent
            and intent is not None
            and not intent.is_empty()
            and intent.confidence >= 0.6
        )

        if use_intent:
            logger.info("[Pipeline Stage 1] Using intent-guided DFS")
            dfs_result = self.tools.search_with_intent_guided_dfs_flow(
                graph_ids=config.graph_ids,
                query=config.query,
                intent=intent,
                limit=20,
                max_depth=config.max_depth,
                root_types=config.root_types,
            )
        else:
            logger.info("[Pipeline Stage 1] Using generic DFS")
            dfs_result = self.tools.search_with_dfs_flow(
                graph_ids=config.graph_ids,
                query=config.query,
                limit=20,
                max_depth=config.max_depth,
                root_types=config.root_types,
            )

        ret_dur = round(time.time() - retrieval_start, 2)
        return dfs_result, ret_dur

    def run_rerank_stage(self, rows, query: str, config: QAPipelineConfig):
        """
        Stage 2: bge-reranker-v2-m3 精排

        Returns:
            (RerankResult, duration_seconds)
        """
        rerank_start = time.time()
        rerank_result = self.tools.run_retrieval_flow(
            final_rows=rows,
            query=query,
            similarity_threshold=config.similarity_threshold,
            top_k=config.top_k,
            rerank_min_score=config.rerank_min_score,
        )
        rerank_dur = round(time.time() - rerank_start, 2)

        logger.info(f"[Pipeline Stage 2] Rerank 完成: scored={len(rerank_result.scored_facts)}, "
                     f"filtered={len(rerank_result.filtered_facts)}, duration={rerank_dur}s")

        return rerank_result, rerank_dur

    def run_llm_stage(self, rerank_result, query: str, config: QAPipelineConfig):
        """
        Stage 3: LLM 推理

        使用统一的 SYSTEM_PROMPT 和扁平 facts 文本格式。

        Returns:
            (answer_str, prompts_dict, duration_seconds)
        """
        llm_start = time.time()

        # 构建 facts 文本：优先使用 original_text（Clause 完整内容），fallback 到 text
        rows_for_llm = rerank_result.rows_with_filtered_facts
        facts_text_parts = []
        for i, f in enumerate(rerank_result.filtered_facts):
            text = (f.get('original_text', '') or f.get('text', '') or '').strip()
            source = f.get('source', '')
            page = f.get('page', '')
            source_str = f"（来源: {source}" + (f", 页码: {page}" if page else "") + "）" if source else ""
            facts_text_parts.append(f"[{i + 1}] {text}{source_str}")

        facts_text = "\n".join(facts_text_parts) if facts_text_parts else "未找到高于阈值的相关事实。"

        user_prompt = (
            f"### 多跳检索结果汇总 (Context from Knowledge Graph):\n{facts_text}\n\n"
            f"### 用户当前问题 (User Query):\n{query}\n\n"
            f"请进行深度推理并回答："
        )

        prompts = {'system': SYSTEM_PROMPT, 'user': user_prompt}

        logger.info(f"[Pipeline Stage 3] LLM 推理: input_facts={len(rerank_result.filtered_facts)}, "
                     f"input_rows={len(rows_for_llm)}")

        answer = self.llm.chat(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=config.temperature,
        )

        llm_dur = round(time.time() - llm_start, 2)
        logger.info(f"[Pipeline Stage 3] LLM 推理完成: duration={llm_dur}s, answer_length={len(answer)}")

        return answer, prompts, llm_dur
