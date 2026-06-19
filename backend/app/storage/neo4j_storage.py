"""
Neo4jStorage — Neo4j Community Edition implementation of GraphStorage.

Replaces all Zep Cloud API calls with local Neo4j Cypher queries.
Includes: CRUD, NER/RE-based text ingestion, hybrid search, retry logic.
"""

import json
import time
import uuid
import hashlib
import logging
import traceback
import concurrent.futures
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Callable, Union, Tuple, TYPE_CHECKING

from neo4j import GraphDatabase, Session as Neo4jSession
from neo4j.exceptions import (
    TransientError,
    ServiceUnavailable,
    SessionExpired,
)

from ..config import Config
from .graph_storage import GraphStorage
from .embedding_service import EmbeddingService
from .ner_extractor import NERExtractor
from .search_service import SearchService
from . import neo4j_schema

logger = logging.getLogger('mirofish.neo4j_storage')


# ============================================================
# Neo4j Label 白名单（防止 Cypher 注入）
# ============================================================

# Entity/Topic/Clause 节点支持的类型标签
VALID_NODE_LABELS = frozenset({
    "Term", "Entity", "Component", "Action", "Condition", "Object",
    "Topic", "Clause", "Image", "Table",
})

# 用于验证并返回安全标签的辅助函数
def _safe_label(label: str, allowed: frozenset = VALID_NODE_LABELS) -> Optional[str]:
    """
    验证标签是否在白名单中，返回安全标签或 None。
    防止 Cypher 注入：确保标签名不包含特殊字符且在白名单中。
    """
    if not label or not isinstance(label, str):
        return None
    # 验证标签只包含字母数字，且在白名单中
    safe_label = label.strip()
    if not safe_label.isalnum():
        return None
    if safe_label not in allowed:
        return None
    return safe_label



class Neo4jStorage(GraphStorage):
    """Neo4j CE implementation of the GraphStorage interface."""

    MAX_RETRIES = 3
    RETRY_DELAY_BASE = 1  # seconds

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        embedding_service: Optional[EmbeddingService] = None,
        ner_extractor: Optional[NERExtractor] = None,
    ):
        self._uri = uri or Config.NEO4J_URI
        self._user = user or Config.NEO4J_USER
        self._password = password or Config.NEO4J_PASSWORD

        self._driver = GraphDatabase.driver(
            self._uri,
            auth=(self._user, self._password),
            max_connection_lifetime=3600,
            max_connection_pool_size=50,
            connection_acquisition_timeout=60,  # 60秒获取连接超时
        )
        self._embedding = embedding_service or EmbeddingService()
        self._ner = ner_extractor or NERExtractor()
        self._search = SearchService(self._embedding)

        # Lazy schema init: defer to first use instead of blocking startup
        self._schema_initialized = False

    def _lazy_init_schema(self):
        """Lazily initialize schema on first database access."""
        if self._schema_initialized:
            return
        try:
            self._ensure_schema()
            self._schema_initialized = True
        except Exception as e:
            logger.warning(f"Schema initialization deferred to first query: {e}")
            self._schema_initialized = True  # Mark as attempted to prevent repeated failures

    def close(self):
        """Close the Neo4j driver connection."""
        self._driver.close()

    @property
    def driver(self) -> GraphDatabase.driver:
        """Expose the Neo4j driver. Triggers lazy schema init on first access."""
        self._lazy_init_schema()
        return self._driver

    def _ensure_schema(self):
        """Create indexes and constraints if they don't exist."""
        logger.info("=== Starting schema initialization ===")
        with self._driver.session() as session:
            # Check Neo4j version for vector index compatibility
            self._check_neo4j_version(session)

            # First, check existing indexes to avoid redundant operations
            existing_indexes = self._get_existing_indexes(session)
            logger.info(f"Existing indexes found: {list(existing_indexes.keys())}")

            # Debug: Directly query for any index containing 'embed'
            try:
                embed_check = session.run("SHOW INDEXES WHERE name CONTAINS 'embed'")
                embed_indexes = [dict(r) for r in embed_check]
                logger.info(f"[INDEX DEBUG] Indexes containing 'embed': {embed_indexes}")
            except Exception as e:
                logger.warning(f"[INDEX DEBUG] Could not query embed indexes: {e}")

            # Separate queries into critical (constraints, regular indexes) and optional (vector indexes)
            critical_queries = [
                neo4j_schema.CREATE_GRAPH_UUID_CONSTRAINT,
                neo4j_schema.CREATE_ENTITY_UUID_CONSTRAINT,
                neo4j_schema.CREATE_EPISODE_UUID_CONSTRAINT,
                neo4j_schema.CREATE_DOCUMENT_UUID_CONSTRAINT,
                neo4j_schema.CREATE_PAGE_UUID_CONSTRAINT,
                neo4j_schema.CREATE_TOPIC_UUID_CONSTRAINT,
                neo4j_schema.CREATE_CLAUSE_UUID_CONSTRAINT,
                neo4j_schema.CREATE_ENTITY_GRAPH_ID_INDEX,
                neo4j_schema.CREATE_ENTITY_NAME_LOWER_INDEX,
                neo4j_schema.CREATE_DOC_GRAPH_ID_INDEX,
                neo4j_schema.CREATE_PAGE_GRAPH_ID_INDEX,
                neo4j_schema.CREATE_EPISODE_GRAPH_ID_INDEX,
                neo4j_schema.CREATE_EPISODE_SOURCE_INDEX,
                neo4j_schema.CREATE_EPISODE_CHUNK_INDEX,
                neo4j_schema.CREATE_TOPIC_GRAPH_ID_INDEX,
                neo4j_schema.CREATE_TOPIC_CLAUSE_ID_INDEX,
                neo4j_schema.CREATE_CLAUSE_GRAPH_ID_INDEX,
                neo4j_schema.CREATE_CLAUSE_CLAUSE_ID_INDEX,
                neo4j_schema.CREATE_IMAGE_UUID_CONSTRAINT,
                neo4j_schema.CREATE_IMAGE_GRAPH_ID_INDEX,
                neo4j_schema.CREATE_TABLE_UUID_CONSTRAINT,
                neo4j_schema.CREATE_TABLE_GRAPH_ID_INDEX,
            ]

            # 1. Create critical indexes first
            for query in critical_queries:
                try:
                    session.run(query)
                    logger.debug(f"Schema query executed: {query[:50]}...")
                except Exception as e:
                    logger.warning(f"Critical schema query failed: {e}")

            # 2. Create vector indexes (require Neo4j 5.11+)
            vector_index_queries = [
                ("entity_embedding", neo4j_schema.get_entity_vector_index_query(Config.EMBEDDING_DIMENSION)),
                ("episode_embedding", neo4j_schema.get_episode_vector_index_query(Config.EMBEDDING_DIMENSION)),
                ("fact_embedding", neo4j_schema.get_relation_vector_index_query(Config.EMBEDDING_DIMENSION)),
                ("topic_embedding", neo4j_schema.get_topic_vector_index_query(Config.EMBEDDING_DIMENSION)),
                ("clause_embedding", neo4j_schema.get_clause_vector_index_query(Config.EMBEDDING_DIMENSION)),
            ]

            for index_name, query in vector_index_queries:
                try:
                    if index_name not in existing_indexes:
                        logger.info(f"Creating vector index '{index_name}'...")
                        logger.info(f"[VECTOR CREATE] Query:\n{query.strip()}")
                        session.run(query)
                        logger.info(f"✅ Vector index '{index_name}' created/verified")
                    else:
                        logger.info(f"⏭️ Vector index '{index_name}' already exists")
                except Exception as e:
                    error_msg = str(e)
                    error_code = getattr(e, 'code', None)
                    error_description = getattr(e, 'message', str(e))

                    logger.warning(f"❌ Vector index '{index_name}' creation failed:")
                    logger.warning(f"   Error code: {error_code}")
                    logger.warning(f"   Error message: {error_description}")

                    # Provide helpful troubleshooting info based on error type
                    if "VECTOR" in error_msg.upper() or "Unable to create vector index" in error_msg:
                        logger.warning(f"   💡 Hint: Vector indexes require Neo4j 5.11+ with vector plugin enabled")
                        logger.warning(f"   💡 Solution: Check neo4j.conf for 'dbms.security.procedures.unrestricted'")
                        logger.warning(f"   💡 Or run: CALL dbms.security.procedures.allowlist()")
                    elif "index with that name already exists" in error_msg.lower():
                        logger.warning(f"   💡 Index may exist with different name. Check SHOW INDEXES")
                    elif "Invalid input" in error_msg or "Syntax error" in error_msg:
                        logger.warning(f"   💡 Possible syntax error in CREATE INDEX statement")
                        logger.warning(f"   💡 Verify Neo4j version supports vector indexes")

                    logger.warning(f"   Vector search will be disabled for this index")

            # 3. Create fulltext indexes (with automatic upgrade to CJK analyzer)
            fulltext_queries = [
                ("entity_fulltext", neo4j_schema.CREATE_ENTITY_FULLTEXT_INDEX),
                ("fact_fulltext", neo4j_schema.CREATE_FACT_FULLTEXT_INDEX),
                ("episode_fulltext", neo4j_schema.CREATE_EPISODE_FULLTEXT_INDEX),
                ("topic_fulltext", neo4j_schema.CREATE_TOPIC_FULLTEXT_INDEX),
                ("clause_fulltext", neo4j_schema.CREATE_CLAUSE_FULLTEXT_INDEX),
            ]

            # Fetch analyzer details for existing fulltext indexes
            analyzer_map = {}
            try:
                analyzer_check = session.run(
                    "SHOW INDEXES YIELD name, type, options WHERE type = 'FULLTEXT' RETURN name, options.indexConfig['fulltext.analyzer'] AS analyzer"
                )
                for record in analyzer_check:
                    idx_name = record.get("name")
                    idx_analyzer = record.get("analyzer")
                    if idx_name:
                        analyzer_map[idx_name] = idx_analyzer
            except Exception as e:
                logger.warning(f"Could not retrieve fulltext index analyzer info: {e}")

            for index_name, query in fulltext_queries:
                try:
                    # If index exists but does NOT use CJK analyzer, drop it first to force upgrade
                    if index_name in existing_indexes:
                        current_analyzer = analyzer_map.get(index_name)
                        if current_analyzer != "cjk":
                            logger.info(f"🔄 Fulltext index '{index_name}' uses analyzer '{current_analyzer}'. Upgrading to CJK...")
                            session.run(f"DROP INDEX {index_name} IF EXISTS")
                            # Remove from existing_indexes map so it triggers creation below
                            existing_indexes.pop(index_name, None)

                    if index_name not in existing_indexes:
                        session.run(query)
                        logger.info(f"✅ Fulltext index '{index_name}' created/verified with CJK analyzer")
                    else:
                        logger.info(f"⏭️ Fulltext index '{index_name}' already exists (using CJK analyzer)")
                except Exception as e:
                    logger.warning(f"❌ Fulltext index '{index_name}' creation/upgrade failed: {e}")

            # 4. Verify all required indexes after creation
            self._verify_indexes(session)

            # 5. Refresh index status cache so search_service knows about new indexes
            _index_status._checked = False
            _index_status.check_indexes(session)

    def _check_neo4j_version(self, session):
        """Check Neo4j version for vector index compatibility."""
        try:
            result = session.run("CALL dbms.components() YIELD name, versions RETURN name, versions")
            for record in result:
                if record["name"] == "Neo4j Kernel":
                    versions = record["versions"]
                    if versions:
                        version_str = versions[0]
                        logger.info(f"Neo4j version: {version_str}")
                        # Parse major.minor version
                        major, minor = map(int, version_str.split('.')[:2])
                        if major < 5 or (major == 5 and minor < 11):
                            logger.warning(
                                f"⚠️ Neo4j {version_str} detected. "
                                f"Vector indexes require Neo4j 5.11 or later. "
                                f"Semantic search will be disabled."
                            )
                        else:
                            logger.info("✅ Neo4j version supports vector indexes")
                        break
        except Exception as e:
            logger.warning(f"Could not determine Neo4j version: {e}")

    def _parse_json_safe(self, json_str: str, default: Any = None) -> Any:
        """
        安全解析 JSON 字符串，失败时返回默认值并记录日志。

        Args:
            json_str: JSON 字符串
            default: 解析失败时返回的默认值

        Returns:
            解析后的对象，或默认值
        """
        if not json_str:
            return default if default is not None else {}
        try:
            return json.loads(json_str)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"JSON parse error: {e}")
            return default if default is not None else {}

    def _get_existing_indexes(self, session) -> Dict[str, str]:
        """Get existing indexes from Neo4j."""
        try:
            result = session.run("SHOW INDEXES")
            indexes = {}
            for record in result:
                # Log raw record for debugging
                logger.debug(f"[INDEX DEBUG] Raw record: {dict(record)}")

                # Handle different Neo4j versions (index name might be in different fields)
                index_name = record.get("name") or record.get("indexName", "") or record.get("id", "")
                index_type = str(record.get("type", record.get("indexType", "")))
                if index_name:
                    indexes[index_name] = index_type
                    logger.debug(f"[INDEX DEBUG] Found index: name='{index_name}', type='{index_type}'")

            logger.info(f"[INDEX DEBUG] All existing indexes: {indexes}")
            return indexes
        except Exception as e:
            logger.warning(f"Failed to query existing indexes: {e}")
            return {}

    def _verify_indexes(self, session):
        """Verify critical indexes exist and log status."""
        required_indexes = [
            "graph_uuid", "entity_uuid", "episode_uuid", "topic_uuid", "clause_uuid",
            "entity_graph_id", "entity_name_lower", "topic_graph_id", "topic_clause_id",
            "clause_graph_id", "clause_clause_id",
            "image_uuid", "image_graph_id",
            "table_uuid", "table_graph_id",
            "entity_embedding", "episode_embedding", "fact_embedding",
            "topic_embedding", "clause_embedding",
        ]

        existing = self._get_existing_indexes(session)
        existing_names = set(existing.keys())

        for idx_name in required_indexes:
            if idx_name in existing_names:
                logger.info(f"✅ Index '{idx_name}' is ready")
            else:
                logger.warning(f"⚠️ Index '{idx_name}' is MISSING - some features may not work")

    # ----------------------------------------------------------------
    # Retry wrapper
    # ----------------------------------------------------------------

    def _call_with_retry(self, func, *args, **kwargs):
        """
        Execute a function with retry on Neo4j transient errors.
        Replaces 3 different retry patterns from the Zep codebase.
        """
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except (TransientError, ServiceUnavailable, SessionExpired) as e:
                last_error = e
                wait = self.RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning(
                    f"Neo4j transient error (attempt {attempt + 1}/{self.MAX_RETRIES}), "
                    f"retrying in {wait}s: {e}"
                )
                time.sleep(wait)
            except Exception:
                raise

        raise last_error  # type: ignore

    # ----------------------------------------------------------------
    # Graph lifecycle
    # ----------------------------------------------------------------

    def create_graph(self, name: str, description: str = "") -> str:
        graph_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        def _create(tx):
            tx.run(
                """
                CREATE (g:Graph {
                    graph_id: $graph_id,
                    name: $name,
                    description: $description,
                    ontology_json: '{}',
                    created_at: $created_at
                })
                """,
                graph_id=graph_id,
                name=name,
                description=description,
                created_at=now,
            )

        with self._driver.session() as session:
            self._call_with_retry(session.execute_write, _create)

        logger.info(f"Created graph '{name}' with id {graph_id}")
        return graph_id

    def delete_graph(self, graph_id: str) -> None:
        def _delete(tx):
            # Delete all entities and their relationships
            tx.run(
                "MATCH (n {graph_id: $gid}) DETACH DELETE n",
                gid=graph_id,
            )
            # Delete graph node
            tx.run(
                "MATCH (g:Graph {graph_id: $gid}) DELETE g",
                gid=graph_id,
            )

        with self._driver.session() as session:
            self._call_with_retry(session.execute_write, _delete)
        logger.info(f"Deleted graph {graph_id}")

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]) -> None:
        def _set(tx):
            tx.run(
                """
                MATCH (g:Graph {graph_id: $gid})
                SET g.ontology_json = $ontology_json
                """,
                gid=graph_id,
                ontology_json=json.dumps(ontology, ensure_ascii=False),
            )

        with self._driver.session() as session:
            self._call_with_retry(session.execute_write, _set)


    def get_node(self, uuid: str) -> Optional[Dict[str, Any]]:
        def _read(tx):
            result = tx.run(
                """
                MATCH (n) WHERE n.uuid = $uuid AND (n:Entity OR n:Topic OR n:Clause)
                RETURN n, labels(n) AS labels
                """,
                uuid=uuid,
            )
            record = result.single()
            if record:
                return self._node_to_dict(record["n"], record["labels"])
            return None

        with self._driver.session() as session:
            return self._call_with_retry(session.execute_read, _read)

    def get_node_edges(self, node_uuid: str) -> List[Dict[str, Any]]:
        """O(1) Cypher — NOT full scan + filter like the old Zep code."""
        def _read(tx):
            result = tx.run(
                """
                MATCH (n {uuid: $uuid})-[r]-(m)
                WHERE n:Entity OR n:Topic OR n:Clause
                RETURN r, startNode(r).uuid AS src_uuid, endNode(r).uuid AS tgt_uuid
                """,
                uuid=node_uuid,
            )
            return [
                self._edge_to_dict(record["r"], record["src_uuid"], record["tgt_uuid"])
                for record in result
            ]

        with self._driver.session() as session:
            return self._call_with_retry(session.execute_read, _read)

    def get_node_outgoing_edges(self, node_uuid: str) -> List[Dict[str, Any]]:
        """获取节点的所有出边（node → neighbor，按语义方向）"""
        def _read(tx):
            result = tx.run(
                """
                MATCH (n {uuid: $uuid})-[r]->(m)
                WHERE n:Entity OR n:Topic OR n:Clause
                RETURN r, startNode(r).uuid AS src_uuid, endNode(r).uuid AS tgt_uuid
                """,
                uuid=node_uuid,
            )
            return [
                self._edge_to_dict(record["r"], record["src_uuid"], record["tgt_uuid"])
                for record in result
            ]

        with self._driver.session() as session:
            return self._call_with_retry(session.execute_read, _read)

    def get_nodes_batch(self, node_uuids: List[str]) -> Dict[str, Dict[str, Any]]:
        """批量获取多个节点数据（Entity, Topic, Clause）"""
        if not node_uuids:
            return {}
        def _read(tx):
            result = tx.run(
                """
                MATCH (n) WHERE n.uuid IN $uuids
                AND (n:Entity OR n:Topic OR n:Clause)
                RETURN n, labels(n) AS labels
                """,
                uuids=node_uuids,
            )
            return {
                record["n"]["uuid"]: self._node_to_dict(record["n"], record["labels"])
                for record in result
            }
        with self._driver.session() as session:
            return self._call_with_retry(session.execute_read, _read)

    def get_entity_topic_clause_paths(
        self,
        entity_uuids: List[str],
        graph_id: str,
        include_clause_data: bool = False,
    ) -> Union[Dict[str, List[Dict[str, Any]]], Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Dict[str, Any]]]]:
        """
        直接查询 Entity → Topic → Clause 路径。

        路径: (e:Entity)-[:MENTIONS]-(t:Topic)-[:HAS_TOPIC]-(c:Clause)

        Args:
            entity_uuids: Entity 节点 UUID 列表
            graph_id: 图谱 ID（用于过滤）
            include_clause_data: 是否同时返回 Clause 节点完整数据（合并查询减少 DB 往返）

        Returns:
            include_clause_data=False: { entity_uuid: [ {topic_uuid, clause_uuid}, ... ] }
            include_clause_data=True:  (paths_map, clause_nodes_map)
        """
        if not entity_uuids:
            if include_clause_data:
                return {}, {}
            return {}

        if include_clause_data:
            def _read_with_data(tx):
                result = tx.run(
                    """
                    MATCH (e:Entity)<-[:MENTIONS]-(t:Topic)<-[:HAS_TOPIC]-(c:Clause)
                    WHERE e.uuid IN $uuids
                      AND e.graph_id = $gid
                      AND t.graph_id = $gid
                      AND c.graph_id = $gid
                    RETURN e.uuid AS entity_uuid, t.uuid AS topic_uuid, t.name AS topic_name,
                           c.uuid AS clause_uuid, c AS clause_node, labels(c) AS clause_labels
                    """,
                    uuids=entity_uuids,
                    gid=graph_id,
                )
                paths_map: Dict[str, List[Dict[str, Any]]] = {uid: [] for uid in entity_uuids}
                clause_nodes_map: Dict[str, Dict[str, Any]] = {}
                for record in result:
                    entity_uuid = record["entity_uuid"]
                    clause_uuid = record["clause_uuid"]
                    if entity_uuid in paths_map:
                        paths_map[entity_uuid].append({
                            "topic_uuid": record["topic_uuid"],
                            "topic_name": record.get("topic_name", ""),
                            "clause_uuid": clause_uuid,
                        })
                    # 收集 Clause 节点数据（去重）
                    if clause_uuid and clause_uuid not in clause_nodes_map:
                        node = record["clause_node"]
                        clause_labels = record["clause_labels"]
                        clause_nodes_map[clause_uuid] = self._node_to_dict(node, clause_labels)
                return paths_map, clause_nodes_map

            with self._driver.session() as session:
                return self._call_with_retry(session.execute_read, _read_with_data)
        else:
            def _read(tx):
                result = tx.run(
                    """
                    MATCH (e:Entity)<-[:MENTIONS]-(t:Topic)<-[:HAS_TOPIC]-(c:Clause)
                    WHERE e.uuid IN $uuids
                      AND e.graph_id = $gid
                      AND t.graph_id = $gid
                      AND c.graph_id = $gid
                    RETURN e.uuid AS entity_uuid, t.uuid AS topic_uuid, t.name AS topic_name, c.uuid AS clause_uuid
                    """,
                    uuids=entity_uuids,
                    gid=graph_id,
                )
                paths_map: Dict[str, List[Dict[str, Any]]] = {uid: [] for uid in entity_uuids}
                for record in result:
                    entity_uuid = record["entity_uuid"]
                    if entity_uuid in paths_map:
                        paths_map[entity_uuid].append({
                            "topic_uuid": record["topic_uuid"],
                            "topic_name": record.get("topic_name", ""),
                            "clause_uuid": record["clause_uuid"],
                        })
                return paths_map

            with self._driver.session() as session:
                return self._call_with_retry(session.execute_read, _read)


    def search_nodes_by_name(
        self,
        graph_id: str,
        query: str,
        node_type: str = None,
        node_types: List[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        按名称模糊搜索节点，支持单类型或多类型过滤。
        使用 toLower CONTAINS 匹配，不依赖向量索引。
        """
        keyword = query.strip().lower()
        if not keyword:
            return []

        def _read(tx):
            # 多类型过滤
            if node_types and isinstance(node_types, list):
                safe_types = []
                for t in node_types:
                    safe = _safe_label(t, VALID_NODE_LABELS)
                    if safe:
                        safe_types.append(safe)
                if not safe_types:
                    logger.warning(f"[storage] search_nodes_by_name: all node_types invalid: {node_types}")
                    return []

                label_conditions = " OR ".join([f"n:{lt}" for lt in safe_types])
                has_clause = "Clause" in safe_types
                has_image = "Image" in safe_types
                has_table = "Table" in safe_types
                text_conditions = ["toLower(n.name) CONTAINS $keyword", "toLower(n.topic) CONTAINS $keyword"]
                if has_clause:
                    text_conditions.append("(n:Clause AND toLower(n.clause_id) CONTAINS $keyword)")
                if has_image:
                    text_conditions.append("(n:Image AND (toLower(n.caption) CONTAINS $keyword OR toLower(n.img_path) CONTAINS $keyword))")
                if has_table:
                    text_conditions.append("(n:Table AND (toLower(n.caption) CONTAINS $keyword OR toLower(n.table_id) CONTAINS $keyword OR toLower(n.table_content) CONTAINS $keyword))")
                cypher = f"""
                    MATCH (n {{graph_id: $gid}})
                    WHERE ({label_conditions})
                      AND ({" OR ".join(text_conditions)})
                    RETURN n, labels(n) AS labels
                    LIMIT $limit
                """
            # 单类型过滤（向后兼容）
            elif node_type and node_type != "All":
                safe_label = _safe_label(node_type, VALID_NODE_LABELS)
                if not safe_label:
                    logger.warning(f"[storage] search_nodes_by_name: invalid label '{node_type}'")
                    return []
                if safe_label == "Clause":
                    cypher = f"""
                        MATCH (n:{safe_label} {{graph_id: $gid}})
                        WHERE toLower(n.name) CONTAINS $keyword
                           OR toLower(n.topic) CONTAINS $keyword
                           OR toLower(n.clause_id) CONTAINS $keyword
                        RETURN n, labels(n) AS labels
                        LIMIT $limit
                    """
                elif safe_label == "Image":
                    cypher = f"""
                        MATCH (n:{safe_label} {{graph_id: $gid}})
                        WHERE toLower(n.caption) CONTAINS $keyword
                           OR toLower(n.img_path) CONTAINS $keyword
                        RETURN n, labels(n) AS labels
                        LIMIT $limit
                    """
                elif safe_label == "Table":
                    cypher = f"""
                        MATCH (n:{safe_label} {{graph_id: $gid}})
                        WHERE toLower(n.caption) CONTAINS $keyword
                           OR toLower(n.table_id) CONTAINS $keyword
                           OR toLower(n.table_content) CONTAINS $keyword
                        RETURN n, labels(n) AS labels
                        LIMIT $limit
                    """
                else:
                    cypher = f"""
                        MATCH (n:{safe_label} {{graph_id: $gid}})
                        WHERE toLower(n.name) CONTAINS $keyword
                           OR toLower(n.topic) CONTAINS $keyword
                        RETURN n, labels(n) AS labels
                        LIMIT $limit
                    """
            else:
                cypher = """
                    MATCH (n {graph_id: $gid})
                    WHERE (n:Entity OR n:Topic OR n:Clause OR n:Image OR n:Table)
                      AND (toLower(n.name) CONTAINS $keyword
                           OR toLower(n.topic) CONTAINS $keyword
                           OR (n:Clause AND toLower(n.clause_id) CONTAINS $keyword)
                           OR (n:Image AND (toLower(n.caption) CONTAINS $keyword OR toLower(n.img_path) CONTAINS $keyword))
                           OR (n:Table AND (toLower(n.caption) CONTAINS $keyword OR toLower(n.table_id) CONTAINS $keyword OR toLower(n.table_content) CONTAINS $keyword)))
                    RETURN n, labels(n) AS labels
                    LIMIT $limit
                """
            result = tx.run(cypher, gid=graph_id, keyword=keyword, limit=limit)
            return [self._node_to_dict(record["n"], record["labels"]) for record in result]

        with self._driver.session() as session:
            return self._call_with_retry(session.execute_read, _read)

    def get_node_neighborhood(
        self,
        node_uuid: str,
        graph_id: str,
    ) -> Dict[str, Any]:
        """
        获取节点的 1 跳邻域：中心节点、双向邻边、邻接节点。
        返回 {center_node, nodes, edges}
        """
        def _read(tx):
            # 中心节点
            center_result = tx.run(
                """
                MATCH (n)
                WHERE n.uuid = $uuid AND n.graph_id = $gid
                RETURN n, labels(n) AS labels
                LIMIT 1
                """,
                uuid=node_uuid, gid=graph_id,
            )
            center_record = center_result.single()
            if not center_record:
                return None
            center_node = self._node_to_dict(center_record["n"], center_record["labels"])

            # 双向邻边 + 邻接节点 (1 跳)
            edge_result = tx.run(
                """
                MATCH (n)
                WHERE n.uuid = $uuid AND n.graph_id = $gid
                OPTIONAL MATCH (n)-[r:HAS_TOPIC|MENTIONS]-(m)
                WHERE r.graph_id = $gid
                RETURN r, n.uuid AS src_uuid, m.uuid AS tgt_uuid, m, labels(m) AS m_labels
                LIMIT 50
                """,
                uuid=node_uuid, gid=graph_id,
            )

            # 2 跳邻居 (如 Clause-HAS_TOPIC->Topic-MENTIONS->Entity)
            hop2_result = tx.run(
                """
                MATCH (n)
                WHERE n.uuid = $uuid AND n.graph_id = $gid
                OPTIONAL MATCH (n)-[r1:HAS_TOPIC|MENTIONS]-(mid)-[r2:HAS_TOPIC|MENTIONS]-(m)
                WHERE r1.graph_id = $gid AND r2.graph_id = $gid AND m <> n
                RETURN r1, r2,
                       n.uuid AS src1_uuid, mid.uuid AS tgt1_uuid,
                       mid.uuid AS src2_uuid, m.uuid AS tgt2_uuid,
                       mid, labels(mid) AS mid_labels,
                       m, labels(m) AS m_labels
                LIMIT 50
                """,
                uuid=node_uuid, gid=graph_id,
            )

            neighbor_nodes = {}
            edges = []
            seen_edge_uuids = set()

            # 处理 1 跳结果
            for record in edge_result:
                rel = record.get("r")
                if not rel:
                    continue
                m = record.get("m")
                if m:
                    m_uuid = m.get("uuid", "")
                    if m_uuid and m_uuid != node_uuid and m_uuid not in neighbor_nodes:
                        neighbor_nodes[m_uuid] = self._node_to_dict(m, record["m_labels"])

                edge_dict = self._edge_to_dict(rel, record["src_uuid"], record["tgt_uuid"])
                e_uuid = edge_dict.get("uuid", "")
                if e_uuid and e_uuid not in seen_edge_uuids:
                    seen_edge_uuids.add(e_uuid)
                    edges.append(edge_dict)
                elif not e_uuid:
                    edges.append(edge_dict)

            # 处理 2 跳结果
            for record in hop2_result:
                r1 = record.get("r1")
                r2 = record.get("r2")
                if not r1 or not r2:
                    continue

                mid = record.get("mid")
                if mid:
                    mid_uuid = mid.get("uuid", "")
                    if mid_uuid and mid_uuid != node_uuid and mid_uuid not in neighbor_nodes:
                        neighbor_nodes[mid_uuid] = self._node_to_dict(mid, record["mid_labels"])

                m = record.get("m")
                if m:
                    m_uuid = m.get("uuid", "")
                    if m_uuid and m_uuid != node_uuid and m_uuid not in neighbor_nodes:
                        neighbor_nodes[m_uuid] = self._node_to_dict(m, record["m_labels"])

                edge1_dict = self._edge_to_dict(r1, record["src1_uuid"], record["tgt1_uuid"])
                e1_uuid = edge1_dict.get("uuid", "")
                if e1_uuid and e1_uuid not in seen_edge_uuids:
                    seen_edge_uuids.add(e1_uuid)
                    edges.append(edge1_dict)
                elif not e1_uuid:
                    edges.append(edge1_dict)

                edge2_dict = self._edge_to_dict(r2, record["src2_uuid"], record["tgt2_uuid"])
                e2_uuid = edge2_dict.get("uuid", "")
                if e2_uuid and e2_uuid not in seen_edge_uuids:
                    seen_edge_uuids.add(e2_uuid)
                    edges.append(edge2_dict)
                elif not e2_uuid:
                    edges.append(edge2_dict)

            return {
                "center_node": center_node,
                "nodes": list(neighbor_nodes.values()),
                "edges": edges,
            }

        with self._driver.session() as session:
            return self._call_with_retry(session.execute_read, _read)

    # ----------------------------------------------------------------
    # Read edges
    # ----------------------------------------------------------------

    def get_episodes(self, episode_uuids: List[str]) -> List[Dict[str, Any]]:
        if not episode_uuids:
            return []

        def _read(tx):
            result = tx.run(
                """
                MATCH (ep:Episode)
                WHERE ep.uuid IN $uuids
                RETURN ep
                """,
                uuids=episode_uuids,
            )
            episodes = []
            for record in result:
                props = dict(record["ep"])

                # Convert Neo4j DateTime to string
                for k, v in props.items():
                    if hasattr(v, "isoformat"):
                        props[k] = v.isoformat()

                meta_json = props.pop("metadata_json", "{}")
                metadata = self._parse_json_safe(meta_json)

                episodes.append({
                    "uuid": props.get("uuid"),
                    "text": props.get("data"),
                    "source": props.get("source"),
                    "page": props.get("page"),
                    "metadata": metadata,
                    "created_at": props.get("created_at")
                })
            return episodes

        with self._driver.session() as session:
            return self._call_with_retry(session.execute_read, _read)


    def batch_get_node_pdf_info(self, node_uuids: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        批量获取 Clause 节点的 PDF 定位信息。

        PDF 位置信息仅存储在 Clause 节点上（pdf_source, pdf_page, pdf_bboxes 等），
        因此直接匹配 :Clause 标签即可利用 clause_uuid 唯一约束索引。

        Returns:
            Dict[node_uuid -> pdf_info dict]
        """
        if not node_uuids:
            return {}

        def _read(tx):
            result_map: Dict[str, Dict[str, Any]] = {}
            records = tx.run(
                """
                MATCH (c:Clause) WHERE c.uuid IN $uuids
                RETURN c.uuid AS node_uuid,
                       c.pdf_source AS source,
                       c.pdf_page AS page,
                       c.pdf_bboxes AS pdf_bboxes_raw,
                       c.pdf_page_width AS page_width,
                       c.pdf_page_height AS page_height,
                       c.summary AS episode_text
                """,
                uuids=node_uuids
            )
            for record in records:
                uid = record["node_uuid"]
                if not uid or uid in result_map:
                    continue
                pdf_bboxes_raw = record.get("pdf_bboxes_raw")
                pdf_bboxes = None
                if pdf_bboxes_raw:
                    if isinstance(pdf_bboxes_raw, str):
                        try:
                            pdf_bboxes = json.loads(pdf_bboxes_raw)
                        except (json.JSONDecodeError, TypeError):
                            pdf_bboxes = None
                    elif isinstance(pdf_bboxes_raw, list):
                        pdf_bboxes = pdf_bboxes_raw

                bbox = None
                page = record.get("page")
                if pdf_bboxes and len(pdf_bboxes) > 0 and len(pdf_bboxes[0]) >= 5:
                    page = pdf_bboxes[0][0]
                    bbox = pdf_bboxes[0][1:5]

                result_map[uid] = {
                    "source": record.get("source"),
                    "page": page,
                    "bbox": bbox,
                    "page_width": record.get("page_width"),
                    "page_height": record.get("page_height"),
                    "episode_text": record.get("episode_text"),
                }
            return result_map

        with self._driver.session() as session:
            return self._call_with_retry(session.execute_read, _read)

    def get_node_episodes(self, node_uuid: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Get episodes (text chunks) that mentioned this node for traceability."""
        def _read(tx):
            result = tx.run(
                """
                MATCH (n:Entity {uuid: $uuid})<-[:MENTIONS]-(ep:Episode)
                RETURN ep
                LIMIT $limit
                """,
                uuid=node_uuid,
                limit=limit
            )
            episodes = []
            for record in result:
                props = dict(record["ep"])
                meta_json = props.pop("metadata_json", "{}")
                metadata = self._parse_json_safe(meta_json)
                episodes.append({
                    "uuid": props.get("uuid"),
                    "text": props.get("data"),
                    "source": props.get("source"),
                    "page": props.get("page"),
                    "metadata": metadata
                })
            return episodes

        with self._driver.session() as session:
            return self._call_with_retry(session.execute_read, _read)

    # ----------------------------------------------------------------
    # Search
    # ----------------------------------------------------------------

    def search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges",
    ):
        """
        Hybrid search — returns results matching the scope.

        Returns a dict with 'edges', 'nodes', and 'episodes' lists.
        """
        result = {"edges": [], "nodes": [], "episodes": [], "query": query}

        with self._driver.session() as session:
            # 1. Search Episodes (Raw text chunks) - ALWAYS search as fallback/context
            episodes = self._search.search_episodes(session, graph_id, query, limit)
            for e in episodes:
                for k, v in e.items():
                    if hasattr(v, "isoformat"):
                        e[k] = v.isoformat()
            result["episodes"] = episodes

            # 2. Search Edges
            if scope in ("edges", "both"):
                edges = self._search.search_edges(
                    session, graph_id, query, limit
                )
                # Sanitize results for JSON serialization
                for e in edges:
                    for k, v in e.items():
                        if hasattr(v, "isoformat"):
                            e[k] = v.isoformat()
                result["edges"] = edges

            # 3. Search Nodes
            if scope in ("nodes", "both"):
                nodes = self._search.search_nodes(
                    session, graph_id, query, limit
                )
                # Sanitize results
                for n in nodes:
                    for k, v in n.items():
                        if hasattr(v, "isoformat"):
                            n[k] = v.isoformat()
                result["nodes"] = nodes

        return result

    def search_object_nodes(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        min_score: float = None,
        query_vector: List[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search Entity nodes specifically using hybrid scoring (vector + BM25).
        Only returns nodes with label 'Entity'.

        Returns list of dicts with node properties + 'score'.
        """
        with self._driver.session() as session:
            results = self._search.search_object_nodes(
                session, graph_id, query, limit, min_score, query_vector
            )
            for n in results:
                for k, v in n.items():
                    if hasattr(v, "isoformat"):
                        n[k] = v.isoformat()
            return results

    def search_term_nodes(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        min_score: float = None,
        query_vector: List[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search Term nodes specifically using hybrid scoring (vector + keyword).
        Only returns nodes with label 'Term'.

        Returns list of dicts with node properties + 'score'.
        """
        with self._driver.session() as session:
            results = self._search.search_term_nodes(
                session, graph_id, query, limit, min_score, query_vector
            )
            for n in results:
                for k, v in n.items():
                    if hasattr(v, "isoformat"):
                        n[k] = v.isoformat()
            return results

    def search_topic_nodes(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        query_vector: List[float] = None,
        min_score: float = None,
    ) -> List[Dict[str, Any]]:
        """
        Search Topic nodes using hybrid scoring (vector + keyword).

        Returns list of dicts with node properties + 'score'.
        """
        with self._driver.session() as session:
            results = self._search.search_topic_nodes(
                session, graph_id, query, limit,
                query_vector=query_vector, min_score=min_score,
            )
            for n in results:
                for k, v in n.items():
                    if hasattr(v, "isoformat"):
                        n[k] = v.isoformat()
            return results

    def search_clause_nodes(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        query_vector: List[float] = None,
        min_score: float = None,
    ) -> List[Dict[str, Any]]:
        """
        Search Clause nodes using hybrid scoring (vector + keyword).

        Returns list of dicts with node properties + 'score'.
        """
        with self._driver.session() as session:
            results = self._search.search_clause_nodes(
                session, graph_id, query, limit,
                query_vector=query_vector, min_score=min_score,
            )
            for n in results:
                for k, v in n.items():
                    if hasattr(v, "isoformat"):
                        n[k] = v.isoformat()
            return results

    def search_clauses_by_id(
        self,
        graph_id: str,
        clause_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """根据 clause_id 列表精确匹配 Clause 节点。"""
        if not clause_ids:
            return []
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (c:Clause {graph_id: $gid})
                WHERE c.clause_id IN $clause_ids
                RETURN c AS n, labels(c) AS node_labels
                """,
                gid=graph_id,
                clause_ids=[cid.strip() for cid in clause_ids if cid and cid.strip()],
            )
            return [
                self._node_to_dict(record["n"], record["node_labels"])
                for record in result
            ]

    def get_entities_by_topic_uuids(
        self,
        topic_uuids: List[str],
        graph_id: str,
    ) -> List[Dict[str, Any]]:
        """根据 Topic UUID 列表获取其 MENTIONS 的 Entity 节点（去重）。"""
        if not topic_uuids:
            return []

        def _read(tx):
            result = tx.run(
                """
                MATCH (t:Topic)-[:MENTIONS]->(e:Entity)
                WHERE t.uuid IN $uuids AND e.graph_id = $gid AND NOT 'Term' IN labels(e)
                RETURN DISTINCT e AS n, labels(e) AS node_labels
                """,
                uuids=topic_uuids,
                gid=graph_id,
            )
            return [
                {**self._node_to_dict(record["n"], record["node_labels"]), "_source_topic": True}
                for record in result
            ]

        with self._driver.session() as session:
            return self._call_with_retry(session.execute_read, _read)

    # ----------------------------------------------------------------
    # Graph info
    # ----------------------------------------------------------------

    def get_graph_info(self, graph_id: str) -> Dict[str, Any]:
        def _read(tx):
            # Count nodes
            node_result = tx.run(
                "MATCH (n:Entity {graph_id: $gid}) RETURN count(n) AS cnt",
                gid=graph_id,
            )
            entity_count = node_result.single()["cnt"]

            image_result = tx.run(
                "MATCH (n:Image {graph_id: $gid}) RETURN count(n) AS cnt",
                gid=graph_id,
            )
            image_count = image_result.single()["cnt"]

            table_result = tx.run(
                "MATCH (n:Table {graph_id: $gid}) RETURN count(n) AS cnt",
                gid=graph_id,
            )
            table_count = table_result.single()["cnt"]

            node_count = entity_count + image_count + table_count

            # Count edges (实际图谱中只有 HAS_TOPIC 和 MENTIONS 两种边)
            edge_result = tx.run(
                "MATCH ()-[r:HAS_TOPIC|MENTIONS {graph_id: $gid}]->() RETURN count(r) AS cnt",
                gid=graph_id,
            )
            edge_count = edge_result.single()["cnt"]

            # Distinct entity types
            label_result = tx.run(
                """
                MATCH (n:Entity {graph_id: $gid})
                UNWIND labels(n) AS lbl
                WITH lbl WHERE lbl <> 'Entity'
                RETURN DISTINCT lbl
                """,
                gid=graph_id,
            )
            entity_types = [record["lbl"] for record in label_result]

            return {
                "graph_id": graph_id,
                "node_count": node_count,
                "edge_count": edge_count,
                "entity_types": entity_types,
                "entity_count": entity_count,
                "image_count": image_count,
                "table_count": table_count,
                "has_topic_count": 0,  # placeholder, computed below
                "mentions_count": 0,   # placeholder, computed below
            }

        def _read_relation_stats(tx):
            # HAS_TOPIC: Clause(Clause) → Topic
            has_topic_result = tx.run(
                "MATCH (e:Clause)-[r:HAS_TOPIC]->(t:Topic) WHERE e.graph_id = $gid RETURN count(r) AS cnt",
                gid=graph_id,
            )
            has_topic_count = has_topic_result.single()["cnt"]

            # MENTIONS: Topic → Entity 或 Topic → Entity:Term 或 Clause → Image/Table
            mentions_result = tx.run(
                "MATCH ()-[r:MENTIONS]->(e) WHERE r.graph_id = $gid AND (e:Entity OR e:Entity:Term OR e:Image OR e:Table) RETURN count(r) AS cnt",
                gid=graph_id,
            )
            mentions_count = mentions_result.single()["cnt"]

            return has_topic_count, mentions_count

        with self._driver.session() as session:
            base_info = self._call_with_retry(session.execute_read, _read)
            has_topic_count, mentions_count = self._call_with_retry(session.execute_read, _read_relation_stats)
            base_info["has_topic_count"] = has_topic_count
            base_info["mentions_count"] = mentions_count
            return base_info

    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        """
        Full graph dump with enriched edge format (for frontend).
        Focuses on semantic Entities and their Relationships.
        Structural nodes (Document, Page, Episode) are included only as context.
        """
        def _read(tx):
            # 1. Get semantic nodes (Entities AND Topics) and their labels
            # Include Clause nodes as Clause nodes for frontend stats
            # Include Image and Table nodes
            node_result = tx.run(
                """
                MATCH (n:Entity {graph_id: $gid})
                RETURN n, labels(n) AS labels
                UNION
                MATCH (n:Topic {graph_id: $gid})
                RETURN n, labels(n) AS labels
                UNION
                MATCH (n:Clause {graph_id: $gid})
                RETURN n, labels(n) AS labels
                UNION
                MATCH (n:Image {graph_id: $gid})
                RETURN n, labels(n) AS labels
                UNION
                MATCH (n:Table {graph_id: $gid})
                RETURN n, labels(n) AS labels
                """,
                gid=graph_id,
            )
            nodes = []
            node_map: Dict[str, str] = {}  # uuid -> name
            for record in node_result:
                nd = self._node_to_dict(record["n"], record["labels"])
                nodes.append(nd)
                node_map[nd["uuid"]] = nd.get("name") or "Unnamed"

            # 2. Get PDF location info for each node by querying its related Episode
            # 实际图谱中 Entity/Clause 与 Episode 通过 MENTIONS 关联
            node_uuids = [n["uuid"] for n in nodes]
            if node_uuids:
                episode_result = tx.run(
                    """
                    MATCH (ep:Episode {graph_id: $gid})-[r:MENTIONS]->(e:Entity {graph_id: $gid})
                    WHERE e.uuid IN $uuids
                    RETURN e.uuid AS entity_uuid,
                           ep.uuid AS episode_uuid,
                           ep.data AS episode_text,
                           ep.metadata_json AS episode_metadata,
                           ep.pdf_bboxes AS pdf_bboxes
                    """,
                    gid=graph_id,
                    uuids=node_uuids,
                )
                # Build a map of entity_uuid -> first episode with PDF location
                node_pdf_info: Dict[str, Dict[str, Any]] = {}
                for record in episode_result:
                    entity_uuid = record["entity_uuid"]
                    if entity_uuid not in node_pdf_info:
                        metadata = record["episode_metadata"] or {}
                        # Only store if we have PDF location info
                        if metadata.get("source") or metadata.get("page"):
                            # 解析 pdf_bboxes（存储为 JSON 字符串）
                            pdf_bboxes_raw = record.get("pdf_bboxes")
                            pdf_bboxes = []
                            if pdf_bboxes_raw:
                                    pdf_bboxes = self._parse_json_safe(pdf_bboxes_raw, []) if isinstance(pdf_bboxes_raw, str) else pdf_bboxes_raw
                            node_pdf_info[entity_uuid] = {
                                "source": metadata.get("source", ""),
                                "page": metadata.get("page"),
                                "bbox": metadata.get("bbox"),
                                "pdf_bboxes": pdf_bboxes,
                                "page_width": metadata.get("page_width"),
                                "page_height": metadata.get("page_height"),
                                "episode_uuid": record["episode_uuid"],
                                "episode_text": record["episode_text"],
                            }
                # Attach PDF info to each node
                for node in nodes:
                    node["pdf_info"] = node_pdf_info.get(node["uuid"], {})

            # 3. Get semantic relationships between entities
            # 实际图谱中只有 HAS_TOPIC (Clause→Topic) 和 MENTIONS (Topic→Entity/Term, Episode→Clause, Clause→Image/Table) 两种边
            edge_result = tx.run(
                """
                MATCH (c:Clause {graph_id: $gid})-[r:HAS_TOPIC]->(t:Topic {graph_id: $gid})
                RETURN r, c.uuid AS src_uuid, t.uuid AS tgt_uuid,
                       c.name AS src_name, t.name AS tgt_name,
                       type(r) AS rel_type
                UNION
                MATCH (t:Topic {graph_id: $gid})-[r:MENTIONS]->(e:Entity {graph_id: $gid})
                RETURN r, t.uuid AS src_uuid, e.uuid AS tgt_uuid,
                       t.name AS src_name, e.name AS tgt_name,
                       type(r) AS rel_type
                UNION
                MATCH (ep:Episode {graph_id: $gid})-[r:MENTIONS]->(c:Clause {graph_id: $gid})
                RETURN r, ep.uuid AS src_uuid, c.uuid AS tgt_uuid,
                       ep.data AS src_name, c.name AS tgt_name,
                       type(r) AS rel_type
                UNION
                MATCH (c:Clause {graph_id: $gid})-[r:MENTIONS]->(i:Image {graph_id: $gid})
                RETURN r, c.uuid AS src_uuid, i.uuid AS tgt_uuid,
                       c.name AS src_name, COALESCE(i.caption, i.img_path) AS tgt_name,
                       type(r) AS rel_type
                UNION
                MATCH (c:Clause {graph_id: $gid})-[r:MENTIONS]->(tbl:Table {graph_id: $gid})
                RETURN r, c.uuid AS src_uuid, tbl.uuid AS tgt_uuid,
                       c.name AS src_name, COALESCE(tbl.caption, tbl.table_id) AS tgt_name,
                       type(r) AS rel_type
                """,
                gid=graph_id,
            )
            edges = []
            for record in edge_result:
                ed = self._edge_to_dict(record["r"], record["src_uuid"], record["tgt_uuid"])
                ed["fact_type"] = ed.get("name") or record["rel_type"]
                ed["source_node_name"] = record["src_name"] or "Unnamed"
                ed["target_node_name"] = record["tgt_name"] or "Unnamed"
                ed["episodes"] = ed.get("episode_ids", [])
                edges.append(ed)

            return {
                "graph_id": graph_id,
                "nodes": nodes,
                "edges": edges,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "has_topic_count": 0,  # placeholder
                "mentions_count": 0,    # placeholder
            }

        def _read_relation_stats(tx):
            # HAS_TOPIC: Clause(Clause) → Topic
            has_topic_result = tx.run(
                "MATCH (e:Clause)-[r:HAS_TOPIC]->(t:Topic) WHERE e.graph_id = $gid RETURN count(r) AS cnt",
                gid=graph_id,
            )
            has_topic_count = has_topic_result.single()["cnt"]

            # MENTIONS: Topic → Entity/Term 或 Clause → Image/Table
            mentions_result = tx.run(
                "MATCH ()-[r:MENTIONS]->(e) WHERE r.graph_id = $gid AND (e:Entity OR e:Entity:Term OR e:Image OR e:Table) RETURN count(r) AS cnt",
                gid=graph_id,
            )
            mentions_count = mentions_result.single()["cnt"]

            return has_topic_count, mentions_count

        # 使用独立 session 避免被其他长查询阻塞，并设置 60s 事务超时
        result = None
        has_topic_count = 0
        mentions_count = 0
        try:
            with self._driver.session() as session:
                # 事务超时保护：防止大型查询永久阻塞
                tx = session.begin_transaction(timeout=60)
                try:
                    result = _read(tx)
                    tx.commit()
                except Exception:
                    tx.rollback()
                    raise
        except Exception as e:
            logger.warning(f"[get_graph_data] Query timeout or error, returning partial result: {e}")
            # 超时/错误时返回空图谱，避免 worker 线程永久卡死
            return {
                "graph_id": graph_id,
                "nodes": [],
                "edges": [],
                "node_count": 0,
                "edge_count": 0,
                "has_topic_count": 0,
                "mentions_count": 0,
                "query_error": str(e)
            }

        try:
            with self._driver.session() as session:
                tx = session.begin_transaction(timeout=30)
                try:
                    has_topic_count, mentions_count = _read_relation_stats(tx)
                    tx.commit()
                except Exception:
                    tx.rollback()
                    raise
        except Exception as e:
            logger.warning(f"[get_graph_data] Stats query error: {e}")
            has_topic_count = 0
            mentions_count = 0

        result["has_topic_count"] = has_topic_count
        result["mentions_count"] = mentions_count
        return result

    # ----------------------------------------------------------------
    # Dict conversion helpers
    # ----------------------------------------------------------------

    def _node_to_dict(self, node, labels: List[str]) -> Dict[str, Any]:
        """Convert Neo4j node to the standard node dict format."""
        props = dict(node)

        # Convert Neo4j DateTime to string
        for k, v in props.items():
            if hasattr(v, "isoformat"):
                props[k] = v.isoformat()

        attrs_json = props.pop("attributes_json", "{}")
        attributes = self._parse_json_safe(attrs_json)

        # Remove internal fields from dict
        props.pop("embedding", None)
        props.pop("name_lower", None)

        # Handle Clause nodes as Clause nodes for frontend stats
        if "Clause" in labels:
            display_labels = ["Clause"]
            # For Clause nodes, use clause_id or data as name
            data_val = props.get("data")
            if props.get("clause_id"):
                display_name = props.get("clause_id")
            elif data_val:
                display_name = data_val[:50]
            else:
                display_name = ""
        elif "Image" in labels:
            display_labels = ["Image"]
            display_name = props.get("caption") or props.get("img_path", "")
        elif "Table" in labels:
            display_labels = ["Table"]
            display_name = props.get("caption") or props.get("table_id", "")
        else:
            # Keep all labels including Entity for frontend stats
            # Convert to list in case Neo4j returns special iterable type
            labels_list = list(labels) if labels else []
            display_labels = labels_list
            display_name = props.get("name", "")

        return {
            "uuid": props.get("uuid", ""),
            "name": display_name,
            "labels": display_labels,
            "summary": props.get("summary", ""),
            "data": props.get("data", ""),
            "definition": props.get("definition", ""),
            "attributes": attributes,
            "created_at": props.get("created_at"),
            # PDF 定位信息
            "pdf_source": props.get("pdf_source"),
            "pdf_page": props.get("pdf_page"),
            "pdf_bbox": props.get("pdf_bbox"),
            "pdf_page_width": props.get("pdf_page_width"),
            "pdf_page_height": props.get("pdf_page_height"),
            # Clause 特有字段
            "clause_id": props.get("clause_id"),
            # Clause 的多页 bbox 列表
            "pdf_bboxes": props.get("pdf_bboxes"),
            # Image 特有字段
            "img_path": props.get("img_path"),
            "img_vlm_content": props.get("img_vlm_content"),
            "vlm_status": props.get("vlm_status"),
            "content": props.get("content"),
            # Table 特有字段
            "table_id": props.get("table_id"),
            "table_content": props.get("table_content"),
            "table_img_path": props.get("table_img_path"),
            "table_footnote": props.get("table_footnote"),
            "table_image_base64_content": props.get("table_image_base64_content"),
            "bbox_pdf": props.get("bbox_pdf"),
            "bbox_viewport": props.get("bbox_viewport"),
        }

    def _edge_to_dict(self, rel, source_uuid: str, target_uuid: str) -> Dict[str, Any]:
        """Convert Neo4j relationship to the standard edge dict format."""
        props = dict(rel)
        # 从 Neo4j relationship type 提取边类型名
        rel_type = rel.type if hasattr(rel, 'type') else ''

        # Convert Neo4j DateTime to string
        for k, v in props.items():
            if hasattr(v, "isoformat"):
                props[k] = v.isoformat()

        attrs_json = props.pop("attributes_json", "{}")
        attributes = self._parse_json_safe(attrs_json)

        # Remove internal fields
        props.pop("fact_embedding", None)

        episode_ids = props.get("episode_ids", [])
        if episode_ids and not isinstance(episode_ids, list):
            episode_ids = [str(episode_ids)]

        return {
            "uuid": props.get("uuid", ""),
            # 优先用 name 属性，fallback 到 Neo4j relationship type
            "name": props.get("name") or rel_type or "",
            "fact": props.get("fact", ""),
            "source_node_uuid": source_uuid,
            "target_node_uuid": target_uuid,
            "attributes": attributes,
            "created_at": props.get("created_at"),
            "valid_at": props.get("valid_at"),
            "invalid_at": props.get("invalid_at"),
            "expired_at": props.get("expired_at"),
            "episode_ids": episode_ids,
        }

    # ========================================================================
    # 多层级分块支持（新增）
    # ========================================================================



    def build_cross_ref_relations(self, graph_id: str) -> int:
        """
        根据条文元数据中的cross_refs构建交叉引用关系

        优化：一次性批量查询所有 metadata_json，消除 N+1 查询

        Args:
            graph_id: 图谱ID

        Returns:
            构建的关系数量
        """
        count = 0

        with self._driver.session() as session:
            # 一次性获取所有条文的 clause_id + uuid + metadata_json
            result = session.run(
                """
                MATCH (ep:Clause {graph_id: $gid})
                WHERE ep.clause_id IS NOT NULL
                RETURN ep.uuid AS uuid, ep.clause_id AS clause_id, ep.metadata_json AS metadata_json
                """,
                gid=graph_id
            )

            clause_map = {}
            cross_ref_batches = []  # [(src_uuid, tgt_uuid), ...]

            for record in result:
                clause_id = record["clause_id"]
                source_uuid = record["uuid"]
                clause_map[clause_id] = source_uuid

                metadata_json = record.get("metadata_json")
                metadata = self._parse_json_safe(metadata_json, {}) if metadata_json else {}
                cross_refs = metadata.get("cross_refs", [])
                if isinstance(cross_refs, str):
                    cross_refs = [cross_refs]

                for ref_id in cross_refs:
                    if ref_id in clause_map:
                        target_uuid = clause_map[ref_id]
                        if target_uuid != source_uuid:
                            cross_ref_batches.append((source_uuid, target_uuid))

            # 批量写入交叉引用关系（每批 100 条，减少事务次数）
            BATCH_SIZE = 100
            for i in range(0, len(cross_ref_batches), BATCH_SIZE):
                batch = cross_ref_batches[i:i + BATCH_SIZE]
                try:
                    session.run(
                        """
                        UNWIND $pairs AS p
                        MATCH (src:Episode {uuid: p.src_uuid}), (tgt:Episode {uuid: p.tgt_uuid})
                        MERGE (src)-[r:CROSS_REFERENCE]->(tgt)
                        ON CREATE SET r.graph_id = $gid
                        """,
                        pairs=[{"src_uuid": s, "tgt_uuid": t} for s, t in batch],
                        gid=graph_id
                    )
                    count += len(batch)
                except Exception as e:
                    logger.debug(f"[hierarchical] Failed to create cross-ref batch: {e}")

        logger.info(f"[hierarchical] Built {count} cross-reference relations")
        return count


    def batch_add_hierarchical_chunks(
        self,
        graph_id: str,
        chunks_batch: List[Dict[str, Any]],
        embeddings_map: Dict[int, List[float]],
        progress_callback: Optional[Callable] = None,
    ) -> List[Optional[str]]:
        """
        批量插入层级分块（UNWIND 单事务版）

        相较于逐条 add_hierarchical_chunk_with_entities：
        - 减少 N 次事务开销为 BATCH_SIZE 次
        - UNWIND 批量创建 Episode/Clause 节点
        - 同一事务内完成 Episode-Clause-Topic-Term/Entity 全量创建

        Args:
            graph_id: 图谱ID
            chunks_batch: chunk 列表，每个包含 text/metadata/chunk_type/level
            embeddings_map: idx -> embedding 向量（预生成，由调用方并发生成）
            progress_callback: 进度回调 (processed, total)

        Returns:
            episode_id 列表（顺序与 chunks_batch 一致，失败返回 None）
        """
        BATCH_SIZE = 50  # 每批处理量，控制单次事务参数规模
        total = len(chunks_batch)
        episode_ids: List[Optional[str]] = [None] * total
        now_base = datetime.now(timezone.utc).isoformat()

        def _push_progress(processed: int):
            if progress_callback and callable(progress_callback):
                progress_callback(processed, total)

        # 分批处理，避免单次事务参数过大
        for batch_start in range(0, total, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total)
            batch_items = []
            for i in range(batch_start, batch_end):
                chunk = chunks_batch[i]
                text = chunk.get("text") or chunk.get("content", "")
                metadata = chunk.get("metadata", {})
                chunk_type = chunk.get("chunk_type", "clause")
                level = chunk.get("level", 2)
                if not text or not text.strip():
                    continue
                # 稳定 UUID
                clause_id_meta = metadata.get("clause_id", "")
                if clause_id_meta:
                    ep_seed = f"{graph_id}:{clause_id_meta}".encode()
                else:
                    ep_seed = f"{graph_id}:{text}".encode()
                episode_id = str(uuid.UUID(hashlib.md5(ep_seed).hexdigest()))
                clause_uuid_seed = f"{graph_id}:{clause_id_meta}:clause".encode() if clause_id_meta else None
                clause_uuid = str(uuid.UUID(hashlib.md5(clause_uuid_seed).hexdigest())) if clause_uuid_seed else None
                batch_items.append({
                    "idx": i,
                    "episode_id": episode_id,
                    "clause_uuid": clause_uuid,
                    "text": text,
                    "metadata": metadata,
                    "chunk_type": chunk_type,
                    "level": level,
                    "embedding": embeddings_map.get(i, []),
                    "now": now_base,
                })
                episode_ids[i] = episode_id

            if not batch_items:
                continue

            batch_clause_items = [it for it in batch_items if it["chunk_type"] == "clause" and it["clause_uuid"]]
            batch_section_items = [it for it in batch_items if it["chunk_type"] == "section"]
            batch_element_items = [it for it in batch_items if it["chunk_type"] == "element"]

            with self._driver.session() as session:

                def _batch_write(tx):
                    # ===== Phase 1: Episode 节点 (section/clause/element 统一) =====
                    ep_list = []
                    for it in batch_items:
                        ep_meta = it["metadata"]
                        ep_source = ep_meta.get("source", "")
                        ep_page = ep_meta.get("page", 0)
                        ep_pdf_bboxes_raw = ep_meta.get("bboxs", [])
                        if not ep_pdf_bboxes_raw:
                            single_bbox = ep_meta.get("bbox")
                            if single_bbox:
                                if isinstance(single_bbox, list) and len(single_bbox) >= 4:
                                    ep_pdf_bboxes_raw = [[ep_page or 1] + single_bbox[:4]]
                                elif isinstance(single_bbox, dict):
                                    ep_pdf_bboxes_raw = [[ep_page or 1,
                                                      single_bbox.get('x0', 0), single_bbox.get('y0', 0),
                                                      single_bbox.get('x1', 0), single_bbox.get('y1', 0)]]
                        ep_pdf_bboxes = json.dumps(ep_pdf_bboxes_raw, ensure_ascii=False) if ep_pdf_bboxes_raw else "[]"
                        ep_pdf_page_width = ep_meta.get("page_width")
                        ep_pdf_page_height = ep_meta.get("page_height")
                        clause_id_ep = ep_meta.get("clause_id", "")
                        ep_list.append({
                            "uuid": it["episode_id"],
                            "graph_id": graph_id,
                            "data": it["text"],
                            "metadata_json": json.dumps(ep_meta, ensure_ascii=False),
                            "embedding": it["embedding"],
                            "created_at": it["now"],
                            "source": ep_source,
                            "page": ep_page,
                            "clause_id": clause_id_ep,
                            "pdf_source": ep_source,
                            "pdf_page": ep_page,
                            "pdf_bboxes": ep_pdf_bboxes,
                            "pdf_page_width": ep_pdf_page_width,
                            "pdf_page_height": ep_pdf_page_height,
                        })

                    tx.run(
                        """
                        UNWIND $ep_list AS ep
                        MERGE (e:Episode {uuid: ep.uuid})
                        ON CREATE SET
                            e.graph_id = ep.graph_id,
                            e.data = ep.data,
                            e.metadata_json = ep.metadata_json,
                            e.processed = true,
                            e.embedding = ep.embedding,
                            e.created_at = ep.created_at,
                            e.source = ep.source,
                            e.page = ep.page,
                            e.clause_id = ep.clause_id,
                            e.pdf_source = ep.pdf_source,
                            e.pdf_page = ep.pdf_page,
                            e.pdf_bboxes = ep.pdf_bboxes,
                            e.pdf_page_width = ep.pdf_page_width,
                            e.pdf_page_height = ep.pdf_page_height
                        ON MATCH SET
                            e.graph_id = ep.graph_id,
                            e.data = ep.data,
                            e.metadata_json = ep.metadata_json,
                            e.embedding = ep.embedding,
                            e.source = COALESCE(e.source, ep.source),
                            e.page = COALESCE(e.page, ep.page),
                            e.clause_id = ep.clause_id,
                            e.pdf_source = COALESCE(e.pdf_source, ep.pdf_source),
                            e.pdf_page = COALESCE(e.pdf_page, ep.pdf_page),
                            e.pdf_bboxes = COALESCE(e.pdf_bboxes, ep.pdf_bboxes),
                            e.pdf_page_width = COALESCE(e.pdf_page_width, ep.pdf_page_width),
                            e.pdf_page_height = COALESCE(e.pdf_page_height, ep.pdf_page_height)
                        """,
                        ep_list=ep_list
                    )

                    # 移除 Episode 标签，统一为 Clause
                    # 先清理可能冲突的重复 Clause 节点（同一 graph 重复构建时的残留数据）
                    tx.run("""
                        MATCH (e:Episode) WHERE e.graph_id = $gid
                        WITH e
                        OPTIONAL MATCH (dup:Clause {uuid: e.uuid}) WHERE dup <> e
                        DETACH DELETE dup
                        WITH e
                        REMOVE e:Episode SET e:Clause
                    """, gid=graph_id)

                    # ===== Phase 2: Clause 节点 (clause type) =====
                    if batch_clause_items:
                        clause_list = []
                        for it in batch_clause_items:
                            m = it["metadata"]
                            cid = m.get("clause_id", "").strip()
                            clause_name = f"条款{cid}"
                            clause_req = m.get("requirement_type", "recommended").lower()
                            ep_pdf_bboxes_raw = m.get("bboxs", [])
                            if not ep_pdf_bboxes_raw:
                                single_bbox = m.get("bbox")
                                if single_bbox:
                                    if isinstance(single_bbox, list) and len(single_bbox) >= 4:
                                        ep_pdf_bboxes_raw = [[(m.get("page") or 1)] + single_bbox[:4]]
                                    elif isinstance(single_bbox, dict):
                                        ep_pdf_bboxes_raw = [[(m.get("page") or 1),
                                                      single_bbox.get('x0', 0), single_bbox.get('y0', 0),
                                                      single_bbox.get('x1', 0), single_bbox.get('y1', 0)]]
                            ep_pdf_bboxes = json.dumps(ep_pdf_bboxes_raw, ensure_ascii=False) if ep_pdf_bboxes_raw else "[]"
                            clause_list.append({
                                "uuid": it["clause_uuid"],
                                "graph_id": graph_id,
                                "clause_id": cid,
                                "name": clause_name,
                                "name_lower": clause_name.lower(),
                                "summary": it["text"],
                                "embedding": it["embedding"],
                                "req_type": clause_req,
                                "pdf_source": m.get("source"),
                                "pdf_page": m.get("page"),
                                "pdf_bboxes": ep_pdf_bboxes,
                                "pdf_page_width": m.get("page_width"),
                                "pdf_page_height": m.get("page_height"),
                            })

                        tx.run(
                            """
                            UNWIND $clause_list AS c
                            MERGE (e:Clause {graph_id: c.graph_id, clause_id: c.clause_id})
                            ON CREATE SET
                                e.uuid = c.uuid,
                                e.name = c.name,
                                e.name_lower = c.name_lower,
                                e.summary = c.summary,
                                e.embedding = c.embedding,
                                e.requirement_type = c.req_type,
                                e.pdf_source = c.pdf_source,
                                e.pdf_page = c.pdf_page,
                                e.pdf_bboxes = c.pdf_bboxes,
                                e.pdf_page_width = c.pdf_page_width,
                                e.pdf_page_height = c.pdf_page_height,
                                e.created_at = datetime()
                            ON MATCH SET
                                e.embedding = c.embedding,
                                e.summary = COALESCE(e.summary, c.summary),
                                e.pdf_source = COALESCE(e.pdf_source, c.pdf_source),
                                e.pdf_page = COALESCE(e.pdf_page, c.pdf_page),
                                e.pdf_bboxes = COALESCE(e.pdf_bboxes, c.pdf_bboxes),
                                e.pdf_page_width = COALESCE(e.pdf_page_width, c.pdf_page_width),
                                e.pdf_page_height = COALESCE(e.pdf_page_height, c.pdf_page_height)
                            """,
                            clause_list=clause_list
                        )

                        # Episode -> Clause MENTIONS
                        # 注意：Phase 1 已将所有 Episode 标记为 Clause，
                        # 但 Phase 2 MERGE 匹配到 Phase 1 节点时 UUID 不变（仍为 episode_id）
                        # 所以 MENTIONS 的 source 和 target 实际上是同一个节点，跳过
                        ep_clause_pairs = [
                            {"ep_uuid": it["episode_id"], "clause_uuid": it["episode_id"]}
                            for it in batch_clause_items
                            if it["episode_id"] != it["clause_uuid"]
                        ]
                        if ep_clause_pairs:
                            tx.run(
                                """
                                UNWIND $pairs AS p
                                MATCH (ep {uuid: p.ep_uuid}), (c:Clause {uuid: p.clause_uuid})
                                MERGE (ep)-[r:MENTIONS]->(c)
                                ON CREATE SET r.graph_id = $gid
                                """,
                                pairs=ep_clause_pairs, gid=graph_id
                            )

                    # ===== Phase 3: Section 节点 (section type) =====
                    if batch_section_items:
                        section_list = []
                        for it in batch_section_items:
                            m = it["metadata"]
                            title = m.get("title", it["text"][:50])
                            chapter_num = m.get("chapter_number")
                            section_num = m.get("section_number")
                            summary = f"章节 {chapter_num}.{section_num if section_num else ''} - {title}" if chapter_num else title
                            sec_uuid = str(uuid.UUID(hashlib.md5(
                                f"{graph_id}:Section:{title}".encode()).hexdigest()))
                            section_list.append({
                                "uuid": sec_uuid,
                                "graph_id": graph_id,
                                "name": title,
                                "name_lower": title.lower(),
                                "summary": summary,
                                "embedding": it["embedding"],
                            })

                        tx.run(
                            """
                            UNWIND $section_list AS s
                            MERGE (e:Entity:Section {graph_id: s.graph_id, name_lower: s.name_lower})
                            ON CREATE SET
                                e.uuid = s.uuid,
                                e.name = s.name,
                                e.summary = s.summary,
                                e.embedding = s.embedding,
                                e.created_at = datetime()
                            ON MATCH SET
                                e.embedding = s.embedding,
                                e.summary = CASE WHEN e.summary = '' OR e.summary IS NULL THEN s.summary ELSE e.summary END
                            """,
                            section_list=section_list
                        )

                        # 注：不再创建 Episode->Section MENTIONS 边
                        # Section 节点仅保留节点属性，通过 Topic 语义层间接关联

                    # ===== Phase 4: Element 节点 (element type) =====
                    if batch_element_items:
                        element_list = []
                        for it in batch_element_items:
                            m = it["metadata"]
                            etype = m.get("element_type", "parameter").capitalize()
                            if etype not in ["Formula", "Parameter", "Term"]:
                                etype = "Parameter"
                            key = m.get("key", it["text"][:50])
                            value = m.get("value", "")
                            unit = m.get("unit", "")
                            condition = m.get("condition", "")
                            source_id = m.get("source_id", "")
                            parts = []
                            if source_id: parts.append(f"来源: {source_id}")
                            if value: parts.append(f"值: {value}")
                            if unit: parts.append(f"单位: {unit}")
                            if condition: parts.append(f"条件: {condition}")
                            summary = " | ".join(parts) if parts else f"{etype}类型要素"
                            ent_uuid = str(uuid.UUID(hashlib.md5(
                                f"{graph_id}:{etype}:{key}".encode()).hexdigest()))
                            element_list.append({
                                "uuid": ent_uuid,
                                "graph_id": graph_id,
                                "name": key,
                                "name_lower": key.lower(),
                                "summary": summary,
                                "embedding": it["embedding"],
                                "etype": etype,
                            })

                        tx.run(
                            """
                            UNWIND $element_list AS el
                            MERGE (e:Entity:`el.etype` {graph_id: el.graph_id, name_lower: el.name_lower})
                            ON CREATE SET
                                e.uuid = el.uuid,
                                e.name = el.name,
                                e.summary = el.summary,
                                e.embedding = el.embedding,
                                e.created_at = datetime()
                            ON MATCH SET
                                e.embedding = el.embedding,
                                e.summary = CASE WHEN e.summary = '' OR e.summary IS NULL THEN e.summary ELSE e.summary END
                            """,
                            element_list=element_list
                        )

                        # 注：不再创建 Episode->Element MENTIONS 边
                        # Element 节点仅保留节点属性，通过 Topic 语义层间接关联

                    # ===== Phase 5: Topic + HAS_TOPIC =====
                    topic_clause_items = [
                        it for it in batch_clause_items
                        if it["metadata"].get("topics")
                    ]
                    if topic_clause_items:
                        topic_list = []
                        clause_topic_pairs = []
                        for it in topic_clause_items:
                            m = it["metadata"]
                            cid = m.get("clause_id", "").strip()
                            topics_list = m.get("topics", [])
                            for tp in topics_list:
                                topic_text = tp.get("topic", "") if isinstance(tp, dict) else str(tp)
                                if not topic_text:
                                    continue
                                topic_uuid = str(uuid.UUID(hashlib.md5(
                                    f"{graph_id}:{cid}:{topic_text}:topic".encode()).hexdigest()))
                                topic_list.append({
                                    "uuid": topic_uuid,
                                    "clause_uuid": it["clause_uuid"],
                                    "clause_id": cid,
                                    "topic": topic_text,
                                    "graph_id": graph_id,
                                    "created_at": it["now"],
                                    "embedding": it["embedding"],
                                })
                                clause_topic_pairs.append({
                                    "clause_uuid": it["episode_id"],
                                    "topic_uuid": topic_uuid,
                                })

                        tx.run(
                            """
                            UNWIND $topic_list AS t
                            MERGE (tp:Topic {uuid: t.uuid})
                            ON CREATE SET
                                tp.graph_id = t.graph_id,
                                tp.topic = t.topic,
                                tp.clause_id = t.clause_id,
                                tp.created_at = t.created_at,
                                tp.name = t.topic,
                                tp.embedding = t.embedding
                            ON MATCH SET
                                tp.topic = t.topic,
                                tp.clause_id = t.clause_id,
                                tp.name = t.topic,
                                tp.embedding = t.embedding
                            """,
                            topic_list=topic_list
                        )

                        # Clause -> Topic HAS_TOPIC
                        tx.run(
                            """
                            UNWIND $pairs AS p
                            MATCH (c:Clause {uuid: p.clause_uuid}), (tp:Topic {uuid: p.topic_uuid})
                            MERGE (c)-[r:HAS_TOPIC]->(tp)
                            SET r.graph_id = $gid, r.created_at = datetime()
                            """,
                            pairs=clause_topic_pairs, gid=graph_id
                        )

                        # ===== Phase 6: Term 节点 + Topic-MENTIONS (unique per graph_id+term_name) =====
                        # terms 是 clause 级别的，只关联到第一个 topic 避免重复
                        all_terms = []
                        term_topic_map = []
                        term_seen = set()
                        for it in topic_clause_items:
                            m = it["metadata"]
                            cid = m.get("clause_id", "").strip()
                            topics_list = m.get("topics", [])
                            first_topic_uuid = None
                            for tp in topics_list:
                                topic_text = tp.get("topic", "") if isinstance(tp, dict) else str(tp)
                                if topic_text:
                                    first_topic_uuid = str(uuid.UUID(hashlib.md5(
                                        f"{graph_id}:{cid}:{topic_text}:topic".encode()).hexdigest()))
                                    break
                            if not first_topic_uuid:
                                continue
                            for term_item in m.get("terms", []):
                                t_name = term_item if isinstance(term_item, str) else term_item.get("term_name", "")
                                if not t_name or t_name in term_seen:
                                    continue
                                term_seen.add(t_name)
                                t_def = term_item.get("definition", "") if isinstance(term_item, dict) else ""
                                t_uuid = str(uuid.UUID(hashlib.md5(
                                    f"{graph_id}:{t_name}:term".encode()).hexdigest()))
                                all_terms.append({
                                    "uuid": t_uuid,
                                    "graph_id": graph_id,
                                    "name": t_name,
                                    "name_lower": t_name.lower(),
                                    "definition": t_def,
                                    "entity_label": "Term",
                                    "created_at": it["now"],
                                })
                                term_topic_map.append({"uuid": t_uuid, "topic_uuid": first_topic_uuid})

                        if all_terms:
                            tx.run(
                                """
                                UNWIND $terms AS t
                                MERGE (e:Entity:Term {uuid: t.uuid})
                                ON CREATE SET
                                    e.graph_id = t.graph_id,
                                    e.name = t.name,
                                    e.definition = t.definition,
                                    e.entity_label = t.entity_label,
                                    e.created_at = t.created_at,
                                    e.name_lower = t.name_lower
                                ON MATCH SET
                                    e.name = t.name,
                                    e.definition = COALESCE(e.definition, t.definition),
                                    e.name_lower = t.name_lower
                                """,
                                terms=all_terms
                            )

                            tx.run(
                                """
                                UNWIND $pairs AS p
                                MATCH (tp:Topic {uuid: p.topic_uuid}), (e:Entity:Term {uuid: p.uuid})
                                MERGE (tp)-[r:MENTIONS]->(e)
                                SET r.graph_id = $gid, r.created_at = datetime()
                                """,
                                pairs=term_topic_map, gid=graph_id
                            )

                        # ===== Phase 7: Entity 节点 + Topic-MENTIONS (unique per graph_id+entity_name) =====
                        all_entities = []
                        entity_topic_map = []
                        ent_seen = set()
                        for it in topic_clause_items:
                            m = it["metadata"]
                            cid = m.get("clause_id", "").strip()
                            topics_list = m.get("topics", [])
                            for tp in topics_list:
                                topic_text = tp.get("topic", "") if isinstance(tp, dict) else str(tp)
                                if not topic_text:
                                    continue
                                topic_uuid = str(uuid.UUID(hashlib.md5(
                                    f"{graph_id}:{cid}:{topic_text}:topic".encode()).hexdigest()))
                                topic_entities = tp.get("entities", []) if isinstance(tp, dict) else []
                                for ent_item in topic_entities:
                                    e_name = ent_item if isinstance(ent_item, str) else ent_item.get("key", ent_item.get("name", ""))
                                    if not e_name or e_name in ent_seen:
                                        continue
                                    ent_seen.add(e_name)
                                    e_uuid = str(uuid.UUID(hashlib.md5(
                                        f"{graph_id}:{e_name}:entity".encode()).hexdigest()))
                                    all_entities.append({
                                        "uuid": e_uuid,
                                        "graph_id": graph_id,
                                        "name": e_name,
                                        "name_lower": e_name.lower(),
                                        "created_at": it["now"],
                                    })
                                    entity_topic_map.append({"uuid": e_uuid, "topic_uuid": topic_uuid})

                        if all_entities:
                            tx.run(
                                """
                                UNWIND $entities AS e
                                MERGE (ent:Entity {uuid: e.uuid})
                                ON CREATE SET
                                    ent.graph_id = e.graph_id,
                                    ent.name = e.name,
                                    ent.name_lower = e.name_lower,
                                    ent.created_at = e.created_at,
                                    ent.entity_label = 'Entity'
                                ON MATCH SET
                                    ent.name = e.name,
                                    ent.name_lower = e.name_lower
                                """,
                                entities=all_entities
                            )

                            tx.run(
                                """
                                UNWIND $pairs AS p
                                MATCH (tp:Topic {uuid: p.topic_uuid}), (ent:Entity {uuid: p.uuid})
                                MERGE (tp)-[r:MENTIONS]->(ent)
                                SET r.graph_id = $gid, r.created_at = datetime()
                                """,
                                pairs=entity_topic_map, gid=graph_id
                            )

                        # ===== Phase 8: Image / Table 节点 + Clause-MENTIONS =====
                        all_images = []
                        all_tables = []
                        clause_image_pairs = []
                        clause_table_pairs = []
                        for it in batch_clause_items:
                            m = it["metadata"]
                            cid = m.get("clause_id", "").strip()
                            clause_uuid = it["clause_uuid"]
                            images = m.get("images", [])
                            if isinstance(images, list):
                                for img in images:
                                    img_path = img.get("img_path", "") if isinstance(img, dict) else ""
                                    if not img_path:
                                        continue
                                    img_uuid = str(uuid.UUID(hashlib.md5(
                                        f"{graph_id}:{img_path}:image".encode()).hexdigest()))
                                    all_images.append({
                                        "uuid": img_uuid,
                                        "graph_id": graph_id,
                                        "caption": img.get("caption", "") if isinstance(img, dict) else "",
                                        "content": img.get("content", "") if isinstance(img, dict) else "",
                                        "img_path": img_path,
                                        "chunk_id": img.get("chunk_id", "") if isinstance(img, dict) else "",
                                        "page_idx": img.get("page_idx", 0) if isinstance(img, dict) else 0,
                                        "img_vlm_content": img.get("img_vlm_content", "") if isinstance(img, dict) else "",
                                        "vlm_status": img.get("vlm_status", "") if isinstance(img, dict) else "",
                                    })
                                    clause_image_pairs.append({"clause_id": cid, "img_uuid": img_uuid})
                            tables = m.get("referenced_tables", [])
                            if isinstance(tables, list):
                                for tbl in tables:
                                    if not isinstance(tbl, dict):
                                        continue
                                    table_id = tbl.get("table_id", "")
                                    chunk_id = tbl.get("chunk_id", "")
                                    # 任一字段不空即入图：兼容 LLM 未识别 table_id 的情况
                                    if not (table_id or chunk_id):
                                        continue
                                    # uuid 种子：优先 table_id，回退 chunk_id（保证同一引用稳定去重）
                                    seed_key = table_id or chunk_id
                                    tbl_uuid = str(uuid.UUID(hashlib.md5(
                                        f"{graph_id}:{seed_key}:table".encode()).hexdigest()))
                                    all_tables.append({
                                        "uuid": tbl_uuid,
                                        "graph_id": graph_id,
                                        "table_id": table_id,
                                        "caption": tbl.get("caption", ""),
                                        "chunk_id": chunk_id,
                                        "page_idx": tbl.get("page_idx", 0),
                                        "bbox_pdf": json.dumps(tbl.get("bbox_pdf", []), ensure_ascii=False),
                                        "bbox_viewport": json.dumps(tbl.get("bbox_viewport", []), ensure_ascii=False),
                                        "table_content": tbl.get("table_content", ""),
                                        "table_img_path": tbl.get("table_img_path", ""),
                                        "table_footnote": tbl.get("table_footnote", ""),
                                        "table_image_base64_content": tbl.get("table_image_base64_content", ""),
                                    })
                                    clause_table_pairs.append({"clause_id": cid, "tbl_uuid": tbl_uuid})

                        if all_images:
                            tx.run(
                                """
                                UNWIND $images AS img
                                MERGE (i:Image {uuid: img.uuid})
                                ON CREATE SET
                                    i.graph_id = img.graph_id,
                                    i.caption = img.caption,
                                    i.content = img.content,
                                    i.img_path = img.img_path,
                                    i.chunk_id = img.chunk_id,
                                    i.page_idx = img.page_idx,
                                    i.img_vlm_content = img.img_vlm_content,
                                    i.vlm_status = img.vlm_status,
                                    i.created_at = datetime()
                                ON MATCH SET
                                    i.caption = COALESCE(img.caption, i.caption),
                                    i.content = COALESCE(img.content, i.content),
                                    i.img_vlm_content = COALESCE(img.img_vlm_content, i.img_vlm_content),
                                    i.vlm_status = COALESCE(img.vlm_status, i.vlm_status)
                                """,
                                images=all_images
                            )
                            tx.run(
                                """
                                UNWIND $pairs AS p
                                MATCH (c:Clause {graph_id: $gid, clause_id: p.clause_id}), (i:Image {uuid: p.img_uuid})
                                MERGE (c)-[r:MENTIONS]->(i)
                                SET r.graph_id = $gid, r.created_at = datetime()
                                """,
                                pairs=clause_image_pairs, gid=graph_id
                            )
                            logger.info(f"[batch] Created/linked {len(all_images)} Image nodes")

                        if all_tables:
                            tx.run(
                                """
                                UNWIND $tables AS t
                                MERGE (tbl:Table {uuid: t.uuid})
                                ON CREATE SET
                                    tbl.graph_id = t.graph_id,
                                    tbl.table_id = t.table_id,
                                    tbl.caption = t.caption,
                                    tbl.chunk_id = t.chunk_id,
                                    tbl.page_idx = t.page_idx,
                                    tbl.bbox_pdf = t.bbox_pdf,
                                    tbl.bbox_viewport = t.bbox_viewport,
                                    tbl.table_content = t.table_content,
                                    tbl.table_img_path = t.table_img_path,
                                    tbl.table_footnote = t.table_footnote,
                                    tbl.table_image_base64_content = t.table_image_base64_content,
                                    tbl.created_at = datetime()
                                ON MATCH SET
                                    tbl.caption = COALESCE(t.caption, tbl.caption),
                                    tbl.table_content = COALESCE(t.table_content, tbl.table_content),
                                    tbl.table_img_path = COALESCE(t.table_img_path, tbl.table_img_path),
                                    tbl.table_footnote = COALESCE(t.table_footnote, tbl.table_footnote),
                                    tbl.table_image_base64_content = COALESCE(t.table_image_base64_content, tbl.table_image_base64_content)
                                """,
                                tables=all_tables
                            )
                            tx.run(
                                """
                                UNWIND $pairs AS p
                                MATCH (c:Clause {graph_id: $gid, clause_id: p.clause_id}), (tbl:Table {uuid: p.tbl_uuid})
                                MERGE (c)-[r:MENTIONS]->(tbl)
                                SET r.graph_id = $gid, r.created_at = datetime()
                                """,
                                pairs=clause_table_pairs, gid=graph_id
                            )
                            logger.info(f"[batch] Created/linked {len(all_tables)} Table nodes")

                self._call_with_retry(session.execute_write, _batch_write)

            _push_progress(batch_end)

        logger.info(f"[batch] Completed: {total} chunks -> episode_ids")
        return episode_ids


    def _create_clause_entity_only(self, tx, graph_id: str, episode_id: str,
                                   clause_id: str, content: str,
                                   embedding: List[float], metadata: Dict,
                                   now: str):
        """为Clause创建节点（不创建Component/Action/Condition实体）

        当没有 intelligent_chunks 数据时使用此方法。
        只创建 Clause 节点和 Episode 关系，Entity 由 add_topic_and_entity_nodes 统一创建。

        注意：此方法不再处理三元组（Component/Action/Condition），
        因为原来的设计是 Term/Entity - Topic - Clause 结构。
        """
        # 标准化 clause_id（去除首尾空格）
        clause_id_normalized = clause_id.strip() if clause_id else ''
        clause_name = f"条款{clause_id_normalized}"

        # 生成Clause UUID（基于 clause_id 确保唯一性）
        entity_seed = f"{graph_id}:{clause_id_normalized}:clause".encode('utf-8')
        entity_uuid = str(uuid.UUID(hashlib.md5(entity_seed).hexdigest()))

        # 获取条款级要求类型（fallback）
        clause_requirement = metadata.get('requirement_type', 'recommended').lower()
        now = datetime.now(timezone.utc).isoformat()

        # 提取 PDF 定位信息
        pdf_source = metadata.get('source')
        pdf_page = metadata.get('page')
        pdf_bboxes_raw = metadata.get('bboxs', [])
        if not pdf_bboxes_raw:
            single_bbox = metadata.get('bbox')
            if single_bbox:
                if isinstance(single_bbox, list) and len(single_bbox) >= 4:
                    pdf_bboxes_raw = [[pdf_page or 1] + single_bbox[:4]]
                elif isinstance(single_bbox, dict):
                    pdf_bboxes_raw = [[pdf_page or 1,
                                   single_bbox.get('x0', 0), single_bbox.get('y0', 0),
                                   single_bbox.get('x1', 0), single_bbox.get('y1', 0)]]
        pdf_bboxes = json.dumps(pdf_bboxes_raw, ensure_ascii=False) if pdf_bboxes_raw else "[]"
        pdf_page_width = metadata.get('page_width')
        pdf_page_height = metadata.get('page_height')

        # 创建Clause节点（MERGE 条件包含 clause_id）
        tx.run(
            """
            MERGE (e:Clause {graph_id: $gid, clause_id: $clause_id})
            ON CREATE SET
                e.uuid = $uuid,
                e.name = $name,
                e.name_lower = $name_lower,
                e.summary = $summary,
                e.embedding = $embedding,
                e.requirement_type = $req_type,
                e.pdf_source = $pdf_source,
                e.pdf_page = $pdf_page,
                e.pdf_bboxes = $pdf_bboxes,
                e.pdf_page_width = $pdf_page_width,
                e.pdf_page_height = $pdf_page_height,
                e.created_at = datetime()
            ON MATCH SET
                e.embedding = $embedding,
                e.summary = COALESCE(e.summary, $summary),
                e.pdf_source = COALESCE(e.pdf_source, $pdf_source),
                e.pdf_page = COALESCE(e.pdf_page, $pdf_page),
                e.pdf_bboxes = COALESCE(e.pdf_bboxes, $pdf_bboxes),
                e.pdf_page_width = COALESCE(e.pdf_page_width, $pdf_page_width),
                e.pdf_page_height = COALESCE(e.pdf_page_height, $pdf_page_height)
            """,
            gid=graph_id,
            clause_id=clause_id_normalized,
            uuid=entity_uuid,
            name=clause_name,
            name_lower=clause_name.lower(),
            summary=content if content else "",
            embedding=embedding,
            req_type=clause_requirement,
            pdf_source=pdf_source,
            pdf_page=pdf_page,
            pdf_bboxes=pdf_bboxes,
            pdf_page_width=pdf_page_width,
            pdf_page_height=pdf_page_height,
        )

        # 链接Episode -> Clause (MENTIONS)
        tx.run(
            """
            MATCH (ep:Episode {uuid: $ep_uuid}), (e:Clause {uuid: $e_uuid})
            MERGE (ep)-[r:MENTIONS]->(e)
            ON CREATE SET r.graph_id = $gid
            """,
            ep_uuid=episode_id,
            e_uuid=entity_uuid,
            gid=graph_id
        )

        # ========== 术语章节处理 ==========
        terms = metadata.get('terms', [])
        is_term_def = metadata.get('is_term_definition', False)
        if is_term_def and terms:
            term_name = terms[0].get('term_name', '') if terms else ''
            term_definition = terms[0].get('definition', '') if terms else ''
            if term_name:
                logger.info(f"[hierarchical] 创建术语定义: {term_name}")
                term_entity_uuid = self._create_term_entity(
                    tx, graph_id, entity_uuid, term_name, term_definition, clause_id,
                    pdf_source=pdf_source, pdf_page=pdf_page, pdf_bboxes=pdf_bboxes,
                    pdf_page_width=pdf_page_width, pdf_page_height=pdf_page_height
                )
                self._create_defines_relation(tx, term_entity_uuid, entity_uuid)
            return

        # ========== Topic 和 Entity 创建（inline，不再拆分到 add_topic_and_entity_nodes） ==========
        topics_list = metadata.get('topics', [])
        terms = metadata.get('terms', [])

        for tp_idx, tp in enumerate(topics_list):
            topic_text = tp.get('topic', '') if isinstance(tp, dict) else str(tp)
            if not topic_text:
                continue

            # Topic UUID 基于 clause_id + topic_text 生成（确保每个 topic 独立）
            topic_uuid = str(uuid.UUID(hashlib.md5(f"{graph_id}:{clause_id_normalized}:{topic_text}:topic".encode()).hexdigest()))
            tx.run(
                """
                MERGE (t:Topic {uuid: $uuid})
                ON CREATE SET
                    t.graph_id = $gid,
                    t.topic = $topic,
                    t.clause_id = $clause_id,
                    t.created_at = $created_at,
                    t.name = $topic
                ON MATCH SET
                    t.topic = $topic,
                    t.clause_id = $clause_id,
                    t.name = $topic
                """,
                uuid=topic_uuid,
                gid=graph_id,
                topic=topic_text,
                clause_id=clause_id_normalized,
                created_at=now
            )
            logger.info(f"[hierarchical] Topic created: clause={clause_id} topic={topic_text[:30]}")

            # 创建 HAS_TOPIC 关系
            tx.run(
                """
                MATCH (e:Clause {clause_id: $clause_id}), (t:Topic {uuid: $topic_uuid})
                MERGE (e)-[r:HAS_TOPIC]->(t)
                SET r.graph_id = $gid, r.created_at = datetime()
                """,
                clause_id=clause_id_normalized,
                topic_uuid=topic_uuid,
                gid=graph_id
            )

            # terms 是 clause 级别的，只关联到第一个 topic 避免重复
            clause_terms = terms if tp_idx == 0 else []
            if isinstance(clause_terms, list):
                for term_item in clause_terms:
                    term_name = term_item if isinstance(term_item, str) else term_item.get('term_name', '')
                    term_def = term_item.get('definition', '') if isinstance(term_item, dict) else ''
                    if not term_name:
                        continue
                    term_uuid = str(uuid.UUID(hashlib.md5(f"{graph_id}:{term_name}:term".encode()).hexdigest()))
                    tx.run(
                        """
                        MERGE (e:Entity:Term {uuid: $uuid})
                        ON CREATE SET
                            e.graph_id = $gid,
                            e.name = $name,
                            e.definition = $definition,
                            e.entity_label = 'Term',
                            e.created_at = $created_at
                        ON MATCH SET
                            e.name = $name,
                            e.definition = COALESCE($definition, e.definition)
                        """,
                        uuid=term_uuid,
                        gid=graph_id,
                        name=term_name,
                        definition=term_def,
                        created_at=now
                    )
                    tx.run(
                        """
                        MATCH (t:Topic {uuid: $topic_uuid}), (e:Entity:Term {uuid: $entity_uuid})
                        MERGE (t)-[r:MENTIONS]->(e)
                        SET r.graph_id = $gid, r.created_at = datetime()
                        """,
                        topic_uuid=topic_uuid,
                        entity_uuid=term_uuid,
                        gid=graph_id
                    )
                    logger.info(f"[hierarchical] Entity:Term: {term_name} <- Topic({topic_text[:20]}) MENTIONS")

            # 为该 Topic 的 entities 创建 Entity 节点和 MENTIONS 关系
            topic_entities = tp.get('entities', []) if isinstance(tp, dict) else []
            if isinstance(topic_entities, list):
                for ent in topic_entities:
                    entity_name = ent if isinstance(ent, str) else ent.get('key', ent.get('name', ''))
                    if not entity_name:
                        continue
                    entity_uuid = str(uuid.UUID(hashlib.md5(f"{graph_id}:{entity_name}:entity".encode()).hexdigest()))
                    tx.run(
                        """
                        MERGE (e:Entity {uuid: $uuid})
                        ON CREATE SET
                            e.graph_id = $gid,
                            e.name = $name,
                            e.entity_label = 'Entity',
                            e.created_at = $created_at
                        ON MATCH SET
                            e.name = $name
                        """,
                        uuid=entity_uuid,
                        gid=graph_id,
                        name=entity_name,
                        created_at=now
                    )
                    tx.run(
                        """
                        MATCH (t:Topic {uuid: $topic_uuid}), (e:Entity {uuid: $entity_uuid})
                        MERGE (t)-[r:MENTIONS]->(e)
                        SET r.graph_id = $gid, r.created_at = datetime()
                        """,
                        topic_uuid=topic_uuid,
                        entity_uuid=entity_uuid,
                        gid=graph_id
                    )
                    logger.info(f"[hierarchical] Entity: {entity_name} <- Topic({topic_text[:20]}) MENTIONS")

        logger.info(f"[hierarchical] Clause node created: {clause_id}")


    def _create_section_entity(self, tx, graph_id: str, episode_id: str,
                               title: str, metadata: Dict, embedding: List[float]):
        """为Section创建Entity节点"""
        section_name = title if title else metadata.get("title", "未命名章节")

        # 构建 summary
        chapter_num = metadata.get("chapter_number")
        section_num = metadata.get("section_number")
        summary = f"章节 {chapter_num}.{section_num if section_num else ''} - {section_name}" if chapter_num else section_name

        entity_seed = f"{graph_id}:Section:{section_name}".encode('utf-8')
        entity_uuid = str(uuid.UUID(hashlib.md5(entity_seed).hexdigest()))

        tx.run(
            """
            MERGE (e:Entity:Section {graph_id: $gid, name_lower: $name_lower})
            ON CREATE SET
                e.uuid = $uuid,
                e.name = $name,
                e.summary = $summary,
                e.embedding = $embedding,
                e.created_at = datetime()
            ON MATCH SET
                e.embedding = $embedding,
                e.summary = CASE WHEN e.summary = '' OR e.summary IS NULL THEN $summary ELSE e.summary END
            """,
            gid=graph_id,
            name_lower=section_name.lower(),
            uuid=entity_uuid,
            name=section_name,
            summary=summary,
            embedding=embedding
        )

        # 链接Episode -> Entity
        tx.run(
            """
            MATCH (ep:Episode {uuid: $ep_uuid}), (e:Entity {uuid: $e_uuid})
            MERGE (ep)-[r:MENTIONS]->(e)
            ON CREATE SET r.graph_id = $gid
            """,
            ep_uuid=episode_id,
            e_uuid=entity_uuid,
            gid=graph_id
        )

    def _create_element_entity(self, tx, graph_id: str, episode_id: str,
                               element_type: str, key: str,
                               metadata: Dict, embedding: List[float]):
        """为Element（Formula/Parameter/Term）创建Entity节点"""
        entity_type = element_type.capitalize()
        if entity_type not in ["Formula", "Parameter", "Term"]:
            entity_type = "Parameter"

        entity_name = key if key else f"{element_type}_{metadata.get('source_id', 'unknown')}"

        # 构建 summary：从 metadata 中提取有用信息
        value = metadata.get("value", "")
        unit = metadata.get("unit", "")
        condition = metadata.get("condition", "")
        source_id = metadata.get("source_id", "")

        # 生成 summary 描述
        summary_parts = []
        if source_id:
            summary_parts.append(f"来源: {source_id}")
        if value:
            summary_parts.append(f"值: {value}")
        if unit:
            summary_parts.append(f"单位: {unit}")
        if condition:
            summary_parts.append(f"条件: {condition}")
        summary = " | ".join(summary_parts) if summary_parts else f"{entity_type}类型要素"

        entity_seed = f"{graph_id}:{entity_type}:{entity_name}".encode('utf-8')
        entity_uuid = str(uuid.UUID(hashlib.md5(entity_seed).hexdigest()))

        # 提取 PDF 定位信息（Term 需要）
        pdf_source = metadata.get('source')
        pdf_page = metadata.get('page')
        pdf_bbox = metadata.get('bbox')
        pdf_page_width = metadata.get('page_width')
        pdf_page_height = metadata.get('page_height')

        # Term: 带上 PDF 属性
        extra_props = ""
        extra_params = {}
        if entity_type == "Term":
            extra_props = """,
                e.pdf_source = $pdf_source,
                e.pdf_page = $pdf_page,
                e.pdf_bbox = $pdf_bbox,
                e.pdf_page_width = $pdf_page_width,
                e.pdf_page_height = $pdf_page_height"""
            extra_params = {
                "pdf_source": pdf_source,
                "pdf_page": pdf_page,
                "pdf_bbox": pdf_bbox,
                "pdf_page_width": pdf_page_width,
                "pdf_page_height": pdf_page_height,
            }

        tx.run(
            f"""
            MERGE (e:Entity:`{entity_type}` {{graph_id: $gid, name_lower: $name_lower}})
            ON CREATE SET
                e.uuid = $uuid,
                e.name = $name,
                e.summary = $summary,
                e.embedding = $embedding,
                e.created_at = datetime(){extra_props}
            ON MATCH SET
                e.embedding = $embedding,
                e.summary = CASE WHEN e.summary = '' OR e.summary IS NULL THEN $summary ELSE e.summary END{extra_props}
            """,
            gid=graph_id,
            name_lower=entity_name.lower(),
            uuid=entity_uuid,
            name=entity_name,
            summary=summary,
            embedding=embedding,
            **extra_params
        )

        # 链接Episode -> Entity
        tx.run(
            """
            MATCH (ep:Episode {uuid: $ep_uuid}), (e:Entity {uuid: $e_uuid})
            MERGE (ep)-[r:MENTIONS]->(e)
            ON CREATE SET r.graph_id = $gid
            """,
            ep_uuid=episode_id,
            e_uuid=entity_uuid,
            gid=graph_id
        )

        # Term: 建立 DEFINES -> Clause 的关系
        if entity_type == "Term":
            clause_id = metadata.get('clause_id', '')
            if clause_id:
                clause_name = f"条款{clause_id}"
                clause_seed = f"{graph_id}:{clause_name}".encode('utf-8')
                clause_uuid = str(uuid.UUID(hashlib.md5(clause_seed).hexdigest()))
                tx.run(
                    """
                    MATCH (t:Entity:Term {uuid: $term_uuid}), (c:Entity {uuid: $clause_uuid})
                    MERGE (t)-[r:DEFINES]->(c)
                    ON CREATE SET r.created_at = datetime()
                    """,
                    term_uuid=entity_uuid,
                    clause_uuid=clause_uuid
                )

    def _parse_semantic_from_content(self, content: str, clause_id: str, requirement: str = 'mandatory') -> List[Dict]:
        """
        使用 LLM 从条文内容中解析语义三元组

        Args:
            content: 条文内容
            clause_id: 条文编号（用于日志）
            requirement: 要求类型（mandatory/recommended/prohibited）

        Returns:
            语义三元组列表，每个元素包含 {component, action, obj, condition, requirement}
        """
        from ..utils.llm_client import LLMClient

        if not content:
            return []

        system_prompt = """你是一个工程规范条文语义分析专家。

你的任务是从条文内容中提取**语义三元组**，而不是平铺列表。

## 语义三元组格式
每条条文提取一个或多个三元组：
{
    "triplets": [
        {"component": "施事组件", "action": "实操动作", "obj": "受事对象", "condition": "适用条件", "requirement": "mandatory|recommended|prohibited"}
    ]
}

**三元组含义**：施事组件 →(实操动作)→ 受事对象，在 适用条件下

**✅ 正确示例**：
- "电器应选用符合产品标准的断路器" → {"component": "电器", "action": "选用", "obj": "断路器"}
- "导体在短路条件下应能承受热稳定" → {"component": "导体", "action": "承受", "obj": "热稳定", "condition": "短路条件下"}
- "配电箱严禁使用TN-C系统" → {"component": "配电箱", "action": "使用", "obj": "TN-C系统", "requirement": "prohibited"}

**❌ 错误示例（笛卡尔积，禁止！）**：
{
    "components": ["电器", "断路器"],
    "actions": ["选用", "应符合"],
    "objects": ["产品标准", "额定电压"]
}
→ 不知道 component/action/obj 之间的对应关系，完全错误！

**Action 提取原则**：
- ✅ 实操动作：选用、敷设、连接、设置、接地、承受、安装
- ❌ "符合"、"满足"、"达到" → 省略 action，只填 component + obj

**要求类型**：
- mandatory: 必须、应、须
- recommended: 建议、宜
- prohibited: 严禁、不得、禁止

请直接输出 JSON 格式，不要解释。"""

        user_prompt = f"""分析以下条文，提取语义三元组：

条文编号：{clause_id}
条文内容：{content}

输出 JSON 格式：
{{
    "triplets": [
        {{"component": "施事组件", "action": "实操动作", "obj": "受事对象", "condition": "适用条件", "requirement": "mandatory"}}
    ]
}}

如果没有发现有效三元组，返回：
{{"triplets": []}}"""

        try:
            llm_client = LLMClient()
            logger.info(f"[semantic_parse] 调用 LLM 分析 clause {clause_id}...")
            response = llm_client.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=1024
            )
            logger.info(f"[semantic_parse] LLM 返回: {response}")

            triplets_raw = response.get('triplets', [])
            triplets = []
            for t in triplets_raw:
                comp = (t.get('component') or '').strip()
                act = (t.get('action') or '').strip()
                obj = (t.get('obj') or '').strip()
                cond = (t.get('condition') or '').strip()
                req = t.get('requirement', requirement)
                if comp or act or obj:
                    triplets.append({
                        "component": comp,
                        "action": act,
                        "obj": obj,
                        "condition": cond,
                        "requirement": req
                    })

            logger.info(f"[semantic_parse] LLM 分析 Clause {clause_id}: {len(triplets)} triplets")
            return triplets

        except Exception as e:
            logger.warning(f"[semantic_parse] LLM 调用失败 for clause {clause_id}: {e}")
            return []

    def _create_term_entity(self, tx, graph_id: str, clause_uuid: str,
                           term_name: str, definition: str, source_id: str = "",
                           pdf_source: str = None, pdf_page: int = None,
                           pdf_bboxes: list = None, pdf_page_width: int = None,
                           pdf_page_height: int = None) -> str:
        """创建 Term（术语）实体"""
        entity_seed = f"{graph_id}:Term:{term_name}".encode('utf-8')
        entity_uuid = str(uuid.UUID(hashlib.md5(entity_seed).hexdigest()))

        try:
            tx.run(
                """
                MERGE (e:Entity:Term {graph_id: $gid, name_lower: $name_lower})
                ON CREATE SET
                    e.uuid = $uuid,
                    e.name = $name,
                    e.definition = $definition,
                    e.source_id = $source_id,
                    e.summary = $summary,
                    e.pdf_source = $pdf_source,
                    e.pdf_page = $pdf_page,
                    e.pdf_bboxes = $pdf_bboxes,
                    e.pdf_page_width = $pdf_page_width,
                    e.pdf_page_height = $pdf_page_height,
                    e.created_at = datetime()
                ON MATCH SET
                    e.definition = COALESCE(e.definition, $definition),
                    e.summary = COALESCE(e.summary, $summary),
                    e.pdf_source = COALESCE(e.pdf_source, $pdf_source),
                    e.pdf_page = COALESCE(e.pdf_page, $pdf_page),
                    e.pdf_bboxes = COALESCE(e.pdf_bboxes, $pdf_bboxes),
                    e.pdf_page_width = COALESCE(e.pdf_page_width, $pdf_page_width),
                    e.pdf_page_height = COALESCE(e.pdf_page_height, $pdf_page_height)
                """,
                gid=graph_id,
                name_lower=term_name.lower(),
                uuid=entity_uuid,
                name=term_name,
                definition=definition,
                source_id=source_id,
                summary=f"{term_name}: {definition[:200]}" if definition else term_name,
                pdf_source=pdf_source,
                pdf_page=pdf_page,
                pdf_bboxes=pdf_bboxes,
                pdf_page_width=pdf_page_width,
                pdf_page_height=pdf_page_height,
            )
            logger.debug(f"[hierarchical] Created Term entity: {term_name}")
        except Exception as e:
            logger.debug(f"Failed to create term entity: {e}")

        return entity_uuid

    def _create_defines_relation(self, tx, term_uuid: str, clause_uuid: str):
        """创建 DEFINES 关系: Term --[DEFINES]--> Clause (术语定义来源)"""
        try:
            tx.run(
                """
                MATCH (t:Entity {uuid: $term_uuid}), (c:Entity {uuid: $clause_uuid})
                MERGE (t)-[r:DEFINES]->(c)
                ON CREATE SET r.created_at = datetime()
                """,
                term_uuid=term_uuid,
                clause_uuid=clause_uuid
            )
            logger.debug(f"[hierarchical] Created DEFINES relation: {term_uuid} -> {clause_uuid}")
        except Exception as e:
            logger.debug(f"Failed to create defines relation: {e}")

    def sync_term_entities(self, graph_id: str, clause_id: str, terms: List[Dict]):
        """同步 Clause 的术语实体到 Neo4j（创建 Term 节点 + DEFINES 边）

        terms 格式: [{"term_name": "xxx", "definition": "yyy"}, ...]
        """
        if not terms:
            return

        # 计算 Clause Entity UUID
        clause_name = f"条款{clause_id}"
        clause_seed = f"{graph_id}:{clause_name}".encode('utf-8')
        clause_uuid = str(uuid.UUID(hashlib.md5(clause_seed).hexdigest()))

        def _do_sync(tx):
            for term in terms:
                term_name = term.get('term_name', '')
                definition = term.get('definition', '')
                if not term_name:
                    continue
                # 创建 Term 节点
                term_uuid = self._create_term_entity(
                    tx, graph_id, clause_uuid, term_name, definition, clause_id
                )
                # 创建 DEFINES 关系
                self._create_defines_relation(tx, term_uuid, clause_uuid)
                logger.info(f"[sync_terms] Synced term: {term_name} -> clause {clause_id}")

        with self._driver.session() as session:
            session.execute_write(_do_sync)

    def _find_entity_uuid(self, tx, graph_id: str, entity_type: str, entity_name: str) -> Optional[str]:
        """根据实体类型和名称查找实体UUID"""
        try:
            # 使用白名单验证，防止 Cypher 注入
            safe_label = _safe_label(entity_type, VALID_NODE_LABELS)
            if not safe_label:
                logger.warning(f"[storage] _find_entity_uuid: invalid entity_type '{entity_type}'")
                return None

            result = tx.run(
                f"""
                MATCH (e:Entity:`{{safe_label}}` {{graph_id: $gid, name_lower: $name_lower}})
                RETURN e.uuid AS uuid
                """,
                gid=graph_id,
                name_lower=entity_name.lower()
            )
            records = list(result)
            if records:
                return records[0]["uuid"]
        except Exception as e:
            logger.debug(f"Failed to find entity: {e}")
        return None

    def build_hierarchical_relations(self, graph_id: str) -> int:
        """
        构建层级关系（PART_OF）

        - Clause 5.2.8 PART_OF Section 5.2
        - Section 5.2 PART_OF Section 5 (Chapter)

        Returns:
            构建的关系数量
        """
        count = 0

        with self._driver.session() as session:
            # 获取所有Clause实体
            result = session.run(
                """
                MATCH (c:Clause {graph_id: $gid})
                WHERE c.name_lower STARTS WITH '条款'
                RETURN c.uuid AS uuid, c.name AS name
                """,
                gid=graph_id
            )

            clause_map = {}
            for record in result:
                name = record["name"] or ""
                # 提取条款编号，如 "条款5.2.8" -> "5.2.8"
                import re
                match = re.search(r'(\d+\.\d+\.\d+)', name)
                if match:
                    clause_num = match.group(1)
                    clause_map[clause_num] = record["uuid"]

            # 构建Clause的PART_OF关系
            for clause_num, clause_uuid in clause_map.items():
                parts = clause_num.split('.')
                if len(parts) >= 2:
                    # 5.2.8 -> parent 5.2
                    parent_num = '.'.join(parts[:-1])

                    if parent_num in clause_map:
                        try:
                            session.run(
                                """
                                MATCH (child:Entity {uuid: $child_uuid}), (parent:Entity {uuid: $parent_uuid})
                                MERGE (child)-[r:PART_OF]->(parent)
                                ON CREATE SET r.graph_id = $gid
                                """,
                                child_uuid=clause_uuid,
                                parent_uuid=clause_map[parent_num],
                                gid=graph_id
                            )
                            count += 1
                        except Exception as e:
                            logger.debug(f"Failed to create PART_OF: {e}")

            # 构建Section的层级关系
            section_result = session.run(
                """
                MATCH (s:Entity:Section {graph_id: $gid})
                RETURN s.uuid AS uuid, s.name AS name
                """,
                gid=graph_id
            )

            for record in section_result:
                name = record["name"] or ""
                # 尝试提取章节编号
                import re
                match = re.search(r'^第?(\d+)\.?\d*\s', name)
                if match:
                    chapter_num = match.group(1)
                    chapter_name = f"第{chapter_num}章"
                    # 创建Chapter实体
                    chapter_seed = f"{graph_id}:Section:{chapter_name}".encode('utf-8')
                    chapter_uuid = str(uuid.UUID(hashlib.md5(chapter_seed).hexdigest()))

                    try:
                        session.run(
                            """
                            MERGE (c:Entity:Section {graph_id: $gid, name_lower: $name_lower})
                            ON CREATE SET
                                c.uuid = $uuid,
                                c.name = $name,
                                c.created_at = datetime()
                            """,
                            gid=graph_id,
                            name_lower=chapter_name.lower(),
                            uuid=chapter_uuid,
                            name=chapter_name
                        )

                        # Section PART_OF Chapter
                        session.run(
                            """
                            MATCH (s:Entity {uuid: $s_uuid}), (c:Entity {uuid: $c_uuid})
                            MERGE (s)-[r:PART_OF]->(c)
                            ON CREATE SET r.graph_id = $gid
                            """,
                            s_uuid=record["uuid"],
                            c_uuid=chapter_uuid,
                            gid=graph_id
                        )
                        count += 1
                    except Exception as e:
                        logger.debug(f"Failed to create Section hierarchy: {e}")

        logger.info(f"[hierarchical] Built {count} hierarchical relations")
        return count

