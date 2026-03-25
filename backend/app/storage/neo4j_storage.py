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
                        logger.debug(f"[VECTOR CREATE] Running query: {query}")
                        session.run(query)
                        logger.info(f"✅ Vector index '{index_name}' created/verified")
                    else:
                        logger.info(f"⏭️ Vector index '{index_name}' already exists")
                except Exception as e:
                    error_msg = str(e)
                    logger.warning(f"❌ Vector index '{index_name}' creation failed: {error_msg}")
                    # Provide helpful troubleshooting info
                    if "VECTOR" in error_msg.upper() or "not exist" in error_msg.lower():
                        logger.warning(f"   💡 Hint: Vector indexes require Neo4j 5.11+ with vector plugin enabled")
                        logger.warning(f"   💡 Alternative: Check if db.index.vector procedure exists")
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
                "MATCH (n:Entity {uuid: $uuid}) RETURN n, labels(n) AS labels",
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
                MATCH (n:Entity {uuid: $uuid})-[r:RELATION]-(m:Entity)
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
            }

        with self._driver.session() as session:
            return self._call_with_retry(session.execute_read, _read)

    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        """
        Full graph dump with enriched edge format (for frontend).
        Focuses on semantic Entities and their Relationships.
        Structural nodes (Document, Page, Episode) are included only as context.
        """
        def _read(tx):
            # 1. Get semantic nodes (Entities) and their labels
            node_result = tx.run(
                """
                MATCH (n:Entity {graph_id: $gid})
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

            # 2. Get semantic relationships between entities
            edge_result = tx.run(
                """
                MATCH (src:Entity {graph_id: $gid})-[r:RELATION]->(tgt:Entity {graph_id: $gid})
                RETURN r, src.uuid AS src_uuid, tgt.uuid AS tgt_uuid,
                       src.name AS src_name, tgt.name AS tgt_name,
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
            }

        with self._driver.session() as session:
            return self._call_with_retry(session.execute_read, _read)

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

        return {
            "uuid": props.get("uuid", ""),
            "name": props.get("name", ""),
            "labels": [l for l in labels if l != "Entity"] if labels else [],
            "summary": props.get("summary", ""),
            "attributes": attributes,
            "created_at": props.get("created_at"),
        }

    @staticmethod
    def _edge_to_dict(rel, source_uuid: str, target_uuid: str) -> Dict[str, Any]:
        """Convert Neo4j relationship to the standard edge dict format."""
        props = dict(rel)

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
            "name": props.get("name", ""),
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
                MATCH (ep:Episode:Level2 {graph_id: $gid})
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
                MATCH (ep:Episode:Level2 {graph_id: $gid})
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
                # 1. 创建Episode节点
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

                # 2. 添加层级标签
                level_label = f"Level{level}"
                tx.run(
                    f"MATCH (ep:Episode {{uuid: $uuid}}) SET ep:`{level_label}`",
                    uuid=episode_id
                )

                # 3. 根据chunk_type创建Entity节点
                if chunk_type == "clause":
                    clause_id = metadata.get("clause_id", "")
                    if clause_id:
                        # 创建Clause实体
                        self._create_entity_for_clause(tx, graph_id, episode_id, clause_id, content, embedding, metadata)
                elif chunk_type == "section":
                    title = metadata.get("title", content[:50])
                    self._create_section_entity(tx, graph_id, episode_id, title, metadata, embedding)
                elif chunk_type == "element":
                    element_type = metadata.get("element_type", "parameter")
                    key = metadata.get("key", content[:50])
                    self._create_element_entity(tx, graph_id, episode_id, element_type, key, metadata, embedding)

            self._call_with_retry(session.execute_write, _create_episode_and_entities)

        logger.info(f"[hierarchical] Created episode {episode_id[:8]} with entities (Level{level}, {chunk_type})")
        return episode_id

    def _create_entity_for_clause(self, tx, graph_id: str, episode_id: str,
                                  clause_id: str, content: str,
                                  embedding: List[float], metadata: Dict):
        """为Clause创建Entity节点"""
        clause_name = f"条款{clause_id}"

        # 生成Entity UUID
        entity_seed = f"{graph_id}:{clause_name}".encode('utf-8')
        entity_uuid = str(uuid.UUID(hashlib.md5(entity_seed).hexdigest()))

        # 创建Clause Entity节点
        tx.run(
            """
            MERGE (e:Entity:Clause {graph_id: $gid, name_lower: $name_lower})
            ON CREATE SET
                e.uuid = $uuid,
                e.name = $name,
                e.summary = $summary,
                e.embedding = $embedding,
                e.created_at = datetime()
            ON MATCH SET
                e.embedding = $embedding,
                e.summary = COALESCE(e.summary, $summary)
            """,
            gid=graph_id,
            name_lower=clause_name.lower(),
            uuid=entity_uuid,
            name=clause_name,
            summary=content[:500] if content else "",
            embedding=embedding
        )

        # 链接Episode -> Entity (MENTIONS)
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

        # 如果有formula_refs，创建Formula实体
        formula_refs = metadata.get("formula_refs", [])
        if isinstance(formula_refs, str):
            formula_refs = [formula_refs]
        for formula_id in formula_refs:
            if formula_id:
                self._create_formula_entity(tx, graph_id, episode_id, entity_uuid, formula_id)

        # 如果有table_refs，创建Parameter实体
        table_refs = metadata.get("table_refs", [])
        if isinstance(table_refs, str):
            table_refs = [table_refs]
        for table_ref in table_refs:
            if table_ref:
                self._create_table_parameter_entity(tx, graph_id, episode_id, entity_uuid, table_ref)

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

        tx.run(
            f"""
            MERGE (e:Entity:`{entity_type}` {{graph_id: $gid, name_lower: $name_lower}})
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
            name_lower=entity_name.lower(),
            uuid=entity_uuid,
            name=entity_name,
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
                                      clause_entity_uuid: str, table_ref: str):
        """创建Table/Parameter实体"""
        param_name = f"{table_ref}"

        entity_seed = f"{graph_id}:Parameter:{param_name}".encode('utf-8')
        entity_uuid = str(uuid.UUID(hashlib.md5(entity_seed).hexdigest()))

        try:
            tx.run(
                """
                MERGE (e:Entity:Parameter {graph_id: $gid, name_lower: $name_lower})
                ON CREATE SET
                    e.uuid = $uuid,
                    e.name = $name,
                    e.created_at = datetime()
                """,
                gid=graph_id,
                name_lower=param_name.lower(),
                uuid=entity_uuid,
                name=param_name
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
                MATCH (c:Entity:Clause {graph_id: $gid})
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
