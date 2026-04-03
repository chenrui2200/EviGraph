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
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

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
                    # text 可能是 "[RELATION] 内容"，去掉前缀
                    display_text = text
                    for prefix in ["[DEFINES]", "[MANDATES]", "[RECOMMENDS]", "[PROHIBITS]",
                                   "[OPERATES_ON]", "[HAS_CONDITION]", "[APPLIES_TO]",
                                   "[IN_SITUATION]", "[MENTIONS]"]:
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

    [Core Retrieval Tools - Optimized]
    1. insight_forge - Deep Insight Retrieval (Most powerful, auto-generates sub-questions, multi-dimensional retrieval)
    2. panorama_search - Breadth Search (Get comprehensive view, including expired content)
    3. quick_search - Simple Search (Quick retrieval)
    4. interview_agents - Deep Interview (Interview simulated Agents, obtain multi-perspective insights)

    [Basic Tools]
    - search_graph - Graph semantic search
    - get_all_nodes - Get all nodes in graph
    - get_all_edges - Get all edges in graph (with temporal information)
    - get_node_detail - Get detailed node information
    - get_node_edges - Get edges related to a node
    - get_entities_by_type - Get entities by type
    - get_entity_summary - Get entity relationship summary
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

        extract_prompt = f"""你是一个搜索专家。请从用户的问题中提取出 3-5 个核心关键词或短语，用于在知识图谱中进行检索。
提取的关键词应能代表问题的核心实体、动作和约束。

### 用户问题:
{query}

### 要求:
1. 关键词应简洁、具有代表性。
2. 以空格分隔返回关键词。

输出示例:
多孔导管 敷设 规定

请直接输出提取后的关键词字符串："""

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
        Use LLM to rerank facts based on relevance to the query.
        Returns sorted facts with 'relevance_score' and 'relevance_reasoning'.
        """
        if not facts:
            return []

        logger.info(f"Performing LLM Reranking for {len(facts)} facts...")

        # Prepare fact list for LLM (limit to top 30 to avoid prompt too long)
        fact_list_str = ""
        facts_to_process = facts[:30]
        for i, f in enumerate(facts_to_process):
            # 优先使用 original_text（真实条款内容），否则用 text
            raw_text = f.get('original_text', '').strip() or f.get('text', '')
            relation = f.get('relation_name', '')
            source = f.get('source', 'Graph')
            page = f.get('page', '')
            rel_str = f"[{relation}] " if relation else ""
            pg_str = f" (来源: {source}, 页码: {page})" if page else (f" (来源: {source})" if source != 'Graph' else "")
            fact_list_str += f"[{i}] {rel_str}{raw_text[:500]}{pg_str}\n"

        rerank_prompt = f"""你是一个专业的知识重排（Rerank）专家。请根据【用户问题】，对【候选事实列表】中的每一条记录进行相关性打分。

### 用户问题:
{query}

### 候选事实列表:
{fact_list_str}

### 任务要求:
1. 对每个事实，评估其对回答【用户问题】的直接贡献度和核心程度。
2. 打分范围为 0-100（分值越高越相关）：
   - 90-100: 事实直接、完整地回答了用户问题
   - 60-89: 事实提供了重要参考信息，但需要进一步推理
   - 30-59: 事实有一定关联，但偏离核心问题
   - 0-29: 事实与问题几乎无关
3. 重点关注事实的条款内容（括号外的文本），区分关系类型（如 DEFINES/HAS_CONDITION/MANDATES 等）。
4. 返回 JSON 格式：
{{
  "rerank_results": [
    {{"index": 0, "score": 95, "reason": "直接引用了多孔导管敷设的具体间距规定"}},
    {{"index": 2, "score": 40, "reason": "提及了导管，但主要讨论材质而非敷设规定"}}
  ]
}}

请输出打分后的结果 JSON："""

        try:
            response = self.llm.chat_json(messages=[{"role": "user", "content": rerank_prompt}], temperature=0.1)
            rerank_results = response.get("rerank_results", [])

            # Map results
            scored_facts = []
            for item in rerank_results:
                idx = item.get('index')
                if isinstance(idx, int) and 0 <= idx < len(facts_to_process):
                    fact = facts_to_process[idx].copy()
                    fact['relevance_score'] = item.get('score', 0)
                    fact['relevance_reasoning'] = item.get('reason', '')
                    scored_facts.append(fact)

            # Sort by score descending
            scored_facts.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)

            # Add any facts that LLM missed (at the end with 0 score)
            seen_texts = {f.get('text') for f in scored_facts}
            for f in facts_to_process:
                if f.get('text') not in seen_texts:
                    f_copy = f.copy()
                    f_copy['relevance_score'] = 0
                    f_copy['relevance_reasoning'] = "LLM missed during rerank"
                    scored_facts.append(f_copy)

            return scored_facts
        except Exception as e:
            logger.error(f"LLM Fact Reranking failed: {str(e)}")
            return facts # Return all if reranking fails

    def search_with_agentic_flow(
        self,
        graph_ids: List[str],
        query: str,
        limit: int = 20,
        max_hops: int = 3,
        scope: str = "both"
    ) -> SearchResult:
        """
        Encapsulated Agentic Retrieval Flow:
        1. Optimize query keywords
        2. Multi-hop search
        3. LLM filtering
        4. Deduplication
        5. Final Reranking Card

        Args:
            graph_ids: List of graph IDs to search
            query: Search query
            limit: Maximum number of facts to return
            max_hops: Maximum number of reasoning hops
            scope: Search scope, "edges" or "nodes" or "both" (default: "both")
        """
        logger.info(f"Starting search_with_agentic_flow for query: {query[:50]}..., scope={scope}")

        current_context_facts = []
        seen_fact_texts = set()
        seen_fact_uuids = set()
        retrieval_history = []

        # 1. Initial Query Optimization
        search_query = self.optimize_query(query)

        # 2. Agentic Multi-hop Loop
        for hop in range(max_hops):
            logger.info(f"Agentic Flow: Hop {hop+1}/{max_hops} with query: {search_query}")

            # 2.1 Perform Search
            search_result = self.search_multi_graphs(graph_ids=graph_ids, query=search_query, limit=limit, scope=scope)
            new_raw_facts = search_result.facts

            if not new_raw_facts:
                logger.info(f"Hop {hop+1}: No more facts found.")
                break

            # 2.2 LLM Filtering
            filtered_new_facts = self.filter_facts(query, new_raw_facts)

            # 2.3 Merge & Deduplicate
            new_facts_added = 0
            for fact in filtered_new_facts:
                txt = fact.get("text", "")
                norm_txt = self.normalize_text(txt)
                fact_uuid = fact.get("uuid", "")

                is_duplicate = False
                if fact_uuid and fact_uuid in seen_fact_uuids:
                    is_duplicate = True
                elif norm_txt and norm_txt in seen_fact_texts:
                    is_duplicate = True

                if not is_duplicate:
                    current_context_facts.append(fact)
                    if norm_txt:
                        seen_fact_texts.add(norm_txt)
                    if fact_uuid:
                        seen_fact_uuids.add(fact_uuid)
                    new_facts_added += 1

            retrieval_history.append({
                "hop": hop + 1,
                "query": search_query,
                "raw_found": len(new_raw_facts),
                "added": new_facts_added
            })

            # 2.4 Multi-hop Reasoning: Do we need more?
            facts_summary = "\n".join([f"- {f.get('text')} [Source: {f.get('source')}]" for f in current_context_facts])

            reasoning_prompt = f"""You are a knowledge retrieval agent. Analyze the current context and the original question.

### Original Question:
{query}

### Current Knowledge Context:
{facts_summary}

### Task:
1. Identify if the current context contains EXPLICIT references to other sections, clauses, or technical terms that are MISSING but necessary to answer the question (e.g., "See Section 7.6.1", "Refer to standard XYZ").
2. If more info is needed, respond with ONLY a search query for the next hop in the format: [SEARCH: query]
3. If the knowledge is sufficient or no clear references are found, respond with ONLY: [READY]

Your response:"""

            try:
                analysis = self.llm.chat(messages=[{"role": "user", "content": reasoning_prompt}], temperature=0).strip()
                if "[READY]" in analysis or "[SEARCH:" not in analysis:
                    logger.info(f"Knowledge sufficient after {hop+1} hops.")
                    break

                import re
                match = re.search(r"\[SEARCH:\s*(.*?)\]", analysis)
                if match:
                    search_query = match.group(1)
                else:
                    break
            except Exception as e:
                logger.error(f"Multi-hop reasoning failed: {str(e)}")
                break

        # 3. Final Reranking Card (Added as a dedicated step)
        logger.info(f"Final Step: Reranking {len(current_context_facts)} gathered facts...")
        reranked_facts = self.rerank_facts(query, current_context_facts)

        # Limit to final results
        final_facts = reranked_facts[:limit]

        # Prepare rerank details for visibility in frontend/API
        rerank_details = []
        for i, f in enumerate(final_facts):
            rerank_details.append({
                "index": i,
                "score": f.get('relevance_score', 0),
                "reason": f.get('relevance_reasoning', ''),
                "text": f.get('text', '')[:100] + "..."
            })

        return SearchResult(
            facts=final_facts,
            edges=[],
            nodes=[],
            query=query,
            total_count=len(final_facts),
            rerank_details=rerank_details
        )

    def search_with_dfs_flow(
        self,
        graph_ids: List[str],
        query: str,
        limit: int = 10,
        max_depth: int = 3,
        root_types: List[str] = None,
    ) -> ObjectFirstSearchResult:
        """
        DFS-based retrieval flow aligned with hit-test query logic.
        similarity_threshold filtering is done on frontend by relevance_score (参考 HitTest).
        Stage 2 LLM fact reranking + filter_threshold filtering are done in SSE stream.

        Args:
            graph_ids: List of graph IDs to search
            query: Search query
            limit: Maximum number of result rows to return
            max_depth: Maximum DFS traversal depth
        """
        logger.info(f"Starting search_with_dfs_flow: query={query[:30]}, graphs={len(graph_ids)}, max_depth={max_depth}, root_types={root_types}, limit={limit}")

        if root_types is None:
            root_types = ["Object", "Term"]

        all_rows: List[ObjectFirstRow] = []
        seen_fact_texts: set = set()

        # Search each graph
        for graph_id in graph_ids:
            # --- Object root search ---
            if "Object" in root_types:
                object_roots = self.storage.search_object_nodes(
                    graph_id=graph_id,
                    query=query,
                    limit=limit,
                )

                for obj_node in object_roots:
                    obj_uuid = obj_node.get("uuid", "")
                    if not obj_uuid:
                        continue
                    # Hybrid search score (0-1) converted to 0-100 scale for similarity_threshold
                    root_score = (obj_node.get("score", 0)) * 100
                    row = self._dfs_from_object(
                        graph_id=graph_id,
                        object_uuid=obj_uuid,
                        object_data=obj_node,
                        max_depth=max_depth,
                        seen_fact_texts=seen_fact_texts,
                        root_score=root_score,
                    )
                    all_rows.append(row)

            # --- Term root search ---
            if "Term" in root_types:
                term_roots = self.storage.search_term_nodes(
                    graph_id=graph_id,
                    query=query,
                    limit=limit,
                )

                for term_node in term_roots:
                    term_uuid = term_node.get("uuid", "")
                    if not term_uuid:
                        continue
                    root_score = (term_node.get("score", 0)) * 100
                    row = self._dfs_from_object(
                        graph_id=graph_id,
                        object_uuid=term_uuid,
                        object_data=term_node,
                        max_depth=max_depth,
                        seen_fact_texts=seen_fact_texts,
                        root_score=root_score,
                    )
                    all_rows.append(row)

        if not all_rows:
            return ObjectFirstSearchResult(
                query=query,
                rows=[],
                total_objects=0,
                total_facts=0,
            )

        # --- Stage 1: Similarity threshold pre-filtering ---
        # --- LLM rerank rows ---
        scored_rows = self._rerank_object_rows(query, all_rows)
        final_rows = scored_rows[:limit]

        # --- Batch PDF info for top rows ---
        if final_rows:
            root_uuids = [row.object_node.get("uuid") for row in final_rows]
            root_uuids = [uid for uid in root_uuids if uid]
            if root_uuids:
                batch_pdf_info = self._batch_get_node_pdf_info(root_uuids)
                for row in final_rows:
                    root_uuid = row.object_node.get("uuid")
                    if root_uuid and root_uuid in batch_pdf_info:
                        row.object_node["pdf_info"] = batch_pdf_info[root_uuid]

        # Stage 2 LLM fact reranking is done in SSE stream (graph.py)
        # filter_threshold 过滤也在 SSE 流中完成

        logger.info(f"search_with_dfs_flow complete: input=rows:{len(all_rows)}, output=rows:{len(final_rows)}, facts:{sum(len(r.facts) for r in final_rows)}")

        total_facts = sum(len(row.facts) for row in final_rows)
        return ObjectFirstSearchResult(
            query=query,
            rows=final_rows,
            total_objects=len(final_rows),
            total_facts=total_facts,
        )

    # ========== Basic Tools ==========

    def search_graph(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        Graph semantic search (hybrid: vector + BM25 via Neo4j)

        Args:
            graph_id: Graph ID
            query: Search query
            limit: Number of results to return
            scope: Search scope, "edges" or "nodes" or "both"

        Returns:
            SearchResult
        """
        logger.info(f"Graph search: graph_id={graph_id}, query={query[:50]}...")

        try:
            search_results = self.storage.search(
                graph_id=graph_id,
                query=query,
                limit=limit,
                scope=scope,
            )

            facts = []
            edges = []
            nodes = []

            # Parse edge results
            edge_list = search_results.get('edges', [])
            all_episode_uuids = []
            for edge in edge_list:
                if isinstance(edge, dict) and edge.get('episode_ids'):
                    uuids = edge.get('episode_ids')
                    if isinstance(uuids, list):
                        all_episode_uuids.extend(uuids)
                    else:
                        all_episode_uuids.append(str(uuids))

            # Fetch episodes once to get metadata
            episodes_data = self.storage.get_episodes(list(set(all_episode_uuids)))
            episode_map = {ep["uuid"]: ep for ep in episodes_data}

            for edge in edge_list:
                if isinstance(edge, dict):
                    fact = edge.get('fact', '')
                    if fact:
                        # Extract source info if found in episodes
                        source = "Unknown"
                        page = None
                        total_pages = None
                        bbox = None

                        edge_ep_ids = edge.get('episode_ids', [])
                        if not isinstance(edge_ep_ids, list):
                            edge_ep_ids = [str(edge_ep_ids)]

                        # Use first found source info
                        original_text = ""
                        for ep_id in edge_ep_ids:
                            ep = episode_map.get(ep_id)
                            if ep:
                                if ep.get("metadata"):
                                    meta = ep["metadata"]
                                    source = meta.get("source", "Unknown")
                                    page = meta.get("page")
                                    total_pages = meta.get("total_pages")
                                    bbox = meta.get("bbox")
                                    page_width = meta.get("page_width")
                                    page_height = meta.get("page_height")

                                # Store the actual raw text from PDF chunk
                                if ep.get("text"):
                                    original_text = ep["text"]
                                break

                        fact_obj = {
                            "uuid": edge.get('uuid', ''),
                            "text": fact,
                            "original_text": original_text, # Explicitly store raw text
                            "source": source,
                            "page": page,
                            "total_pages": total_pages,
                            "bbox": bbox,
                            "page_width": page_width,
                            "page_height": page_height,
                            "graph_id": graph_id,
                            "source_node_uuid": edge.get('source_node_uuid', ''),
                            "target_node_uuid": edge.get('target_node_uuid', '')
                        }
                        facts.append(fact_obj)

                    edges.append({
                        "uuid": edge.get('uuid', ''),
                        "name": edge.get('name', ''),
                        "fact": fact,
                        "source_node_uuid": edge.get('source_node_uuid', ''),
                        "target_node_uuid": edge.get('target_node_uuid', ''),
                        "episodes": edge.get('episode_ids', [])
                    })

            # Parse node results
            if hasattr(search_results, 'nodes'):
                node_list = search_results.nodes
            elif isinstance(search_results, dict) and 'nodes' in search_results:
                node_list = search_results['nodes']
            else:
                node_list = []

            for node in node_list:
                if isinstance(node, dict):
                    node_uuid = node.get('uuid', '')

                    # 优先从 node 自身属性读取 PDF 定位信息（Entity 创建时已存储）
                    # 回退到 get_node_episodes 查询
                    pdf_info = {
                        "source": node.get("pdf_source") or node.get("source"),
                        "page": node.get("pdf_page") or node.get("page"),
                        "bbox": node.get("pdf_bbox"),
                        "page_width": node.get("pdf_page_width"),
                        "page_height": node.get("pdf_page_height"),
                        "episode_text": None
                    }

                    # 如果 node 自身没有 bbox，尝试从关联的 episode 获取
                    if not pdf_info["bbox"]:
                        try:
                            node_eps = self.storage.get_node_episodes(node_uuid, limit=1)
                            if node_eps:
                                meta = node_eps[0].get("metadata", {})
                                nested_meta = meta.get("metadata", {})
                                pdf_info.update({
                                    "source": pdf_info["source"] or meta.get("source") or nested_meta.get("source"),
                                    "page": pdf_info["page"] or meta.get("page") or nested_meta.get("page"),
                                    "bbox": pdf_info["bbox"] or meta.get("bbox") or nested_meta.get("bbox"),
                                    "page_width": pdf_info["page_width"] or meta.get("page_width") or nested_meta.get("page_width"),
                                    "page_height": pdf_info["page_height"] or meta.get("page_height") or nested_meta.get("page_height"),
                                    "episode_text": node_eps[0].get("text"),
                                })
                        except:
                            pass

                    nodes.append({
                        "uuid": node_uuid,
                        "name": node.get('name', ''),
                        "labels": node.get('labels', []),
                        "summary": node.get('summary', ''),
                        "pdf_info": pdf_info,
                    })
                    summary = node.get('summary', '')
                    if summary:
                        facts.append({
                            "uuid": node_uuid,
                            "text": f"Entity Knowledge: {node.get('name', '')} - {summary}",
                            "source": pdf_info["source"] or "Knowledge Graph",
                            "page": pdf_info["page"],
                            "bbox": pdf_info["bbox"],
                            "page_width": pdf_info.get("page_width"),
                            "page_height": pdf_info.get("page_height"),
                            "graph_id": graph_id,
                            "entity_name": node.get('name', '')
                        })

                    # Path Extension: Also pull some top relations for this node to provide context
                    if node_uuid and len(facts) < limit * 2:
                        try:
                            node_rels = self.storage.get_node_edges(node_uuid)
                            for rel in node_rels[:3]: # Add up to 3 context relations
                                if rel.get('fact'):
                                    # Traceability for Relation: Find original episodes from edge
                                    rel_source_info = {"source": "Graph Path Extension", "page": None, "bbox": None}
                                    ep_ids = rel.get("episode_ids", [])
                                    if ep_ids:
                                        try:
                                            rel_eps = self.storage.get_episodes([ep_ids[0]])
                                            if rel_eps:
                                                meta = rel_eps[0].get("metadata", {})
                                                nested_meta = meta.get("metadata", {})
                                                rel_source_info.update({
                                                    "source": meta.get("source") or nested_meta.get("source") or "Graph Path Extension",
                                                    "page": meta.get("page") or nested_meta.get("page"),
                                                    "bbox": meta.get("bbox") or nested_meta.get("bbox"),
                                                    "page_width": meta.get("page_width") or nested_meta.get("page_width"),
                                                    "page_height": meta.get("page_height") or nested_meta.get("page_height")
                                                })
                                        except:
                                            pass

                                    facts.append({
                                        "text": f"Contextual Fact: {rel['fact']}",
                                        "source": rel_source_info["source"],
                                        "page": rel_source_info["page"],
                                        "bbox": rel_source_info["bbox"],
                                        "page_width": rel_source_info.get("page_width"),
                                        "page_height": rel_source_info.get("page_height"),
                                        "graph_id": graph_id
                                    })
                        except:
                            pass

            logger.info(f"Search complete: Found {len(facts)} related facts")

            return SearchResult(
                facts=facts,
                edges=edges,
                nodes=nodes,
                query=query,
                total_count=len(facts)
            )

        except Exception as e:
            logger.warning(f"Graph search failed, degrading to local search: {str(e)}")
            return self._local_search(graph_id, query, limit, scope)

    def search_multi_graphs(
        self,
        graph_ids: List[str],
        query: str,
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        Multi-graph semantic search (searches across multiple graphs and merges results)

        Args:
            graph_ids: List of Graph IDs to search
            query: Search query
            limit: Total number of results to return (distributed across graphs)
            scope: Search scope, "edges" or "nodes" or "both"

        Returns:
            SearchResult with merged results from all graphs
        """
        logger.info(f"Multi-graph search: {len(graph_ids)} graphs, query={query[:50]}...")

        if not graph_ids:
            logger.warning("No graph IDs provided for multi-graph search")
            return SearchResult(facts=[], edges=[], nodes=[], query=query, total_count=0)

        # Calculate limit per graph
        limit_per_graph = max(1, limit // len(graph_ids))

        all_facts = []
        all_edges = []
        all_nodes = []
        seen_facts = set()
        seen_edge_uuids = set()
        seen_node_uuids = set()

        # Search each graph
        for graph_id in graph_ids:
            try:
                result = self.search_graph(
                    graph_id=graph_id,
                    query=query,
                    limit=limit_per_graph,
                    scope=scope
                )

                # Merge facts (deduplicate by text content)
                for fact_obj in result.facts:
                    fact_text = fact_obj.get("text", "")
                    if fact_text and fact_text not in seen_facts:
                        all_facts.append(fact_obj)
                        seen_facts.add(fact_text)

                # Merge edges (deduplicate by uuid)
                for edge in result.edges:
                    edge_uuid = edge.get('uuid', '')
                    if edge_uuid and edge_uuid not in seen_edge_uuids:
                        all_edges.append(edge)
                        seen_edge_uuids.add(edge_uuid)

                # Merge nodes (deduplicate by uuid)
                for node in result.nodes:
                    node_uuid = node.get('uuid', '')
                    if node_uuid and node_uuid not in seen_node_uuids:
                        all_nodes.append(node)
                        seen_node_uuids.add(node_uuid)

            except Exception as e:
                logger.warning(f"Failed to search graph {graph_id}: {str(e)}")
                continue

        # Trim to limit if needed
        all_facts = all_facts[:limit]
        all_edges = all_edges[:limit]
        all_nodes = all_nodes[:limit]

        logger.info(f"Multi-graph search complete: {len(all_facts)} facts from {len(graph_ids)} graphs")

        return SearchResult(
            facts=all_facts,
            edges=all_edges,
            nodes=all_nodes,
            query=query,
            total_count=len(all_facts)
        )

    def _local_search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        Local keyword matching search (fallback approach)
        """
        logger.info(f"Using local search: query={query[:30]}...")

        facts = []
        edges_result = []
        nodes_result = []

        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').replace('，', ' ').split() if len(w.strip()) > 1]

        def match_score(text: str) -> int:
            if not text:
                return 0
            text_lower = text.lower()
            if query_lower in text_lower:
                return 100
            score = 0
            for keyword in keywords:
                if keyword in text_lower:
                    score += 10
            return score

        try:
            if scope in ["edges", "both"]:
                all_edges = self.storage.get_all_edges(graph_id)
                scored_edges = []
                for edge in all_edges:
                    score = match_score(edge.get("fact", "")) + match_score(edge.get("name", ""))
                    if score > 0:
                        scored_edges.append((score, edge))

                scored_edges.sort(key=lambda x: x[0], reverse=True)

                for score, edge in scored_edges[:limit]:
                    fact = edge.get("fact", "")
                    if fact:
                        facts.append({
                            "text": fact,
                            "source": "Local Search",
                            "page": None,
                            "graph_id": graph_id
                        })
                    edges_result.append({
                        "uuid": edge.get("uuid", ""),
                        "name": edge.get("name", ""),
                        "fact": fact,
                        "source_node_uuid": edge.get("source_node_uuid", ""),
                        "target_node_uuid": edge.get("target_node_uuid", ""),
                    })

            if scope in ["nodes", "both"]:
                all_nodes = self.storage.get_all_nodes(graph_id)
                scored_nodes = []
                for node in all_nodes:
                    score = match_score(node.get("name", "")) + match_score(node.get("summary", ""))
                    if score > 0:
                        scored_nodes.append((score, node))

                scored_nodes.sort(key=lambda x: x[0], reverse=True)

                for score, node in scored_nodes[:limit]:
                    nodes_result.append({
                        "uuid": node.get("uuid", ""),
                        "name": node.get("name", ""),
                        "labels": node.get("labels", []),
                        "summary": node.get("summary", ""),
                    })
                    summary = node.get("summary", "")
                    if summary:
                        facts.append(f"[{node.get('name', '')}]: {summary}")

            logger.info(f"Local search complete: Found {len(facts)} related facts")

        except Exception as e:
            logger.error(f"Local search failed: {str(e)}")

        return SearchResult(
            facts=facts,
            edges=edges_result,
            nodes=nodes_result,
            query=query,
            total_count=len(facts)
        )

    def search_object_first(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        max_depth: int = 3,
        root_type: str = "Object",
    ) -> ObjectFirstSearchResult:
        """
        Root-node-first DFS 检索。

        检索策略：
        1. 首轮命中 root_type 节点（Object 或 Term，支持混合向量+关键词搜索）
        2. 从每个根节点出发，深度优先遍历图谱
        3. 每个根节点生成一条结果行（ObjectFirstRow）
        4. 对各行按相关性打分排序

        Args:
            graph_id: 图谱 ID
            query: 检索查询
            limit: 最多返回多少个根节点行
            max_depth: DFS 最大深度（默认 3）
            root_type: 根节点类型，"Object"（默认）或 "Term"

        Returns:
            ObjectFirstSearchResult，按根节点分组的 DFS 检索结果
        """
        valid_root_types = ["Object", "Term"]
        if root_type not in valid_root_types:
            root_type = "Object"
        logger.info(f"Root-node DFS search: graph_id={graph_id}, query={query[:50]}..., max_depth={max_depth}, root_type={root_type}")

        try:
            # Step 1: 搜索根节点（Object 或 Term）
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

    def _dfs_from_object(
        self,
        graph_id: str,
        object_uuid: str,
        object_data: Dict[str, Any],
        max_depth: int,
        seen_fact_texts: set,
        root_score: float = 0.0,
    ) -> ObjectFirstRow:
        """
        从一个 Object 节点执行优化的迭代 DFS 遍历，收集所有关联节点和边。

        遍历规则（Normative KG Schema）：
        - Object --OPERATES_ON--> Action
        - Action --HAS_CONDITION--> Condition
        - Action --MANDATES/RECOMMENDS/PROHIBITS--> 子Action/Component
        - Condition --TRIGGERS--> Action
        - 任意节点均可能被 Component/Section/Term 等节点引用

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

            # Add node to DFS path
            if node_data:
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

                # Add edge to traversal path
                traversal_edges.append(ObjectPathEdge(
                    uuid=edge_uuid,
                    name=edge_name,
                    fact=edge_fact,
                    source_node_uuid=src_uuid,
                    target_node_uuid=tgt_uuid,
                    depth=depth,
                ))

                # Collect neighbor for Phase 2 batch fetch (avoid recursive get_node call here)
                if neighbor_uuid and neighbor_uuid not in visited:
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
                        source_info = {"source": "Graph", "page": None, "bbox": None,
                                       "page_width": None, "page_height": None}
                        original_text = ""
                        if ep_ids:
                            try:
                                eps = self.storage.get_episodes(
                                    [ep_ids[0]] if isinstance(ep_ids, list) else [ep_ids]
                                )
                                if eps:
                                    meta = eps[0].get("metadata", {})
                                    source_info.update({
                                        "source": meta.get("source", "Graph"),
                                        "page": meta.get("page"),
                                        "bbox": meta.get("bbox"),
                                        "page_width": meta.get("page_width"),
                                        "page_height": meta.get("page_height"),
                                    })
                                    original_text = eps[0].get("text", "")
                            except Exception:
                                pass

                        facts.append({
                            "uuid": edge_uuid,
                            "text": fact_to_add,
                            "original_text": original_text,
                            "source": source_info["source"],
                            "page": source_info["page"],
                            "bbox": source_info["bbox"],
                            "page_width": source_info.get("page_width"),
                            "page_height": source_info.get("page_height"),
                            "graph_id": graph_id,
                            "source_node_uuid": src_uuid,
                            "target_node_uuid": tgt_uuid,
                            "relation_name": edge_name,
                            "traversal_depth": depth,
                            "similarity_score": root_score,  # Hybrid search score from root node
                        })

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
                    "MANDATES": f"强制要求: {neighbor_name}",
                    "RECOMMENDS": f"推荐: {neighbor_name}",
                    "PROHIBITS": f"禁止: {neighbor_name}",
                    "OPERATES_ON": f"操作对象: {neighbor_name}",
                    "HAS_CONDITION": f"前提条件: {neighbor_name}",
                    "APPLIES_TO": f"适用于: {neighbor_name}",
                    "IN_SITUATION": f"触发条件: {neighbor_name}",
                    "MENTIONS": f"提及: {neighbor_name}",
                }
                fact_to_add = rel_facts.get(edge_name, f"[{edge_name}] {neighbor_name}")

                norm = self.normalize_text(fact_to_add)
                if norm and norm not in seen_fact_texts:
                    seen_fact_texts.add(norm)
                    edge_uuid = edge.get("uuid", "")
                    ep_ids = edge.get("episode_ids", [])
                    # 优先取 neighbor_data（Clause 实体）的原文和 PDF 信息
                    source = neighbor_data.get("pdf_source") or "Graph"
                    page = neighbor_data.get("pdf_page")
                    bbox = neighbor_data.get("pdf_bbox")
                    page_width = neighbor_data.get("pdf_page_width")
                    page_height = neighbor_data.get("pdf_page_height")
                    original_text = neighbor_data.get("summary", "") or ""
                    # 回退：查 episode
                    if ep_ids and not original_text:
                        try:
                            eps = self.storage.get_episodes(
                                [ep_ids[0]] if isinstance(ep_ids, list) else [ep_ids]
                            )
                            if eps:
                                meta = eps[0].get("metadata", {})
                                source = meta.get("source", "Graph")
                                page = meta.get("page")
                                bbox = meta.get("bbox")
                                page_width = meta.get("page_width")
                                page_height = meta.get("page_height")
                                original_text = eps[0].get("text", "")
                        except Exception:
                            pass

                    facts.append({
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
                    })

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
    ) -> Dict[str, Dict[str, Any]]:
        """
        批量获取多个节点的 PDF 定位信息。

        策略：优先读取节点自身的 pdf_* 属性（快速路径），对缺失的节点回退查 Episode。

        Args:
            node_uuids: 节点 UUID 列表

        Returns:
            Dict[node_uuid -> pdf_info dict]
        """
        result: Dict[str, Dict[str, Any]] = {}

        if not node_uuids:
            return result

        # Step 1: Batch fetch all nodes
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

        # Step 2: Enrich partial nodes — try node's own episodes first, then Term->Clause
        for node_uuid in partial_uuids:
            if node_uuid in result:
                continue  # already complete from Step 1
            pdf_info: Dict[str, Any] = {
                "source": None, "page": None, "bbox": None,
                "page_width": None, "page_height": None, "episode_text": None,
            }
            try:
                # Try node's own episodes (MENTIONS relationship)
                node_eps = self.storage.get_node_episodes(node_uuid, limit=1)
                if node_eps:
                    ep = node_eps[0]
                    meta = ep.get("metadata", {})
                    pdf_info.update({
                        "source": ep.get("source") or meta.get("source"),
                        "page": ep.get("page") or meta.get("page"),
                        "bbox": meta.get("bbox"),
                        "page_width": meta.get("page_width") or meta.get("pageWidth"),
                        "page_height": meta.get("page_height") or meta.get("pageHeight"),
                        "episode_text": ep.get("text"),
                    })
                else:
                    # Term nodes: follow DEFINES to find Clause, then get its episodes
                    clause_eps = self.storage.get_term_clause_episodes(node_uuid, limit=1)
                    if clause_eps:
                        clause_ep = clause_eps[0]
                        meta = clause_ep.get("metadata", {})
                        pdf_info.update({
                            "source": clause_ep.get("source") or meta.get("source"),
                            "page": clause_ep.get("page") or meta.get("page"),
                            "bbox": meta.get("bbox"),
                            "page_width": meta.get("page_width") or meta.get("pageWidth"),
                            "page_height": meta.get("page_height") or meta.get("pageHeight"),
                            "episode_text": clause_ep.get("text"),
                        })
            except Exception:
                pass
            result[node_uuid] = pdf_info

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
        fact_count_max = max(len(r.facts) for r in rows) or 1
        query_lower = query.lower()

        for row in rows:
            obj_name = row.object_node.get("name", "").lower()
            name_score = 50 if query_lower in obj_name else (
                30 if any(kw in obj_name for kw in query_lower.split() if len(kw) > 1) else 0
            )
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


        """
        Local keyword matching search (fallback approach)
        """
        logger.info(f"Using local search: query={query[:30]}...")

        facts = []
        edges_result = []
        nodes_result = []

        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').replace('，', ' ').split() if len(w.strip()) > 1]

        def match_score(text: str) -> int:
            if not text:
                return 0
            text_lower = text.lower()
            if query_lower in text_lower:
                return 100
            score = 0
            for keyword in keywords:
                if keyword in text_lower:
                    score += 10
            return score

        try:
            if scope in ["edges", "both"]:
                all_edges = self.storage.get_all_edges(graph_id)
                scored_edges = []
                for edge in all_edges:
                    score = match_score(edge.get("fact", "")) + match_score(edge.get("name", ""))
                    if score > 0:
                        scored_edges.append((score, edge))

                scored_edges.sort(key=lambda x: x[0], reverse=True)

                for score, edge in scored_edges[:limit]:
                    fact = edge.get("fact", "")
                    if fact:
                        facts.append({
                            "text": fact,
                            "source": "Local Search",
                            "page": None,
                            "graph_id": graph_id
                        })
                    edges_result.append({
                        "uuid": edge.get("uuid", ""),
                        "name": edge.get("name", ""),
                        "fact": fact,
                        "source_node_uuid": edge.get("source_node_uuid", ""),
                        "target_node_uuid": edge.get("target_node_uuid", ""),
                    })

            if scope in ["nodes", "both"]:
                all_nodes = self.storage.get_all_nodes(graph_id)
                scored_nodes = []
                for node in all_nodes:
                    score = match_score(node.get("name", "")) + match_score(node.get("summary", ""))
                    if score > 0:
                        scored_nodes.append((score, node))

                scored_nodes.sort(key=lambda x: x[0], reverse=True)

                for score, node in scored_nodes[:limit]:
                    nodes_result.append({
                        "uuid": node.get("uuid", ""),
                        "name": node.get("name", ""),
                        "labels": node.get("labels", []),
                        "summary": node.get("summary", ""),
                    })
                    summary = node.get("summary", "")
                    if summary:
                        facts.append(f"[{node.get('name', '')}]: {summary}")

            logger.info(f"Local search complete: Found {len(facts)} related facts")

        except Exception as e:
            logger.error(f"Local search failed: {str(e)}")

        return SearchResult(
            facts=facts,
            edges=edges_result,
            nodes=nodes_result,
            query=query,
            total_count=len(facts)
        )

    def get_all_nodes(self, graph_id: str) -> List[NodeInfo]:
        """Get all nodes in the graph"""
        logger.info(f"Getting all nodes in graph {graph_id}...")

        raw_nodes = self.storage.get_all_nodes(graph_id)

        result = []
        for node in raw_nodes:
            result.append(NodeInfo(
                uuid=node.get("uuid", ""),
                name=node.get("name", ""),
                labels=node.get("labels", []),
                summary=node.get("summary", ""),
                attributes=node.get("attributes", {})
            ))

        logger.info(f"Retrieved {len(result)} nodes")
        return result

    def get_all_edges(self, graph_id: str, include_temporal: bool = True) -> List[EdgeInfo]:
        """Get all edges in the graph (with temporal information)"""
        logger.info(f"Getting all edges in graph {graph_id}...")

        raw_edges = self.storage.get_all_edges(graph_id)

        result = []
        for edge in raw_edges:
            edge_info = EdgeInfo(
                uuid=edge.get("uuid", ""),
                name=edge.get("name", ""),
                fact=edge.get("fact", ""),
                source_node_uuid=edge.get("source_node_uuid", ""),
                target_node_uuid=edge.get("target_node_uuid", "")
            )

            if include_temporal:
                edge_info.created_at = edge.get("created_at")
                edge_info.valid_at = edge.get("valid_at")
                edge_info.invalid_at = edge.get("invalid_at")
                edge_info.expired_at = edge.get("expired_at")

            result.append(edge_info)

        logger.info(f"Retrieved {len(result)} edges")
        return result

    def get_node_detail(self, node_uuid: str) -> Optional[NodeInfo]:
        """Get detailed information about a single node"""
        logger.info(f"Getting node details: {node_uuid[:8]}...")

        try:
            node = self.storage.get_node(node_uuid)
            if not node:
                return None

            return NodeInfo(
                uuid=node.get("uuid", ""),
                name=node.get("name", ""),
                labels=node.get("labels", []),
                summary=node.get("summary", ""),
                attributes=node.get("attributes", {})
            )
        except Exception as e:
            logger.error(f"Failed to get node details: {str(e)}")
            return None

    def get_node_edges(self, graph_id: str, node_uuid: str) -> List[EdgeInfo]:
        """
        Get all edges related to a node

        Optimized: uses storage.get_node_edges() (O(degree) Cypher)
        instead of loading ALL edges and filtering.
        """
        logger.info(f"Getting edges related to node {node_uuid[:8]}...")

        try:
            raw_edges = self.storage.get_node_edges(node_uuid)

            result = []
            for edge in raw_edges:
                result.append(EdgeInfo(
                    uuid=edge.get("uuid", ""),
                    name=edge.get("name", ""),
                    fact=edge.get("fact", ""),
                    source_node_uuid=edge.get("source_node_uuid", ""),
                    target_node_uuid=edge.get("target_node_uuid", ""),
                    created_at=edge.get("created_at"),
                    valid_at=edge.get("valid_at"),
                    invalid_at=edge.get("invalid_at"),
                    expired_at=edge.get("expired_at"),
                ))

            logger.info(f"Found {len(result)} edges related to the node")
            return result

        except Exception as e:
            logger.warning(f"Failed to get node edges: {str(e)}")
            return []

    def get_entities_by_type(
        self,
        graph_id: str,
        entity_type: str
    ) -> List[NodeInfo]:
        """Get entities by type"""
        logger.info(f"Getting entities of type {entity_type}...")

        # Use optimized label-based query from storage
        raw_nodes = self.storage.get_nodes_by_label(graph_id, entity_type)

        result = []
        for node in raw_nodes:
            result.append(NodeInfo(
                uuid=node.get("uuid", ""),
                name=node.get("name", ""),
                labels=node.get("labels", []),
                summary=node.get("summary", ""),
                attributes=node.get("attributes", {})
            ))

        logger.info(f"Found {len(result)} entities of type {entity_type}")
        return result

    def get_entity_summary(
        self,
        graph_id: str,
        entity_name: str
    ) -> Dict[str, Any]:
        """Get relationship summary for a specific entity"""
        logger.info(f"Getting relationship summary for entity {entity_name}...")

        search_result = self.search_graph(
            graph_id=graph_id,
            query=entity_name,
            limit=20
        )

        all_nodes = self.get_all_nodes(graph_id)
        entity_node = None
        for node in all_nodes:
            if node.name.lower() == entity_name.lower():
                entity_node = node
                break

        related_edges = []
        if entity_node:
            related_edges = self.get_node_edges(graph_id, entity_node.uuid)

        return {
            "entity_name": entity_name,
            "entity_info": entity_node.to_dict() if entity_node else None,
            "related_facts": search_result.facts,
            "related_edges": [e.to_dict() for e in related_edges],
            "total_relations": len(related_edges)
        }

    def get_graph_statistics(self, graph_id: str) -> Dict[str, Any]:
        """Get statistics for the graph"""
        logger.info(f"Getting statistics for graph {graph_id}...")

        nodes = self.get_all_nodes(graph_id)
        edges = self.get_all_edges(graph_id)

        entity_types = {}
        for node in nodes:
            for label in node.labels:
                if label not in ["Entity", "Node"]:
                    entity_types[label] = entity_types.get(label, 0) + 1

        relation_types = {}
        for edge in edges:
            relation_types[edge.name] = relation_types.get(edge.name, 0) + 1

        return {
            "graph_id": graph_id,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "entity_types": entity_types,
            "relation_types": relation_types
        }

    # ========== Core Retrieval Tools (Optimized) ==========

    def insight_forge(
        self,
        graph_id: str,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_sub_queries: int = 5
    ) -> InsightForgeResult:
        """
        [InsightForge - Deep Clue Retrieval via RRF & Graph Expansion]

        The most powerful retrieval function using a "Retrieve-then-Expand" strategy:
        1. Decompose query into specialized sub-queries (targeting clauses, parameters).
        2. Perform RRF hybrid search to find high-confidence "Seed Facts".
        3. Expand clues: Follow both semantic edges (REFERENCES) and physical edges (NEXT_EPISODE).
        4. Synthesize results into a logical reasoning chain.
        """
        logger.info(f"InsightForge starting deep clue discovery: {query[:50]}...")

        result = InsightForgeResult(
            query=query,
            simulation_requirement=simulation_requirement,
            sub_queries=[]
        )

        # Step 1: Sub-query generation (optimizing for engineering logic if applicable)
        sub_queries = self._generate_sub_queries(
            query=query,
            simulation_requirement=simulation_requirement,
            report_context=report_context,
            max_queries=max_sub_queries
        )
        result.sub_queries = sub_queries

        # Step 2: Seed Discovery using RRF
        seed_facts = []
        seed_edges = []
        seen_fact_texts = set()

        # Combine main query and sub-queries for broader coverage
        search_targets = [query] + sub_queries
        for q in search_targets:
            search_res = self.search_graph(graph_id, q, limit=10, scope="both")
            for f in search_res.facts:
                text = f.get("text", "")
                if text and text not in seen_fact_texts:
                    seed_facts.append(f)
                    seen_fact_texts.add(text)
            seed_edges.extend(search_res.edges)

        # Step 3: Clue Expansion (Graph Walking)
        # We track entities and episodes to find context clues
        expanded_facts = []
        relationship_chains = []
        entity_insights = []
        node_map = {}

        # 3.1 Trace Semantic Chains (Entity-based)
        entity_uuids = {e["source_node_uuid"] for e in seed_edges if e.get("source_node_uuid")}
        entity_uuids.update({e["target_node_uuid"] for e in seed_edges if e.get("target_node_uuid")})

        for uuid in list(entity_uuids)[:20]: # Limit expansion
            node = self.get_node_detail(uuid)
            if node:
                node_map[uuid] = node
                # Find clues related to this node (Parameters, Constraints)
                node_rels = self.get_node_edges(graph_id, uuid)
                for rel in node_rels:
                    if rel.fact and rel.fact not in seen_fact_texts:
                        expanded_facts.append({
                            "text": f"[Clue from {node.name}]: {rel.fact}",
                            "source": "Graph Expansion"
                        })
                        seen_fact_texts.add(rel.fact)

                    # Build relationship chain strings
                    src_name = node.name
                    tgt_name = "Unknown" # In a real implementation, we'd fetch the other end too
                    relationship_chains.append(f"{src_name} --[{rel.name}]--> {tgt_name}")

        # 3.2 Trace Physical Logic (Episode-based context already handled by viewDocument and get_episodes)
        # In InsightForge, we ensure facts already include context via the updated search_graph logic

        result.semantic_facts = [f.get("text") for f in seed_facts] + [f.get("text") for f in expanded_facts]
        result.total_facts = len(result.semantic_facts)
        result.relationship_chains = list(set(relationship_chains))
        result.total_relationships = len(result.relationship_chains)

        logger.info(f"InsightForge complete: Found {len(seed_facts)} seeds and {len(expanded_facts)} clues.")
        return result

    def _generate_sub_queries(
        self,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_queries: int = 5
    ) -> List[str]:
        """Use LLM to generate sub-questions"""
        system_prompt = """You are a professional question analysis expert. Your task is to decompose a complex question into multiple sub-questions that can be independently observed in a simulated world.

Requirements:
1. Each sub-question should be specific enough to find related Agent behavior or events in the simulated world
2. Sub-questions should cover different dimensions of the original question (e.g., who, what, why, how, when, where)
3. Sub-questions should be relevant to the simulation scenario
4. Return in JSON format: {"sub_queries": ["sub-question 1", "sub-question 2", ...]}"""

        user_prompt = f"""Simulation requirement background:
{simulation_requirement}

{f"Report context: {report_context[:500]}" if report_context else ""}

Please decompose the following question into {max_queries} sub-questions:
{query}

Return the sub-questions as a JSON list."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )

            sub_queries = response.get("sub_queries", [])
            return [str(sq) for sq in sub_queries[:max_queries]]

        except Exception as e:
            logger.warning(f"Failed to generate sub-questions: {str(e)}, using default sub-questions")
            return [
                query,
                f"Main participants in {query}",
                f"Causes and impacts of {query}",
                f"Development process of {query}"
            ][:max_queries]

    def panorama_search(
        self,
        graph_id: str,
        query: str,
        include_expired: bool = True,
        limit: int = 50
    ) -> PanoramaResult:
        """
        [PanoramaSearch - Breadth Search]

        Get a comprehensive panoramic view, including all related content and historical/expired information.
        """
        logger.info(f"PanoramaSearch breadth search: {query[:50]}...")

        result = PanoramaResult(query=query)

        # Get all nodes
        all_nodes = self.get_all_nodes(graph_id)
        node_map = {n.uuid: n for n in all_nodes}
        result.all_nodes = all_nodes
        result.total_nodes = len(all_nodes)

        # Get all edges (including temporal information)
        all_edges = self.get_all_edges(graph_id, include_temporal=True)
        result.all_edges = all_edges
        result.total_edges = len(all_edges)

        # Categorize facts
        active_facts = []
        historical_facts = []

        for edge in all_edges:
            if not edge.fact:
                continue

            source_name = node_map.get(edge.source_node_uuid, NodeInfo('', '', [], '', {})).name or edge.source_node_uuid[:8]
            target_name = node_map.get(edge.target_node_uuid, NodeInfo('', '', [], '', {})).name or edge.target_node_uuid[:8]

            is_historical = edge.is_expired or edge.is_invalid

            if is_historical:
                valid_at = edge.valid_at or "Unknown"
                invalid_at = edge.invalid_at or edge.expired_at or "Unknown"
                fact_with_time = f"[{valid_at} - {invalid_at}] {edge.fact}"
                historical_facts.append(fact_with_time)
            else:
                active_facts.append(edge.fact)

        # Sort by relevance based on query
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').replace('，', ' ').split() if len(w.strip()) > 1]

        def relevance_score(fact: str) -> int:
            fact_lower = fact.lower()
            score = 0
            if query_lower in fact_lower:
                score += 100
            for kw in keywords:
                if kw in fact_lower:
                    score += 10
            return score

        active_facts.sort(key=relevance_score, reverse=True)
        historical_facts.sort(key=relevance_score, reverse=True)

        result.active_facts = active_facts[:limit]
        result.historical_facts = historical_facts[:limit] if include_expired else []
        result.active_count = len(active_facts)
        result.historical_count = len(historical_facts)

        logger.info(f"PanoramaSearch complete: {result.active_count} valid, {result.historical_count} historical")
        return result

    def quick_search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10
    ) -> SearchResult:
        """
        [QuickSearch - Simple Search]
        Fast and lightweight retrieval tool.
        """
        logger.info(f"QuickSearch simple search: {query[:50]}...")

        result = self.search_graph(
            graph_id=graph_id,
            query=query,
            limit=limit,
            scope="edges"
        )

        logger.info(f"QuickSearch complete: {result.total_count} results")
        return result
