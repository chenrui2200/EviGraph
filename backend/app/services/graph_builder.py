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

    def __init__(self, storage: GraphStorage):
        self.storage = storage
        self.task_manager = TaskManager()

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
        batch_size: int = 3,
        progress_callback: Optional[Callable] = None,
        max_workers: int = 2
    ) -> List[str]:
        """
        Add text in parallel to graph using ThreadPoolExecutor.
        Each thread processes one chunk independently (LLM call + Neo4j merge).
        Returns uuid list of all episodes. Supports both strings and TextChunks.
        """
        # Lower default max_workers to 2 for better stability on local LLMs
        episode_uuids = [None] * len(chunks)
        total_chunks = len(chunks)

        logger.info(f"[graph_build] Starting Parallel Extraction: {total_chunks} chunks (max_workers={max_workers})")

        def _process_single_chunk(idx, chunk_data):
            # Handle both raw strings and TextChunk objects
            if hasattr(chunk_data, 'text'):
                text = chunk_data.text
                metadata = chunk_data.metadata
            elif isinstance(chunk_data, dict) and "text" in chunk_data:
                text = chunk_data["text"]
                metadata = chunk_data.get("metadata")
            else:
                text = str(chunk_data)
                metadata = None

            t0 = time.time()
            try:
                episode_id = self.storage.add_text(graph_id, text, metadata=metadata)
                elapsed = time.time() - t0
                log_msg = f"Chunk {idx+1}/{total_chunks} extracted in {elapsed:.1f}s"
                logger.info(f"[graph_build] {log_msg}")
                return idx, episode_id, log_msg
            except Exception as e:
                elapsed = time.time() - t0
                err_msg = f"Chunk {idx+1}/{total_chunks} FAILED after {elapsed:.1f}s: {e}"
                logger.error(f"[graph_build] {err_msg}")
                raise e

        processed_count = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Map futures to indices
            future_to_idx = {
                executor.submit(_process_single_chunk, i, chunk): i
                for i, chunk in enumerate(chunks)
            }

            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    res_idx, episode_id, log_msg = future.result()
                    episode_uuids[res_idx] = episode_id
                    processed_count += 1

                    if progress_callback:
                        progress = processed_count / total_chunks
                        # Pass log message to progress callback if it supports it
                        # The callback in api/graph.py updates task message and logs
                        progress_callback(
                            f"Extracted {processed_count}/{total_chunks} chunks...",
                            progress,
                            log=log_msg
                        )
                except Exception as e:
                    logger.error(f"Error processing chunk at index {idx}: {e}")
                    # In case of failure, we still want to finish other threads or stop depending on policy
                    # Here we let it continue but log the error.
                    # Note: The task will eventually be marked as failed by the caller if we raise.
                    if progress_callback:
                        progress_callback(f"Chunk processing failed: {str(e)}", 0)
                    raise

        logger.info(f"[graph_build] All {total_chunks} chunks processed successfully")
        return [uid for uid in episode_uuids if uid is not None]

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
