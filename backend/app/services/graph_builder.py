"""
Graph build service.
Uses the configured graph backend (Zep or Neo4j) to build and manage knowledge graphs.
"""

import time
import threading
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass

from ..config import Config
from ..models.task import TaskManager, TaskStatus
from .graph_backend import get_graph_backend, IGraphBackend
from .text_processor import TextProcessor


@dataclass
class GraphInfo:
    """Graph info."""
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
    Graph build service: creates graphs, sets ontology, ingests text via the configured backend (Zep or Neo4j).
    """

    def __init__(self, api_key: Optional[str] = None, backend: Optional[IGraphBackend] = None):
        self.backend = backend or get_graph_backend(api_key=api_key or Config.ZEP_API_KEY)
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
        """Build graph asynchronously. Returns task_id."""
        task_id = self.task_manager.create_task(
            task_type="graph_build",
            metadata={
                "graph_name": graph_name,
                "chunk_size": chunk_size,
                "text_length": len(text),
            }
        )
        
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
        """Worker: build graph in background."""
        try:
            self.task_manager.update_task(
                task_id,
                status=TaskStatus.PROCESSING,
                progress=5,
                message="Building graph..."
            )
            graph_id = self.backend.create_graph(graph_name)
            self.task_manager.update_task(
                task_id,
                progress=10,
                message=f"Graph created: {graph_id}"
            )
            self.backend.set_ontology(graph_id, ontology)
            self.task_manager.update_task(
                task_id,
                progress=15,
                message="Ontology set"
            )
            chunks = TextProcessor.split_text(text, chunk_size, chunk_overlap)
            total_chunks = len(chunks)
            self.task_manager.update_task(
                task_id,
                progress=20,
                message=f"Split into {total_chunks} chunks"
            )
            episode_uuids = self.backend.add_text_batches(
                graph_id, chunks, batch_size,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=20 + int(prog * 0.4),  # 20-60%
                    message=msg
                )
            )
            
            self.task_manager.update_task(
                task_id,
                progress=60,
                message="Waiting for processing..."
            )
            
            self.backend.wait_for_episodes(
                episode_uuids,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=60 + int(prog * 0.3),  # 60-90%
                    message=msg
                )
            )
            
            self.task_manager.update_task(
                task_id,
                progress=90,
                message="Fetching graph info..."
            )
            info = self.backend.get_graph_info(graph_id)
            graph_info = GraphInfo(
                graph_id=info["graph_id"],
                node_count=info["node_count"],
                edge_count=info["edge_count"],
                entity_types=info.get("entity_types", []),
            )
            self.task_manager.complete_task(task_id, {
                "graph_id": graph_id,
                "graph_info": graph_info.to_dict(),
                "chunks_processed": total_chunks,
            })
            
        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self.task_manager.fail_task(task_id, error_msg)
    
    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        """
        Return full graph data (nodes, edges with temporal and attributes).
        """
        return self.backend.get_graph_data(graph_id)

    def delete_graph(self, graph_id: str) -> None:
        """Delete the graph and all its data."""
        self.backend.delete_graph(graph_id)

