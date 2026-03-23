"""
SearchService — hybrid search (vector + keyword) over Neo4j graph data.

Replaces Zep Cloud's built-in search with reranker.
Scoring: 0.7 * vector_score + 0.3 * keyword_score (BM25 via fulltext index).
"""

import logging
from typing import List, Dict, Any, Optional

from neo4j import Session as Neo4jSession

from .embedding_service import EmbeddingService

logger = logging.getLogger('mirofish.search')

# Cypher for vector search on edges (facts)
_VECTOR_SEARCH_EDGES = """
CALL db.index.vector.queryRelationships('fact_embedding', $limit, $query_vector)
YIELD relationship, score
WHERE relationship.graph_id = $graph_id
RETURN relationship AS r, score
ORDER BY score DESC
LIMIT $limit
"""

# Cypher for vector search on nodes (entities)
_VECTOR_SEARCH_NODES = """
CALL db.index.vector.queryNodes('entity_embedding', $limit, $query_vector)
YIELD node, score
WHERE node.graph_id = $graph_id
RETURN node AS n, score
ORDER BY score DESC
LIMIT $limit
"""

# Cypher for fulltext (BM25) search on edges
_FULLTEXT_SEARCH_EDGES = """
CALL db.index.fulltext.queryRelationships('fact_fulltext', $query_text)
YIELD relationship, score
WHERE relationship.graph_id = $graph_id
RETURN relationship AS r, score
ORDER BY score DESC
LIMIT $limit
"""

# Cypher for fulltext search on nodes
_FULLTEXT_SEARCH_NODES = """
CALL db.index.fulltext.queryNodes('entity_fulltext', $query_text)
YIELD node, score
WHERE node.graph_id = $graph_id
RETURN node AS n, score
ORDER BY score DESC
LIMIT $limit
"""

# Cypher for vector search on episodes (raw text chunks)
_VECTOR_SEARCH_EPISODES = """
CALL db.index.vector.queryNodes('episode_embedding', $limit, $query_vector)
YIELD node, score
WHERE node.graph_id = $graph_id
RETURN node AS e, score
ORDER BY score DESC
LIMIT $limit
"""

# Cypher for fulltext search on episodes
_FULLTEXT_SEARCH_EPISODES = """
CALL db.index.fulltext.queryNodes('episode_fulltext', $query_text)
YIELD node, score
WHERE node.graph_id = $graph_id
RETURN node AS e, score
ORDER BY score DESC
LIMIT $limit
"""


class SearchService:
    """Hybrid search combining vector similarity and keyword matching."""

    VECTOR_WEIGHT = 0.7
    KEYWORD_WEIGHT = 0.3

    def __init__(self, embedding_service: EmbeddingService):
        self.embedding = embedding_service

    def search_episodes(
        self,
        session: Neo4jSession,
        graph_id: str,
        query: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search raw text chunks (episodes)."""
        query_vector = self.embedding.embed(query)

        # Vector search
        vector_results = self._run_episode_vector_search(
            session, graph_id, query_vector, limit * 2
        )

        # Keyword search
        keyword_results = self._run_episode_keyword_search(
            session, graph_id, query, limit * 2
        )

        # Merge
        return self._merge_results(vector_results, keyword_results, key="uuid", limit=limit)

    def _run_episode_vector_search(
        self, session: Neo4jSession, graph_id: str, query_vector: List[float], limit: int
    ) -> List[Dict[str, Any]]:
        try:
            result = session.run(
                _VECTOR_SEARCH_EPISODES,
                graph_id=graph_id,
                query_vector=query_vector,
                limit=limit,
            )
            return [
                {**dict(record["e"]), "uuid": record["e"]["uuid"], "_score": record["score"]}
                for record in result
            ]
        except Exception as e:
            logger.warning(f"Vector episode search failed: {e}")
            return []

    def _run_episode_keyword_search(
        self, session: Neo4jSession, graph_id: str, query: str, limit: int
    ) -> List[Dict[str, Any]]:
        try:
            safe_query = self._escape_lucene(query)
            result = session.run(
                _FULLTEXT_SEARCH_EPISODES,
                graph_id=graph_id,
                query_text=safe_query,
                limit=limit,
            )
            return [
                {**dict(record["e"]), "uuid": record["e"]["uuid"], "_score": record["score"]}
                for record in result
            ]
        except Exception as e:
            logger.warning(f"Keyword episode search failed: {e}")
            return []

    def search_edges(
        self,
        session: Neo4jSession,
        graph_id: str,
        query: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Search edges (facts/relations) using hybrid scoring.

        Returns list of dicts with edge properties + 'score'.
        """
        query_vector = self.embedding.embed(query)

        # Vector search
        vector_results = self._run_edge_vector_search(
            session, graph_id, query_vector, limit * 2
        )

        # Keyword search
        keyword_results = self._run_edge_keyword_search(
            session, graph_id, query, limit * 2
        )

        # Merge and rank
        merged = self._merge_results(
            vector_results, keyword_results, key="uuid", limit=limit
        )
        return merged

    def search_nodes(
        self,
        session: Neo4jSession,
        graph_id: str,
        query: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Search nodes (entities) using hybrid scoring.

        Returns list of dicts with node properties + 'score'.
        """
        query_vector = self.embedding.embed(query)

        vector_results = self._run_node_vector_search(
            session, graph_id, query_vector, limit * 2
        )

        keyword_results = self._run_node_keyword_search(
            session, graph_id, query, limit * 2
        )

        merged = self._merge_results(
            vector_results, keyword_results, key="uuid", limit=limit
        )
        return merged

    def _run_edge_vector_search(
        self, session: Neo4jSession, graph_id: str, query_vector: List[float], limit: int
    ) -> List[Dict[str, Any]]:
        """Run vector similarity search on edge fact_embedding."""
        try:
            result = session.run(
                _VECTOR_SEARCH_EDGES,
                graph_id=graph_id,
                query_vector=query_vector,
                limit=limit,
            )
            return [
                {**dict(record["r"]), "uuid": record["r"]["uuid"], "_score": record["score"]}
                for record in result
            ]
        except Exception as e:
            logger.warning(f"Vector edge search failed (index may not exist yet): {e}")
            return []

    def _run_edge_keyword_search(
        self, session: Neo4jSession, graph_id: str, query: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Run fulltext (BM25) search on edge fact + name."""
        try:
            # Escape special Lucene characters in query
            safe_query = self._escape_lucene(query)
            result = session.run(
                _FULLTEXT_SEARCH_EDGES,
                graph_id=graph_id,
                query_text=safe_query,
                limit=limit,
            )
            return [
                {**dict(record["r"]), "uuid": record["r"]["uuid"], "_score": record["score"]}
                for record in result
            ]
        except Exception as e:
            logger.warning(f"Keyword edge search failed: {e}")
            return []

    def _run_node_vector_search(
        self, session: Neo4jSession, graph_id: str, query_vector: List[float], limit: int
    ) -> List[Dict[str, Any]]:
        """Run vector similarity search on entity embedding."""
        try:
            result = session.run(
                _VECTOR_SEARCH_NODES,
                graph_id=graph_id,
                query_vector=query_vector,
                limit=limit,
            )
            return [
                {**dict(record["n"]), "uuid": record["n"]["uuid"], "_score": record["score"]}
                for record in result
            ]
        except Exception as e:
            logger.warning(f"Vector node search failed: {e}")
            return []

    def _run_node_keyword_search(
        self, session: Neo4jSession, graph_id: str, query: str, limit: int
    ) -> List[Dict[str, Any]]:
        """Run fulltext search on entity name + summary."""
        try:
            safe_query = self._escape_lucene(query)
            result = session.run(
                _FULLTEXT_SEARCH_NODES,
                graph_id=graph_id,
                query_text=safe_query,
                limit=limit,
            )
            return [
                {**dict(record["n"]), "uuid": record["n"]["uuid"], "_score": record["score"]}
                for record in result
            ]
        except Exception as e:
            logger.warning(f"Keyword node search failed: {e}")
            return []

    def _merge_results(
        self,
        vector_results: List[Dict[str, Any]],
        keyword_results: List[Dict[str, Any]],
        key: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        """
        Merge vector and keyword results using Reciprocal Rank Fusion (RRF).
        RRF is robust against different score scales and prioritizes items
        that appear high in both result sets.
        """
        k_rrf = 60  # Standard constant for RRF

        # Map to store combined info and RRF score
        combined_map: Dict[str, Dict[str, Any]] = {}

        # Process vector results (rank is index + 1)
        for rank, res in enumerate(vector_results, 1):
            uid = res[key]
            if uid not in combined_map:
                combined_map[uid] = {k: v for k, v in res.items() if k != "_score"}
                combined_map[uid]["rrf_score"] = 0.0
            combined_map[uid]["rrf_score"] += 1.0 / (k_rrf + rank)

        # Process keyword results
        for rank, res in enumerate(keyword_results, 1):
            uid = res[key]
            if uid not in combined_map:
                combined_map[uid] = {k: v for k, v in res.items() if k != "_score"}
                combined_map[uid]["rrf_score"] = 0.0
            combined_map[uid]["rrf_score"] += 1.0 / (k_rrf + rank)

        # Sort by RRF score descending
        sorted_items = sorted(
            combined_map.values(),
            key=lambda x: x["rrf_score"],
            reverse=True
        )

        # Return top results with normalized score
        results = sorted_items[:limit]
        if results:
            max_rrf = results[0]["rrf_score"]
            for r in results:
                r["score"] = r["rrf_score"] / max_rrf

        return results

    @staticmethod
    def _escape_lucene(query: str) -> str:
        """Escape special Lucene query characters."""
        special = r'+-&|!(){}[]^"~*?:\/'
        result = []
        for ch in query:
            if ch in special:
                result.append('\\')
            result.append(ch)
        return ''.join(result)
