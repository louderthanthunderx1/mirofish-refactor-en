"""
Graph backend abstraction.

Defines the interface that both Zep and Neo4j implementations must satisfy,
so that graph build, entity read, report tools, and memory updater can work
with either backend via the same API.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ...config import Config
from ...utils.logger import get_logger

logger = get_logger("mirofish.graph_backend")


def get_graph_backend(api_key: Optional[str] = None, **kwargs: Any) -> "IGraphBackend":
    """
    Factory: return the configured graph backend (Zep or Neo4j).

    Uses GRAPH_BACKEND env (zep | neo4j). Pass api_key only for Zep;
    Neo4j uses NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD from config.
    """
    backend = (kwargs.get("backend") or Config.GRAPH_BACKEND or "zep").lower().strip()
    if backend == "zep":
        from .zep_backend import ZepGraphBackend
        return ZepGraphBackend(api_key=api_key or Config.ZEP_API_KEY)
    if backend == "neo4j":
        from .neo4j_backend import Neo4jGraphBackend
        return Neo4jGraphBackend()
    raise ValueError(f"Unknown GRAPH_BACKEND: {backend}. Use 'zep' or 'neo4j'.")


class IGraphBackend:
    """
    Interface for graph storage and retrieval.

    All methods use plain dicts/lists; callers may convert to GraphInfo,
    SearchResult, NodeInfo, etc.
    """

    def create_graph(self, name: str) -> str:
        """Create a new graph; return its graph_id."""
        raise NotImplementedError

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]) -> None:
        """Set entity/edge schema for the graph (from ontology generator output)."""
        raise NotImplementedError

    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[str],
        batch_size: int = 3,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> List[str]:
        """
        Ingest text chunks into the graph (extract entities/relations and store).
        Returns a list of episode/chunk identifiers (for Zep: episode uuids).
        """
        raise NotImplementedError

    def wait_for_episodes(
        self,
        episode_uuids: List[str],
        progress_callback: Optional[Callable[[str, float], None]] = None,
        timeout: int = 600,
    ) -> None:
        """
        Wait until ingested episodes are processed (e.g. Zep pipeline).
        No-op for backends that ingest synchronously (e.g. Neo4j).
        """
        raise NotImplementedError

    def get_graph_info(self, graph_id: str) -> Dict[str, Any]:
        """
        Return dict with: graph_id, node_count, edge_count, entity_types (list of str).
        """
        raise NotImplementedError

    def get_all_nodes(self, graph_id: str) -> List[Dict[str, Any]]:
        """
        Return list of nodes; each dict: uuid, name, labels (list), summary, attributes (dict).
        """
        raise NotImplementedError

    def get_all_edges(self, graph_id: str) -> List[Dict[str, Any]]:
        """
        Return list of edges; each dict: uuid, name, fact, source_node_uuid, target_node_uuid, attributes.
        Optional: created_at, valid_at, invalid_at, expired_at, episodes.
        """
        raise NotImplementedError

    def get_node(self, node_uuid: str) -> Optional[Dict[str, Any]]:
        """Return a single node by uuid, or None."""
        raise NotImplementedError

    def get_node_edges(self, node_uuid: str) -> List[Dict[str, Any]]:
        """Return edges where the node is source or target; same shape as get_all_edges items."""
        raise NotImplementedError

    def search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges",
    ) -> Dict[str, Any]:
        """
        Search the graph; return dict with: facts (list), edges (list), nodes (list), query, total_count.
        """
        raise NotImplementedError

    def add_episode_text(self, graph_id: str, text: str) -> None:
        """Append one text episode (e.g. simulation activity) to the graph."""
        raise NotImplementedError

    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        """
        Full export: dict with graph_id, nodes (list with created_at etc.), edges (list with temporal),
        node_count, edge_count.
        """
        raise NotImplementedError

    def delete_graph(self, graph_id: str) -> None:
        """Delete the graph and all its data."""
        raise NotImplementedError
