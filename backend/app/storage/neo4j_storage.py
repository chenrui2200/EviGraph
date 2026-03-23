"""
Neo4jStorage — Neo4j Community Edition implementation of GraphStorage.

Replaces all Zep Cloud API calls with local Neo4j Cypher queries.
Includes: CRUD, NER/RE-based text ingestion, hybrid search, retry logic.
"""

import json
import time
import uuid
import logging
import traceback
import concurrent.futures
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Callable, Union

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
        with self._driver.session() as session:
            for query in neo4j_schema.ALL_SCHEMA_QUERIES:
                try:
                    session.run(query)
                except Exception as e:
                    logger.warning(f"Schema query warning (may already exist): {e}")

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
        import hashlib
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

        print(f"🔨 [START] Processing Episode {episode_id[:8]}...")
        logger.info(f"🔨 [START] Processing Episode {episode_id[:8]} (Content Length: {len(text)})")

        # 1. Create episode node and structural skeleton IMMEDIATELY
        chunk_embedding = []
        try:
            print(f"📡 [STEP 1/6] [{episode_id[:8]}] Requesting Embedding...")
            logger.info(f"📡 [STEP 1/6] [{episode_id[:8]}] Requesting Embedding...")
            # Embedding is often the first bottleneck
            chunk_embedding = self._embedding.embed(text)
            print(f"✅ [STEP 1/6] [{episode_id[:8]}] Embedding received.")
            logger.info(f"✅ [STEP 1/6] [{episode_id[:8]}] Embedding received.")
        except Exception as e:
            print(f"❌ [STEP 1/6] [{episode_id[:8]}] Embedding failed: {e}")
            logger.warning(f"❌ [STEP 1/6] [{episode_id[:8]}] Embedding failed: {e}")

        with self._driver.session() as session:
            def _create_skeleton(tx):
                print(f"💾 [STEP 2/6] [{episode_id[:8]}] Saving Skeleton to Neo4j...")
                logger.info(f"💾 [STEP 2/6] [{episode_id[:8]}] Creating/Merging Episode node in Neo4j...")
                # Use MERGE instead of CREATE to handle episodes that were created but not fully processed
                tx.run(
                    """
                    MERGE (ep:Episode {uuid: $uuid})
                    ON CREATE SET
                        ep.graph_id = $graph_id,
                        ep.data = $data,
                        ep.metadata_json = $metadata_json,
                        ep.processed = false,
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
                    data=text,
                    metadata_json=metadata_json,
                    embedding=chunk_embedding,
                    created_at=now,
                )

                # Link to Page/Document if metadata is available
                if metadata:
                    filename = metadata.get("source")
                    page_num = metadata.get("page")
                    chunk_idx = metadata.get("chunk_index", 0)
                    if filename:
                        doc_uuid = self._ensure_document(tx, graph_id, filename)

                        # Sequential linking
                        if chunk_idx > 0:
                            tx.run(
                                """
                                MATCH (prev:Episode {graph_id: $gid})
                                WHERE prev.metadata_json CONTAINS $filename
                                  AND prev.metadata_json CONTAINS $prev_idx_str
                                MATCH (curr:Episode {uuid: $curr_uuid})
                                MERGE (prev)-[r:NEXT_EPISODE]->(curr)
                                ON CREATE SET r.graph_id = $gid
                                """,
                                gid=graph_id,
                                filename=f'"source": "{filename}"',
                                prev_idx_str=f'"chunk_index": {chunk_idx - 1}',
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
            print(f"✅ [STEP 2/6] [{episode_id[:8]}] Episode skeleton ready.")
            logger.info(f"✅ [STEP 2/6] [{episode_id[:8]}] Episode skeleton ready.")

        # 2. Knowledge Extraction (Guided by Ontology)
        print(f"🔍 [STEP 3/6] [{episode_id[:8]}] Fetching ontology...")
        logger.info(f"🔍 [STEP 3/6] [{episode_id[:8]}] Fetching ontology...")
        ontology = self.get_ontology(graph_id)

        print(f"🤖 [STEP 4/6] [{episode_id[:8]}] Calling LLM Chat (NER)...")
        logger.info(f"🤖 [STEP 4/6] [{episode_id[:8]}] Calling LLM for NER extraction (Chat)...")
        # This is where 'chat' method is called
        extraction = self._ner.extract(text, ontology)
        entities = extraction.get("entities", [])
        relations = extraction.get("relations", [])

        print(f"✅ [STEP 4/6] [{episode_id[:8]}] NER Done: {len(entities)} entities found.")
        logger.info(f"✅ [STEP 4/6] [{episode_id[:8]}] NER extracted {len(entities)} entities and {len(relations)} relations.")

        # 3. Batch embed all extraction results
        entity_summaries = [f"{e['name']} ({e['type']})" for e in entities]
        fact_texts = [r.get("fact", f"{r['source']} {r['type']} {r['target']}") for r in relations]
        all_texts_to_embed = entity_summaries + fact_texts

        all_embeddings: list = []
        if all_texts_to_embed:
            try:
                print(f"📡 [STEP 5/6] [{episode_id[:8]}] Embedding results...")
                logger.info(f"📡 [STEP 5/6] [{episode_id[:8]}] Embedding {len(all_texts_to_embed)} facts/entities...")
                all_embeddings = self._embedding.embed_batch(all_texts_to_embed)
                print(f"✅ [STEP 5/6] [{episode_id[:8]}] Knowledge embeddings received.")
                logger.info(f"✅ [STEP 5/6] [{episode_id[:8]}] Knowledge embeddings received.")
            except Exception as e:
                print(f"❌ [STEP 5/6] [{episode_id[:8]}] Knowledge embedding failed: {e}")
                logger.warning(f"❌ [STEP 5/6] [{episode_id[:8]}] Knowledge embedding failed: {e}")
                all_embeddings = [[] for _ in all_texts_to_embed]

        entity_embeddings = all_embeddings[:len(entities)]
        relation_embeddings = all_embeddings[len(entities):]

        # 4. Write knowledge back to Neo4j
        print(f"💾 [STEP 6/6] [{episode_id[:8]}] Finalizing in Neo4j...")
        logger.info(f"💾 [STEP 6/6] [{episode_id[:8]}] Writing entities and relations to Neo4j...")
        with self._driver.session() as session:
            # Mark episode as processed
            session.run("MATCH (ep:Episode {uuid: $uuid}) SET ep.processed = true", uuid=episode_id)
            # Mark episode as processed
            session.run("MATCH (ep:Episode {uuid: $uuid}) SET ep.processed = true", uuid=episode_id)

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

                    # 3. Auto-Hierarchy for Clauses
                    import re
                    clause_match = re.match(r'^(\d+\.\d+)\.\d+$', _name) # matches 3.1.1
                    if clause_match:
                        parent_name = clause_match.group(1)
                        # Create/Link to parent clause automatically
                        tx.run(
                            """
                            MERGE (p:Entity {graph_id: $gid, name_lower: $p_name_lower})
                            ON CREATE SET
                                p.uuid = randomUUID(),
                                p.name = $p_name,
                                p.created_at = datetime()
                            WITH p
                            MATCH (c:Entity {uuid: $c_uuid})
                            MERGE (c)-[r:SUB_CLAUSE_OF]->(p)
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
                # This ensures the graph remains a meaningful DAG for reasoning.
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

        logger.info(f"[add_text] Knowledge update done for episode {episode_id[:8]}")
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
