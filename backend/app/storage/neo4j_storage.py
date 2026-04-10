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
from typing import Dict, Any, List, Optional, Callable, Union, TYPE_CHECKING

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

# Action 过滤器：识别非实操性词汇（状态描述而非具体操作）
# 这些词汇不应作为 Action 实体提取
ACTION_FILTER_WORDS = {
    # 状态描述词 - 这些是"符合性"要求，不是具体动作
    "符合", "满足", "达到", "遵守", "遵循", "适应",
    "符合要求", "满足要求", "达到要求", "符合标准", "满足标准",
    "应符合", "应满足", "应达到", "应遵守", "应遵循",
    # 被动/抽象动词
    "承受", "涉及", "属于", "包括",
    # 程度副词 + 动词组合（需要拆分）
    "合理", "正确", "可靠", "安全",
    # 常见的"假动作"短语
    "具有", "具备", "保有", "保持", "维持",
}

# 允许的实操性动作词根（用于验证）
ACTION_ALLOWED_PREFIXES = {
    "安装", "敷设", "连接", "选用", "配置", "设置", "布置",
    "采用", "使用", "应用", "使用", "运用",
    "接地", "接零", "接保护", "屏蔽", "隔离",
    "检测", "试验", "校验", "测量", "检查", "检验",
    "防护", "保护", "报警", "断开", "接通",
    "预留", "预埋", "固定", "支撑", "吊装",
    "配电", "供电", "馈电", "控制",
    "标识", "标记", "标志", "挂牌",
    "阻燃", "耐火", "防腐", "防水",  # 这些通常是材料属性，但可作为动作理解
}


def is_actionable(action_name: str) -> bool:
    """
    判断一个动作名称是否是实操性的。

    Args:
        action_name: 动作名称

    Returns:
        True 如果是实操性动作，False 否则
    """
    if not action_name:
        return False

    action = action_name.strip()

    # 过滤空字符串和太短的
    if len(action) < 2:
        return False

    # 检查是否在黑名单中
    if action in ACTION_FILTER_WORDS:
        return False

    # 检查是否包含黑名单词
    for black_word in ACTION_FILTER_WORDS:
        if black_word in action:
            return False

    # 检查是否以允许的前缀开头
    for prefix in ACTION_ALLOWED_PREFIXES:
        if action.startswith(prefix):
            return True

    # 检查长度：太长的可能是复合短语，需要人工审核
    # 典型实操动作应该在 4 个字以内
    if len(action) > 6:
        # 可能是复合短语，尝试检查是否包含实操词
        for prefix in ACTION_ALLOWED_PREFIXES:
            if prefix in action:
                return True
        # 不包含任何实操词，拒绝
        return False

    # 2-4 个字的中文词，默认允许（需要依赖 LLM 提示词的质量）
    # 但排除纯状态词
    state_words = {"的", "应", "须", "要", "能", "会"}
    if action in state_words or action[0] in state_words:
        return False

    return True


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
            self._uri, auth=(self._user, self._password)
        )
        self._embedding = embedding_service or EmbeddingService()
        self._ner = ner_extractor or NERExtractor()
        self._search = SearchService(self._embedding)

        # Initialize schema (indexes, constraints)
        self._ensure_schema()

    @property
    def driver(self) -> GraphDatabase.driver:
        """Expose the Neo4j driver."""
        return self._driver

    def close(self):
        """Close the Neo4j driver connection."""
        self._driver.close()

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
                neo4j_schema.CREATE_ENTITY_GRAPH_ID_INDEX,
                neo4j_schema.CREATE_ENTITY_NAME_LOWER_INDEX,
                neo4j_schema.CREATE_DOC_GRAPH_ID_INDEX,
                neo4j_schema.CREATE_PAGE_GRAPH_ID_INDEX,
                neo4j_schema.CREATE_EPISODE_GRAPH_ID_INDEX,
                neo4j_schema.CREATE_EPISODE_SOURCE_INDEX,
                neo4j_schema.CREATE_EPISODE_CHUNK_INDEX,
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

            # 3. Create fulltext indexes
            fulltext_queries = [
                ("entity_fulltext", neo4j_schema.CREATE_ENTITY_FULLTEXT_INDEX),
                ("fact_fulltext", neo4j_schema.CREATE_FACT_FULLTEXT_INDEX),
                ("episode_fulltext", neo4j_schema.CREATE_EPISODE_FULLTEXT_INDEX),
            ]

            for index_name, query in fulltext_queries:
                try:
                    if index_name not in existing_indexes:
                        session.run(query)
                        logger.info(f"✅ Fulltext index '{index_name}' created/verified")
                    else:
                        logger.info(f"⏭️ Fulltext index '{index_name}' already exists")
                except Exception as e:
                    logger.warning(f"❌ Fulltext index '{index_name}' creation failed: {e}")

            # 4. Verify all required indexes after creation
            self._verify_indexes(session)

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
            "graph_uuid", "entity_uuid", "episode_uuid",
            "entity_graph_id", "entity_name_lower",
            "entity_embedding", "episode_embedding", "fact_embedding"
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

    def _ensure_document(self, tx, graph_id: str, filename: str) -> str:
        """Ensure Document node exists and link to Graph."""
        import hashlib
        # Use stable UUID based on graph and filename
        doc_seed = f"{graph_id}:{filename}".encode('utf-8')
        doc_uuid = str(uuid.UUID(hashlib.md5(doc_seed).hexdigest()))

        tx.run(
            """
            MATCH (g:Graph {graph_id: $gid})
            MERGE (d:Document {uuid: $doc_uuid})
            ON CREATE SET
                d.name = $name,
                d.graph_id = $gid,
                d.created_at = datetime()
            ON MATCH SET
                d.graph_id = $gid
            MERGE (g)-[r:HAS_DOCUMENT]->(d)
            ON CREATE SET r.graph_id = $gid
            ON MATCH SET r.graph_id = $gid
            """,
            gid=graph_id,
            doc_uuid=doc_uuid,
            name=filename
        )
        return doc_uuid

    def _ensure_page(self, tx, graph_id: str, doc_uuid: str, page_num: int) -> str:
        """Ensure Page node exists and link to Document."""
        import hashlib
        page_seed = f"{doc_uuid}:{page_num}".encode('utf-8')
        page_uuid = str(uuid.UUID(hashlib.md5(page_seed).hexdigest()))

        tx.run(
            """
            MATCH (d:Document {uuid: $doc_uuid})
            MERGE (p:Page {uuid: $page_uuid})
            ON CREATE SET
                p.number = $num,
                p.doc_uuid = $doc_uuid,
                p.graph_id = $gid,
                p.created_at = datetime()
            ON MATCH SET
                p.graph_id = $gid
            MERGE (d)-[r:HAS_PAGE]->(p)
            ON CREATE SET r.graph_id = $gid
            ON MATCH SET r.graph_id = $gid
            """,
            doc_uuid=doc_uuid,
            page_uuid=page_uuid,
            num=page_num,
            gid=graph_id
        )
        return page_uuid

    def get_ontology(self, graph_id: str) -> Dict[str, Any]:
        with self._driver.session() as session:
            result = session.run(
                "MATCH (g:Graph {graph_id: $gid}) RETURN g.ontology_json AS oj",
                gid=graph_id,
            )
            record = result.single()
            if record and record["oj"]:
                return json.loads(record["oj"])
            return {}

    # ----------------------------------------------------------------
    # Add data (NER → nodes/edges)
    # ----------------------------------------------------------------

    def add_text(self, graph_id: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Process text: Create skeleton -> NER/RE -> batch embed -> update knowledge."""
        # Generate stable UUID based on graph_id and content to allow idempotency
        content_seed = f"{graph_id}:{text}".encode('utf-8')
        episode_id = str(uuid.UUID(hashlib.md5(content_seed).hexdigest()))

        now = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)

        # 0. Check if already processed
        with self._driver.session() as session:
            existing = session.run(
                "MATCH (ep:Episode {uuid: $uuid}) RETURN ep.processed AS processed",
                uuid=episode_id
            ).single()
            if existing and existing["processed"]:
                print(f"⏩ [SKIP] Episode {episode_id[:8]} already processed.")
                logger.info(f"⏩ [SKIP] Episode {episode_id[:8]} already processed. Moving to next.")
                return episode_id

        logger.info(f"🔨 [START] Processing Episode {episode_id[:8]} (Content Length: {len(text)})")

        # 1. Create episode node and structural skeleton IMMEDIATELY
        chunk_embedding = []
        try:
            logger.info(f"📡 [STEP 1/6] [{episode_id[:8]}] Requesting Embedding...")
            # Embedding is often the first bottleneck
            chunk_embedding = self._embedding.embed(text)
            logger.info(f"✅ [STEP 1/6] [{episode_id[:8]}] Embedding received.")
        except Exception as e:
            logger.warning(f"❌ [STEP 1/6] [{episode_id[:8]}] Embedding failed: {e}")

        with self._driver.session() as session:
            def _create_skeleton(tx):
                logger.info(f"💾 [STEP 2/6] [{episode_id[:8]}] Creating/Merging Episode node in Neo4j...")

                # Extract indexing properties from metadata for direct storage
                filename = metadata.get("source") if metadata else None
                chunk_idx = metadata.get("chunk_index", 0) if metadata else 0

                # Use MERGE instead of CREATE to handle episodes that were created but not fully processed
                tx.run(
                    """
                    MERGE (ep:Episode {uuid: $uuid})
                    ON CREATE SET
                        ep.graph_id = $graph_id,
                        ep.data = $data,
                        ep.source = $source,
                        ep.chunk_index = $chunk_index,
                        ep.metadata_json = $metadata_json,
                        ep.processed = false,
                        ep.embedding = $embedding,
                        ep.created_at = $created_at
                    ON MATCH SET
                        ep.graph_id = $graph_id,
                        ep.data = $data,
                        ep.source = $source,
                        ep.chunk_index = $chunk_index,
                        ep.metadata_json = $metadata_json,
                        ep.embedding = $embedding
                    """,
                    uuid=episode_id,
                    graph_id=graph_id,
                    data=text,
                    source=filename,
                    chunk_index=chunk_idx,
                    metadata_json=metadata_json,
                    embedding=chunk_embedding,
                    created_at=now,
                )

                # Apply hierarchy labels to Episode node if present in metadata
                hierarchy_type = metadata.get("hierarchy_type") if metadata else None
                if hierarchy_type:
                    # Convert to PascalCase for Neo4j label (e.g., 'chapter' -> 'Chapter')
                    label = hierarchy_type.capitalize()
                    tx.run(f"MATCH (ep:Episode {{uuid: $uuid}}) SET ep:`{label}`", uuid=episode_id)

                # Link to Page/Document if metadata is available
                if metadata:
                    page_num = metadata.get("page")
                    if filename:
                        doc_uuid = self._ensure_document(tx, graph_id, filename)

                        # Sequential linking - Optimized to use indexed properties
                        if chunk_idx > 0:
                            tx.run(
                                """
                                MATCH (prev:Episode {graph_id: $gid, source: $filename, chunk_index: $prev_idx})
                                MATCH (curr:Episode {uuid: $curr_uuid})
                                MERGE (prev)-[r:NEXT_EPISODE]->(curr)
                                ON CREATE SET r.graph_id = $gid
                                """,
                                gid=graph_id,
                                filename=filename,
                                prev_idx=chunk_idx - 1,
                                curr_uuid=episode_id
                            )

                        if page_num:
                            page_uuid = self._ensure_page(tx, graph_id, doc_uuid, page_num)
                            tx.run(
                                """
                                MATCH (p:Page {uuid: $p_uuid}), (ep:Episode {uuid: $ep_uuid})
                                MERGE (p)-[r:HAS_EPISODE]->(ep)
                                ON CREATE SET r.graph_id = $gid
                                ON MATCH SET r.graph_id = $gid
                                """,
                                p_uuid=page_uuid, ep_uuid=episode_id, gid=graph_id
                            )
                        else:
                            tx.run(
                                """
                                MATCH (d:Document {uuid: $d_uuid}), (ep:Episode {uuid: $ep_uuid})
                                MERGE (d)-[r:HAS_EPISODE]->(ep)
                                ON CREATE SET r.graph_id = $gid
                                ON MATCH SET r.graph_id = $gid
                                """,
                                d_uuid=doc_uuid, ep_uuid=episode_id, gid=graph_id
                            )

            self._call_with_retry(session.execute_write, _create_skeleton)
            logger.info(f"✅ [STEP 2/6] [{episode_id[:8]}] Episode skeleton ready.")

        # 2. Knowledge Extraction (Guided by Ontology)
        logger.info(f"🔍 [STEP 3/6] [{episode_id[:8]}] Fetching ontology...")
        ontology = self.get_ontology(graph_id)

        logger.info(f"🤖 [STEP 4/6] [{episode_id[:8]}] Calling LLM for NER extraction (Chat)...")
        # This is where 'chat' method is called
        extraction = self._ner.extract(text, ontology)
        entities = extraction.get("entities", [])
        relations = extraction.get("relations", [])

        logger.info(f"✅ [STEP 4/6] [{episode_id[:8]}] NER extracted {len(entities)} entities and {len(relations)} relations.")

        # 3. Batch embed all extraction results
        entity_summaries = []
        for e in entities:
            # Prefer LLM-extracted description, fallback to "Name (Type)"
            desc = e.get("description")
            if not desc or len(desc) < 3:
                desc = f"{e['name']} ({e['type']})"
            entity_summaries.append(desc)

        fact_texts = [r.get("fact", f"{r['source']} {r['type']} {r['target']}") for r in relations]
        all_texts_to_embed = entity_summaries + fact_texts

        all_embeddings: list = []
        if all_texts_to_embed:
            try:
                logger.info(f"📡 [STEP 5/6] [{episode_id[:8]}] Embedding {len(all_texts_to_embed)} facts/entities...")
                all_embeddings = self._embedding.embed_batch(all_texts_to_embed)
                logger.info(f"✅ [STEP 5/6] [{episode_id[:8]}] Knowledge embeddings received.")
            except Exception as e:
                logger.warning(f"❌ [STEP 5/6] [{episode_id[:8]}] Knowledge embedding failed: {e}")
                all_embeddings = [[] for _ in all_texts_to_embed]

        entity_embeddings = all_embeddings[:len(entities)]
        relation_embeddings = all_embeddings[len(entities):]

        # 4. Write knowledge back to Neo4j
        logger.info(f"💾 [STEP 6/6] [{episode_id[:8]}] Writing entities and relations to Neo4j...")
        with self._driver.session() as session:
            # MERGE entities and link to episode
            entity_uuid_map: Dict[str, str] = {}
            for idx, entity in enumerate(entities):
                ename = entity["name"]
                etype = entity["type"]
                attrs = entity.get("attributes", {})
                summary_text = entity_summaries[idx]
                embedding = entity_embeddings[idx] if idx < len(entity_embeddings) else []

                def _merge_knowledge(tx, _name=ename, _type=etype, _attrs=attrs,
                                    _emb=embedding, _summary=summary_text, _ep_id=episode_id):
                    # 1. First get existing attributes if any
                    existing = tx.run(
                        "MATCH (n:Entity {graph_id: $gid, name_lower: $name_lower}) RETURN n.attributes_json AS attrs",
                        gid=graph_id, name_lower=_name.lower()
                    ).single()

                    final_attrs = _attrs
                    if existing and existing["attrs"]:
                        try:
                            old_attrs = json.loads(existing["attrs"])
                            # Merge: new attributes override old ones
                            final_attrs = {**old_attrs, **_attrs}
                        except:
                            pass

                    # 2. Merge Entity
                    res = tx.run(
                        """
                        MERGE (n:Entity {graph_id: $gid, name_lower: $name_lower})
                        ON CREATE SET
                            n.uuid = randomUUID(),
                            n.name = $name,
                            n.summary = $summary,
                            n.attributes_json = $attrs_json,
                            n.embedding = $embedding,
                            n.created_at = datetime()
                        ON MATCH SET
                            n.summary = CASE WHEN n.summary = '' OR n.summary IS NULL THEN $summary ELSE n.summary END,
                            n.attributes_json = $attrs_json,
                            n.embedding = $embedding
                        RETURN n.uuid AS uuid
                        """,
                        gid=graph_id, name_lower=_name.lower(), name=_name,
                        summary=_summary, attrs_json=json.dumps(final_attrs, ensure_ascii=False),
                        embedding=_emb
                    )
                    e_uuid = res.single()["uuid"]

                    # Link Episode -> Entity (for source tracking)
                    tx.run(
                        """
                        MATCH (n:Entity {uuid: $e_uuid}), (ep:Episode {uuid: $ep_id})
                        MERGE (ep)-[r:MENTIONS]->(n)
                        ON CREATE SET r.graph_id = $gid
                        """,
                        e_uuid=e_uuid, ep_id=_ep_id, gid=graph_id
                    )

                    # Add label
                    if _type and _type != "Entity":
                        tx.run(f"MATCH (n:Entity {{uuid: $uuid}}) SET n:`{_type}`", uuid=e_uuid)

                    # 3. Auto-Hierarchy for Chapters, Sections, and Clauses
                    import re
                    # Match clause like 3.1.1 (links to 3.1)
                    clause_match = re.match(r'^(\d+\.\d+)\.\d+$', _name)
                    # Match section like 3.1 (links to Chapter 3)
                    section_match = re.match(r'^(\d+)\.\d+$', _name)

                    if clause_match:
                        parent_name = clause_match.group(1)
                        # Create/Link to parent section automatically
                        tx.run(
                            """
                            MERGE (p:Entity {graph_id: $gid, name_lower: $p_name_lower})
                            ON CREATE SET
                                p.uuid = randomUUID(),
                                p.name = $p_name,
                                p.created_at = datetime()
                            WITH p
                            MATCH (c:Entity {uuid: $c_uuid})
                            MERGE (c)-[r:PART_OF]->(p)
                            ON CREATE SET r.graph_id = $gid
                            """,
                            gid=graph_id, p_name_lower=parent_name.lower(), p_name=parent_name,
                            c_uuid=e_uuid
                        )
                    elif section_match:
                        chapter_num = section_match.group(1)
                        parent_name = f"第{chapter_num}章"
                        # Create/Link to parent chapter automatically
                        tx.run(
                            """
                            MERGE (p:Entity {graph_id: $gid, name_lower: $p_name_lower})
                            ON CREATE SET
                                p.uuid = randomUUID(),
                                p.name = $p_name,
                                p.created_at = datetime()
                            WITH p
                            MATCH (c:Entity {uuid: $c_uuid})
                            MERGE (c)-[r:PART_OF]->(p)
                            ON CREATE SET r.graph_id = $gid
                            """,
                            gid=graph_id, p_name_lower=parent_name.lower(), p_name=parent_name,
                            c_uuid=e_uuid
                        )

                    return e_uuid

                actual_uuid = self._call_with_retry(session.execute_write, _merge_knowledge)
                entity_uuid_map[ename.lower()] = actual_uuid

            # Create or Merge relations
            for idx, relation in enumerate(relations):
                s_name = relation["source"]
                t_name = relation["target"]
                r_type = relation["type"]
                fact = relation["fact"]
                s_uuid = entity_uuid_map.get(s_name.lower())
                t_uuid = entity_uuid_map.get(t_name.lower())

                # Issue 1 Fix: Prevent self-loops (Entity pointing to itself)
                if s_uuid and t_uuid and s_uuid == t_uuid:
                    logger.debug(f"[add_text] Skipping self-loop relation: {s_name} --[{r_type}]--> {t_name}")
                    continue

                if s_uuid and t_uuid:
                    fact_emb = relation_embeddings[idx] if idx < len(relation_embeddings) else []

                    def _merge_rel(tx, _suid=s_uuid, _tuid=t_uuid, _rt=r_type, _f=fact, _fe=fact_emb, _ep_id=episode_id):
                        # 1. Check for existing relation to merge episode_ids
                        existing_rel = tx.run(
                            """
                            MATCH (src:Entity {uuid: $suid})-[r:RELATION {graph_id: $gid, name: $name}]->(tgt:Entity {uuid: $tuid})
                            RETURN r.episode_ids AS ep_ids
                            """,
                            suid=_suid, tuid=_tuid, gid=graph_id, name=_rt
                        ).single()

                        new_ep_ids = [_ep_id]
                        if existing_rel and existing_rel["ep_ids"]:
                            old_ep_ids = existing_rel["ep_ids"]
                            if _ep_id not in old_ep_ids:
                                new_ep_ids = old_ep_ids + [_ep_id]
                            else:
                                new_ep_ids = old_ep_ids

                        # 2. MERGE relationship between entities based on type and graph
                        tx.run(
                            """
                            MATCH (src:Entity {uuid: $suid}), (tgt:Entity {uuid: $tuid})
                            MERGE (src)-[r:RELATION {graph_id: $gid, name: $name}]->(tgt)
                            ON CREATE SET
                                r.uuid = randomUUID(),
                                r.fact = $fact,
                                r.fact_embedding = $fact_embedding,
                                r.episode_ids = $ep_ids,
                                r.created_at = datetime()
                            ON MATCH SET
                                r.episode_ids = $ep_ids,
                                r.fact = CASE WHEN r.fact = '' OR r.fact IS NULL THEN $fact ELSE r.fact END
                            """,
                            suid=_suid, tuid=_tuid, gid=graph_id, name=_rt,
                            fact=_f, fact_embedding=_fe, ep_ids=new_ep_ids
                        )
                    self._call_with_retry(session.execute_write, _merge_rel)

            # CRITICAL: Mark episode as processed ONLY AFTER EVERYTHING IS DONE
            session.run("MATCH (ep:Episode {uuid: $uuid}) SET ep.processed = true", uuid=episode_id)

        logger.info(f"✅ [DONE] Episode {episode_id[:8]} processed successfully.")
        return episode_id

    def add_text_batch(
        self,
        graph_id: str,
        chunks: List[Union[str, Any]],
        batch_size: int = 5,
        progress_callback: Optional[Callable] = None,
    ) -> List[str]:
        """
        Batch-add text chunks sequentially to ensure stability and clear progress.
        Leverages the 'already processed' skip logic for fast resumption.
        """
        episode_ids = []
        total = len(chunks)
        completed = 0

        logger.info(f"🚀 [BATCH START] Processing {total} chunks for graph {graph_id}...")

        # We process sequentially to fulfill the user's request for "one by one"
        # and to ensure a single hung chunk doesn't block a parallel batch.
        # Speed is maintained via the fast-skip logic for already processed chunks.
        for idx, chunk_data in enumerate(chunks):
            # Normalize chunk data
            if hasattr(chunk_data, 'text') and hasattr(chunk_data, 'metadata'):
                text = chunk_data.text
                metadata = chunk_data.metadata
            elif isinstance(chunk_data, dict) and "text" in chunk_data:
                text = chunk_data["text"]
                metadata = chunk_data.get("metadata")
            else:
                text = str(chunk_data)
                metadata = None

            if not text or not text.strip():
                completed += 1
                continue

            log_msg = ""
            try:
                # Call add_text (which handles the skip logic internally)
                episode_id = self.add_text(graph_id, text, metadata=metadata)
                episode_ids.append(episode_id)
                log_msg = f"Chunk {idx+1}/{total} handled."
            except Exception as e:
                log_msg = f"❌ Error processing chunk {idx+1}: {e}"
                logger.error(f"{log_msg}\n{traceback.format_exc()}")

            completed += 1
            if progress_callback:
                import inspect
                sig = inspect.signature(progress_callback)
                if len(sig.parameters) >= 2:
                    # Report progress to UI
                    progress_callback(
                        f"Extracted {completed}/{total} chunks...",
                        completed / total,
                        log=log_msg
                    )
                else:
                    progress_callback(completed / total)

            if completed % 10 == 0 or completed == total:
                logger.info(f"📊 Progress: {completed}/{total} chunks handled.")

        logger.info(f"🏁 [BATCH END] All {total} chunks handled.")
        return episode_ids

    def wait_for_processing(
        self,
        episode_ids: List[str],
        progress_callback: Optional[Callable] = None,
        timeout: int = 600,
    ) -> None:
        """No-op — processing is synchronous in Neo4j."""
        if progress_callback:
            progress_callback(1.0)

    # ----------------------------------------------------------------
    # Read nodes
    # ----------------------------------------------------------------

    def get_all_nodes(self, graph_id: str, limit: int = 2000) -> List[Dict[str, Any]]:
        def _read(tx):
            result = tx.run(
                """
                MATCH (n:Entity {graph_id: $gid})
                RETURN n, labels(n) AS labels
                ORDER BY n.created_at DESC
                LIMIT $limit
                """,
                gid=graph_id,
                limit=limit,
            )
            return [self._node_to_dict(record["n"], record["labels"]) for record in result]

        with self._driver.session() as session:
            return self._call_with_retry(session.execute_read, _read)

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

    def update_node_labels(
        self,
        graph_id: str,
        node_uuid: str,
        add_labels: List[str],
        remove_labels: Optional[List[str]] = None
    ) -> bool:
        """
        更新节点的标签

        Args:
            graph_id: 图谱ID
            node_uuid: 节点UUID
            add_labels: 要添加的标签列表（如 ["Term"] 或 ["Entity"]）
            remove_labels: 要移除的标签列表（可选）

        Returns:
            是否成功
        """
        if not add_labels and not remove_labels:
            return True

        try:
            with self._driver.session() as session:
                def _update(tx):
                    # 首先检查节点是否存在
                    check = tx.run(
                        "MATCH (n {uuid: $uuid, graph_id: $gid}) RETURN count(n) as cnt",
                        uuid=node_uuid,
                        gid=graph_id
                    ).single()
                    if not check or check["cnt"] == 0:
                        return False

                    # 添加标签
                    if add_labels:
                        for label in add_labels:
                            # 标签名只允许特定值，防止注入
                            if label not in ("Term", "Entity", "Component", "Action", "Condition"):
                                continue
                            tx.run(
                                f"MATCH (n {{uuid: $uuid, graph_id: $gid}}) SET n:`{label}`",
                                uuid=node_uuid,
                                gid=graph_id
                            )

                    # 移除标签
                    if remove_labels:
                        for label in remove_labels:
                            if label not in ("Term", "Entity", "Object", "Component", "Action", "Condition"):
                                continue
                            tx.run(
                                f"MATCH (n {{uuid: $uuid, graph_id: $gid}}) REMOVE n:`{label}`",
                                uuid=node_uuid,
                                gid=graph_id
                            )
                    return True

                return self._call_with_retry(session.execute_write, _update)
        except Exception as e:
            logger.warning(f"[storage] update_node_labels failed: {e}")
            return False

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

    def get_edges_for_nodes_batch(self, node_uuids: List[str], bidirectional: bool = True) -> Dict[str, List[Dict[str, Any]]]:
        """批量获取多个节点的所有边（一次性 Cypher 查询，支持 Entity/Topic/Clause）"""
        if not node_uuids:
            return {}
        def _read(tx):
            rel_type = "[r]-(m)" if bidirectional else "[r]->(m)"
            result = tx.run(
                f"""
                MATCH (n)-{rel_type}
                WHERE n.uuid IN $uuids AND (n:Entity OR n:Topic OR n:Clause)
                RETURN n.uuid AS node_uuid, r, startNode(r).uuid AS src_uuid, endNode(r).uuid AS tgt_uuid
                """,
                uuids=node_uuids,
            )
            edges_map: Dict[str, List[Dict[str, Any]]] = {uid: [] for uid in node_uuids}
            for record in result:
                src_uuid = record["src_uuid"]
                edges_map.setdefault(src_uuid, []).append(
                    self._edge_to_dict(record["r"], record["src_uuid"], record["tgt_uuid"])
                )
            return edges_map
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
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        直接查询 Entity → Topic → Clause 路径。

        路径: (e:Entity)-[:MENTIONS]-(t:Topic)-[:HAS_TOPIC]-(c:Clause)
        返回格式: { entity_uuid: [ {topic_uuid, clause_uuid}, ... ], ... }

        Args:
            entity_uuids: Entity 节点 UUID 列表

        Returns:
            Dict mapping entity_uuid to list of {topic_uuid, clause_uuid} path info
        """
        if not entity_uuids:
            return {}

        def _read(tx):
            result = tx.run(
                """
                MATCH (e:Entity)-[:MENTIONS]-(t:Topic)-[:HAS_TOPIC]-(c:Clause)
                WHERE e.uuid IN $uuids
                RETURN e.uuid AS entity_uuid, t.uuid AS topic_uuid, c.uuid AS clause_uuid
                """,
                uuids=entity_uuids,
            )
            paths_map: Dict[str, List[Dict[str, Any]]] = {uid: [] for uid in entity_uuids}
            for record in result:
                entity_uuid = record["entity_uuid"]
                if entity_uuid in paths_map:
                    paths_map[entity_uuid].append({
                        "topic_uuid": record["topic_uuid"],
                        "clause_uuid": record["clause_uuid"],
                    })
            return paths_map

        with self._driver.session() as session:
            return self._call_with_retry(session.execute_read, _read)

    def get_nodes_by_label(self, graph_id: str, label: str) -> List[Dict[str, Any]]:
        def _read(tx):
            # Dynamic label in query (safe — label comes from ontology, not user input)
            query = f"""
                MATCH (n:Entity:`{label}` {{graph_id: $gid}})
                RETURN n, labels(n) AS labels
            """
            result = tx.run(query, gid=graph_id)
            return [self._node_to_dict(record["n"], record["labels"]) for record in result]

        with self._driver.session() as session:
            return self._call_with_retry(session.execute_read, _read)

    # ----------------------------------------------------------------
    # Read edges
    # ----------------------------------------------------------------

    def get_all_edges(self, graph_id: str) -> List[Dict[str, Any]]:
        def _read(tx):
            result = tx.run(
                """
                MATCH (src:Entity)-[r:RELATION {graph_id: $gid}]->(tgt:Entity)
                RETURN r, src.uuid AS src_uuid, tgt.uuid AS tgt_uuid
                ORDER BY r.created_at DESC
                """,
                gid=graph_id,
            )
            return [
                self._edge_to_dict(record["r"], record["src_uuid"], record["tgt_uuid"])
                for record in result
            ]

        with self._driver.session() as session:
            return self._call_with_retry(session.execute_read, _read)

    def get_episodes(self, episode_uuids: List[str]) -> List[Dict[str, Any]]:
        if not episode_uuids:
            return []

        def _read(tx):
            # Enriched query to get document and page info via relationships
            # Also get adjacent episodes for context
            result = tx.run(
                """
                MATCH (ep:Episode)
                WHERE ep.uuid IN $uuids
                OPTIONAL MATCH (p:Page)-[:HAS_EPISODE]->(ep)
                OPTIONAL MATCH (d:Document)-[:HAS_PAGE]->(p)
                OPTIONAL MATCH (d2:Document)-[:HAS_EPISODE]->(ep)
                OPTIONAL MATCH (prev:Episode)-[:NEXT_EPISODE]->(ep)
                OPTIONAL MATCH (ep)-[:NEXT_EPISODE]->(next:Episode)
                RETURN ep, p.number AS page_num,
                       coalesce(d.name, d2.name) AS doc_name,
                       prev.data AS prev_text,
                       next.data AS next_text
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
                try:
                    metadata = json.loads(meta_json) if meta_json else {}
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

                # Overlay graph structure info onto metadata if present
                if record["doc_name"]:
                    metadata["source"] = record["doc_name"]
                if record["page_num"]:
                    metadata["page"] = record["page_num"]

                # Include context in metadata
                metadata["prev_context"] = record["prev_text"]
                metadata["next_context"] = record["next_text"]

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

    def get_term_clause_episodes(self, term_uuid: str, limit: int = 1) -> List[Dict[str, Any]]:
        """Get episodes for the Clause that a Term DEFINES, for PDF tracing.

        Fallback: If no DEFINES relationship exists (e.g., Term created via add_topic_and_entity_nodes),
        try to find Episode via Topic path: Term <-MENTIONS- Topic <-HAS_TOPIC- Episode
        """
        def _read(tx):
            # Try the standard DEFINES path first
            result = tx.run(
                """
                MATCH (t:Entity:Term {uuid: $term_uuid})-[:DEFINES]->(c:Entity)
                OPTIONAL MATCH (c)<-[:MENTIONS]-(ep:Episode)
                OPTIONAL MATCH (p:Page)-[:HAS_EPISODE]->(ep)
                OPTIONAL MATCH (d:Document)-[:HAS_PAGE]->(p)
                OPTIONAL MATCH (d2:Document)-[:HAS_EPISODE]->(ep)
                RETURN ep, p.number AS page_num,
                       coalesce(d.name, d2.name) AS doc_name, 0 AS via_topic
                LIMIT $limit
                """,
                term_uuid=term_uuid,
                limit=limit,
            )
            episodes = []
            for record in result:
                ep_node = record.get("ep")
                if ep_node:
                    props = dict(ep_node)
                    for k, v in props.items():
                        if hasattr(v, "isoformat"):
                            props[k] = v.isoformat()
                    meta_json = props.pop("metadata_json", "{}")
                    try:
                        metadata = json.loads(meta_json) if meta_json else {}
                    except (json.JSONDecodeError, TypeError):
                        metadata = {}
                    if record["doc_name"]:
                        metadata["source"] = record["doc_name"]
                    if record["page_num"]:
                        metadata["page"] = record["page_num"]
                    episodes.append({
                        "uuid": props.get("uuid"),
                        "text": props.get("data"),
                        "source": props.get("source"),
                        "page": props.get("page"),
                        "metadata": metadata,
                        "created_at": props.get("created_at")
                    })

            # Fallback: if no episodes found via DEFINES, try via Topic path
            # Path: Term <-MENTIONS- Topic <-HAS_TOPIC- Episode
            if not episodes:
                result = tx.run(
                    """
                    MATCH (et:Entity:Term {uuid: $term_uuid})<-[:MENTIONS]-(t:Topic)
                    MATCH (t)<-[:HAS_TOPIC]-(ep:Episode)
                    OPTIONAL MATCH (p:Page)-[:HAS_EPISODE]->(ep)
                    OPTIONAL MATCH (d:Document)-[:HAS_PAGE]->(p)
                    OPTIONAL MATCH (d2:Document)-[:HAS_EPISODE]->(ep)
                    RETURN ep, p.number AS page_num,
                           coalesce(d.name, d2.name) AS doc_name
                    LIMIT $limit
                    """,
                    term_uuid=term_uuid,
                    limit=limit,
                )
                for record in result:
                    ep_node = record.get("ep")
                    if ep_node:
                        props = dict(ep_node)
                        for k, v in props.items():
                            if hasattr(v, "isoformat"):
                                props[k] = v.isoformat()
                        meta_json = props.pop("metadata_json", "{}")
                        try:
                            metadata = json.loads(meta_json) if meta_json else {}
                        except (json.JSONDecodeError, TypeError):
                            metadata = {}
                        if record["doc_name"]:
                            metadata["source"] = record["doc_name"]
                        if record["page_num"]:
                            metadata["page"] = record["page_num"]
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

    def get_node_episodes(self, node_uuid: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Get episodes (text chunks) that mentioned this node for traceability."""
        def _read(tx):
            result = tx.run(
                """
                MATCH (n:Entity {uuid: $uuid})<-[:MENTIONS]-(ep:Episode)
                OPTIONAL MATCH (p:Page)-[:HAS_EPISODE]->(ep)
                OPTIONAL MATCH (d:Document)-[:HAS_PAGE]->(p)
                OPTIONAL MATCH (d2:Document)-[:HAS_EPISODE]->(ep)
                RETURN ep, p.number AS page_num,
                       coalesce(d.name, d2.name) AS doc_name
                LIMIT $limit
                """,
                uuid=node_uuid,
                limit=limit
            )
            episodes = []
            for record in result:
                props = dict(record["ep"])
                meta_json = props.pop("metadata_json", "{}")
                try:
                    metadata = json.loads(meta_json) if meta_json else {}
                except:
                    metadata = {}

                if record["doc_name"]:
                    metadata["source"] = record["doc_name"]
                if record["page_num"]:
                    metadata["page"] = record["page_num"]

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
    ) -> List[Dict[str, Any]]:
        """
        Search Entity nodes specifically using hybrid scoring (vector + BM25).
        Only returns nodes with label 'Entity'.

        Returns list of dicts with node properties + 'score'.
        """
        with self._driver.session() as session:
            results = self._search.search_object_nodes(
                session, graph_id, query, limit, min_score
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
    ) -> List[Dict[str, Any]]:
        """
        Search Term nodes specifically using hybrid scoring (vector + keyword).
        Only returns nodes with label 'Term'.

        Returns list of dicts with node properties + 'score'.
        """
        with self._driver.session() as session:
            results = self._search.search_term_nodes(
                session, graph_id, query, limit, min_score
            )
            for n in results:
                for k, v in n.items():
                    if hasattr(v, "isoformat"):
                        n[k] = v.isoformat()
            return results

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
            node_count = node_result.single()["cnt"]

            # Count edges
            edge_result = tx.run(
                "MATCH ()-[r:RELATION {graph_id: $gid}]->() RETURN count(r) AS cnt",
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

            # MENTIONS: Topic → Entity 或 Topic → Entity:Term
            mentions_result = tx.run(
                "MATCH (t:Topic)-[r:MENTIONS]->(e) WHERE t.graph_id = $gid AND (e:Entity OR e:Entity:Term) RETURN count(r) AS cnt",
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
            # This enables "click node to locate document" feature
            node_uuids = [n["uuid"] for n in nodes]
            if node_uuids:
                # Query Episode nodes connected to Entity nodes via HAS_EPISODE
                episode_result = tx.run(
                    """
                    MATCH (e:Entity {graph_id: $gid})-[r:HAS_EPISODE]->(ep:Episode)
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
                                try:
                                    pdf_bboxes = json.loads(pdf_bboxes_raw) if isinstance(pdf_bboxes_raw, str) else pdf_bboxes_raw
                                except (json.JSONDecodeError, TypeError):
                                    pdf_bboxes = []
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
            # 匹配所有语义关系类型（Entity-Entity + Topic关系）
            edge_result = tx.run(
                """
                MATCH (src:Entity {graph_id: $gid})-[r]->(tgt:Entity {graph_id: $gid})
                WHERE type(r) IN ['RELATION', 'MANDATES', 'PROHIBITS', 'RECOMMENDS',
                                   'HAS_CONDITION', 'OPERATES_ON', 'APPLIES_TO',
                                   'IN_SITUATION',
                                   'PART_OF', 'NEXT_EPISODE', 'MENTIONS', 'CROSS_REFERENCE',
                                   'HAS_DOCUMENT', 'HAS_PAGE', 'HAS_EPISODE']
                RETURN r, src.uuid AS src_uuid, tgt.uuid AS tgt_uuid,
                       src.name AS src_name, tgt.name AS tgt_name,
                       type(r) AS rel_type
                UNION
                MATCH (ep:Episode {graph_id: $gid})-[r:HAS_TOPIC]->(t:Topic {graph_id: $gid})
                RETURN r, ep.uuid AS src_uuid, t.uuid AS tgt_uuid,
                       ep.data AS src_name, t.name AS tgt_name,
                       type(r) AS rel_type
                UNION
                MATCH (t:Topic {graph_id: $gid})-[r:MENTIONS]->(e:Entity {graph_id: $gid})
                RETURN r, t.uuid AS src_uuid, e.uuid AS tgt_uuid,
                       t.name AS src_name, e.name AS tgt_name,
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

            # MENTIONS: Topic → Entity 或 Topic → Entity:Term
            mentions_result = tx.run(
                "MATCH (t:Topic)-[r:MENTIONS]->(e) WHERE t.graph_id = $gid AND (e:Entity OR e:Entity:Term) RETURN count(r) AS cnt",
                gid=graph_id,
            )
            mentions_count = mentions_result.single()["cnt"]

            return has_topic_count, mentions_count

        with self._driver.session() as session:
            result = self._call_with_retry(session.execute_read, _read)
            has_topic_count, mentions_count = self._call_with_retry(session.execute_read, _read_relation_stats)
            result["has_topic_count"] = has_topic_count
            result["mentions_count"] = mentions_count
            return result

    # ----------------------------------------------------------------
    # Dict conversion helpers
    # ----------------------------------------------------------------

    @staticmethod
    def _node_to_dict(node, labels: List[str]) -> Dict[str, Any]:
        """Convert Neo4j node to the standard node dict format."""
        props = dict(node)

        # Convert Neo4j DateTime to string
        for k, v in props.items():
            if hasattr(v, "isoformat"):
                props[k] = v.isoformat()

        attrs_json = props.pop("attributes_json", "{}")
        try:
            attributes = json.loads(attrs_json) if attrs_json else {}
        except (json.JSONDecodeError, TypeError):
            attributes = {}

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
        else:
            # Keep all labels including Entity for frontend stats
            # Convert to list in case Neo4j returns special iterable type
            labels_list = list(labels) if labels else []
            display_labels = labels_list
            display_name = props.get("name", "")
            # Debug: log Entity nodes with their labels
            if "Entity" in labels_list and not any(l in labels_list for l in ["Term", "Clause", "Section", "Component", "Action", "Condition", "Parameter", "Formula", "ExternalStandard"]):
                logger.info(f"[DEBUG] Entity node: uuid={props.get('uuid')}, name={props.get('name')}, labels={display_labels}")

        return {
            "uuid": props.get("uuid", ""),
            "name": display_name,
            "labels": display_labels,
            "summary": props.get("summary", ""),
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
        }

    @staticmethod
    def _edge_to_dict(rel, source_uuid: str, target_uuid: str) -> Dict[str, Any]:
        """Convert Neo4j relationship to the standard edge dict format."""
        props = dict(rel)
        # 从 Neo4j relationship type 提取边类型名（如 MANDATES, OPERATES_ON）
        rel_type = rel.type if hasattr(rel, 'type') else ''

        # Convert Neo4j DateTime to string
        for k, v in props.items():
            if hasattr(v, "isoformat"):
                props[k] = v.isoformat()

        attrs_json = props.pop("attributes_json", "{}")
        try:
            attributes = json.loads(attrs_json) if attrs_json else {}
        except (json.JSONDecodeError, TypeError):
            attributes = {}

        # Remove internal fields
        props.pop("fact_embedding", None)

        episode_ids = props.get("episode_ids", [])
        if episode_ids and not isinstance(episode_ids, list):
            episode_ids = [str(episode_ids)]

        return {
            "uuid": props.get("uuid", ""),
            # 优先用 name 属性（RELATION 边），fallback 到 Neo4j relationship type
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

    def add_hierarchical_chunk(
        self,
        graph_id: str,
        chunk_data: Dict[str, Any]
    ) -> str:
        """
        存储多层级chunk及其引用关系

        Args:
            graph_id: 图谱ID
            chunk_data: 包含以下字段的字典：
                - level: 1/2/3
                - content: 文本内容
                - chunk_type: section/clause/element
                - metadata: 元数据字典

        Returns:
            episode_uuid
        """
        level = chunk_data.get("level", 2)
        content = chunk_data.get("content", "")
        chunk_type = chunk_data.get("chunk_type", "clause")
        metadata = chunk_data.get("metadata", {})

        # 生成稳定UUID
        content_seed = f"{graph_id}:{content}".encode('utf-8')
        episode_id = str(uuid.UUID(hashlib.md5(content_seed).hexdigest()))

        now = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata, ensure_ascii=False)

        # 生成embedding
        embedding = []
        try:
            embedding = self._embedding.embed(content)
        except Exception as e:
            logger.warning(f"[hierarchical] Embedding failed: {e}")

        with self._driver.session() as session:
            def _create_hierarchical_episode(tx):
                # 创建Episode节点
                tx.run(
                    """
                    MERGE (ep:Episode {uuid: $uuid})
                    ON CREATE SET
                        ep.graph_id = $graph_id,
                        ep.data = $data,
                        ep.metadata_json = $metadata_json,
                        ep.processed = true,
                        ep.embedding = $embedding,
                        ep.created_at = $created_at
                    ON MATCH SET
                        ep.graph_id = $graph_id,
                        ep.data = $data,
                        ep.metadata_json = $metadata_json,
                        ep.embedding = $embedding
                    """,
                    uuid=episode_id,
                    graph_id=graph_id,
                    data=content,
                    metadata_json=metadata_json,
                    embedding=embedding,
                    created_at=now,
                )

                # 添加层级标签
                level_label = f"Level{level}"
                tx.run(
                    f"MATCH (ep:Episode {{uuid: $uuid}}) SET ep:`{level_label}`",
                    uuid=episode_id
                )

                # 添加类型标签
                if chunk_type:
                    type_label = chunk_type.capitalize()
                    tx.run(
                        f"MATCH (ep:Episode {{uuid: $uuid}}) SET ep:`{type_label}`",
                        uuid=episode_id
                    )

                # 如果是条文，提取clause_id并添加标签
                clause_id = metadata.get("clause_id")
                if clause_id:
                    tx.run(
                        """
                        MATCH (ep:Episode {uuid: $uuid})
                        SET ep.clause_id = $clause_id
                        """,
                        uuid=episode_id,
                        clause_id=clause_id
                    )

            self._call_with_retry(session.execute_write, _create_hierarchical_episode)

        logger.info(f"[hierarchical] Created episode {episode_id[:8]} (Level{level}, {chunk_type})")
        return episode_id

    def add_hierarchical_chunks_batch(
        self,
        graph_id: str,
        chunks: List[Dict[str, Any]],
        progress_callback: Optional[Callable] = None
    ) -> List[str]:
        """
        批量存储多层级chunks

        Args:
            graph_id: 图谱ID
            chunks: chunk列表
            progress_callback: 进度回调

        Returns:
            episode_uuid列表
        """
        episode_ids = []
        total = len(chunks)

        for idx, chunk in enumerate(chunks):
            try:
                episode_id = self.add_hierarchical_chunk(graph_id, chunk)
                episode_ids.append(episode_id)
            except Exception as e:
                logger.error(f"[hierarchical] Failed to add chunk: {e}")

            if progress_callback and (idx + 1) % 10 == 0:
                progress_callback((idx + 1) / total)

        return episode_ids

    def add_clause_relations(
        self,
        graph_id: str,
        clause_uuid: str,
        relations: List[Dict[str, Any]],
        episode_ids: List[str]
    ) -> None:
        """
        为条文添加语义关系（由SemanticEnricher生成）

        Args:
            graph_id: 图谱ID
            clause_uuid: 条文节点UUID
            relations: 关系列表，格式：
                [{"type": "MANDATES", "target": "xxx", "fact": "xxx"}, ...]
            episode_ids: 来源episode列表
        """
        with self._driver.session() as session:
            for rel in relations:
                rel_type = rel.get("type", "RELATION")
                target_name = rel.get("target", "")
                fact = rel.get("fact", "")

                if not target_name:
                    continue

                def _add_relation(tx, _clause_uuid=clause_uuid, _rel_type=rel_type,
                                  _target=target_name, _fact=fact, _ep_ids=episode_ids):
                    # 创建/获取目标实体
                    target_lower = target_name.lower()
                    target_uuid_result = tx.run(
                        """
                        MERGE (t:Entity {graph_id: $gid, name_lower: $name_lower})
                        ON CREATE SET
                            t.uuid = randomUUID(),
                            t.name = $name,
                            t.created_at = datetime()
                        RETURN t.uuid AS uuid
                        """,
                        gid=graph_id,
                        name_lower=target_lower,
                        name=target_name
                    ).single()

                    if not target_uuid_result:
                        return

                    target_uuid = target_uuid_result["uuid"]

                    # 添加目标类型标签
                    target_type = rel.get("target_type", "Entity")
                    if target_type != "Entity":
                        tx.run(
                            f"MATCH (n:Entity {{uuid: $uuid}}) SET n:`{target_type}`",
                            uuid=target_uuid
                        )

                    # 创建关系
                    tx.run(
                        """
                        MATCH (src:Entity {uuid: $src_uuid}), (tgt:Entity {uuid: $tgt_uuid})
                        MERGE (src)-[r:RELATION {graph_id: $gid, name: $rel_type}]->(tgt)
                        ON CREATE SET
                            r.uuid = randomUUID(),
                            r.fact = $fact,
                            r.episode_ids = $ep_ids,
                            r.created_at = datetime()
                        ON MATCH SET
                            r.episode_ids = CASE
                                WHEN r.episode_ids IS NULL THEN $ep_ids
                                ELSE r.episode_ids + $ep_ids
                            END
                        """,
                        src_uuid=_clause_uuid,
                        tgt_uuid=target_uuid,
                        gid=graph_id,
                        rel_type=_rel_type,
                        fact=_fact,
                        ep_ids=_ep_ids
                    )

                try:
                    self._call_with_retry(session.execute_write, _add_relation)
                except Exception as e:
                    logger.warning(f"[hierarchical] Failed to add relation {_rel_type}: {e}")

    def get_unenriched_clauses(
        self,
        graph_id: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        获取未语义化的条文（用于LLM补充）

        Args:
            graph_id: 图谱ID
            limit: 返回数量限制

        Returns:
            条文列表
        """
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (ep:Clause {graph_id: $gid})
                WHERE ep.semantics_enriched <> true
                RETURN ep.uuid AS uuid,
                       ep.clause_id AS clause_id,
                       ep.data AS content,
                       ep.metadata_json AS metadata_json
                LIMIT $limit
                """,
                gid=graph_id,
                limit=limit
            )

            clauses = []
            for record in result:
                meta_json = record["metadata_json"] or "{}"
                try:
                    metadata = json.loads(meta_json)
                except:
                    metadata = {}

                clauses.append({
                    "uuid": record["uuid"],
                    "clause_id": record["clause_id"] or "",
                    "content": record["content"] or "",
                    "metadata": metadata
                })

            return clauses

    def get_all_clauses_with_metadata(
        self,
        graph_id: str,
        limit: int = 200
    ) -> List[Dict[str, Any]]:
        """
        获取所有条文（用于要素提取）

        Args:
            graph_id: 图谱ID
            limit: 返回数量限制

        Returns:
            条文列表，包含完整metadata
        """
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (ep:Clause {graph_id: $gid})
                RETURN ep.uuid AS uuid,
                       ep.clause_id AS clause_id,
                       ep.data AS content,
                       ep.metadata_json AS metadata_json
                LIMIT $limit
                """,
                gid=graph_id,
                limit=limit
            )

            clauses = []
            for record in result:
                meta_json = record["metadata_json"] or "{}"
                try:
                    metadata = json.loads(meta_json)
                except:
                    metadata = {}

                clauses.append({
                    "uuid": record["uuid"],
                    "clause_id": record["clause_id"] or "",
                    "content": record["content"] or "",
                    "metadata": metadata
                })

            return clauses

    def mark_clause_enriched(self, clause_uuid: str) -> None:
        """
        标记条文已语义化

        Args:
            clause_uuid: 条文Episode的UUID
        """
        with self._driver.session() as session:
            session.run(
                """
                MATCH (ep:Episode {uuid: $uuid})
                SET ep.semantics_enriched = true
                """,
                uuid=clause_uuid
            )

    def build_cross_ref_relations(self, graph_id: str) -> int:
        """
        根据条文元数据中的cross_refs构建交叉引用关系

        Args:
            graph_id: 图谱ID

        Returns:
            构建的关系数量
        """
        count = 0

        with self._driver.session() as session:
            # 获取所有条文
            result = session.run(
                """
                MATCH (ep:Clause {graph_id: $gid})
                WHERE ep.clause_id IS NOT NULL
                RETURN ep.uuid AS uuid, ep.clause_id AS clause_id
                """,
                gid=graph_id
            )

            clause_map = {}
            for record in result:
                clause_map[record["clause_id"]] = record["uuid"]

            # 构建交叉引用关系
            for clause_id, source_uuid in clause_map.items():
                # 获取该条文的引用
                ref_result = session.run(
                    """
                    MATCH (ep:Episode {uuid: $uuid})
                    RETURN ep.metadata_json AS metadata_json
                    """,
                    uuid=source_uuid
                ).single()

                if not ref_result:
                    continue

                try:
                    metadata = json.loads(ref_result["metadata_json"] or "{}")
                except:
                    continue

                cross_refs = metadata.get("cross_refs", [])
                if isinstance(cross_refs, str):
                    cross_refs = [cross_refs]

                for ref_id in cross_refs:
                    if ref_id in clause_map:
                        target_uuid = clause_map[ref_id]
                        if target_uuid != source_uuid:
                            try:
                                session.run(
                                    """
                                    MATCH (src:Episode {uuid: $src_uuid}), (tgt:Episode {uuid: $tgt_uuid})
                                    MERGE (src)-[r:CROSS_REFERENCE]->(tgt)
                                    ON CREATE SET r.graph_id = $gid
                                    """,
                                    src_uuid=source_uuid,
                                    tgt_uuid=target_uuid,
                                    gid=graph_id
                                )
                                count += 1
                            except Exception as e:
                                logger.debug(f"[hierarchical] Failed to create cross-ref: {e}")

        logger.info(f"[hierarchical] Built {count} cross-reference relations")
        return count

    def add_hierarchical_chunk_with_entities(
        self,
        graph_id: str,
        chunk_data: Dict[str, Any]
    ) -> str:
        """
        存储多层级chunk，同时创建Episode和Entity节点及关系

        Args:
            graph_id: 图谱ID
            chunk_data: 包含以下字段的字典：
                - level: 1/2/3
                - content: 文本内容 (也支持 "text" 字段)
                - chunk_type: section/clause/element
                - metadata: 元数据字典

        Returns:
            episode_uuid or None if chunk is empty
        """
        level = chunk_data.get("level", 2)
        # Support both "content" (standard) and "text" (from HierarchicalChunk.to_episode_dict)
        content = chunk_data.get("content") or chunk_data.get("text", "")
        chunk_type = chunk_data.get("chunk_type", "clause")
        metadata = chunk_data.get("metadata", {})

        # Skip empty chunks
        if not content or not content.strip():
            logger.warning(f"[hierarchical] Skipping empty chunk: {chunk_data}")
            return None

        # 生成稳定UUID
        content_seed = f"{graph_id}:{content}".encode('utf-8')
        episode_id = str(uuid.UUID(hashlib.md5(content_seed).hexdigest()))

        now = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata, ensure_ascii=False)

        # 生成embedding
        embedding = []
        try:
            embedding = self._embedding.embed(content)
        except Exception as e:
            logger.warning(f"[hierarchical] Embedding failed: {e}")

        with self._driver.session() as session:
            def _create_episode_and_entities(tx):
                # 提取 PDF 定位信息和 clause_id
                ep_source = metadata.get("source", "")
                ep_page = metadata.get("page", 0)
                ep_clause_id = metadata.get("clause_id", "")
                # 提取 PDF 定位信息（优先 bboxs，回退 bbox）
                # 注意：Neo4j 不支持嵌套集合，直接存储会报错
                # 转换为 JSON 字符串存储，读取时需解析
                ep_pdf_bboxes_raw = metadata.get("bboxs", [])
                if not ep_pdf_bboxes_raw:
                    single_bbox = metadata.get("bbox")
                    if single_bbox:
                        if isinstance(single_bbox, list) and len(single_bbox) >= 4:
                            ep_pdf_bboxes_raw = [[ep_page or 1] + single_bbox[:4]]
                        elif isinstance(single_bbox, dict):
                            ep_pdf_bboxes_raw = [[ep_page or 1,
                                              single_bbox.get('x0', 0), single_bbox.get('y0', 0),
                                              single_bbox.get('x1', 0), single_bbox.get('y1', 0)]]
                # 转换为 JSON 字符串以兼容 Neo4j 属性限制
                ep_pdf_bboxes = json.dumps(ep_pdf_bboxes_raw, ensure_ascii=False) if ep_pdf_bboxes_raw else "[]"
                ep_pdf_page_width = metadata.get("page_width")
                ep_pdf_page_height = metadata.get("page_height")
                # DEBUG: 打印 metadata 中 PDF 相关字段的实际值
                logger.info(f"[hierarchical] Storing Episode: clause_id={repr(ep_clause_id)} content={content[:30] if content else '(empty)'}")
                logger.info(f"[hierarchical] DEBUG metadata: source={repr(metadata.get('source'))}, page={repr(metadata.get('page'))}, bboxs={metadata.get('bboxs')}, bbox={metadata.get('bbox')}")
                logger.info(f"[hierarchical] DEBUG ep_*: source={repr(ep_source)}, page={repr(ep_page)}, bboxs={ep_pdf_bboxes}")

                # 1. 创建Episode/Clause节点，同时设置 PDF 定位信息
                tx.run(
                    """
                    MERGE (ep:Episode {uuid: $uuid})
                    ON CREATE SET
                        ep.graph_id = $graph_id,
                        ep.data = $data,
                        ep.metadata_json = $metadata_json,
                        ep.processed = true,
                        ep.embedding = $embedding,
                        ep.created_at = $created_at,
                        ep.source = $source,
                        ep.page = $page,
                        ep.clause_id = $clause_id,
                        ep.pdf_source = $pdf_source,
                        ep.pdf_page = $pdf_page,
                        ep.pdf_bboxes = $pdf_bboxes,
                        ep.pdf_page_width = $pdf_page_width,
                        ep.pdf_page_height = $pdf_page_height
                    ON MATCH SET
                        ep.graph_id = $graph_id,
                        ep.data = $data,
                        ep.metadata_json = $metadata_json,
                        ep.embedding = $embedding,
                        ep.source = COALESCE(ep.source, $source),
                        ep.page = COALESCE(ep.page, $page),
                        ep.clause_id = $clause_id,
                        ep.pdf_source = COALESCE(ep.pdf_source, $pdf_source),
                        ep.pdf_page = COALESCE(ep.pdf_page, $pdf_page),
                        ep.pdf_bboxes = COALESCE(ep.pdf_bboxes, $pdf_bboxes),
                        ep.pdf_page_width = COALESCE(ep.pdf_page_width, $pdf_page_width),
                        ep.pdf_page_height = COALESCE(ep.pdf_page_height, $pdf_page_height)
                    """,
                    uuid=episode_id,
                    graph_id=graph_id,
                    data=content,
                    metadata_json=metadata_json,
                    embedding=embedding,
                    created_at=now,
                    source=ep_source,
                    page=ep_page,
                    clause_id=ep_clause_id,
                    pdf_source=ep_source,
                    pdf_page=ep_page,
                    pdf_bboxes=ep_pdf_bboxes,
                    pdf_page_width=ep_pdf_page_width,
                    pdf_page_height=ep_pdf_page_height,
                )

                # 2. 移除 Episode 标签，设置为 Clause（统一为 Clause 标签，不含 Episode 前缀）
                tx.run(
                    """
                    MATCH (ep:Episode {uuid: $uuid})
                    REMOVE ep:Episode
                    SET ep:Clause
                    """,
                    uuid=episode_id
                )

                # 3. 根据chunk_type创建Entity节点
                if chunk_type == "clause":
                    clause_id = metadata.get("clause_id", "")
                    if clause_id:
                        # 如果 metadata 已包含 topic/entities（来自 intelligent_chunks），跳过 LLM 语义解析
                        # Topic/Entity 节点将由 add_topic_and_entity_nodes 统一创建
                        has_topic = metadata.get("topic") and metadata.get("entities")
                        if has_topic:
                            logger.info(f"[hierarchical] Skipping LLM parse for clause {clause_id} (has topic+entities from intelligent_chunks)")
                        else:
                            # 创建Clause实体（旧路径：需要 LLM 解析三元组）
                            self._create_entity_for_clause(tx, graph_id, episode_id, clause_id, content, embedding, metadata)
                elif chunk_type == "section":
                    title = metadata.get("title", content[:50])
                    self._create_section_entity(tx, graph_id, episode_id, title, metadata, embedding)
                elif chunk_type == "element":
                    element_type = metadata.get("element_type", "parameter")
                    key = metadata.get("key", content[:50])
                    self._create_element_entity(tx, graph_id, episode_id, element_type, key, metadata, embedding)

            self._call_with_retry(session.execute_write, _create_episode_and_entities)

        logger.info(f"[hierarchical] Created episode {episode_id[:8]} (Level{level}, {chunk_type}) clause_id={metadata.get('clause_id','') or metadata.get('key','')}")
        return episode_id

    def add_topic_and_entity_nodes(
        self,
        graph_id: str,
        clauses_data: List[Dict],
        entities_data: List[Dict],
    ) -> Dict[str, int]:
        """
        为图谱添加 Topic 和 Entity 节点及关系

        图谱结构：
            Clause (Clause) --HAS_TOPIC--> Topic
            Topic --MENTIONS--> Entity:Term (来自 clause.terms)
            Topic --MENTIONS--> Entity (来自 clause.entities)

        节点类型：
            Topic: 一个 Topic 对应一个 clause 的 topic 摘要
            Entity:Term: 来自 clause.terms，包含术语定义
            Entity: 来自 clause.entities，包含通用实体

        Args:
            graph_id: 图谱ID
            clauses_data: 条款列表（来自 intelligent_chunks['clauses']）
            entities_data: 要素列表（来自 intelligent_chunks['elements']）

        Returns:
            {"topics": N, "entities": M, "has_topic": X, "mentions": Y}
        """
        now = datetime.now(timezone.utc).isoformat()
        topic_count = 0
        entity_count = 0
        clause_count = 0  # Clause 节点数量（来自 Clause）
        has_topic_count = 0
        mentions_count = 0

        # 预统计 clauses 总数（从 Clause 节点计数）
        clauses_with_topic = [c for c in clauses_data if c.get('topic')]

        # DEBUG: 记录传入的 clauses_data 信息
        logger.info(f"[add_topic_and_entity_nodes] DEBUG: clauses_data length = {len(clauses_data)}, clauses_with_topic length = {len(clauses_with_topic)}, entities_data length = {len(entities_data)}")
        if clauses_data:
            sample = clauses_data[0]
            logger.info(f"[add_topic_and_entity_nodes] DEBUG: first clause has topic={sample.get('topic', 'MISSING')}, entities={sample.get('entities', 'MISSING')}, clause_id={sample.get('clause_id', 'MISSING')}")

        with self._driver.session() as session:
            def _create_nodes_and_relations(tx):
                nonlocal topic_count, entity_count, clause_count, has_topic_count, mentions_count

                # 预统计
                clauses_with_topic = [c for c in clauses_data if c.get('topic')]

                # 检查数据库中是否存在 Clause 节点
                check_episodes = tx.run(
                    """
                    MATCH (ep:Clause {graph_id: $gid})
                    RETURN count(ep) as total_episodes, head(collect(ep.metadata_json)) as sample_metadata
                    """,
                    gid=graph_id
                )
                ep_check = check_episodes.single()
                total_eps = ep_check["total_episodes"] if ep_check else 0
                sample_meta = ep_check["sample_metadata"] if ep_check else None
                logger.info(f"[add_topic_and_entity_nodes] DEBUG: Total Clause nodes in DB: {total_eps}")
                if sample_meta:
                    logger.info(f"[add_topic_and_entity_nodes] DEBUG: Sample metadata_json: {sample_meta[:200] if sample_meta else 'None'}...")

                # 检查有 clause_id 属性的 Episode 数量
                check_with_clause_id = tx.run(
                    """
                    MATCH (ep:Clause {graph_id: $gid})
                    WHERE ep.clause_id IS NOT NULL
                    RETURN count(ep) as cnt, head(collect([ep.clause_id, ep.data])) as sample
                    """,
                    gid=graph_id
                )
                rec = check_with_clause_id.single()
                cnt = rec["cnt"] if rec else 0
                sample_pair = rec["sample"] if rec else None
                clause_count = cnt  # 使用数据库中实际的 Clause 节点数量
                logger.info(f"[add_topic_and_entity_nodes] DEBUG: Clause 有 clause_id 的数量: {cnt}, clauses_with_topic length: {len(clauses_with_topic)}")
                entities_by_clause = {}
                for e in entities_data:
                    src = e.get('source_clause_id', '')
                    if src:
                        if src not in entities_by_clause:
                            entities_by_clause[src] = []
                        entities_by_clause[src].append(e)

                # 批量 MERGE Topic 节点和 HAS_TOPIC 关系
                for clause in clauses_with_topic:
                    clause_id = clause.get('clause_id', '')
                    topic_text = clause.get('topic', '')
                    if not clause_id or not topic_text:
                        continue

                    # 创建 Topic 节点（纯 Topic 标签，不加 :Entity 避免 entity_uuid 约束冲突）
                    topic_uuid = str(uuid.UUID(hashlib.md5(f"{graph_id}:{clause_id}:topic".encode()).hexdigest()))
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
                        clause_id=clause_id,
                        created_at=now
                    )
                    topic_count += 1
                    logger.info(f"[topic_entity] Topic node: clause={clause_id} topic={topic_text[:30]}")

                    # 找到对应的 Clause 节点并创建 HAS_TOPIC 关系
                    # 注意：Clause 节点的 graph_id 可能与当前不同（早期重建遗留），
                    # 因此仅通过 clause_id 匹配（clause_id 在重建间保持稳定）
                    logger.info(f"[add_topic_and_entity_nodes] DEBUG: clause_id={clause_id}, topic={topic_text[:30] if topic_text else 'EMPTY'}")
                    check_result = tx.run(
                        """
                        MATCH (ep:Clause {clause_id: $clause_id})
                        RETURN count(ep) as ep_count
                        """,
                        clause_id=clause_id
                    )
                    check_record = check_result.single()
                    ep_count = check_record["ep_count"] if check_record else 0
                    logger.info(f"[add_topic_and_entity_nodes] DEBUG: Found {ep_count} Clause nodes for clause_id={clause_id}")

                    if ep_count > 0:
                        # 使用独立 MATCH 模式，并通过 USING INDEX 提示加速
                        tx.run(
                            """
                            MATCH (ep:Clause {clause_id: $clause_id})
                            MATCH (t:Topic {uuid: $uuid})
                            MERGE (ep)-[r:HAS_TOPIC]->(t)
                            SET r.graph_id = $gid, r.created_at = datetime()
                            """,
                            clause_id=clause_id,
                            uuid=topic_uuid,
                            gid=graph_id
                        )
                        has_topic_count += 1
                    else:
                        logger.warning(f"[add_topic_and_entity_nodes] WARNING: No Clause found for clause_id={clause_id}, skipping HAS_TOPIC relationship")

                    # 为该条款的 terms 创建 Entity:Term 节点和 MENTIONS 关系
                    clause_terms = clause.get('terms', [])
                    if isinstance(clause_terms, list):
                        for term_item in clause_terms:
                            # term_item 可以是字典 {'term_name': ..., 'definition': ...} 或字符串
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
                            entity_count += 1
                            logger.info(f"[topic_entity]   Entity:Term: {term_name} <- Topic({topic_text[:20]}) MENTIONS")

                            # 创建 Topic --MENTIONS--> Entity:Term 关系
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
                            mentions_count += 1

                    # 为该条款的 entities 创建 Entity 节点和 MENTIONS 关系
                    clause_entities = clause.get('entities', [])
                    if isinstance(clause_entities, list):
                        for ent in clause_entities:
                            # 兼容字符串实体和字典实体
                            entity_name = ent if isinstance(ent, str) else ent.get('key', '')
                            if not entity_name:
                                continue

                            entity_uuid = str(uuid.UUID(hashlib.md5(f"{graph_id}:{entity_name}:entity".encode()).hexdigest()))

                            # 统一使用 Entity 标签，便于检索
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
                            logger.info(f"[topic_entity]   Entity: {entity_name} <- Topic({topic_text[:20]}) MENTIONS")
                            entity_count += 1

                            # 创建 Topic --MENTIONS--> Entity 关系
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
                            mentions_count += 1

                return {
                    "topics": topic_count,
                    "entities": entity_count,
                    "clauses": clause_count,
                    "has_topic": has_topic_count,
                    "mentions": mentions_count
                }

            result = self._call_with_retry(session.execute_write, _create_nodes_and_relations)
            return result

    def _create_entity_for_clause(self, tx, graph_id: str, episode_id: str,
                                  clause_id: str, content: str,
                                  embedding: List[float], metadata: Dict):
        """为Clause创建Entity节点及其语义关系

        核心改进：使用语义三元组 (SemanticTriplet) 而非平行列表，
        每个三元组 (component, action, obj, condition, requirement) 精确建一条关系，
        消除笛卡尔积歧义。

        关系模型（4条核心路径）：
        1. Term --defines--> Clause          （_create_defines_relation）
        2. Clause --applies_to--> Component   （APPLIES_TO）
        3. Clause --mandates/recommends/prohibits--> Action  （MANDATES/RECOMMENDS/PROHIBITS）
        4. Condition --in_situation--> Action  （IN_SITUATION）
        """
        clause_name = f"条款{clause_id}"

        # 生成Entity UUID
        entity_seed = f"{graph_id}:{clause_name}".encode('utf-8')
        entity_uuid = str(uuid.UUID(hashlib.md5(entity_seed).hexdigest()))

        # 获取条款级要求类型（fallback）
        clause_requirement = metadata.get('requirement_type', 'recommended').lower()

        # 提取 PDF 定位信息
        pdf_source = metadata.get('source')
        pdf_page = metadata.get('page')
        # 优先读取 bboxs（跨页 bbox 列表），回退读取 bbox（单个 bbox）
        # 注意：Neo4j 不支持嵌套集合，存储时转为 JSON 字符串
        pdf_bboxes_raw = metadata.get('bboxs', [])
        if not pdf_bboxes_raw:
            # 兼容 bbox 单个边框格式: [x0, y0, x1, y1] 或 {x0, y0, x1, y1}
            single_bbox = metadata.get('bbox')
            if single_bbox:
                if isinstance(single_bbox, list) and len(single_bbox) >= 4:
                    pdf_bboxes_raw = [[pdf_page or 1] + single_bbox[:4]]
                elif isinstance(single_bbox, dict):
                    pdf_bboxes_raw = [[pdf_page or 1,
                                   single_bbox.get('x0', 0), single_bbox.get('y0', 0),
                                   single_bbox.get('x1', 0), single_bbox.get('y1', 0)]]
        # 转换为 JSON 字符串以兼容 Neo4j 属性限制
        pdf_bboxes = json.dumps(pdf_bboxes_raw, ensure_ascii=False) if pdf_bboxes_raw else "[]"
        pdf_page_width = metadata.get('page_width')
        pdf_page_height = metadata.get('page_height')

        # 创建Clause Entity节点
        tx.run(
            """
            MERGE (e:Clause {graph_id: $gid, name_lower: $name_lower})
            ON CREATE SET
                e.uuid = $uuid,
                e.name = $name,
                e.summary = $summary,
                e.embedding = $embedding,
                e.clause_id = $clause_id,
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
            name_lower=clause_name.lower(),
            uuid=entity_uuid,
            name=clause_name,
            summary=content if content else "",
            embedding=embedding,
            clause_id=clause_id,
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
            MATCH (ep:Episode {uuid: $ep_uuid}), (e:Entity {uuid: $e_uuid})
            MERGE (ep)-[r:MENTIONS]->(e)
            ON CREATE SET r.graph_id = $gid
            """,
            ep_uuid=episode_id,
            e_uuid=entity_uuid,
            gid=graph_id
        )

        # ========== 术语章节处理（独立处理，不走三元组）==========
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
            return  # 术语章节不需要三元组处理

        # ========== 核心：按语义三元组建关系 ==========
        triplets = metadata.get('triplets', [])

        # 如果没有三元组（fallback），从 content 调用 LLM 解析
        if not triplets:
            logger.info(f"[hierarchical] [Fallback] clause {clause_id}: 调用 LLM 解析三元组")
            parsed_triplets = self._parse_semantic_from_content(content, clause_id, clause_requirement)
            triplets.extend(parsed_triplets)

        # ========== 遍历每个三元组，精确建关系（5条核心路径） ==========
        for t in triplets:
            comp = (t.get('component') or '').strip()
            act = (t.get('action') or '').strip()
            obj = (t.get('obj') or '').strip()
            cond = (t.get('condition') or '').strip()
            t_req = t.get('requirement', clause_requirement).lower()

            # 过滤非实操性动作
            if act and not is_actionable(act):
                logger.debug(f"[hierarchical] 过滤非实操性 Action: '{act}' in clause {clause_id}")
                act = ''

            # 路径2: Clause --applies_to--> Component
            comp_uuid = None
            if comp:
                self._create_component_entity(tx, graph_id, episode_id, comp)
                comp_uuid = self._find_entity_uuid(tx, graph_id, 'Component', comp)
                if comp_uuid:
                    self._create_applies_to_relation(tx, entity_uuid, comp_uuid)

            # 路径5（部分）: Action --requires--> Entity（作为参数要求）
            obj_uuid = None
            if obj:
                self._create_object_entity(tx, graph_id, episode_id, obj)
                obj_uuid = self._find_entity_uuid(tx, graph_id, 'Entity', obj)

            # 路径3: Clause --mandates/recommends/prohibits--> Action
            action_uuid = None
            if act:
                action_uuid = self._create_action_entity(tx, graph_id, entity_uuid, act)
                if t_req == 'mandatory':
                    self._create_mandates_relation(tx, entity_uuid, action_uuid)
                elif t_req == 'prohibited':
                    self._create_prohibits_relation(tx, entity_uuid, action_uuid)
                else:
                    self._create_recommends_relation(tx, entity_uuid, action_uuid)

                # 路径5（精确配对）: Action --OPERATES_ON--> Entity
                if obj_uuid:
                    self._create_operates_on_relation(tx, action_uuid, obj_uuid)

            # 路径4: Condition --in_situation--> Action
            cond_uuid = None
            if cond:
                cond_uuid = self._create_condition_entity(tx, graph_id, entity_uuid, cond)
                # 当同时存在 condition 和 action 时，建立 in_situation 关系
                if action_uuid and cond_uuid:
                    self._create_in_situation_relation(tx, cond_uuid, action_uuid)

        # ========== 表格/公式引用 ==========
        for formula_id in metadata.get("formula_refs", []):
            if formula_id:
                self._create_formula_entity(tx, graph_id, episode_id, entity_uuid, formula_id)
        for table_ref in metadata.get("table_refs", []):
            if table_ref:
                self._create_table_parameter_entity(tx, graph_id, episode_id, entity_uuid, table_ref, content)

        # ========== 条款引用 ==========
        # Clause --[REFERENCES]--> Clause (被引用的条款)
        referenced_clauses = metadata.get("referenced_clauses", [])
        for ref in referenced_clauses:
            if isinstance(ref, dict):
                ref_clause_id = ref.get("clause_id", "")
                ref_context = ref.get("context", "")
            else:
                ref_clause_id = str(ref)
                ref_context = f"本规范第{ref_clause_id}条"
            if not ref_clause_id:
                continue
            ref_clause_name = f"条款{ref_clause_id}"
            # 生成被引用条款的 Entity UUID（与 _create_entity_for_clause 一致）
            ref_entity_seed = f"{graph_id}:{ref_clause_name}".encode('utf-8')
            ref_entity_uuid = str(uuid.UUID(hashlib.md5(ref_entity_seed).hexdigest()))
            # 查找被引用条款的实际 UUID（MATCH）
            lookup = tx.run(
                "MATCH (e:Clause {graph_id: $gid, clause_id: $cid}) RETURN e.uuid AS uuid LIMIT 1",
                gid=graph_id, cid=ref_clause_id
            ).single()
            if lookup:
                ref_entity_uuid = lookup["uuid"]
                # 创建 REFERENCES 关系
                tx.run(
                    """
                    MATCH (src:Entity {uuid: $src_uuid}), (tgt:Entity {uuid: $tgt_uuid})
                    MERGE (src)-[r:REFERENCES {graph_id: $gid}]->(tgt)
                    ON CREATE SET
                        r.uuid = randomUUID(),
                        r.context = $context,
                        r.episode_ids = [$ep_id],
                        r.created_at = datetime()
                    ON MATCH SET
                        r.context = COALESCE(r.context, $context),
                        r.episode_ids = CASE
                            WHEN r.episode_ids IS NULL THEN [$ep_id]
                            ELSE r.episode_ids + [$ep_id]
                        END
                    """,
                    src_uuid=entity_uuid,
                    tgt_uuid=ref_entity_uuid,
                    gid=graph_id,
                    context=ref_context,
                    ep_id=episode_id
                )
                logger.debug(f"[hierarchical] Clause {clause_id} REFERENCES {ref_clause_id}")

        # ========== 外部标准引用 ==========
        # Clause --[CITES]--> ExternalStandard
        for std in metadata.get("referenced_standards", []):
            std = str(std).strip()
            if not std:
                continue
            std_entity_seed = f"{graph_id}:Standard:{std}".encode('utf-8')
            std_entity_uuid = str(uuid.UUID(hashlib.md5(std_entity_seed).hexdigest()))
            # 创建 ExternalStandard 节点（如不存在）
            tx.run(
                """
                MERGE (s:Entity:ExternalStandard {graph_id: $gid, name_lower: $nl})
                ON CREATE SET
                    s.uuid = $uuid,
                    s.name = $name,
                    s.created_at = datetime()
                """,
                gid=graph_id,
                nl=std.lower(),
                uuid=std_entity_uuid,
                name=std
            )
            # 建立 CITES 关系
            tx.run(
                """
                MATCH (src:Entity {uuid: $src_uuid}), (tgt:Entity {uuid: $tgt_uuid})
                MERGE (src)-[r:CITES {graph_id: $gid}]->(tgt)
                ON CREATE SET
                    r.uuid = randomUUID(),
                    r.episode_ids = [$ep_id],
                    r.created_at = datetime()
                """,
                src_uuid=entity_uuid,
                tgt_uuid=std_entity_uuid,
                gid=graph_id,
                ep_id=episode_id
            )
            logger.debug(f"[hierarchical] Clause {clause_id} CITES {std}")

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

    def _create_formula_entity(self, tx, graph_id: str, episode_id: str,
                               clause_entity_uuid: str, formula_id: str):
        """创建Formula实体"""
        formula_name = f"公式{formula_id}"

        entity_seed = f"{graph_id}:Formula:{formula_id}".encode('utf-8')
        entity_uuid = str(uuid.UUID(hashlib.md5(entity_seed).hexdigest()))

        try:
            tx.run(
                """
                MERGE (e:Entity:Formula {graph_id: $gid, name_lower: $name_lower})
                ON CREATE SET
                    e.uuid = $uuid,
                    e.name = $name,
                    e.created_at = datetime()
                """,
                gid=graph_id,
                name_lower=formula_name.lower(),
                uuid=entity_uuid,
                name=formula_name
            )

            # 链接Clause -> REFERENCES -> Formula
            tx.run(
                """
                MATCH (clause:Entity {uuid: $clause_uuid}), (f:Entity {uuid: $formula_uuid})
                MERGE (clause)-[r:RELATION {graph_id: $gid, name: 'REFERENCES'}]->(f)
                ON CREATE SET r.graph_id = $gid
                """,
                clause_uuid=clause_entity_uuid,
                formula_uuid=entity_uuid,
                gid=graph_id
            )
        except Exception as e:
            logger.debug(f"Failed to create formula entity: {e}")

    def _create_table_parameter_entity(self, tx, graph_id: str, episode_id: str,
                                      clause_entity_uuid: str, table_ref: str,
                                      clause_content: str = ""):
        """
        创建Table/Parameter实体

        Args:
            table_ref: 表格编号，如 "表3.2.9"
            clause_content: 关联的条款内容，用于生成 summary
        """
        param_name = f"{table_ref}"

        entity_seed = f"{graph_id}:Parameter:{param_name}".encode('utf-8')
        entity_uuid = str(uuid.UUID(hashlib.md5(entity_seed).hexdigest()))

        # 构建 summary，包含表格编号和条款描述
        summary_parts = [f"表格编号: {table_ref}"]
        if clause_content:
            # 提取条款描述的前100字
            desc = clause_content[:100].replace('\n', ' ').strip()
            if desc:
                summary_parts.append(f"条款描述: {desc}")
        summary = " | ".join(summary_parts)

        try:
            tx.run(
                """
                MERGE (e:Entity:Parameter {graph_id: $gid, name_lower: $name_lower})
                ON CREATE SET
                    e.uuid = $uuid,
                    e.name = $name,
                    e.summary = $summary,
                    e.created_at = datetime()
                ON MATCH SET
                    e.summary = COALESCE(e.summary, $summary)
                """,
                gid=graph_id,
                name_lower=param_name.lower(),
                uuid=entity_uuid,
                name=param_name,
                summary=summary
            )

            # 链接Clause -> HAS_VALUE -> Parameter
            tx.run(
                """
                MATCH (clause:Entity {uuid: $clause_uuid}), (p:Entity {uuid: $param_uuid})
                MERGE (clause)-[r:RELATION {graph_id: $gid, name: 'HAS_VALUE'}]->(p)
                ON CREATE SET r.graph_id = $gid
                """,
                clause_uuid=clause_entity_uuid,
                param_uuid=entity_uuid,
                gid=graph_id
            )
        except Exception as e:
            logger.debug(f"Failed to create parameter entity: {e}")

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

    def _create_condition_entity(self, tx, graph_id: str, clause_uuid: str, condition_name: str):
        """创建 Condition（前提条件）实体"""
        entity_seed = f"{graph_id}:Condition:{condition_name}".encode('utf-8')
        entity_uuid = str(uuid.UUID(hashlib.md5(entity_seed).hexdigest()))

        try:
            tx.run(
                """
                MERGE (e:Entity:Condition {graph_id: $gid, name_lower: $name_lower})
                ON CREATE SET
                    e.uuid = $uuid,
                    e.name = $name,
                    e.summary = $summary,
                    e.created_at = datetime()
                """,
                gid=graph_id,
                name_lower=condition_name.lower(),
                uuid=entity_uuid,
                name=condition_name,
                summary=f"前提条件: {condition_name}"
            )

            # 链接Clause -> HAS_CONDITION -> Condition
            tx.run(
                """
                MATCH (c:Entity {uuid: $clause_uuid}), (cond:Entity {uuid: $cond_uuid})
                MERGE (c)-[r:HAS_CONDITION]->(cond)
                ON CREATE SET r.graph_id = $gid
                """,
                clause_uuid=clause_uuid,
                cond_uuid=entity_uuid,
                gid=graph_id
            )
        except Exception as e:
            logger.debug(f"Failed to create condition entity: {e}")

        return entity_uuid

    def _create_applies_to_relation(self, tx, clause_uuid: str, component_uuid: str):
        """创建 Clause --applies_to--> Component 关系（路径2）"""
        try:
            tx.run(
                """
                MATCH (c:Entity {uuid: $clause_uuid}), (comp:Entity {uuid: $comp_uuid})
                MERGE (c)-[r:APPLIES_TO]->(comp)
                ON CREATE SET r.graph_id = 'default'
                """,
                clause_uuid=clause_uuid,
                comp_uuid=component_uuid
            )
        except Exception as e:
            logger.debug(f"Failed to create APPLIES_TO relation: {e}")

    def _create_in_situation_relation(self, tx, condition_uuid: str, action_uuid: str):
        """创建 Condition --in_situation--> Action 关系（路径4）"""
        try:
            tx.run(
                """
                MATCH (cond:Entity {uuid: $cond_uuid}), (a:Entity {uuid: $action_uuid})
                MERGE (cond)-[r:IN_SITUATION]->(a)
                ON CREATE SET r.graph_id = 'default'
                """,
                cond_uuid=condition_uuid,
                action_uuid=action_uuid
            )
        except Exception as e:
            logger.debug(f"Failed to create IN_SITUATION relation: {e}")

    def _create_action_entity(self, tx, graph_id: str, clause_uuid: str, action_name: str) -> str:
        """创建 Action（规定动作）实体"""
        entity_seed = f"{graph_id}:Action:{action_name}".encode('utf-8')
        entity_uuid = str(uuid.UUID(hashlib.md5(entity_seed).hexdigest()))

        try:
            tx.run(
                """
                MERGE (e:Entity:Action {graph_id: $gid, name_lower: $name_lower})
                ON CREATE SET
                    e.uuid = $uuid,
                    e.name = $name,
                    e.summary = $summary,
                    e.created_at = datetime()
                """,
                gid=graph_id,
                name_lower=action_name.lower(),
                uuid=entity_uuid,
                name=action_name,
                summary=f"规定动作: {action_name}"
            )
        except Exception as e:
            logger.debug(f"Failed to create action entity: {e}")

        return entity_uuid

    def _create_component_entity(self, tx, graph_id: str, episode_id: str, component_name: str):
        """创建 Component（设备/系统/材料）实体"""
        entity_seed = f"{graph_id}:Component:{component_name}".encode('utf-8')
        entity_uuid = str(uuid.UUID(hashlib.md5(entity_seed).hexdigest()))

        try:
            tx.run(
                """
                MERGE (e:Entity:Component {graph_id: $gid, name_lower: $name_lower})
                ON CREATE SET
                    e.uuid = $uuid,
                    e.name = $name,
                    e.summary = $summary,
                    e.created_at = datetime()
                """,
                gid=graph_id,
                name_lower=component_name.lower(),
                uuid=entity_uuid,
                name=component_name,
                summary=f"设备/系统/材料: {component_name}"
            )

            # 链接Episode -> MENTIONS -> Component
            tx.run(
                """
                MATCH (ep:Episode {uuid: $ep_uuid}), (c:Entity {uuid: $c_uuid})
                MERGE (ep)-[r:MENTIONS]->(c)
                ON CREATE SET r.graph_id = $gid
                """,
                ep_uuid=episode_id,
                c_uuid=entity_uuid,
                gid=graph_id
            )
        except Exception as e:
            logger.debug(f"Failed to create component entity: {e}")

    def _create_object_entity(self, tx, graph_id: str, episode_id: str, object_name: str):
        """创建 Entity（操作对象）实体"""
        entity_seed = f"{graph_id}:Entity:{object_name}".encode('utf-8')
        entity_uuid = str(uuid.UUID(hashlib.md5(entity_seed).hexdigest()))

        try:
            tx.run(
                """
                MERGE (e:Entity {graph_id: $gid, name_lower: $name_lower})
                ON CREATE SET
                    e.uuid = $uuid,
                    e.name = $name,
                    e.summary = $summary,
                    e.created_at = datetime()
                """,
                gid=graph_id,
                name_lower=object_name.lower(),
                uuid=entity_uuid,
                name=object_name,
                summary=f"操作对象: {object_name}"
            )

            # 链接Episode -> MENTIONS -> Entity
            tx.run(
                """
                MATCH (ep:Episode {uuid: $ep_uuid}), (o:Entity {uuid: $o_uuid})
                MERGE (ep)-[r:MENTIONS]->(o)
                ON CREATE SET r.graph_id = $gid
                """,
                ep_uuid=episode_id,
                o_uuid=entity_uuid,
                gid=graph_id
            )
        except Exception as e:
            logger.debug(f"Failed to create object entity: {e}")

    def _create_mandates_relation(self, tx, clause_uuid: str, action_uuid: str):
        """创建 Clause -MANDATES-> Action 关系"""
        try:
            tx.run(
                """
                MATCH (c:Entity {uuid: $clause_uuid}), (a:Entity {uuid: $action_uuid})
                MERGE (c)-[r:MANDATES]->(a)
                ON CREATE SET r.graph_id = 'default'
                """,
                clause_uuid=clause_uuid,
                action_uuid=action_uuid
            )
        except Exception as e:
            logger.debug(f"Failed to create MANDATES relation: {e}")

    def _create_recommends_relation(self, tx, clause_uuid: str, action_uuid: str):
        """创建 Clause -RECOMMENDS-> Action 关系"""
        try:
            tx.run(
                """
                MATCH (c:Entity {uuid: $clause_uuid}), (a:Entity {uuid: $action_uuid})
                MERGE (c)-[r:RECOMMENDS]->(a)
                ON CREATE SET r.graph_id = 'default'
                """,
                clause_uuid=clause_uuid,
                action_uuid=action_uuid
            )
        except Exception as e:
            logger.debug(f"Failed to create RECOMMENDS relation: {e}")

    def _create_prohibits_relation(self, tx, clause_uuid: str, action_uuid: str):
        """创建 Clause -PROHIBITS-> Action 关系"""
        try:
            tx.run(
                """
                MATCH (c:Entity {uuid: $clause_uuid}), (a:Entity {uuid: $action_uuid})
                MERGE (c)-[r:PROHIBITS]->(a)
                ON CREATE SET r.graph_id = 'default'
                """,
                clause_uuid=clause_uuid,
                action_uuid=action_uuid
            )
        except Exception as e:
            logger.debug(f"Failed to create PROHIBITS relation: {e}")

    def _create_operates_on_relation(self, tx, action_uuid: str, object_uuid: str):
        """创建 Action -OPERATES_ON-> Entity 关系"""
        try:
            tx.run(
                """
                MATCH (a:Entity {uuid: $action_uuid}), (o:Entity {uuid: $object_uuid})
                MERGE (a)-[r:OPERATES_ON]->(o)
                ON CREATE SET r.graph_id = 'default'
                """,
                action_uuid=action_uuid,
                object_uuid=object_uuid
            )
        except Exception as e:
            logger.debug(f"Failed to create OPERATES_ON relation: {e}")

    def _find_entity_uuid(self, tx, graph_id: str, entity_type: str, entity_name: str) -> Optional[str]:
        """根据实体类型和名称查找实体UUID"""
        try:
            result = tx.run(
                """
                MATCH (e:Entity:`{entity_type}` {{graph_id: $gid, name_lower: $name_lower}})
                RETURN e.uuid AS uuid
                """.format(entity_type=entity_type),
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

    # ========================================================================
    # LLM 解析器支持方法（新增）
    # ========================================================================

    def find_clause_by_id(self, graph_id: str, clause_id: str) -> Optional[Dict[str, Any]]:
        """
        根据条款 ID 查找条款 Episode

        Args:
            graph_id: 图谱ID
            clause_id: 条款编号（如 "3.2.1"）

        Returns:
            条款数据或 None
        """
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (ep:Clause {graph_id: $gid})
                WHERE ep.clause_id = $clause_id
                   OR ep.clause_id = $clause_id_full
                RETURN ep.uuid AS uuid, ep.data AS content
                LIMIT 1
                """,
                gid=graph_id,
                clause_id=clause_id,
                clause_id_full=f"条款{clause_id}"
            )

            record = result.single()
            if record:
                return {
                    "uuid": record["uuid"],
                    "content": record["content"]
                }
            return None

    def get_or_create_entity(
        self,
        graph_id: str,
        entity_type: str,
        entity_name: str,
        description: str = ""
    ) -> Optional[str]:
        """
        获取或创建实体节点

        直接使用 LLM 返回的实体类型，不再硬编码推断

        Args:
            graph_id: 图谱ID
            entity_type: 实体类型（如 "Component", "Action", "Condition"）
            entity_name: 实体名称
            description: 实体描述

        Returns:
            实体 UUID
        """
        import re
        # 清理实体名称中的特殊字符用于 name_lower
        name_clean = re.sub(r'[^\w\u4e00-\u9fff]', '_', entity_name).lower()[:100]

        # 生成稳定 UUID
        entity_seed = f"{graph_id}:{entity_type}:{entity_name}".encode('utf-8')
        entity_uuid = str(uuid.UUID(hashlib.md5(entity_seed).hexdigest()))

        try:
            with self._driver.session() as session:
                def _create_entity(tx):
                    # 使用参数化标签（Neo4j 支持）
                    tx.run(
                        f"""
                        MERGE (e:Entity:{entity_type} {{graph_id: $gid, name_lower: $name_lower}})
                        ON CREATE SET
                            e.uuid = $uuid,
                            e.name = $name,
                            e.summary = $summary,
                            e.created_at = datetime()
                        ON MATCH SET
                            e.summary = COALESCE(e.summary, $summary)
                        """,
                        gid=graph_id,
                        name_lower=name_clean,
                        uuid=entity_uuid,
                        name=entity_name,
                        summary=description[:500] if description else ""
                    )
                    return entity_uuid

                return self._call_with_retry(session.execute_write, _create_entity)

        except Exception as e:
            logger.warning(f"[entity] Failed to create entity {entity_type}:{entity_name}: {e}")
            return None

    def add_edge(
        self,
        graph_id: str,
        source_uuid: str,
        target_uuid: str,
        properties: Dict[str, Any]
    ) -> bool:
        """
        添加边（关系）

        Args:
            graph_id: 图谱ID
            source_uuid: 源节点 UUID
            target_uuid: 目标节点 UUID
            properties: 关系属性

        Returns:
            是否成功
        """
        rel_type = properties.get("type", "RELATES_TO")

        try:
            with self._driver.session() as session:
                def _create_edge(tx):
                    tx.run(
                        f"""
                        MATCH (s {{uuid: $source_uuid}}), (t {{uuid: $target_uuid}})
                        MERGE (s)-[r:{rel_type}]->(t)
                        ON CREATE SET
                            r.graph_id = $gid,
                            r.fact = $fact,
                            r.target_type = $target_type,
                            r.created_at = datetime()
                        ON MATCH SET
                            r.fact = COALESCE(r.fact, $fact)
                        """,
                        source_uuid=source_uuid,
                        target_uuid=target_uuid,
                        gid=graph_id,
                        fact=properties.get("fact", ""),
                        target_type=properties.get("target_type", "")
                    )
                    return True

                return self._call_with_retry(session.execute_write, _create_edge)

        except Exception as e:
            logger.warning(f"[edge] Failed to create edge: {e}")
            return False
