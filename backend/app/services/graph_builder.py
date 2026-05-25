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


    def create_graph(self, name: str) -> str:
        """Create graph"""
        return self.storage.create_graph(
            name=name,
            description="Knowledge EviGraph Social Simulation Graph"
        )

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]):
        """
        SetGraphOntology

        Simply stores ontology as JSON in the Graph node.
        No more dynamic Pydantic class creation (was Zep-specific).
        The NER extractor reads this ontology to guide extraction.
        """
        self.storage.set_ontology(graph_id, ontology)


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
    # 多层级分块支持
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

        # 过滤 Level3 element，只保留 Level1/Level2（Level3 由 Topic-Entity 关系处理）
        work_items = [
            (idx, chunk_dict) for idx, chunk_dict in enumerate(all_chunks)
            if chunk_dict.get('metadata', {}).get('level') != 3
        ]
        total_work = len(work_items)
        logger.info(f"[hierarchical] Filtering: {total} total -> {total_work} chunks (excluded level=3)")

        # 第一阶段：并发生成所有 embeddings（Embedding 服务无状态，线程安全）
        logger.info(f"[hierarchical] Phase 1: Pre-generating {total_work} embeddings with max_workers=12")
        wrapped_callback(0, total_work)
        embeddings_map: Dict[int, List[float]] = {}

        def gen_embedding(args):
            idx, chunk_dict = args
            try:
                text = chunk_dict.get("text") or chunk_dict.get("content", "")
                if not text or not text.strip():
                    return idx, []
                emb = self.storage._embedding.embed(text)
                return idx, emb
            except Exception as e:
                logger.warning(f"[hierarchical] Embedding failed for idx={idx}: {e}")
                return idx, []

        with ThreadPoolExecutor(max_workers=12) as emb_executor:
            emb_futures = {emb_executor.submit(gen_embedding, item): item for item in work_items}
            for fut in as_completed(emb_futures):
                idx, emb = fut.result()
                embeddings_map[idx] = emb

        logger.info(f"[hierarchical] Phase 1 done: {len(embeddings_map)} embeddings generated")

        # 第二阶段：UNWIND 批量写入 Neo4j（单事务，批量节点创建）
        logger.info(f"[hierarchical] Phase 2: Batch UNWIND writing {total_work} chunks to Neo4j")

        def batch_progress_callback(processed, total_chunks):
            wrapped_callback(processed, total_chunks)
            if processed == 1:
                logger.info(f"[hierarchical] First batch committed: 1 chunk written to Neo4j")

        chunks_to_write = [chunk_dict for _, chunk_dict in work_items]
        try:
            episode_ids_from_batch = self.storage.batch_add_hierarchical_chunks(
                graph_id,
                chunks_to_write,
                embeddings_map,
                progress_callback=batch_progress_callback,
            )
            # 按原始顺序组装 episode_ids（batch 返回顺序与 work_items 一致）
            episode_ids = [eid for eid in episode_ids_from_batch if eid]
            logger.info(f"[hierarchical] ✅ Neo4j写入成功: {len(episode_ids)}/{total} episodes created")
        except Exception as e:
            logger.error(f"[hierarchical] ❌ Neo4j写入失败: {e}")
            raise

        # 构建交叉引用关系 + PART_OF层级关系（相互独立，并行执行）
        cross_ref_count, hier_count = 0, 0
        try:
            logger.info("[hierarchical] Building cross-reference & hierarchical relations in parallel...")
            with ThreadPoolExecutor(max_workers=2) as rel_executor:
                future_cross = rel_executor.submit(self.storage.build_cross_ref_relations, graph_id)
                future_hier = rel_executor.submit(self.storage.build_hierarchical_relations, graph_id)
                try:
                    cross_ref_count = future_cross.result()
                    logger.info(f"[hierarchical] Built {cross_ref_count} cross-reference relations")
                except Exception as e:
                    logger.warning(f"[hierarchical] Failed to build cross-refs: {e}")
                try:
                    hier_count = future_hier.result()
                    logger.info(f"[hierarchical] Built {hier_count} hierarchical relations")
                except Exception as e:
                    logger.warning(f"[hierarchical] Failed to build hierarchical relations: {e}")
        except Exception as e:
            logger.warning(f"[hierarchical] Parallel relation building failed: {e}")

        logger.info(f"[hierarchical] Complete: {len(episode_ids)} episodes created")
        return episode_ids