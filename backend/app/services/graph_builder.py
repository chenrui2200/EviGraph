"""
Graph building service.
Uses GraphStorage (Neo4j) to replace Zep Cloud API.
"""

import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass

from ..config import Config
from ..models.task import TaskManager, TaskStatus
from ..storage import GraphStorage
from .text_processor import TextProcessor

logger = logging.getLogger('mirofish.graph_builder')


@dataclass
class GraphInfo:
    """Graph information"""
    graph_id: str
    node_count: int
    edge_count: int
    entity_types: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "entity_types": self.entity_types,
        }


class GraphBuilderService:
    """
    Graph building service
    Build knowledge graph through GraphStorage interface
    """

    # Global registry to track active build workers
    _active_workers: Dict[str, bool] = {} # project_id -> is_active
    _registry_lock = threading.Lock()

    # Global lock to prevent multiple build threads for the same graph
    _build_locks: Dict[str, threading.Lock] = {}
    _lock_manager_lock = threading.Lock()

    def __init__(self, storage: GraphStorage):
        self.storage = storage
        self.task_manager = TaskManager()

    def _get_build_lock(self, graph_id: str) -> threading.Lock:
        with self._lock_manager_lock:
            if graph_id not in self._build_locks:
                self._build_locks[graph_id] = threading.Lock()
            return self._build_locks[graph_id]

    def is_worker_active(self, project_id: str) -> bool:
        """Check if a worker is currently running for this project"""
        with self._registry_lock:
            return self._active_workers.get(project_id, False)

    def register_worker(self, project_id: str):
        with self._registry_lock:
            self._active_workers[project_id] = True

    def unregister_worker(self, project_id: str):
        with self._registry_lock:
            if project_id in self._active_workers:
                self._active_workers[project_id] = False

    def build_graph_async(
        self,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str = "MiroFish Graph",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        batch_size: int = 3
    ) -> str:
        """
        Build graph asynchronously

        Args:
            text: Input text to process
            ontology: Ontology definition (from ontology generator output)
            graph_name: Name for the graph
            chunk_size: Text chunk size
            chunk_overlap: Chunk overlap size
            batch_size: Number of chunks to send per batch

        Returns:
            Task ID
        """
        # Create task
        task_id = self.task_manager.create_task(
            task_type="graph_build",
            metadata={
                "graph_name": graph_name,
                "chunk_size": chunk_size,
                "text_length": len(text),
            }
        )

        # Execute build in background thread
        thread = threading.Thread(
            target=self._build_graph_worker,
            args=(task_id, text, ontology, graph_name, chunk_size, chunk_overlap, batch_size)
        )
        thread.daemon = True
        thread.start()

        return task_id

    def _build_graph_worker(
        self,
        task_id: str,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str,
        chunk_size: int,
        chunk_overlap: int,
        batch_size: int
    ):
        """Graph build worker thread"""
        try:
            self.task_manager.update_task(
                task_id,
                status=TaskStatus.PROCESSING,
                progress=5,
                message="Starting graph building..."
            )

            # 1. Create graph
            graph_id = self.create_graph(graph_name)
            self.task_manager.update_task(
                task_id,
                progress=10,
                message=f"Graph created: {graph_id}"
            )

            # 2. Set ontology
            self.set_ontology(graph_id, ontology)
            self.task_manager.update_task(
                task_id,
                progress=15,
                message="Ontology set"
            )

            # 3. Text chunking
            chunks = TextProcessor.split_text(text, chunk_size, chunk_overlap)
            total_chunks = len(chunks)
            self.task_manager.update_task(
                task_id,
                progress=20,
                message=f"Text split into {total_chunks} chunks"
            )

            # 4. Send data in batches (NER + embedding + Neo4j insert — synchronous)
            episode_uuids = self.add_text_batches(
                graph_id, chunks, batch_size,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=20 + int(prog * 0.6),  # 20-80%
                    message=msg
                )
            )

            # 5. Wait for processing (no-op for Neo4j — already synchronous)
            self.storage.wait_for_processing(episode_uuids)

            self.task_manager.update_task(
                task_id,
                progress=85,
                message="Data processing completed, getting graph information..."
            )

            # 6. Get graph information
            graph_info = self._get_graph_info(graph_id)

            # Completed
            self.task_manager.complete_task(task_id, {
                "graph_id": graph_id,
                "graph_info": graph_info.to_dict(),
                "chunks_processed": total_chunks,
            })

        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self.task_manager.fail_task(task_id, error_msg)

    def create_graph(self, name: str) -> str:
        """Create graph"""
        return self.storage.create_graph(
            name=name,
            description="MiroFish Social Simulation Graph"
        )

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]):
        """
        SetGraphOntology

        Simply stores ontology as JSON in the Graph node.
        No more dynamic Pydantic class creation (was Zep-specific).
        The NER extractor reads this ontology to guide extraction.
        """
        self.storage.set_ontology(graph_id, ontology)

    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[Any],
        batch_size: int = 5,
        progress_callback: Optional[Callable] = None,
    ) -> List[str]:
        """
        Add text chunks to the graph.
        Now leverages the concurrent batch processing in Neo4jStorage.
        """
        logger.info(f"[graph_build] Delegating processing of {len(chunks)} chunks to Neo4jStorage (batch_size={batch_size})")

        # Directly call the storage-level batch processor which we optimized for concurrency
        episode_uuids = self.storage.add_text_batch(
            graph_id,
            chunks,
            batch_size=batch_size,
            progress_callback=progress_callback
        )

        logger.info(f"[graph_build] All {len(chunks)} chunks processed successfully")
        return episode_uuids

    def _get_graph_info(self, graph_id: str) -> GraphInfo:
        """Get graph information"""
        info = self.storage.get_graph_info(graph_id)
        return GraphInfo(
            graph_id=info["graph_id"],
            node_count=info["node_count"],
            edge_count=info["edge_count"],
            entity_types=info.get("entity_types", []),
        )

    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        """Get complete graph data (including details)"""
        return self.storage.get_graph_data(graph_id)

    def delete_graph(self, graph_id: str):
        """Delete graph"""
        self.storage.delete_graph(graph_id)

    # ========================================================================
    # 多层级分块支持（新增）
    # ========================================================================

    def add_hierarchical_chunks(
        self,
        graph_id: str,
        hierarchical_result: "HierarchicalChunkResult",
        progress_callback: Optional[Callable] = None
    ) -> List[str]:
        """
        添加多层级分块到图谱

        同时创建Episode节点和Entity节点，构建知识图谱：
        - Clause -> Entity节点（Clause类型）
        - Formula/Parameter -> Entity节点（相应类型）
        - Episode -> Entity的MENTIONS关系
        - 实体之间的交叉引用关系

        Args:
            graph_id: 图谱ID
            hierarchical_result: HierarchicalChunkResult对象
            progress_callback: 进度回调

        Returns:
            episode_uuid列表
        """
        from ..models.clause import HierarchicalChunkResult

        all_chunks = hierarchical_result.to_episode_list()
        total = len(all_chunks)
        episode_ids = []

        logger.info(f"[hierarchical] Processing {total} hierarchical chunks")

        def wrapped_callback(processed, total_chunks):
            """包装回调，将processed/total转换为进度百分比"""
            if progress_callback:
                # progress_ratio: 0-1 的浮点数
                progress_ratio = processed / total_chunks if total_chunks > 0 else 0
                # 映射到15-75%的进度
                mapped_progress = 15 + int(progress_ratio * 60)
                # 回调签名: (msg, progress_ratio, log=None)
                log_msg = f"Processed {processed}/{total_chunks} chunks"
                progress_callback(log_msg, mapped_progress / 100)

        # 批量存储
        for idx, chunk_dict in enumerate(all_chunks):
            try:
                # 使用add_hierarchical_chunk_with_entities创建Episode和Entity
                episode_id = self.storage.add_hierarchical_chunk_with_entities(graph_id, chunk_dict)
                if episode_id:  # Skip empty chunks
                    episode_ids.append(episode_id)
            except Exception as e:
                logger.error(f"[hierarchical] Failed to add chunk: {e}")

            if (idx + 1) % 10 == 0:
                wrapped_callback(idx + 1, total)
                logger.info(f"[hierarchical] Progress: {idx + 1}/{total}")

        # 构建交叉引用关系
        try:
            logger.info("[hierarchical] Building cross-reference relations...")
            cross_ref_count = self.storage.build_cross_ref_relations(graph_id)
            logger.info(f"[hierarchical] Built {cross_ref_count} cross-reference relations")
        except Exception as e:
            logger.warning(f"[hierarchical] Failed to build cross-refs: {e}")

        # 构建PART_OF层级关系
        try:
            logger.info("[hierarchical] Building hierarchical relations...")
            hier_count = self.storage.build_hierarchical_relations(graph_id)
            logger.info(f"[hierarchical] Built {hier_count} hierarchical relations")
        except Exception as e:
            logger.warning(f"[hierarchical] Failed to build hierarchical relations: {e}")

        logger.info(f"[hierarchical] Complete: {len(episode_ids)} episodes created")
        return episode_ids

    def enrich_clauses_semantics(
        self,
        graph_id: str,
        progress_callback: Optional[Callable] = None
    ) -> int:
        """
        对条文进行LLM语义补充

        Args:
            graph_id: 图谱ID
            progress_callback: 进度回调

        Returns:
            补充的条文数量
        """
        from ..services.semantic_enricher import SemanticEnricher

        enricher = SemanticEnricher()

        # 获取未语义化的条文
        unenriched = self.storage.get_unenriched_clauses(graph_id, limit=100)
        total = len(unenriched)

        if total == 0:
            logger.info("[semantic] No unenriched clauses found")
            return 0

        logger.info(f"[semantic] Enriching {total} clauses...")

        def wrapped_callback(progress_ratio):
            if progress_callback:
                progress_callback(progress_ratio)

        # 批量语义补充
        enrichments = enricher.enrich_clauses(
            unenriched,
            progress_callback=wrapped_callback
        )

        # 更新Neo4j
        for enrichment in enrichments:
            try:
                # 查找对应条文
                clause_uuid = None
                for uc in unenriched:
                    if uc.get('clause_id') == enrichment.clause_id:
                        clause_uuid = uc.get('uuid')
                        break

                if clause_uuid:
                    # 生成关系
                    relations = enricher.generate_neo4j_relations(enrichment, clause_uuid)
                    # 添加关系
                    self.storage.add_clause_relations(
                        graph_id,
                        clause_uuid,
                        relations,
                        episode_ids=[]
                    )
                    # 标记已语义化
                    self.storage.mark_clause_enriched(clause_uuid)
            except Exception as e:
                logger.warning(f"[semantic] Failed to process enrichment: {e}")

        logger.info(f"[semantic] Enriched {len(enrichments)} clauses")
        return len(enrichments)
