"""
Graph Retrieval Tools Service
Encapsulates graph search, node retrieval, edge queries, and other tools for use by Report Agent.

Replaces zep_tools.py — all Zep Cloud calls replaced by GraphStorage.

Core Retrieval Tools (Optimized):
1. InsightForge (Deep Insight Retrieval) - Most powerful hybrid search, automatically generates sub-questions and multi-dimensional retrieval
2. PanoramaSearch (Breadth Search) - Get comprehensive view, including expired content
3. QuickSearch (Simple Search) - Quick retrieval
"""

import json
import re
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..utils.logger import get_logger
from ..utils.llm_client import LLMClient
from ..storage import GraphStorage

logger = get_logger('mirofish.graph_tools')


@dataclass
class SearchResult:
    """Search Result"""
    facts: List[Dict[str, Any]]  # List of {text, source, page, graph_id, relevance_score, reasoning}
    edges: List[Dict[str, Any]]
    nodes: List[Dict[str, Any]]
    query: str
    total_count: int
    rerank_details: List[Dict[str, Any]] = field(default_factory=list) # [{index, score, reason}]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "facts": self.facts,
            "edges": self.edges,
            "nodes": self.nodes,
            "query": self.query,
            "total_count": self.total_count,
            "rerank_details": self.rerank_details
        }

    def to_text(self) -> str:
        """Convert to text format for LLM understanding with source tracking"""
        text_parts = [f"Search Query: {self.query}", f"Found {self.total_count} related results"]

        if self.facts:
            text_parts.append("\n### 检索到的知识参考详情 (Retrieved Knowledge References):")

            # Show each fact followed immediately by its raw context
            for i, fact_obj in enumerate(self.facts, 1):
                text = fact_obj.get("text", "")
                source = fact_obj.get("source", "Unknown")
                page = fact_obj.get("page", "")
                raw_text = fact_obj.get("original_text", "")

                source_str = f" [来源: {source}{f', 页码 {page}' if page else ''}]"

                # Header: Fact + Source info
                text_parts.append(f"{i}. {text}{source_str}")

                # Context: Raw PDF Text if available
                if raw_text:
                    # Provide raw text to LLM to verify fact accuracy
                    text_parts.append(f"{raw_text.strip()}\n")

        return "\n".join(text_parts)


@dataclass
class NodeInfo:
    """Node Information"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": self.labels,
            "summary": self.summary,
            "attributes": self.attributes
        }

    def to_text(self) -> str:
        """Convert to text format"""
        entity_type = next((la for la in self.labels if la not in ["Entity", "Node"]), "Unknown type")
        return f"Entity: {self.name} (Type: {entity_type})\nSummary: {self.summary}"


@dataclass
class EdgeInfo:
    """Edge Information"""
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    source_node_name: Optional[str] = None
    target_node_name: Optional[str] = None
    # Temporal information (may be absent in Neo4j — kept for interface compat)
    created_at: Optional[str] = None
    valid_at: Optional[str] = None
    invalid_at: Optional[str] = None
    expired_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "fact": self.fact,
            "source_node_uuid": self.source_node_uuid,
            "target_node_uuid": self.target_node_uuid,
            "source_node_name": self.source_node_name,
            "target_node_name": self.target_node_name,
            "created_at": self.created_at,
            "valid_at": self.valid_at,
            "invalid_at": self.invalid_at,
            "expired_at": self.expired_at
        }

    def to_text(self, include_temporal: bool = False) -> str:
        """Convert to text format"""
        source = self.source_node_name or self.source_node_uuid[:8]
        target = self.target_node_name or self.target_node_uuid[:8]
        base_text = f"Relationship: {source} --[{self.name}]--> {target}\nFact: {self.fact}"

        if include_temporal:
            valid_at = self.valid_at or "Unknown"
            invalid_at = self.invalid_at or "Present"
            base_text += f"\nTime Range: {valid_at} - {invalid_at}"
            if self.expired_at:
                base_text += f" (Expired: {self.expired_at})"

        return base_text

    @property
    def is_expired(self) -> bool:
        """Whether already expired"""
        return self.expired_at is not None

    @property
    def is_invalid(self) -> bool:
        """Whether already invalid"""
        return self.invalid_at is not None


@dataclass
class ObjectPathNode:
    """DFS traversal中访问的单个节点路径"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    depth: int  # 深度（0 = Object 起始节点）


@dataclass
class ObjectPathEdge:
    """DFS遍历中访问的边"""
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    depth: int  # 深度（边的起始节点深度）


@dataclass
class ObjectFirstRow:
    """
    单个 Object 节点的检索结果（一行记录）。
    包含 Object 节点本身以及从该节点 DFS 遍历出的所有关联节点和边。
    """
    object_node: Dict[str, Any]          # Object 节点详情
    traversal_paths: List[ObjectPathNode]  # DFS 遍历经过的节点路径
    traversal_edges: List[ObjectPathEdge]  # DFS 遍历经过的边
    facts: List[Dict[str, Any]]           # 关联的事实列表（含 PDF 定位）
    relevance_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "object_node": self.object_node,
            "traversal_paths": [
                {
                    "uuid": p.uuid,
                    "name": p.name,
                    "labels": p.labels,
                    "summary": p.summary,
                    "depth": p.depth,
                }
                for p in self.traversal_paths
            ],
            "traversal_edges": [
                {
                    "uuid": e.uuid,
                    "name": e.name,
                    "fact": e.fact,
                    "source_node_uuid": e.source_node_uuid,
                    "target_node_uuid": e.target_node_uuid,
                    "depth": e.depth,
                }
                for e in self.traversal_edges
            ],
            "facts": self.facts,
            "relevance_score": self.relevance_score,
        }

    def to_text(self) -> str:
        """转换为文本格式，便于 LLM 理解"""
        obj_name = self.object_node.get("name", "Unknown")
        summary = self.object_node.get("summary", "N/A")
        parts = [f"## Object: {obj_name}"]
        parts.append(f"说明: {summary}")
        if self.traversal_paths:
            parts.append(f"关联路径 ({len(self.traversal_paths)} 个节点):")
            for p in self.traversal_paths:
                indent = "  " * (p.depth + 1)
                parts.append(f"{indent}- [{p.labels[0] if p.labels else 'Entity'}] {p.name}")
        if self.facts:
            # 统计出处分布
            source_count: Dict[str, int] = {}
            for f in self.facts:
                src = f.get("source", "Graph") or "Graph"
                source_count[src] = source_count.get(src, 0) + 1
            source_info = " | ".join([f"{k}({v}条)" for k, v in source_count.items()])
            parts.append(f"共 {len(self.facts)} 条事实，出处: {source_info}")
            for idx, f in enumerate(self.facts, 1):
                src = f.get("source", "Graph") or "Graph"
                pg = f.get("page")
                rel = f.get("relation_name", "")
                text = f.get("text", "")
                orig = f.get("original_text", "")
                # 优先使用 original_text（真实条款内容），否则去掉 text 中的关系前缀
                if orig.strip():
                    display_text = orig.strip()
                else:
                    # text 可能是 "[MENTIONS] 内容"，去掉前缀
                    display_text = text
                    for prefix in ["[MENTIONS]", "[HAS_TOPIC]"]:
                        if display_text.startswith(prefix):
                            display_text = display_text[len(prefix):].strip()
                            break
                score = f.get("relevance_score")
                score_str = f" (相关性{score}分)" if score is not None else ""
                pg_str = f"，页码 {pg}" if pg else ""
                rel_str = f"[{rel}] " if rel else ""
                parts.append(f"  {idx}. {rel_str}{display_text}{score_str} (来源: {src}{pg_str})")
        return "\n".join(parts)


@dataclass
class RerankResult:
    """
    检索流程结果（知识库检索 + bge-reranker-v2-m3 重排 + 分数过滤 + top_k 截取）。
    供 SSE 流程和 API 流程共用。
    """
    scored_facts: List[Dict[str, Any]]  # 所有 facts 带 relevance_score（供前端展示）
    filtered_facts: List[Dict[str, Any]]  # 过滤后 facts（供 LLM 推理）
    rows_with_scored_facts: List[ObjectFirstRow]  # 带所有打分 facts 的 rows
    rows_with_filtered_facts: List[ObjectFirstRow]  # 带过滤后 facts 的 rows（供 LLM prompt）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scored_facts": self.scored_facts,
            "filtered_facts": self.filtered_facts,
            "rows_scored": [r.to_dict() for r in self.rows_with_scored_facts],
            "rows_filtered": [r.to_dict() for r in self.rows_with_filtered_facts],
        }


@dataclass
class ObjectFirstSearchResult:
    """
    Object-first DFS 检索结果。
    所有结果以 Object 节点为行组织，每行包含该 Object 的 DFS 遍历结果。
    """
    query: str
    rows: List[ObjectFirstRow]
    total_objects: int = 0
    total_facts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "rows": [r.to_dict() for r in self.rows],
            "total_objects": self.total_objects,
            "total_facts": self.total_facts,
        }

    def to_text(self) -> str:
        parts = [f"## Object-First Retrieval Results\nQuery: {self.query}\n"]
        for row in self.rows:
            parts.append(row.to_text())
            parts.append("\n---\n")
        return "\n".join(parts)


@dataclass
class InsightForgeResult:
    """
    Deep Insight Retrieval Result (InsightForge)
    Contains retrieval results from multiple sub-questions and integrated analysis
    """
    query: str
    simulation_requirement: str
    sub_queries: List[str]

    # Retrieval results by dimension
    semantic_facts: List[str] = field(default_factory=list)
    entity_insights: List[Dict[str, Any]] = field(default_factory=list)
    relationship_chains: List[str] = field(default_factory=list)

    # Statistical information
    total_facts: int = 0
    total_entities: int = 0
    total_relationships: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "simulation_requirement": self.simulation_requirement,
            "sub_queries": self.sub_queries,
            "semantic_facts": self.semantic_facts,
            "entity_insights": self.entity_insights,
            "relationship_chains": self.relationship_chains,
            "total_facts": self.total_facts,
            "total_entities": self.total_entities,
            "total_relationships": self.total_relationships
        }

    def to_text(self) -> str:
        """Convert to detailed text format for LLM understanding"""
        text_parts = [
            f"## Future Prediction Deep Analysis",
            f"Analysis Query: {self.query}",
            f"Prediction Scenario: {self.simulation_requirement}",
            f"\n### Prediction Data Statistics",
            f"- Related Prediction Facts: {self.total_facts}",
            f"- Involved Entities: {self.total_entities}",
            f"- Relationship Chains: {self.total_relationships}"
        ]

        if self.sub_queries:
            text_parts.append(f"\n### Analysis Sub-Questions")
            for i, sq in enumerate(self.sub_queries, 1):
                text_parts.append(f"{i}. {sq}")

        if self.semantic_facts:
            text_parts.append(f"\n### Key Facts (Please quote these verbatim in the report)")
            for i, fact in enumerate(self.semantic_facts, 1):
                text_parts.append(f'{i}. "{fact}"')

        if self.entity_insights:
            text_parts.append(f"\n### Core Entities")
            for entity in self.entity_insights:
                text_parts.append(f"- **{entity.get('name', 'Unknown')}** ({entity.get('type', 'Entity')})")
                if entity.get('summary'):
                    text_parts.append(f"  Summary: \"{entity.get('summary')}\"")
                if entity.get('related_facts'):
                    text_parts.append(f"  Related Facts: {len(entity.get('related_facts', []))} facts")

        if self.relationship_chains:
            text_parts.append(f"\n### Relationship Chains")
            for chain in self.relationship_chains:
                text_parts.append(f"- {chain}")

        return "\n".join(text_parts)


@dataclass
class PanoramaResult:
    """
    Breadth Search Result (Panorama)
    Contains all related information, including expired content
    """
    query: str

    all_nodes: List[NodeInfo] = field(default_factory=list)
    all_edges: List[EdgeInfo] = field(default_factory=list)
    active_facts: List[str] = field(default_factory=list)
    historical_facts: List[str] = field(default_factory=list)

    total_nodes: int = 0
    total_edges: int = 0
    active_count: int = 0
    historical_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "all_nodes": [n.to_dict() for n in self.all_nodes],
            "all_edges": [e.to_dict() for e in self.all_edges],
            "active_facts": self.active_facts,
            "historical_facts": self.historical_facts,
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "active_count": self.active_count,
            "historical_count": self.historical_count
        }

    def to_text(self) -> str:
        """Convert to text format (complete version, no truncation)"""
        text_parts = [
            f"## Breadth Search Results (Future Panoramic View)",
            f"Query: {self.query}",
            f"\n### Statistics",
            f"- Total Nodes: {self.total_nodes}",
            f"- Total Edges: {self.total_edges}",
            f"- Current Valid Facts: {self.active_count}",
            f"- Historical/Expired Facts: {self.historical_count}"
        ]

        if self.active_facts:
            text_parts.append(f"\n### Current Valid Facts (Simulation Results Verbatim)")
            for i, fact in enumerate(self.active_facts, 1):
                text_parts.append(f'{i}. "{fact}"')

        if self.historical_facts:
            text_parts.append(f"\n### Historical/Expired Facts (Evolution Record)")
            for i, fact in enumerate(self.historical_facts, 1):
                text_parts.append(f'{i}. "{fact}"')

        if self.all_nodes:
            text_parts.append(f"\n### Involved Entities")
            for node in self.all_nodes:
                entity_type = next((la for la in node.labels if la not in ["Entity", "Node"]), "Entity")
                text_parts.append(f"- **{node.name}** ({entity_type})")

        return "\n".join(text_parts)


class GraphToolsService:
    """
    Graph Retrieval Tools Service (via GraphStorage / Neo4j)

    [Core Retrieval Tools]
    - search_term_entity_to_clause_batch - 条款/实体/术语多路并行召回 + BGE-reranker 重排
    - rerank_search_results - Object-first 检索结果 LLM 精排
    - search_object_first - 根节点优先 DFS 检索

    [图谱查询工具]
    - search_nodes - 按名称模糊搜索节点（支持多类型过滤）
    - search_topic_nodes - Topic hybrid 向量+关键字检索
    - get_node_neighborhood - 单节点 1 跳邻域查询
    """

    def __init__(self, storage: GraphStorage, llm_client: Optional[LLMClient] = None):
        self.storage = storage
        self._llm_client = llm_client
        logger.info("GraphToolsService initialization complete")

    @property
    def llm(self) -> LLMClient:
        """Lazy initialization of LLM client"""
        if self._llm_client is None:
            self._llm_client = LLMClient()
        return self._llm_client

    @staticmethod
    def normalize_text(text: str) -> str:
        """Normalize text for deduplication: lowercase, remove whitespace and punctuation"""
        if not text:
            return ""
        import re
        # Remove whitespace
        text = re.sub(r'\s+', '', text)
        # Remove common punctuation
        text = re.sub(r'[^\w\s]', '', text)
        return text.lower()

    def optimize_query(self, query: str) -> str:
        """Use LLM to extract 3-5 core keywords/phrases from user query"""
        logger.info(f"Optimizing query: {query[:50]}...")

        extract_prompt = f"""你是一个工程规范知识图谱搜索专家。请从用户问题中提取用于知识图谱检索的核心关键词短语。

知识图谱节点类型：术语(Term)、组件(Component)、条款(Clause)、参数(Parameter)。

提取规则（按优先级）：
1. **主语实体**：问题中的核心对象（如"局部等电位联结"、"剩余电流保护电器"）
2. **关联实体**：与主语相关的设备/材料/属性（如"保护联结导体"、"截面积"）
3. **忽略**：通用问句词（"应符合什么规定"、"应如何"、"是什么"、"怎么"）

特殊句式处理：
- "A用B的C" → 提取 A、B、C（如"局部等电位联结 保护联结导体 截面积"）
- "A的B应符合" → 提取 A、B
- "A在B时候" → 提取 A、B
- 逗号分隔的成分要分别提取

输出格式：空格分隔的关键词字符串，最多5个，不要编号，不要解释。

### 用户问题:
{query}

请直接输出关键词："""

        try:
            optimized_keywords = self.llm.chat(messages=[{"role": "user", "content": extract_prompt}], temperature=0.1)
            result = optimized_keywords.strip() if optimized_keywords else query
            logger.info(f"Optimized search query: {result}")
            return result
        except Exception as e:
            logger.error(f"Query optimization failed: {str(e)}")
            return query

    def filter_facts(self, query: str, facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Use LLM to filter only truly relevant facts for the given query"""
        if not facts:
            return []

        if len(facts) == 1:
            return facts

        fact_list_str = ""
        for i, f in enumerate(facts):
            text = f.get('text', '')
            fact_list_str += f"[{i}] {text}\n"

        filter_prompt = f"""你是一个专业的知识过滤专家。请根据【用户问题】，从【候选事实列表】中筛选出与回答该问题直接相关的记录。

### 用户问题:
{query}

### 候选事实列表:
{fact_list_str}

### 任务要求:
1. 仔细阅读每个候选事实，判断其是否能为回答【用户问题】提供核心证据或必要的背景上下文。
2. 特别注意：如果问题涉及多个关键词（如“多孔导管”、“敷设”、“规定”），请保留能体现这些关键词及其关联的事实。
3. 如果候选事实虽然包含关键词但与问题逻辑无关，请将其排除。
4. 返回结果必须是 JSON 格式，包含一个名为 "relevant_indices" 的整数索引列表。

### 输出格式示例:
{{"relevant_indices": [0, 2, 5]}}

请输出筛选后的索引列表 JSON："""

        try:
            # Using a lower temperature for consistent filtering
            response = self.llm.chat_json(messages=[{"role": "user", "content": filter_prompt}], temperature=0.1)
            relevant_indices = response.get("relevant_indices", [])

            filtered_facts = []
            for idx in relevant_indices:
                if isinstance(idx, int) and 0 <= idx < len(facts):
                    filtered_facts.append(facts[idx])

            # Fallback: if LLM filters everything but facts were present, keep top 3 as safety
            if not filtered_facts and facts:
                logger.warning(f"LLM filtered all facts for query: {query[:50]}, using fallback top 5")
                return facts[:5]

            return filtered_facts
        except Exception as e:
            logger.error(f"LLM Fact Filtering failed: {str(e)}")
            return facts # Return all if filtering fails

    def rerank_facts(self, query: str, facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Use SiliconFlow bge-reranker-v2-m3 to rerank facts based on relevance to the query.
        Returns sorted facts with 'relevance_score' (normalized to 0-100 scale).

        Falls back to returning original facts if API call fails.
        """
        if not facts:
            return []

        import requests as _requests

        from ..config import Config

        facts_to_process = facts[:50]  # SiliconFlow reranker limit
        logger.info(f"[Rerank] Using bge-reranker-v2-m3 for {len(facts_to_process)} facts")

        # 准备 documents（使用 text 字段，限制长度避免超限）
        documents = []
        for f in facts_to_process:
            raw_text = f.get('original_text', '').strip() or f.get('text', '').strip()
            documents.append(raw_text[:800])

        try:
            payload = {
                "model": Config.RERANKER_MODEL,
                "query": query,
                "documents": documents,
                "top_n": len(documents),
                "return_documents": False,
            }
            headers = {
                "Authorization": f"Bearer {Config.RERANKER_API_KEY}",
                "Content-Type": "application/json",
            }

            resp = _requests.post(
                Config.RERANKER_BASE_URL,
                json=payload,
                headers=headers,
                timeout=60,
            )
            resp.raise_for_status()
            result = resp.json()

            # 检查 API 错误响应
            if result.get('error') or result.get('code', 200) != 200:
                raise ValueError(f"SiliconFlow API error: {json.dumps(result, ensure_ascii=False)[:500]}")

            logger.info(f"[Rerank] SiliconFlow response: {json.dumps(result, ensure_ascii=False)[:800]}")

            # SiliconFlow 返回: {"results": [{"index": 0, "relevance_score": 0.95}, ...]}
            # 兼容不同字段名：relevance_score / score
            results_list = result.get('results', []) or []
            score_map = {}
            for item in results_list:
                idx = item.get('index')
                raw_score = item.get('relevance_score') if item.get('relevance_score') is not None else item.get('score', 0.0)
                try:
                    score = float(raw_score)
                except (TypeError, ValueError):
                    score = 0.0
                # 兼容 index 为字符串的情况
                if isinstance(idx, int):
                    score_map[idx] = score
                elif isinstance(idx, str) and idx.isdigit():
                    score_map[int(idx)] = score

            logger.info(f"[Rerank] Parsed score_map keys: {list(score_map.keys())}, values: {list(score_map.values())[:5]}")

            scored_facts = []
            for idx, fact in enumerate(facts_to_process):
                f_copy = fact.copy()
                score = score_map.get(idx, 0.0)
                # 转换为百分制（与之前 LLM 打分 0-100 范围一致）
                f_copy['relevance_score'] = round(score * 100, 1)
                f_copy['relevance_reasoning'] = ""  # bge-reranker 不提供 reasoning
                scored_facts.append(f_copy)

            # 按分数降序排列
            scored_facts.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
            logger.info(f"[Rerank] Scored {len(scored_facts)} facts, top_score={scored_facts[0].get('relevance_score') if scored_facts else 'N/A'}")
            return scored_facts
        except Exception as e:
            logger.error(f"[Rerank] bge-reranker API failed: {str(e)}, returning original facts")
            # 给原始 facts 添加默认 relevance_score，避免下游出现 null
            for f in facts:
                if f.get('relevance_score') is None:
                    f['relevance_score'] = 0.0
            return facts

    def _rerank_items(
        self,
        query: str,
        items: List[Tuple[Any, str]],
        top_n: int = 50,
    ) -> List[Tuple[Any, float]]:
        """
        通用 bge-reranker 重排：对任意 (item, text) 列表按与 query 的相关性排序。
        返回 [(item, relevance_score), ...]，按分数降序排列。
        """
        if not items:
            return []

        import requests as _requests
        from ..config import Config

        items_to_process = items[:50]  # SiliconFlow reranker limit
        documents = [text[:800] for _, text in items_to_process]

        try:
            payload = {
                "model": Config.RERANKER_MODEL,
                "query": query,
                "documents": documents,
                "top_n": len(documents),
                "return_documents": False,
            }
            headers = {
                "Authorization": f"Bearer {Config.RERANKER_API_KEY}",
                "Content-Type": "application/json",
            }
            resp = _requests.post(Config.RERANKER_BASE_URL, json=payload, headers=headers, timeout=60)
            resp.raise_for_status()
            result = resp.json()

            if result.get("error") or result.get("code", 200) != 200:
                raise ValueError(f"Reranker API error: {json.dumps(result, ensure_ascii=False)[:500]}")

            results_list = result.get("results", []) or []
            score_map = {}
            for item in results_list:
                idx = item.get("index")
                raw_score = item.get("relevance_score") if item.get("relevance_score") is not None else item.get("score", 0.0)
                try:
                    score = float(raw_score)
                except (TypeError, ValueError):
                    score = 0.0
                if isinstance(idx, int):
                    score_map[idx] = score
                elif isinstance(idx, str) and idx.isdigit():
                    score_map[int(idx)] = score

            scored_items = []
            for idx, (item, _) in enumerate(items_to_process):
                score = score_map.get(idx, 0.0)
                scored_items.append((item, round(score * 100, 1)))

            scored_items.sort(key=lambda x: x[1], reverse=True)
            return scored_items[:top_n]
        except Exception as e:
            logger.error(f"[_rerank_items] bge-reranker failed: {str(e)}")
            # fallback: 保留原始 hybrid score 或默认 0
            return [(item, item.get("score", 0.0) * 100 if isinstance(item, dict) else 0.0) for item, _ in items_to_process]

    def query_intent_match(
        self,
        graph_id: str,
        query: str,
        topic_limit: int = 10,
        entity_limit: int = 10,
    ) -> Dict[str, Any]:
        """
        问题意图摘要匹配：
        1.  提取查询关键词（LLM）
        2.  原查询 + 关键词 多路并行检索 Topic 节点（向量 + BM25）
        3.  合并去重，bge-reranker 对 Topic 重排
        4.  对每个 Top Topic，获取其 MENTIONS 的 Entity / Term
        5.  bge-reranker 对 Entity / Term 重排
        6.  返回 Topic + 关联 Entity 的全量图谱信息与分数
        """
        logger.info(f"[IntentMatch] graph_id={graph_id}, query={query[:60]}, topic_limit={topic_limit}, entity_limit={entity_limit}")

        # Step 0: Extract keywords from query using LLM
        optimized_keywords_text = self.optimize_query(query)
        keywords = [k.strip() for k in optimized_keywords_text.split() if k.strip() and len(k.strip()) > 1]
        # Deduplicate and keep order
        search_queries = list(dict.fromkeys([query] + keywords))
        logger.info(f"[IntentMatch] Extracted keywords: {keywords}, search_queries={search_queries}")

        # Step 1: Parallel Topic search with original query + keywords
        query_vector = self.storage._search.embedding.embed(query)
        all_topic_nodes: List[Dict[str, Any]] = []
        seen_topic_uuids: set = set()

        def _search_topics(q: str) -> List[Dict[str, Any]]:
            return self.storage.search_topic_nodes(
                graph_id=graph_id, query=q, limit=topic_limit * 3,
                query_vector=query_vector, min_score=None,
            )

        search_tasks = search_queries
        search_step_results: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=len(search_tasks)) as executor:
            futures = {executor.submit(_search_topics, q): q for q in search_tasks}
            for future in as_completed(futures):
                q = futures[future]
                nodes = future.result()
                method = "向量 + BM25"
                search_step_results.append({
                    "step": len(search_step_results) + 1,
                    "query": q,
                    "search_method": method,
                    "results_count": len(nodes),
                })
                logger.info(f"[IntentMatch] Topic search '{q[:30]}' -> {len(nodes)} results")
                for t in nodes:
                    uid = t.get("uuid")
                    if uid and uid not in seen_topic_uuids:
                        seen_topic_uuids.add(uid)
                        all_topic_nodes.append(t)

        if not all_topic_nodes:
            logger.info("[IntentMatch] No topic nodes found")
            return {
                "query": query,
                "topics": [],
                "total_topics": 0,
                "retrieval_process": {
                    "keywords": keywords,
                    "search_steps": search_step_results,
                    "merged_candidates": 0,
                    "reranked_topics": 0,
                },
            }

        logger.info(f"[IntentMatch] Found {len(all_topic_nodes)} unique topic candidates")

        # Step 2: Rerank topics with bge-reranker
        topic_items = [(t, f"{t.get('name', '')} {t.get('summary', '')}") for t in all_topic_nodes]
        scored_topics = self._rerank_items(query, topic_items, top_n=topic_limit)

        # Step 3: Fetch associated Entity/Term for each top topic
        topic_uuids = [t.get("uuid") for t, _ in scored_topics if t.get("uuid")]
        topic_entity_map: Dict[str, List[Dict[str, Any]]] = {}
        all_entities: List[Dict[str, Any]] = []

        topic_clause_map: Dict[str, List[Dict[str, Any]]] = {}
        if topic_uuids:
            with self.storage._driver.session() as session:
                # Fetch associated Entity/Term
                result = session.run(
                    """
                    MATCH (t:Topic)-[:MENTIONS]->(e)
                    WHERE t.uuid IN $topic_uuids
                      AND t.graph_id = $gid
                      AND e.graph_id = $gid
                      AND (e:Entity OR e:Term)
                    RETURN t.uuid AS topic_uuid,
                           e.uuid AS entity_uuid,
                           e.name AS entity_name,
                           e.summary AS entity_summary,
                           labels(e) AS entity_labels,
                           e.clause_count AS entity_clause_count
                    """,
                    topic_uuids=topic_uuids,
                    gid=graph_id,
                )
                for record in result:
                    t_uuid = record.get("topic_uuid")
                    entity = {
                        "uuid": record.get("entity_uuid"),
                        "name": record.get("entity_name"),
                        "summary": record.get("entity_summary"),
                        "labels": record.get("entity_labels", []),
                        "clause_count": record.get("entity_clause_count"),
                    }
                    if t_uuid:
                        topic_entity_map.setdefault(t_uuid, []).append(entity)
                        all_entities.append(entity)

                # Fetch associated Clauses (Clause-[:HAS_TOPIC]->Topic)
                clause_result = session.run(
                    """
                    MATCH (c:Clause)-[:HAS_TOPIC]->(t:Topic)
                    WHERE t.uuid IN $topic_uuids
                      AND t.graph_id = $gid
                      AND c.graph_id = $gid
                    RETURN t.uuid AS topic_uuid,
                           c.uuid AS clause_uuid,
                           c.name AS clause_name,
                           c.summary AS clause_summary,
                           c.clause_id AS clause_id,
                           c.pdf_source AS pdf_source,
                           c.pdf_page AS pdf_page,
                           c.pdf_bbox AS pdf_bbox,
                           c.pdf_bboxes AS pdf_bboxes,
                           c.pdf_page_width AS pdf_page_width,
                           c.pdf_page_height AS pdf_page_height
                    ORDER BY c.clause_id ASC
                    """,
                    topic_uuids=topic_uuids,
                    gid=graph_id,
                )
                for record in clause_result:
                    t_uuid = record.get("topic_uuid")
                    # Parse pdf_bboxes JSON string if present
                    pdf_bboxes_raw = record.get("pdf_bboxes")
                    pdf_bboxes = None
                    if pdf_bboxes_raw:
                        try:
                            if isinstance(pdf_bboxes_raw, str):
                                pdf_bboxes = json.loads(pdf_bboxes_raw)
                            elif isinstance(pdf_bboxes_raw, list):
                                pdf_bboxes = pdf_bboxes_raw
                        except Exception:
                            pdf_bboxes = None

                    # Derive page/bbox from pdf_bboxes if available
                    page = record.get("pdf_page")
                    bbox = record.get("pdf_bbox")
                    if pdf_bboxes and len(pdf_bboxes) > 0 and len(pdf_bboxes[0]) >= 5:
                        page = pdf_bboxes[0][0]
                        bbox = pdf_bboxes[0][1:5]

                    clause = {
                        "uuid": record.get("clause_uuid"),
                        "name": record.get("clause_name"),
                        "summary": record.get("clause_summary"),
                        "clause_id": record.get("clause_id"),
                        "source": record.get("pdf_source"),
                        "page": page,
                        "bbox": bbox,
                        "page_width": record.get("pdf_page_width"),
                        "page_height": record.get("pdf_page_height"),
                        "pdf_bboxes": pdf_bboxes,
                    }
                    if t_uuid:
                        topic_clause_map.setdefault(t_uuid, []).append(clause)

        # Deduplicate all_entities by uuid
        seen_entity_uuids = set()
        unique_entities = []
        for e in all_entities:
            uid = e.get("uuid")
            if uid and uid not in seen_entity_uuids:
                seen_entity_uuids.add(uid)
                unique_entities.append(e)

        # Step 4: Rerank all unique entities against the query
        entity_items = [(e, f"{e.get('name', '')} {e.get('summary', '')}") for e in unique_entities]
        scored_entity_map: Dict[str, float] = {}
        if entity_items:
            scored_entities = self._rerank_items(query, entity_items, top_n=entity_limit)
            for entity, score in scored_entities:
                uid = entity.get("uuid")
                if uid:
                    scored_entity_map[uid] = score

        # Build result topics with their associated entities and clauses
        result_topics = []
        for topic, topic_score in scored_topics:
            t_uuid = topic.get("uuid")
            associated = []
            for ent in topic_entity_map.get(t_uuid, []):
                ent_uid = ent.get("uuid")
                associated.append({
                    "uuid": ent_uid,
                    "name": ent.get("name"),
                    "summary": ent.get("summary"),
                    "labels": ent.get("labels", []),
                    "clause_count": ent.get("clause_count"),
                    "relevance_score": scored_entity_map.get(ent_uid, 0.0),
                })
            # Sort associated entities by relevance_score desc
            associated.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
            # Keep only top entity_limit per topic
            associated = associated[:entity_limit]

            # Get associated clauses for this topic
            clauses = topic_clause_map.get(t_uuid, [])

            result_topics.append({
                "uuid": t_uuid,
                "name": topic.get("name", ""),
                "summary": topic.get("summary", ""),
                "labels": topic.get("labels", []),
                "topic": topic.get("topic", ""),
                "hybrid_score": round(topic.get("score", 0.0), 4),
                "relevance_score": topic_score,
                "associated_entities": associated,
                "entity_count": len(associated),
                "associated_clauses": clauses,
                "clause_count": len(clauses),
            })

        total_clauses = sum(len(t.get("associated_clauses", [])) for t in result_topics)
        logger.info(
            f"[IntentMatch] Done: {len(result_topics)} topics, "
            f"{len(scored_entity_map)} unique entities scored, "
            f"{total_clauses} clauses"
        )
        return {
            "query": query,
            "topics": result_topics,
            "total_topics": len(result_topics),
            "retrieval_process": {
                "keywords": keywords,
                "search_steps": search_step_results,
                "merged_candidates": len(all_topic_nodes),
                "reranked_topics": len(scored_topics),
            },
        }

    def run_retrieval_flow(
        self,
        final_rows: List[ObjectFirstRow],
        query: str,
        similarity_threshold: int = 0,
        top_k: int = 10,
        rerank_min_score: int = 0,
    ) -> RerankResult:
        """
        执行检索流程的后两步：bge-reranker 重排 + 分数过滤 + top_k 截取。
        供 SSE 流程（graph.py）和 API 流程（ai_app.py）共用。

        Args:
            final_rows: search_with_dfs_flow 返回的 rows（已做过相似度阈值过滤）
            query: 用户查询
            similarity_threshold: 相似度阈值（用于日志）
            top_k: 保留 top k 个得分最高的 facts（默认 10）
            rerank_min_score: 重排分数阈值（0-100），低于此分数的 facts 会被过滤掉（默认 0 不过滤）

        Returns:
            RerankResult: 包含所有打分 facts 和过滤后的 facts
        """
        logger.info(f"run_retrieval_flow: rows={len(final_rows)}, sim_thresh={similarity_threshold}, top_k={top_k}, rerank_min_score={rerank_min_score}")

        # 收集所有 facts 并附上行索引（按 fact text 去重）
        all_facts = []
        seen_fact_keys = set()
        for row_idx, row in enumerate(final_rows):
            for fact in row.facts:
                # 用 text + source_node_uuid 作为去重 key
                fact_key = (fact.get('text', ''), fact.get('source_node_uuid', ''))
                if fact_key in seen_fact_keys:
                    continue
                seen_fact_keys.add(fact_key)
                f_with_idx = dict(fact)
                f_with_idx['_row_idx'] = row_idx
                all_facts.append(f_with_idx)

        # Stage 2: bge-reranker 重排
        scored_facts = []
        rows_with_scored = []
        if all_facts:
            logger.info(f"[Stage 2] bge-reranker 重排: input=facts:{len(all_facts)}, query={query[:60]}")
            scored_facts = self.rerank_facts(query, all_facts)
            scored_facts_sorted = sorted(scored_facts, key=lambda f: f.get('relevance_score', 0), reverse=True)
            # 日志：重排后每个 fact 的分数
            for i, f in enumerate(scored_facts_sorted[:top_k]):
                score = f.get('relevance_score', 0)
                text_preview = (f.get('text', '') or '')[:80]
                logger.info(f"[Rerank] scored fact[{i}] score={score}: {text_preview}...")
            logger.info(f"[Stage 2] bge-reranker 重排: output=facts:{len(scored_facts_sorted)}")

            # 将打分 facts 挂回 rows（全部打分结果）
            rows_with_scored = []
            for row_idx, original_row in enumerate(final_rows):
                row_facts = [
                    {k: v for k, v in f.items() if k != '_row_idx'}
                    for f in scored_facts_sorted
                    if f.get('_row_idx') == row_idx
                ]
                rows_with_scored.append(ObjectFirstRow(
                    object_node=original_row.object_node,
                    traversal_paths=original_row.traversal_paths,
                    traversal_edges=original_row.traversal_edges,
                    facts=row_facts,
                    relevance_score=original_row.relevance_score,
                ))
        else:
            scored_facts_sorted = []

        # Stage 3: 分数阈值过滤 + top_k 截取
        if rerank_min_score > 0:
            scored_above_threshold = [f for f in scored_facts_sorted if f.get('relevance_score', 0) >= rerank_min_score]
            logger.info(f"[Stage 3] 分数阈值过滤: min_score={rerank_min_score}, before={len(scored_facts_sorted)}, after={len(scored_above_threshold)}")
        else:
            scored_above_threshold = scored_facts_sorted
        filtered_facts = scored_above_threshold[:top_k] if scored_above_threshold else []
        logger.info(f"[Stage 3] top_k 截取: input=facts:{len(scored_above_threshold)}, top_k={top_k}, output=facts:{len(filtered_facts)}")

        # 将 top_k facts 挂回 rows
        rows_with_filtered = []
        for row_idx, original_row in enumerate(final_rows):
            row_facts = [
                {k: v for k, v in f.items() if k != '_row_idx'}
                for f in filtered_facts
                if f.get('_row_idx') == row_idx
            ]
            if row_facts:
                rows_with_filtered.append(ObjectFirstRow(
                    object_node=original_row.object_node,
                    traversal_paths=original_row.traversal_paths,
                    traversal_edges=original_row.traversal_edges,
                    facts=row_facts,
                    relevance_score=original_row.relevance_score,
                ))

        return RerankResult(
            scored_facts=scored_facts_sorted,
            filtered_facts=filtered_facts,
            rows_with_scored_facts=rows_with_scored,
            rows_with_filtered_facts=rows_with_filtered,
        )

    # ========== Basic Tools ==========


    def search_object_first(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        max_depth: int = 3,
        root_type: str = "Entity",
    ) -> ObjectFirstSearchResult:
        """
        Root-node-first DFS 检索。

        检索策略：
        1. 首轮命中 root_type 节点（Entity 或 Term，支持混合向量+关键词搜索）
        2. 从每个根节点出发，深度优先遍历图谱
        3. 每个根节点生成一条结果行（ObjectFirstRow）
        4. 对各行按相关性打分排序

        Args:
            graph_id: 图谱 ID
            query: 检索查询
            limit: 最多返回多少个根节点行
            max_depth: DFS 最大深度（默认 3）
            root_type: 根节点类型，"Entity"（默认）或 "Term"

        Returns:
            ObjectFirstSearchResult，按根节点分组的 DFS 检索结果
        """
        valid_root_types = ["Entity", "Term"]
        if root_type not in valid_root_types:
            root_type = "Entity"
        logger.info(f"Root-node DFS search: graph_id={graph_id}, query={query[:50]}..., max_depth={max_depth}, root_type={root_type}")

        try:
            # Step 1: 搜索根节点（Entity 或 Term）
            if root_type == "Term":
                root_nodes = self.storage.search_term_nodes(
                    graph_id=graph_id,
                    query=query,
                    limit=limit,
                )
            else:
                root_nodes = self.storage.search_object_nodes(
                    graph_id=graph_id,
                    query=query,
                    limit=limit,
                )

            if not root_nodes:
                logger.info(f"No {root_type} nodes found for the query")
                return ObjectFirstSearchResult(
                    query=query,
                    rows=[],
                    total_objects=0,
                    total_facts=0,
                )

            # Step 2: 对每个根节点进行 DFS 遍历
            rows = []
            all_facts_count = 0
            seen_fact_texts: set = set()

            for root_node in root_nodes:
                obj_uuid = root_node.get("uuid", "")
                row = self._dfs_from_object(
                    graph_id=graph_id,
                    object_uuid=obj_uuid,
                    object_data=root_node,
                    max_depth=max_depth,
                    seen_fact_texts=seen_fact_texts,
                )
                rows.append(row)
                all_facts_count += len(row.facts)

            # Step 3: 对行进行 LLM 重排（基于根节点与查询的相关性 + 事实数量）
            scored_rows = self._rerank_object_rows(query, rows)

            # Step 4: 取 top limit 行
            final_rows = scored_rows[:limit]

            # Step 5: 批量获取 top rows 的根节点 PDF 定位信息
            if final_rows:
                root_uuids = [row.object_node.get("uuid") for row in final_rows]
                root_uuids = [uid for uid in root_uuids if uid]
                if root_uuids:
                    batch_pdf_info = self._batch_get_node_pdf_info(root_uuids)
                    for row in final_rows:
                        root_uuid = row.object_node.get("uuid")
                        if root_uuid and root_uuid in batch_pdf_info:
                            row.object_node["pdf_info"] = batch_pdf_info[root_uuid]

            # Step 6: 如果根节点没有 PDF 定位信息，但 facts 中有 Clause 的 pdf_bboxes，
            # 使用第一个 Clause 的 pdf_bboxes 作为根节点的 PDF 定位（用于文档定位）
            for row in final_rows:
                pdf_info = row.object_node.get("pdf_info", {})
                if not pdf_info.get("source") or pdf_info.get("source") == "Graph":
                    # 查找第一个有 pdf_bboxes 的 fact（通常是 Clause 节点）
                    for fact in row.facts:
                        if fact.get("pdf_bboxes") and isinstance(fact["pdf_bboxes"], list) and len(fact["pdf_bboxes"]) > 0:
                            first_bbox = fact["pdf_bboxes"][0]
                            if len(first_bbox) >= 5:
                                pdf_info["source"] = fact.get("source")
                                pdf_info["page"] = first_bbox[0]
                                pdf_info["bbox"] = [first_bbox[1], first_bbox[2], first_bbox[3], first_bbox[4]]
                                pdf_info["page_width"] = fact.get("page_width")
                                pdf_info["page_height"] = fact.get("page_height")
                                pdf_info["pdf_bboxes"] = fact["pdf_bboxes"]
                                row.object_node["pdf_info"] = pdf_info
                                break
                    # 如果 still 没有有效 source，清空 pdf_info 避免前端显示无效按钮
                    if not row.object_node["pdf_info"].get("source") or row.object_node["pdf_info"].get("source") == "Graph":
                        row.object_node["pdf_info"] = {}

            logger.info(
                f"Root-node DFS search complete: {len(final_rows)} {root_type} rows, "
                f"{all_facts_count} total facts"
            )

            return ObjectFirstSearchResult(
                query=query,
                rows=final_rows,
                total_objects=len(final_rows),
                total_facts=all_facts_count,
            )

        except Exception as e:
            logger.error(f"Root-node DFS search failed: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return ObjectFirstSearchResult(
                query=query,
                rows=[],
                total_objects=0,
                total_facts=0,
            )

    def search_term_entity_to_clause_batch(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        root_types: List[str] = None,
        similarity_threshold: float = 0,
    ) -> ObjectFirstSearchResult:
        """
        批量路径检索：一次请求同时搜索多种根节点类型（Entity/Term）。

        检索路径：
        - Entity: Entity ←MENTIONS-- Topic ←HAS_TOPIC-- Clause
        - Term:   Term ←MENTIONS-- Topic ←HAS_TOPIC-- Clause

        优化：
        1. 单次请求共享 embedding 计算
        2. 路径查询与 Clause 节点数据合并为单次 Cypher
        3. PDF 信息直接从 Clause 节点属性提取，不做 Episode 回退
        """
        if root_types is None:
            root_types = ["Entity", "Term"]
        valid_types = [t for t in root_types if t in ("Entity", "Term")]
        if not valid_types:
            valid_types = ["Entity"]

        logger.info(f"Batch search: graph_id={graph_id}, query={query[:50]}..., types={valid_types}, similarity_threshold={similarity_threshold}")

        # 将前端 50-100 阈值转为后端 0-1 的 min_score，提前过滤低分根节点
        min_score = similarity_threshold / 100.0 if similarity_threshold > 0 else None

        try:
            # Step 0: 预计算 embedding，供所有 root_type 搜索共享，避免重复 HTTP 请求
            query_vector = self.storage._search.embedding.embed(query)

            # --- Step 0b: clause_id 精确匹配 ---
            # 检测查询中的条款编号格式（如 3.2.1, 5.1）
            clause_id_pattern = re.compile(r'\b(\d+(?:\.\d+)+)\b')
            clause_id_matches = clause_id_pattern.findall(query)
            exact_clause_nodes: List[Dict[str, Any]] = []
            if clause_id_matches:
                exact_clause_nodes = self.storage.search_clauses_by_id(graph_id, clause_id_matches)
                if exact_clause_nodes:
                    logger.info(f"Exact clause_id match: {len(exact_clause_nodes)} clauses from ids={clause_id_matches}")

            # Step 1: 搜索根节点（Entity/Term/Clause 并行）
            all_root_nodes: List[Dict[str, Any]] = []
            clause_root_nodes: List[Dict[str, Any]] = []

            def _search_type(root_type: str) -> List[Dict[str, Any]]:
                if root_type == "Term":
                    nodes = self.storage.search_term_nodes(
                        graph_id=graph_id, query=query, limit=limit,
                        query_vector=query_vector, min_score=min_score,
                    )
                else:
                    nodes = self.storage.search_object_nodes(
                        graph_id=graph_id, query=query, limit=limit,
                        query_vector=query_vector, min_score=min_score,
                    )
                for n in nodes:
                    n["_root_type"] = root_type
                return nodes

            def _search_clause() -> List[Dict[str, Any]]:
                nodes = self.storage.search_clause_nodes(
                    graph_id=graph_id, query=query, limit=limit,
                    query_vector=query_vector, min_score=min_score,
                )
                for n in nodes:
                    n["_root_type"] = "Clause"
                return nodes

            search_tasks = {rt: (_search_type, rt) for rt in valid_types}
            search_tasks["Clause"] = (_search_clause,)

            with ThreadPoolExecutor(max_workers=len(search_tasks)) as executor:
                futures = {}
                for key, task in search_tasks.items():
                    if key == "Clause":
                        futures[executor.submit(task[0])] = key
                    else:
                        futures[executor.submit(task[0], task[1])] = key
                for future in as_completed(futures):
                    task_key = futures[future]
                    result_nodes = future.result()
                    if task_key == "Clause":
                        clause_root_nodes = result_nodes
                    else:
                        all_root_nodes.extend(result_nodes)

            # Step 1b: fallback 到 Topic 搜索（当 Entity/Term 均未命中时）
            if not all_root_nodes and not clause_root_nodes and not exact_clause_nodes:
                topic_nodes = self.storage.search_topic_nodes(
                    graph_id=graph_id, query=query, limit=limit,
                    query_vector=query_vector, min_score=min_score,
                )
                if topic_nodes:
                    topic_uuids = [t.get("uuid") for t in topic_nodes if t.get("uuid")]
                    # 获取这些 Topic 关联的 Entity 节点作为替代根节点
                    entity_from_topics = self.storage.get_entities_by_topic_uuids(
                        topic_uuids, graph_id
                    )
                    for ent in entity_from_topics:
                        ent["_root_type"] = "Entity"
                        ent["_from_topic"] = True
                        # 赋予一个默认分数，确保能通过前端阈值过滤
                        ent["score"] = ent.get("score", 0.75)
                    all_root_nodes.extend(entity_from_topics)
                    logger.info(f"Topic fallback: found {len(topic_nodes)} topics, mapped to {len(entity_from_topics)} entities")

            if not all_root_nodes and not clause_root_nodes and not exact_clause_nodes:
                return ObjectFirstSearchResult(query=query, rows=[], total_objects=0, total_facts=0)

            # Step 2: Entity/Term 路径查询（Clause 根节点不走此路径）
            entity_term_uuids = [n.get("uuid") for n in all_root_nodes if n.get("_root_type") in ("Entity", "Term") and n.get("uuid")]
            paths_map: Dict[str, List[Dict[str, Any]]] = {}
            clause_nodes_map: Dict[str, Dict[str, Any]] = {}
            if entity_term_uuids:
                paths_map, clause_nodes_map = self.storage.get_entity_topic_clause_paths(
                    entity_term_uuids, graph_id, include_clause_data=True,
                )
                logger.info(f"Batch search found {len(entity_term_uuids)} entity/term root nodes, paths_map keys={len(paths_map)}")

            # Step 2b: Clause 根节点直接收集 Clause 数据
            for cr in clause_root_nodes:
                c_uuid = cr.get("uuid")
                if c_uuid and c_uuid not in clause_nodes_map:
                    clause_nodes_map[c_uuid] = cr

            # Step 2c: 精确匹配的 Clause 节点也加入
            for ec in exact_clause_nodes:
                c_uuid = ec.get("uuid")
                if c_uuid and c_uuid not in clause_nodes_map:
                    clause_nodes_map[c_uuid] = ec

            # Step 3: 构建 ObjectFirstRow
            term_rows = []
            entity_rows = []
            clause_rows = []
            seen_fact_texts: set = set()

            # 处理 Entity/Term 根节点
            for root_node in all_root_nodes:
                root_uuid = root_node.get("uuid", "")
                root_type = root_node.get("_root_type", "Entity")
                if not root_uuid:
                    continue

                root_score = root_node.get("score", 0.0)
                relevance_score = root_score * 100

                paths = paths_map.get(root_uuid, [])
                if not paths:
                    logger.warning(f"[search_batch] root_node {root_node.get('name')} ({root_uuid}) has no Topic->Clause path, returning empty facts")

                traversal_nodes: List[ObjectPathNode] = []
                traversal_edges: List[ObjectPathEdge] = []
                facts: List[Dict[str, Any]] = []

                traversal_nodes.append(ObjectPathNode(
                    uuid=root_uuid,
                    name=root_node.get("name", ""),
                    labels=root_node.get("labels", []),
                    summary=root_node.get("summary", ""),
                    depth=0,
                ))

                # 按 topic 分组，收集所有 Topic → Clause 路径
                topic_groups: Dict[str, Tuple[str, List[str]]] = {}  # topic_uuid -> (topic_name, [clause_uuids])
                for path in paths:
                    topic_uuid = path.get("topic_uuid")
                    topic_name = path.get("topic_name") or "Topic"
                    clause_uuid = path.get("clause_uuid")
                    if clause_uuid and topic_uuid:
                        if topic_uuid not in topic_groups:
                            topic_groups[topic_uuid] = (topic_name, [])
                        topic_groups[topic_uuid][1].append(clause_uuid)

                all_topic_names = [name for name, _ in topic_groups.values()]

                # 遍历路径固定 2 跳：root → Topic → Clause
                first_topic_added = False
                for group_key, (topic_name, clause_list) in topic_groups.items():
                    topic_uuid = group_key
                    # 只在 traversal_nodes 中记录第一个 Topic（路径展示用）
                    if not first_topic_added:
                        traversal_nodes.append(ObjectPathNode(
                            uuid=topic_uuid, name=topic_name, labels=["Topic"], summary="", depth=1,
                        ))
                        traversal_edges.append(ObjectPathEdge(
                            uuid=f"{topic_uuid}-{root_uuid}", name="MENTIONS",
                            fact="提及实体", source_node_uuid=topic_uuid,
                            target_node_uuid=root_uuid, depth=0,
                        ))
                        first_topic_added = True

                    for clause_uuid in clause_list:
                        clause_data = clause_nodes_map.get(clause_uuid, {})
                        clause_name = clause_data.get("name", "") or f"Clause-{clause_uuid[:8]}"
                        clause_labels = clause_data.get("labels", [])
                        clause_summary = clause_data.get("summary", "") or clause_data.get("data", "")

                        # 只记录第一个 Clause 到 traversal_nodes（路径展示用）
                        if len(traversal_nodes) == 2:
                            traversal_nodes.append(ObjectPathNode(
                                uuid=clause_uuid, name=clause_name, labels=clause_labels,
                                summary=clause_summary, depth=2,
                            ))
                            traversal_edges.append(ObjectPathEdge(
                                uuid=f"{clause_uuid}-{topic_uuid}", name="HAS_TOPIC",
                                fact="条款关联主题", source_node_uuid=clause_uuid,
                                target_node_uuid=topic_uuid, depth=1,
                            ))

                        # PDF 信息直接从 Clause 节点属性提取（无额外 DB 查询）
                        pdf_info = self._extract_clause_pdf_info(clause_data)

                        fact_text = f"条款: {clause_name}\n{clause_summary}" if clause_summary else f"条款: {clause_name}"
                        norm = self.normalize_text(fact_text)
                        if norm and norm not in seen_fact_texts:
                            seen_fact_texts.add(norm)
                            fact_entry: Dict[str, Any] = {
                                "uuid": clause_uuid,
                                "clause_id": clause_data.get("clause_id", ""),
                                "text": fact_text,
                                "original_text": clause_summary,
                                "source": pdf_info.get("source") or "Graph",
                                "page": pdf_info.get("page"),
                                "bbox": pdf_info.get("bbox"),
                                "page_width": pdf_info.get("page_width"),
                                "page_height": pdf_info.get("page_height"),
                                "graph_id": graph_id,
                                "source_node_uuid": clause_uuid,
                                "target_node_uuid": root_uuid,
                                "relation_name": "HAS_TOPIC",
                                "traversal_depth": 2,
                                "similarity_score": 0.0,
                                "topic": topic_name,
                            }
                            if pdf_info.get("pdf_bboxes"):
                                fact_entry["pdf_bboxes"] = pdf_info["pdf_bboxes"]
                            facts.append(fact_entry)

                if not facts:
                    continue

                obj_detail: Dict[str, Any] = {
                    "uuid": root_uuid,
                    "name": root_node.get("name", ""),
                    "labels": root_node.get("labels", []),
                    "summary": root_node.get("summary", ""),
                    "topics": all_topic_names,
                    "pdf_info": {},
                    "graph_id": graph_id,
                }

                row = ObjectFirstRow(
                    object_node=obj_detail,
                    traversal_paths=traversal_nodes,
                    traversal_edges=traversal_edges,
                    facts=facts,
                    relevance_score=relevance_score,
                )

                # 根节点 PDF 定位（从第一个 fact 继承）
                first_fact = facts[0]
                if first_fact.get("pdf_bboxes"):
                    fb = first_fact["pdf_bboxes"]
                    first_bbox = fb[0]
                    if len(first_bbox) >= 5:
                        obj_detail["pdf_info"] = {
                            "source": first_fact.get("source"),
                            "page": first_bbox[0],
                            "bbox": [first_bbox[1], first_bbox[2], first_bbox[3], first_bbox[4]],
                            "page_width": first_fact.get("page_width"),
                            "page_height": first_fact.get("page_height"),
                            "pdf_bboxes": fb,
                        }

                if root_type == "Term":
                    term_rows.append(row)
                else:
                    entity_rows.append(row)

            # 处理 Clause 根节点（直接搜索命中）
            for cr in clause_root_nodes:
                c_uuid = cr.get("uuid", "")
                c_score = cr.get("score", 0.0)
                if not c_uuid:
                    continue
                clause_data = clause_nodes_map.get(c_uuid, {})
                if not clause_data:
                    continue
                clause_name = clause_data.get("name", "") or f"Clause-{c_uuid[:8]}"
                clause_summary = clause_data.get("summary", "") or clause_data.get("data", "")
                pdf_info = self._extract_clause_pdf_info(clause_data)
                fact_text = f"条款: {clause_name}\n{clause_summary}" if clause_summary else f"条款: {clause_name}"
                norm = self.normalize_text(fact_text)
                facts = []
                if norm and norm not in seen_fact_texts:
                    seen_fact_texts.add(norm)
                    facts.append({
                        "uuid": c_uuid,
                        "clause_id": clause_data.get("clause_id", ""),
                        "text": fact_text,
                        "original_text": clause_summary,
                        "source": pdf_info.get("source") or "Graph",
                        "page": pdf_info.get("page"),
                        "bbox": pdf_info.get("bbox"),
                        "page_width": pdf_info.get("page_width"),
                        "page_height": pdf_info.get("page_height"),
                        "graph_id": graph_id,
                        "source_node_uuid": c_uuid,
                        "target_node_uuid": c_uuid,
                        "relation_name": "DIRECT",
                        "traversal_depth": 0,
                        "similarity_score": c_score,
                        **({"pdf_bboxes": pdf_info["pdf_bboxes"]} if pdf_info.get("pdf_bboxes") else {}),
                    })
                if facts:
                    clause_rows.append(ObjectFirstRow(
                        object_node={
                            "uuid": c_uuid,
                            "name": clause_name,
                            "labels": ["Clause"],
                            "summary": clause_summary,
                            "pdf_info": {},
                            "graph_id": graph_id,
                        },
                        traversal_paths=[ObjectPathNode(
                            uuid=c_uuid, name=clause_name, labels=["Clause"],
                            summary=clause_summary, depth=0,
                        )],
                        traversal_edges=[],
                        facts=facts,
                        relevance_score=c_score * 100,
                    ))

            # 处理精确匹配的 Clause 节点（clause_id 命中）
            for ec in exact_clause_nodes:
                c_uuid = ec.get("uuid", "")
                if not c_uuid:
                    continue
                clause_name = ec.get("name", "") or f"Clause-{c_uuid[:8]}"
                clause_summary = ec.get("summary", "") or ec.get("data", "")
                pdf_info = self._extract_clause_pdf_info(ec)
                fact_text = f"条款: {clause_name}\n{clause_summary}" if clause_summary else f"条款: {clause_name}"
                norm = self.normalize_text(fact_text)
                facts = []
                if norm and norm not in seen_fact_texts:
                    seen_fact_texts.add(norm)
                    facts.append({
                        "uuid": c_uuid,
                        "clause_id": ec.get("clause_id", ""),
                        "text": fact_text,
                        "original_text": clause_summary,
                        "source": pdf_info.get("source") or "Graph",
                        "page": pdf_info.get("page"),
                        "bbox": pdf_info.get("bbox"),
                        "page_width": pdf_info.get("page_width"),
                        "page_height": pdf_info.get("page_height"),
                        "graph_id": graph_id,
                        "source_node_uuid": c_uuid,
                        "target_node_uuid": c_uuid,
                        "relation_name": "EXACT_MATCH",
                        "traversal_depth": 0,
                        "similarity_score": 1.0,
                        **({"pdf_bboxes": pdf_info["pdf_bboxes"]} if pdf_info.get("pdf_bboxes") else {}),
                    })
                if facts:
                    clause_rows.append(ObjectFirstRow(
                        object_node={
                            "uuid": c_uuid,
                            "name": clause_name,
                            "labels": ["Clause"],
                            "summary": clause_summary,
                            "pdf_info": {},
                            "graph_id": graph_id,
                        },
                        traversal_paths=[ObjectPathNode(
                            uuid=c_uuid, name=clause_name, labels=["Clause"],
                            summary=clause_summary, depth=0,
                        )],
                        traversal_edges=[],
                        facts=facts,
                        relevance_score=100.0,
                    ))

            # Term 优先，按 facts 数量排序
            term_rows.sort(key=lambda r: len(r.facts), reverse=True)
            entity_rows.sort(key=lambda r: len(r.facts), reverse=True)
            clause_rows.sort(key=lambda r: len(r.facts), reverse=True)
            all_rows = term_rows[:limit] + entity_rows[:limit] + clause_rows[:limit]

            # 提升 name 精确匹配的节点到首位（无论 Entity/Term/Clause）
            query_lower = query.strip().lower()
            for i, row in enumerate(all_rows):
                name = (row.object_node.get("name") or "").strip().lower()
                if name == query_lower:
                    matched = all_rows.pop(i)
                    all_rows.insert(0, matched)
                    break

            logger.info(
                f"Batch search complete: {len(all_rows)} rows "
                f"(Term={len(term_rows[:limit])}, Entity={len(entity_rows[:limit])}, "
                f"Clause={len(clause_rows[:limit])}, Exact={len(exact_clause_nodes)}), "
                f"total facts={sum(len(r.facts) for r in all_rows)}"
            )

            return ObjectFirstSearchResult(
                query=query,
                rows=all_rows,
                total_objects=len(all_rows),
                total_facts=sum(len(r.facts) for r in all_rows),
            )

        except Exception as e:
            logger.error(f"Batch search failed: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return ObjectFirstSearchResult(query=query, rows=[], total_objects=0, total_facts=0)

    @staticmethod
    def _extract_clause_pdf_info(clause_data: Dict[str, Any]) -> Dict[str, Any]:
        """从 Clause 节点属性直接提取 PDF 信息（无 DB 查询）。"""
        # pdf_bboxes 可能是 JSON 字符串或列表
        pdf_bboxes_raw = clause_data.get("pdf_bboxes")
        pdf_bboxes = None
        if pdf_bboxes_raw:
            if isinstance(pdf_bboxes_raw, str):
                try:
                    pdf_bboxes = json.loads(pdf_bboxes_raw)
                except (json.JSONDecodeError, TypeError):
                    pdf_bboxes = None
            elif isinstance(pdf_bboxes_raw, list):
                pdf_bboxes = pdf_bboxes_raw

        page = clause_data.get("pdf_page") or clause_data.get("page")
        bbox = clause_data.get("pdf_bbox") or clause_data.get("bbox")
        source = clause_data.get("pdf_source") or clause_data.get("source")

        # 从 pdf_bboxes 提取 page/bbox（更精确）
        if pdf_bboxes and len(pdf_bboxes) > 0 and len(pdf_bboxes[0]) >= 5:
            page = pdf_bboxes[0][0]
            bbox = pdf_bboxes[0][1:5]

        return {
            "source": source,
            "page": page,
            "bbox": bbox,
            "page_width": clause_data.get("pdf_page_width") or clause_data.get("page_width"),
            "page_height": clause_data.get("pdf_page_height") or clause_data.get("page_height"),
            "pdf_bboxes": pdf_bboxes,
        }

    def search_term_entity_to_clause(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        root_type: str = "Entity",
    ) -> ObjectFirstSearchResult:
        """
        专用路径检索：Entity/Term → Topic → Clause。

        检索策略（固定 2 跳路径）：
        1. 混合检索命中 Entity 或 Term 节点
        2. 直接查询 Entity → Topic → Clause 路径
        3. 收集 Clause 的 pdf_bboxes 用于文档定位

        Args:
            graph_id: 图谱 ID
            query: 检索查询
            limit: 最多返回多少个根节点行
            root_type: 根节点类型，"Entity"（默认）或 "Term"

        Returns:
            ObjectFirstSearchResult
        """
        valid_root_types = ["Entity", "Term"]
        if root_type not in valid_root_types:
            root_type = "Entity"
        logger.info(f"Term/Entity→Clause path search: graph_id={graph_id}, query={query[:50]}..., root_type={root_type}")

        try:
            # Step 1: 搜索根节点（Entity 或 Term）
            if root_type == "Term":
                root_nodes = self.storage.search_term_nodes(
                    graph_id=graph_id,
                    query=query,
                    limit=limit,
                )
            else:
                root_nodes = self.storage.search_object_nodes(
                    graph_id=graph_id,
                    query=query,
                    limit=limit,
                )

            if not root_nodes:
                logger.info(f"No {root_type} nodes found for the query")
                return ObjectFirstSearchResult(
                    query=query,
                    rows=[],
                    total_objects=0,
                    total_facts=0,
                )

            # DEBUG: 打印根节点搜索结果
            logger.info(f"[DEBUG] search_term_entity_to_clause root_nodes: {[{'_score': n.get('_score'), 'name': n.get('name'), 'uuid': n.get('uuid')} for n in root_nodes]}")

            # Step 2: 收集所有根节点 UUID
            root_uuids = [n.get("uuid") for n in root_nodes if n.get("uuid")]

            # Step 3: 直接查询 Entity → Topic → Clause 路径（添加 graph_id 过滤避免跨图谱查询）
            paths_map = self.storage.get_entity_topic_clause_paths(root_uuids, graph_id)

            # Step 4: 收集所有 Clause UUID 并批量获取
            all_clause_uuids: set = set()
            for entity_uuid, paths in paths_map.items():
                for path in paths:
                    if path.get("clause_uuid"):
                        all_clause_uuids.add(path["clause_uuid"])

            clause_uuids_list = list(all_clause_uuids)
            clause_nodes_map: Dict[str, Dict[str, Any]] = {}
            clause_pdf_info: Dict[str, Dict[str, Any]] = {}
            if clause_uuids_list:
                clause_nodes_map = self.storage.get_nodes_batch(clause_uuids_list)
                clause_pdf_info = self._batch_get_node_pdf_info(clause_uuids_list)

            # Step 5: 构建 ObjectFirstRow
            rows = []
            seen_fact_texts: set = set()

            for root_node in root_nodes:
                root_uuid = root_node.get("uuid", "")
                if not root_uuid:
                    continue

                # 从根节点获取搜索分数（RRF混合搜索的score，已归一化）
                root_score = root_node.get("score", 0.0)
                # 转换为百分制以便与前端阈值（50-100）兼容
                relevance_score = root_score * 100
                logger.info(f"[DEBUG] root_node={root_node.get('name')}, score={root_score}, relevance_score={relevance_score}")

                paths = paths_map.get(root_uuid, [])
                traversal_nodes: List[ObjectPathNode] = []
                traversal_edges: List[ObjectPathEdge] = []
                facts: List[Dict[str, Any]] = []

                # 添加根节点
                traversal_nodes.append(ObjectPathNode(
                    uuid=root_uuid,
                    name=root_node.get("name", ""),
                    labels=root_node.get("labels", []),
                    summary=root_node.get("summary", ""),
                    depth=0,
                ))

                # 按 topic 分组
                topic_to_clauses_map: Dict[str, List[str]] = {}
                for path in paths:
                    topic_uuid = path.get("topic_uuid", "")
                    clause_uuid = path.get("clause_uuid", "")
                    if topic_uuid and clause_uuid:
                        if topic_uuid not in topic_to_clauses_map:
                            topic_to_clauses_map[topic_uuid] = []
                        topic_to_clauses_map[topic_uuid].append(clause_uuid)

                # 跳过没有 Entity→Topic→Clause 路径的根节点
                if not topic_to_clauses_map:
                    continue

                # 遍历 Topic → Clause 路径
                for topic_uuid, clause_list in topic_to_clauses_map.items():
                    # 添加 Topic 节点
                    traversal_nodes.append(ObjectPathNode(
                        uuid=topic_uuid,
                        name="Topic",
                        labels=["Topic"],
                        summary="",
                        depth=1,
                    ))

                    for clause_uuid in clause_list:
                        clause_data = clause_nodes_map.get(clause_uuid, {})
                        clause_name = clause_data.get("name", "") or f"Clause-{clause_uuid[:8]}"
                        clause_labels = clause_data.get("labels", [])

                        # 添加 Clause 节点
                        traversal_nodes.append(ObjectPathNode(
                            uuid=clause_uuid,
                            name=clause_name,
                            labels=clause_labels,
                            summary=clause_data.get("summary", ""),
                            depth=2,
                        ))

                        # 添加 HAS_TOPIC 边 (Clause → Topic)
                        traversal_edges.append(ObjectPathEdge(
                            uuid=f"{clause_uuid}-{topic_uuid}",
                            name="HAS_TOPIC",
                            fact="条款关联主题",
                            source_node_uuid=clause_uuid,
                            target_node_uuid=topic_uuid,
                            depth=1,
                        ))

                        # 添加 MENTIONS 边 (Topic → Entity)
                        traversal_edges.append(ObjectPathEdge(
                            uuid=f"{topic_uuid}-{root_uuid}",
                            name="MENTIONS",
                            fact="提及实体",
                            source_node_uuid=topic_uuid,
                            target_node_uuid=root_uuid,
                            depth=0,
                        ))

                        # 构建 fact（使用 Clause 的 pdf_bboxes）
                        pdf_info = clause_pdf_info.get(clause_uuid, {})
                        pdf_bboxes = pdf_info.get("pdf_bboxes")
                        page = pdf_info.get("page")
                        bbox = pdf_info.get("bbox")

                        clause_summary = clause_data.get("summary", "") or clause_data.get("data", "")
                        # text 包含完整条款内容，用于 rerank 和 LLM 推理
                        fact_text = f"条款: {clause_name}\n{clause_summary}" if clause_summary else f"条款: {clause_name}"
                        norm = self.normalize_text(fact_text)
                        if norm and norm not in seen_fact_texts:
                            seen_fact_texts.add(norm)
                            fact_entry: Dict[str, Any] = {
                                "uuid": clause_uuid,
                                "text": fact_text,
                                "original_text": clause_summary,  # summary 或 data 的完整内容
                                "source": pdf_info.get("source") or "Graph",
                                "page": page,
                                "bbox": bbox,
                                "page_width": pdf_info.get("page_width"),
                                "page_height": pdf_info.get("page_height"),
                                "graph_id": graph_id,
                                "source_node_uuid": clause_uuid,
                                "target_node_uuid": root_uuid,
                                "relation_name": "HAS_TOPIC",
                                "traversal_depth": 2,
                                "similarity_score": 0.0,
                            }
                            if pdf_bboxes:
                                fact_entry["pdf_bboxes"] = pdf_bboxes
                            facts.append(fact_entry)

                # 构建 object_node（根节点 Entity/Term）
                obj_detail: Dict[str, Any] = {
                    "uuid": root_uuid,
                    "name": root_node.get("name", ""),
                    "labels": root_node.get("labels", []),
                    "summary": root_node.get("summary", ""),
                    "pdf_info": {},
                    "graph_id": graph_id,
                }

                row = ObjectFirstRow(
                    object_node=obj_detail,
                    traversal_paths=traversal_nodes,
                    traversal_edges=traversal_edges,
                    facts=facts,
                    relevance_score=relevance_score,
                )

                # 如果根节点没有 PDF 定位，使用第一个 Clause 的 pdf_bboxes
                if facts:
                    first_fact = facts[0]
                    if first_fact.get("pdf_bboxes"):
                        fb = first_fact["pdf_bboxes"]
                        first_bbox = fb[0]
                        if len(first_bbox) >= 5:
                            obj_detail["pdf_info"] = {
                                "source": first_fact.get("source"),
                                "page": first_bbox[0],
                                "bbox": [first_bbox[1], first_bbox[2], first_bbox[3], first_bbox[4]],
                                "page_width": first_fact.get("page_width"),
                                "page_height": first_fact.get("page_height"),
                                "pdf_bboxes": fb,
                            }

                rows.append(row)

            # Step 6: 按 facts 数量排序
            rows.sort(key=lambda r: len(r.facts), reverse=True)
            final_rows = rows[:limit]

            logger.info(
                f"Term/Entity→Clause path search complete: {len(final_rows)} rows, "
                f"total facts={sum(len(r.facts) for r in final_rows)}"
            )

            return ObjectFirstSearchResult(
                query=query,
                rows=final_rows,
                total_objects=len(final_rows),
                total_facts=sum(len(r.facts) for r in final_rows),
            )

        except Exception as e:
            logger.error(f"Term/Entity→Clause path search failed: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return ObjectFirstSearchResult(
                query=query,
                rows=[],
                total_objects=0,
                total_facts=0,
            )

    def _dfs_from_object(
        self,
        graph_id: str,
        object_uuid: str,
        object_data: Dict[str, Any],
        max_depth: int,
        seen_fact_texts: set,
        root_score: float = 0.0,
        edge_type_filter: Optional[List[str]] = None,
    ) -> ObjectFirstRow:
        """
        从一个 Object 节点执行优化的迭代 DFS 遍历，收集所有关联节点和边。

        遍历规则（实际图谱结构）：
        - Entity/Term ←MENTIONS-- Topic ←HAS_TOPIC-- Clause

        优化策略：
        - Phase 1：栈式迭代遍历，每节点调用一次 get_node_edges / get_node_outgoing_edges，
          收集所有邻居 UUID 到 pending_neighbors，零递归。
        - Phase 2：批量调用 get_nodes_batch 一次性获取所有邻居节点数据，
          用 neighbor_map 填充 facts 中的邻居名称（消除逐节点 get_node 调用）。

        Args:
            graph_id: 图谱 ID
            object_uuid: Object 节点 UUID
            object_data: Object 节点数据
            max_depth: 最大深度
            seen_fact_texts: 全局已见事实文本（去重用）
            root_score: 根节点 hybrid search 相似度分数 (0-1)，注入到 facts 中供预过滤用

        Returns:
            ObjectFirstRow
        """
        traversal_nodes: List[ObjectPathNode] = []
        traversal_edges: List[ObjectPathEdge] = []
        facts: List[Dict[str, Any]] = []
        visited: set = set()
        # Track nodes added to traversal_nodes to prevent duplicates at different depths
        # (同一个节点可能被多次enqueued但只应出现在一个深度层级)
        nodes_in_traversal: set = set()
        # Track edges added to traversal_edges to prevent duplicate edges
        edges_in_traversal: set = set()

        # Phase 1: Stack-based iterative DFS — one edge query per node
        # Stack items: (node_uuid, node_data, depth)
        stack: List[tuple] = [(object_uuid, object_data, 0)]
        # Collect pending neighbor info for Phase 2
        # neighbor_info: List[(neighbor_uuid, depth, edge_dict)]
        pending_neighbor_info: List[tuple] = []

        while stack:
            node_uuid, node_data, depth = stack.pop()

            if node_uuid in visited or depth > max_depth:
                continue
            visited.add(node_uuid)

            # Add node to DFS path (skip if already added at a shallower depth)
            if node_data and node_uuid not in nodes_in_traversal:
                nodes_in_traversal.add(node_uuid)
                traversal_nodes.append(ObjectPathNode(
                    uuid=node_uuid,
                    name=node_data.get("name", ""),
                    labels=node_data.get("labels", []),
                    summary=node_data.get("summary", ""),
                    depth=depth,
                ))

            # Determine edge query mode
            is_root = (depth == 0)
            try:
                if is_root:
                    edges = self.storage.get_node_edges(node_uuid)
                else:
                    edges = self.storage.get_node_outgoing_edges(node_uuid)
            except Exception as e:
                logger.debug(f"Failed to get edges for node {node_uuid[:8]}: {e}")
                edges = []

            for edge in edges:
                edge_uuid = edge.get("uuid", "")
                edge_fact = edge.get("fact", "")
                src_uuid = edge.get("source_node_uuid", "")
                tgt_uuid = edge.get("target_node_uuid", "")
                edge_name = edge.get("name", "")

                neighbor_uuid = tgt_uuid

                # Intent-guided edge filtering:
                # - If edge_type_filter is set, prefer matching edges by expanding them
                # - Non-matching edges are still added to traversal_edges as context
                #   but their neighbors are NOT pushed to the DFS stack (no further expansion)
                is_matching_edge = (
                    not edge_type_filter
                    or edge_name in edge_type_filter
                )

                # Add edge to traversal path (always, for context, skip duplicates)
                if edge_uuid and edge_uuid not in edges_in_traversal:
                    edges_in_traversal.add(edge_uuid)
                    traversal_edges.append(ObjectPathEdge(
                        uuid=edge_uuid,
                        name=edge_name,
                        fact=edge_fact,
                        source_node_uuid=src_uuid,
                        target_node_uuid=tgt_uuid,
                        depth=depth,
                    ))

                # Collect neighbor for Phase 2 batch fetch only if edge matches intent filter
                if neighbor_uuid and neighbor_uuid not in visited and is_matching_edge:
                    pending_neighbor_info.append((neighbor_uuid, depth + 1, edge, edge_fact, edge_name, src_uuid, tgt_uuid))

                # Process fact text inline (fact generation does NOT need neighbor data)
                fact_to_add = edge_fact
                if not fact_to_add and edge_name:
                    # Placeholder — will be filled in Phase 2 after neighbor_map is ready
                    fact_to_add = None  # Mark as pending

                if fact_to_add:
                    norm = self.normalize_text(fact_to_add)
                    if norm and norm not in seen_fact_texts:
                        seen_fact_texts.add(norm)
                        ep_ids = edge.get("episode_ids", [])
                        # 优先取当前节点（edge 源节点）的 PDF 信息
                        source = node_data.get("pdf_source") if node_data else None
                        page = node_data.get("pdf_page") if node_data else None
                        bbox = node_data.get("pdf_bbox") if node_data else None
                        page_width = node_data.get("pdf_page_width") if node_data else None
                        page_height = node_data.get("pdf_page_height") if node_data else None
                        original_text = ""
                        # Clause 节点优先使用 pdf_bboxes（多页 bbox 列表）
                        # 兼容处理：pdf_bboxes 可能是 JSON 字符串或列表
                        pdf_bboxes = None
                        if node_data:
                            node_labels = node_data.get("labels", [])
                            if "Clause" in node_labels:
                                clause_pdf_bboxes_raw = node_data.get("pdf_bboxes")
                                clause_pdf_bboxes = None
                                if clause_pdf_bboxes_raw:
                                    if isinstance(clause_pdf_bboxes_raw, str):
                                        try:
                                            clause_pdf_bboxes = json.loads(clause_pdf_bboxes_raw)
                                        except (json.JSONDecodeError, TypeError):
                                            clause_pdf_bboxes = None
                                    elif isinstance(clause_pdf_bboxes_raw, list):
                                        clause_pdf_bboxes = clause_pdf_bboxes_raw
                                if clause_pdf_bboxes and len(clause_pdf_bboxes) > 0:
                                    pdf_bboxes = clause_pdf_bboxes
                                    first_bbox = clause_pdf_bboxes[0]
                                    if len(first_bbox) >= 5:
                                        page = first_bbox[0]
                                        bbox = [first_bbox[1], first_bbox[2], first_bbox[3], first_bbox[4]]
                        # 查 episode 获取更多信息
                        if ep_ids:
                            try:
                                eps = self.storage.get_episodes(
                                    [ep_ids[0]] if isinstance(ep_ids, list) else [ep_ids]
                                )
                                if eps:
                                    meta = eps[0].get("metadata", {})
                                    if not original_text:
                                        original_text = eps[0].get("text", "")
                                    if not source or source == "Graph":
                                        source = meta.get("source", "Graph")
                                    if not page:
                                        page = meta.get("page")
                                    if not bbox:
                                        bbox = meta.get("bbox")
                                    if not page_width:
                                        page_width = meta.get("page_width")
                                    if not page_height:
                                        page_height = meta.get("page_height")
                            except Exception:
                                pass
                        # 最后 fallback 到 root node（object_data）的 PDF 信息
                        if (not source or source == "Graph") and object_data:
                            source = object_data.get("pdf_source") or "Graph"
                        if not page and object_data:
                            page = object_data.get("pdf_page")
                        if not bbox and object_data:
                            bbox = object_data.get("pdf_bbox")
                        if not page_width and object_data:
                            page_width = object_data.get("pdf_page_width")
                        if not page_height and object_data:
                            page_height = object_data.get("pdf_page_height")

                        fact_entry = {
                            "uuid": edge_uuid,
                            "text": fact_to_add,
                            "original_text": original_text,
                            "source": source,
                            "page": page,
                            "bbox": bbox,
                            "page_width": page_width,
                            "page_height": page_height,
                            "graph_id": graph_id,
                            "source_node_uuid": src_uuid,
                            "target_node_uuid": tgt_uuid,
                            "relation_name": edge_name,
                            "traversal_depth": depth,
                            "similarity_score": root_score,  # Hybrid search score from root node
                        }
                        if pdf_bboxes:
                            fact_entry["pdf_bboxes"] = pdf_bboxes
                        facts.append(fact_entry)

                # Push neighbor onto stack for next iteration
                if neighbor_uuid and neighbor_uuid not in visited:
                    # We don't have neighbor_data yet — use None; Phase 2 will fill it
                    stack.append((neighbor_uuid, None, depth + 1))

        # Phase 2: Batch fetch all visited neighbor data in one call
        neighbor_uuids = [n_uuid for n_uuid, _, _, _, _, _, _ in pending_neighbor_info]
        neighbor_uuids.append(object_uuid)  # Also include root to ensure it's in map

        neighbor_map: Dict[str, Dict[str, Any]] = {}
        if neighbor_uuids:
            try:
                neighbor_map = self.storage.get_nodes_batch(neighbor_uuids)
            except Exception as e:
                logger.debug(f"Batch get nodes failed: {e}")
                neighbor_map = {}

        # Phase 2: Synthesize facts for semantic edges that lacked explicit edge_fact.
        # Neighbors were already added to traversal_nodes during Phase 1 (stack pop).
        # neighbor_map resolves neighbor_name for fact synthesis.
        for neighbor_uuid, n_depth, edge, edge_fact, edge_name, src_uuid, tgt_uuid in pending_neighbor_info:
            # Synthesize fact for semantic edges (those without explicit edge_fact)
            if not edge_fact and edge_name:
                neighbor_data = neighbor_map.get(neighbor_uuid, {})
                neighbor_name = neighbor_data.get("name", "")
                rel_facts = {
                    "MENTIONS": f"提及: {neighbor_name}",
                    "HAS_TOPIC": f"关联主题: {neighbor_name}",
                }
                fact_to_add = rel_facts.get(edge_name, f"[{edge_name}] {neighbor_name}")

                norm = self.normalize_text(fact_to_add)
                if norm and norm not in seen_fact_texts:
                    seen_fact_texts.add(norm)
                    edge_uuid = edge.get("uuid", "")
                    ep_ids = edge.get("episode_ids", [])
                    # 优先取 neighbor_data（可能是 Clause）的原文和 PDF 信息
                    source = neighbor_data.get("pdf_source") or "Graph"
                    page = neighbor_data.get("pdf_page")
                    bbox = neighbor_data.get("pdf_bbox")
                    page_width = neighbor_data.get("pdf_page_width")
                    page_height = neighbor_data.get("pdf_page_height")
                    original_text = neighbor_data.get("summary", "") or ""
                    # Clause 节点优先使用 pdf_bboxes（多页 bbox 列表）
                    # 兼容处理：pdf_bboxes 可能是 JSON 字符串或列表
                    pdf_bboxes = None
                    neighbor_labels = neighbor_data.get("labels", [])
                    if "Clause" in neighbor_labels:
                        clause_pdf_bboxes_raw = neighbor_data.get("pdf_bboxes")
                        clause_pdf_bboxes = None
                        if clause_pdf_bboxes_raw:
                            if isinstance(clause_pdf_bboxes_raw, str):
                                try:
                                    clause_pdf_bboxes = json.loads(clause_pdf_bboxes_raw)
                                except (json.JSONDecodeError, TypeError):
                                    clause_pdf_bboxes = None
                            elif isinstance(clause_pdf_bboxes_raw, list):
                                clause_pdf_bboxes = clause_pdf_bboxes_raw
                        if clause_pdf_bboxes and len(clause_pdf_bboxes) > 0:
                            pdf_bboxes = clause_pdf_bboxes
                            first_bbox = clause_pdf_bboxes[0]  # [page, x0, y0, x1, y1]
                            if len(first_bbox) >= 5:
                                page = first_bbox[0]
                                bbox = [first_bbox[1], first_bbox[2], first_bbox[3], first_bbox[4]]
                    # 回退：查 episode（当原文或 bbox 缺失时）
                        try:
                            eps = self.storage.get_episodes(
                                [ep_ids[0]] if isinstance(ep_ids, list) else [ep_ids]
                            )
                            if eps:
                                meta = eps[0].get("metadata", {})
                                if not original_text:
                                    original_text = eps[0].get("text", "")
                                if not source or source == "Graph":
                                    source = meta.get("source", "Graph")
                                if not page:
                                    page = meta.get("page")
                                if not bbox:
                                    bbox = meta.get("bbox")
                                if not page_width:
                                    page_width = meta.get("page_width")
                                if not page_height:
                                    page_height = meta.get("page_height")
                        except Exception:
                            pass

                    fact_entry = {
                        "uuid": edge_uuid,
                        "text": fact_to_add,
                        "original_text": original_text,
                        "source": source,
                        "page": page,
                        "bbox": bbox,
                        "page_width": page_width,
                        "page_height": page_height,
                        "graph_id": graph_id,
                        "source_node_uuid": src_uuid,
                        "target_node_uuid": tgt_uuid,
                        "relation_name": edge_name,
                        "traversal_depth": n_depth - 1,
                        "similarity_score": root_score,
                    }
                    if pdf_bboxes:
                        fact_entry["pdf_bboxes"] = pdf_bboxes
                    facts.append(fact_entry)

        # Build Object node detail (with PDF info added later by caller via batch)
        obj_detail = {
            "uuid": object_uuid,
            "name": object_data.get("name", ""),
            "labels": object_data.get("labels", []),
            "summary": object_data.get("summary", ""),
            "pdf_info": {},  # Will be filled by search_object_first via batch call
            "graph_id": graph_id,  # Include graph_id for PDF viewing in frontend
        }

        return ObjectFirstRow(
            object_node=obj_detail,
            traversal_paths=traversal_nodes,
            traversal_edges=traversal_edges,
            facts=facts,
            relevance_score=0.0,
        )

    def _dfs_visit(
        self,
        node_uuid: str,
        node_data: Optional[Dict[str, Any]],
        depth: int,
        max_depth: int,
        visited: set,
        traversal_nodes: List[ObjectPathNode],
        traversal_edges: List[ObjectPathEdge],
        facts: List[Dict[str, Any]],
        graph_id: str,
        seen_fact_texts: set,
        bidirectional: bool = False,
    ):
        """
        [已废弃 — 保留签名以兼容外部调用]
        DFS 递归访问已由 _dfs_from_object 中的迭代版本替代。
        该方法不再执行任何操作，遍历逻辑完全在 _dfs_from_object 内完成。
        """
        pass

    def _get_node_pdf_info(self, node_uuid: str) -> Dict[str, Any]:
        """获取节点的 PDF 定位信息，优先读节点自身属性（Clause），回退查 Episode"""
        pdf_info = {
            "source": None, "page": None, "bbox": None,
            "page_width": None, "page_height": None, "episode_text": None,
        }
        try:
            # Fast path: read directly from node's own pdf_* properties
            node = self.storage.get_node(node_uuid)
            if node:
                source = node.get("pdf_source") or node.get("source")
                page = node.get("pdf_page") or node.get("page")
                bbox = node.get("pdf_bbox") or node.get("bbox")
                page_width = node.get("pdf_page_width") or node.get("page_width")
                page_height = node.get("pdf_page_height") or node.get("page_height")
                if source or page:
                    pdf_info.update({
                        "source": source,
                        "page": page,
                        "bbox": bbox,
                        "page_width": page_width,
                        "page_height": page_height,
                    })
                    return pdf_info

            # Fallback: query Episode via MENTIONS relationship
            node_eps = self.storage.get_node_episodes(node_uuid, limit=1)
            if node_eps:
                ep = node_eps[0]
                meta = ep.get("metadata", {})
                # Episode 的 source/page 可能是直接属性，也可能在 metadata_json 中
                ep_source = ep.get("source") or meta.get("source")
                ep_page = ep.get("page") or meta.get("page")
                ep_bbox = meta.get("bbox")
                ep_page_width = meta.get("page_width") or meta.get("pageWidth")
                ep_page_height = meta.get("page_height") or meta.get("pageHeight")
                pdf_info.update({
                    "source": ep_source,
                    "page": ep_page,
                    "bbox": ep_bbox,
                    "page_width": ep_page_width,
                    "page_height": ep_page_height,
                    "episode_text": ep.get("text"),
                })
        except Exception:
            pass
        return pdf_info

    def _batch_get_node_pdf_info(
        self,
        node_uuids: List[str],
        pre_fetched_nodes: Dict[str, Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        批量获取多个节点的 PDF 定位信息。

        策略：优先读取节点自身的 pdf_* 属性（快速路径），对缺失的节点回退查 Episode。

        Args:
            node_uuids: 节点 UUID 列表
            pre_fetched_nodes: 已获取的节点数据（避免重复 DB 查询）

        Returns:
            Dict[node_uuid -> pdf_info dict]
        """
        result: Dict[str, Dict[str, Any]] = {}

        if not node_uuids:
            return result

        # Step 1: 使用已获取的节点数据，或重新查询
        if pre_fetched_nodes is not None:
            nodes_map = pre_fetched_nodes
        else:
            try:
                nodes_map = self.storage.get_nodes_batch(node_uuids)
            except Exception as e:
                logger.debug(f"Batch get nodes for PDF info failed: {e}")
                nodes_map = {}

        # Step 1: Quick path — use node's own pdf_* properties if bbox is available
        complete_uuids: List[str] = []
        partial_uuids: List[str] = []

        for node_uuid in node_uuids:
            pdf_info: Dict[str, Any] = {
                "source": None, "page": None, "bbox": None,
                "page_width": None, "page_height": None, "episode_text": None,
            }

            node = nodes_map.get(node_uuid)
            if node:
                labels = node.get("labels", [])
                is_clause = "Clause" in labels

                # Clause 节点优先使用 pdf_bboxes（多页 bbox 列表）
                if is_clause:
                    pdf_bboxes_raw = node.get("pdf_bboxes")
                    # 兼容处理：pdf_bboxes 可能是 JSON 字符串或列表
                    pdf_bboxes = None
                    if pdf_bboxes_raw:
                        if isinstance(pdf_bboxes_raw, str):
                            try:
                                pdf_bboxes = json.loads(pdf_bboxes_raw)
                            except (json.JSONDecodeError, TypeError):
                                pdf_bboxes = None
                        elif isinstance(pdf_bboxes_raw, list):
                            pdf_bboxes = pdf_bboxes_raw
                    if pdf_bboxes and len(pdf_bboxes) > 0:
                        first_bbox = pdf_bboxes[0]  # [page, x0, y0, x1, y1]
                        if len(first_bbox) >= 5:
                            pdf_info.update({
                                "source": node.get("pdf_source") or node.get("source"),
                                "page": first_bbox[0],
                                "bbox": [first_bbox[1], first_bbox[2], first_bbox[3], first_bbox[4]],
                                "page_width": node.get("pdf_page_width"),
                                "page_height": node.get("pdf_page_height"),
                                "pdf_bboxes": pdf_bboxes,  # 保留完整多页 bbox 供前端使用
                            })
                            result[node_uuid] = pdf_info
                            complete_uuids.append(node_uuid)
                            continue
                    # Clause 但没有 pdf_bboxes，fallthrough to Step 2
                    partial_uuids.append(node_uuid)
                    continue

                source = node.get("pdf_source") or node.get("source")
                page = node.get("pdf_page") or node.get("page")
                bbox = node.get("pdf_bbox") or node.get("bbox")
                page_width = node.get("pdf_page_width") or node.get("page_width")
                page_height = node.get("pdf_page_height") or node.get("page_height")
                pdf_info.update({
                    "source": source,
                    "page": page,
                    "bbox": bbox,
                    "page_width": page_width,
                    "page_height": page_height,
                })
                # Complete: has bbox (can locate on PDF)
                if bbox is not None:
                    result[node_uuid] = pdf_info
                    complete_uuids.append(node_uuid)
                else:
                    partial_uuids.append(node_uuid)
            else:
                partial_uuids.append(node_uuid)

        # Step 2: Enrich partial nodes — batch query instead of per-node loops
        if partial_uuids:
            try:
                batch_results = self.storage.batch_get_node_pdf_info(partial_uuids)
                for node_uuid, pdf_data in batch_results.items():
                    if node_uuid not in result:
                        result[node_uuid] = {
                            "source": None, "page": None, "bbox": None,
                            "page_width": None, "page_height": None, "episode_text": None,
                        }
                        result[node_uuid].update(pdf_data)
            except Exception as e:
                logger.debug(f"Batch PDF info fallback failed: {e}")

        # Fill remaining partial_uuids not found by batch query
        for node_uuid in partial_uuids:
            if node_uuid not in result:
                result[node_uuid] = {
                    "source": None, "page": None, "bbox": None,
                    "page_width": None, "page_height": None, "episode_text": None,
                }

        return result

    def _rerank_object_rows(
        self,
        query: str,
        rows: List[ObjectFirstRow],
    ) -> List[ObjectFirstRow]:
        """
        对 ObjectFirstRow 列表进行重排，基于：
        1. Object 节点与查询的相关性（向量分数）
        2. 该 Object 的 DFS 事实数量
        3. LLM 评估 Object 与查询的整体相关性
        """
        if not rows:
            return rows

        # 预评分：fact 数量越多 + Object name 匹配度越高，得分越高
        import re
        fact_count_max = max(len(r.facts) for r in rows) or 1
        query_lower = query.lower()
        # 提取中文实体词（>=4个中文字符）和英文/数字词（>=2字符）
        chinese_keywords = re.findall(r'[\u4e00-\u9fff]{4,}', query_lower)
        english_keywords = [kw for kw in query_lower.split() if len(kw) > 1]

        for row in rows:
            obj_name = row.object_node.get("name", "").lower()
            if query_lower in obj_name:
                name_score = 50
            elif any(kw in obj_name for kw in chinese_keywords):
                name_score = 35
            elif any(kw in obj_name for kw in english_keywords):
                name_score = 20
            else:
                name_score = 0
            fact_score = (len(row.facts) / fact_count_max) * 30
            row.relevance_score = name_score + fact_score + 20  # 基础分 20

        # LLM 精排（top 候选）
        top_rows = sorted(rows, key=lambda r: r.relevance_score, reverse=True)[:10]

        if len(top_rows) <= 1:
            return rows

        # 构建 LLM 重排 prompt
        row_summaries = []
        for i, row in enumerate(top_rows):
            obj_name = row.object_node.get("name", "Unknown")
            fact_count = len(row.facts)
            fact_preview = " | ".join([
                f.get("text", "")[:50] for f in row.facts[:3]
            ])
            row_summaries.append(
                f"[{i}] Object: {obj_name} | Facts: {fact_count} | Preview: {fact_preview}"
            )

        rerank_prompt = f"""你是知识检索重排专家。请根据【用户查询】，对以下 Object-first 检索结果进行相关性打分。

### 用户查询:
{query}

### 候选结果列表:
{chr(10).join(row_summaries)}

### 打分规则:
1. 评估每个 Object 节点是否与查询主题直接相关（0-100分）
2. 考虑 Object 的含义、与查询的语义匹配度
3. 注意：事实数量多的不一定更相关，要看内容质量
4. 返回 JSON 格式：{{"scores": [{{"index": 0, "score": 95}}, ...]}}

请输出打分 JSON："""

        try:
            response = self.llm.chat_json(
                messages=[{"role": "user", "content": rerank_prompt}],
                temperature=0.1,
            )
            scores = response.get("scores", [])

            # 建立 index -> score 映射
            score_map: Dict[int, float] = {}
            for item in scores:
                idx = item.get("index")
                score = item.get("score", 0)
                if isinstance(idx, int) and 0 <= idx < len(top_rows):
                    score_map[idx] = score

            # 更新 top_rows 的 relevance_score
            for idx, row in enumerate(top_rows):
                if idx in score_map:
                    row.relevance_score = score_map[idx]

            # 合并排序：top_rows 按 LLM 分数，其余按预评分
            remaining = [r for r in rows if r not in top_rows]
            all_sorted = sorted(
                top_rows + remaining,
                key=lambda r: r.relevance_score,
                reverse=True,
            )
            return all_sorted

        except Exception as e:
            logger.warning(f"LLM rerank failed for Object rows: {e}")
            return sorted(rows, key=lambda r: r.relevance_score, reverse=True)


    # ========== Core Retrieval Tools (Optimized) ==========


