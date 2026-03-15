"""
Zep Cloud implementation of the graph backend.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from zep_cloud.client import Zep
from zep_cloud import EpisodeData, EntityEdgeSourceTarget

from ...config import Config
from ...utils.logger import get_logger
from ...utils.zep_paging import fetch_all_nodes, fetch_all_edges

from .base import IGraphBackend

logger = get_logger("mirofish.graph_backend.zep")


class ZepGraphBackend(IGraphBackend):
    """Graph backend using Zep Cloud API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or Config.ZEP_API_KEY
        if not self.api_key:
            raise ValueError("ZEP_API_KEY is required when GRAPH_BACKEND=zep")
        self.client = Zep(api_key=self.api_key)

    def create_graph(self, name: str) -> str:
        graph_id = f"mirofish_{uuid.uuid4().hex[:16]}"
        self.client.graph.create(
            graph_id=graph_id,
            name=name,
            description="MiroFish Social Simulation Graph",
        )
        return graph_id

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]) -> None:
        import warnings
        from pydantic import Field
        from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel

        warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")
        RESERVED_NAMES = {"uuid", "name", "group_id", "name_embedding", "summary", "created_at"}

        def safe_attr_name(attr_name: str) -> str:
            if attr_name.lower() in RESERVED_NAMES:
                return f"entity_{attr_name}"
            return attr_name

        entity_types = {}
        for entity_def in ontology.get("entity_types", []):
            name = entity_def["name"]
            description = entity_def.get("description", f"A {name} entity.")
            attrs = {"__doc__": description}
            annotations = {}
            for attr_def in entity_def.get("attributes", []):
                attr_name = safe_attr_name(attr_def["name"])
                attr_desc = attr_def.get("description", attr_name)
                attrs[attr_name] = Field(description=attr_desc, default=None)
                annotations[attr_name] = Optional[EntityText]
            attrs["__annotations__"] = annotations
            entity_class = type(name, (EntityModel,), attrs)
            entity_class.__doc__ = description
            entity_types[name] = entity_class

        edge_definitions = {}
        for edge_def in ontology.get("edge_types", []):
            name = edge_def["name"]
            description = edge_def.get("description", f"A {name} relationship.")
            attrs = {"__doc__": description}
            annotations = {}
            for attr_def in edge_def.get("attributes", []):
                attr_name = safe_attr_name(attr_def["name"])
                attr_desc = attr_def.get("description", attr_name)
                attrs[attr_name] = Field(description=attr_desc, default=None)
                annotations[attr_name] = Optional[str]
            attrs["__annotations__"] = annotations
            class_name = "".join(word.capitalize() for word in name.split("_"))
            edge_class = type(class_name, (EdgeModel,), attrs)
            edge_class.__doc__ = description
            source_targets = [
                EntityEdgeSourceTarget(
                    source=st.get("source", "Entity"),
                    target=st.get("target", "Entity"),
                )
                for st in edge_def.get("source_targets", [])
            ]
            if source_targets:
                edge_definitions[name] = (edge_class, source_targets)

        if entity_types or edge_definitions:
            self.client.graph.set_ontology(
                graph_ids=[graph_id],
                entities=entity_types if entity_types else None,
                edges=edge_definitions if edge_definitions else None,
            )

    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[str],
        batch_size: int = 3,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> List[str]:
        episode_uuids = []
        total_chunks = len(chunks)
        for i in range(0, total_chunks, batch_size):
            batch_chunks = chunks[i : i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total_chunks + batch_size - 1) // batch_size
            if progress_callback:
                progress = (i + len(batch_chunks)) / total_chunks
                progress_callback(
                    f"Batch {batch_num}/{total_batches} ({len(batch_chunks)} chunks)...",
                    progress,
                )
            episodes = [EpisodeData(data=chunk, type="text") for chunk in batch_chunks]
            try:
                batch_result = self.client.graph.add_batch(graph_id=graph_id, episodes=episodes)
                if batch_result and isinstance(batch_result, list):
                    for ep in batch_result:
                        ep_uuid = getattr(ep, "uuid_", None) or getattr(ep, "uuid", None)
                        if ep_uuid:
                            episode_uuids.append(ep_uuid)
                time.sleep(1)
            except Exception as e:
                if progress_callback:
                    progress_callback(f"Batch {batch_num} failed: {e}", 0)
                raise
        return episode_uuids

    def wait_for_episodes(
        self,
        episode_uuids: List[str],
        progress_callback: Optional[Callable[[str, float], None]] = None,
        timeout: int = 600,
    ) -> None:
        if not episode_uuids:
            if progress_callback:
                progress_callback("No episodes to wait for", 1.0)
            return
        start_time = time.time()
        pending = set(episode_uuids)
        total = len(episode_uuids)
        completed = 0
        while pending:
            if time.time() - start_time > timeout:
                if progress_callback:
                    progress_callback(f"Timeout; {completed}/{total} done", completed / total if total else 0)
                break
            for ep_uuid in list(pending):
                try:
                    episode = self.client.graph.episode.get(uuid_=ep_uuid)
                    if getattr(episode, "processed", False):
                        pending.discard(ep_uuid)
                        completed += 1
                except Exception:
                    pass
            if progress_callback:
                progress_callback(f"Processing... {completed}/{total} done", completed / total if total else 0)
            if pending:
                time.sleep(3)
        if progress_callback:
            progress_callback(f"Done: {completed}/{total}", 1.0)

    def get_graph_info(self, graph_id: str) -> Dict[str, Any]:
        nodes = fetch_all_nodes(self.client, graph_id)
        edges = fetch_all_edges(self.client, graph_id)
        entity_types = set()
        for node in nodes:
            if node.labels:
                for label in node.labels:
                    if label not in ("Entity", "Node"):
                        entity_types.add(label)
        return {
            "graph_id": graph_id,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "entity_types": list(entity_types),
        }

    def _node_to_dict(self, node: Any) -> Dict[str, Any]:
        uid = getattr(node, "uuid_", None) or getattr(node, "uuid", "") or ""
        return {
            "uuid": uid,
            "name": node.name or "",
            "labels": node.labels or [],
            "summary": node.summary or "",
            "attributes": node.attributes or {},
        }

    def _edge_to_dict(self, edge: Any, node_map: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        uid = getattr(edge, "uuid_", None) or getattr(edge, "uuid", "") or ""
        d = {
            "uuid": uid,
            "name": edge.name or "",
            "fact": edge.fact or "",
            "source_node_uuid": edge.source_node_uuid,
            "target_node_uuid": edge.target_node_uuid,
            "attributes": edge.attributes or {},
        }
        if node_map:
            d["source_node_name"] = node_map.get(edge.source_node_uuid, "")
            d["target_node_name"] = node_map.get(edge.target_node_uuid, "")
        for key in ("created_at", "valid_at", "invalid_at", "expired_at"):
            v = getattr(edge, key, None)
            if v is not None:
                d[key] = str(v)
        eps = getattr(edge, "episodes", None) or getattr(edge, "episode_ids", None)
        if eps and not isinstance(eps, list):
            eps = [str(eps)]
        elif eps:
            eps = [str(e) for e in eps]
        if eps:
            d["episodes"] = eps
        if hasattr(edge, "fact_type"):
            d["fact_type"] = edge.fact_type or edge.name or ""
        return d

    def get_all_nodes(self, graph_id: str) -> List[Dict[str, Any]]:
        nodes = fetch_all_nodes(self.client, graph_id)
        return [self._node_to_dict(n) for n in nodes]

    def get_all_edges(self, graph_id: str) -> List[Dict[str, Any]]:
        edges = fetch_all_edges(self.client, graph_id)
        nodes = fetch_all_nodes(self.client, graph_id)
        node_map = {getattr(n, "uuid_", None) or getattr(n, "uuid", ""): n.name or "" for n in nodes}
        return [self._edge_to_dict(e, node_map) for e in edges]

    def get_node(self, node_uuid: str) -> Optional[Dict[str, Any]]:
        try:
            node = self.client.graph.node.get(uuid_=node_uuid)
            if node:
                return self._node_to_dict(node)
        except Exception as e:
            logger.debug("get_node %s failed: %s", node_uuid[:8], e)
        return None

    def get_node_edges(self, node_uuid: str) -> List[Dict[str, Any]]:
        try:
            edges = self.client.graph.node.get_entity_edges(node_uuid=node_uuid)
            return [self._edge_to_dict(e) for e in edges]
        except Exception as e:
            logger.warning("get_node_edges %s failed: %s", node_uuid[:8], e)
            return []

    def search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges",
    ) -> Dict[str, Any]:
        try:
            search_results = self.client.graph.search(
                graph_id=graph_id,
                query=query,
                limit=limit,
                scope=scope,
                reranker="cross_encoder",
            )
            facts = []
            edges = []
            nodes = []
            if hasattr(search_results, "edges") and search_results.edges:
                for edge in search_results.edges:
                    if hasattr(edge, "fact") and edge.fact:
                        facts.append(edge.fact)
                    edges.append({
                        "uuid": getattr(edge, "uuid_", None) or getattr(edge, "uuid", ""),
                        "name": getattr(edge, "name", ""),
                        "fact": getattr(edge, "fact", ""),
                        "source_node_uuid": getattr(edge, "source_node_uuid", ""),
                        "target_node_uuid": getattr(edge, "target_node_uuid", ""),
                    })
            if hasattr(search_results, "nodes") and search_results.nodes:
                for node in search_results.nodes:
                    nodes.append({
                        "uuid": getattr(node, "uuid_", None) or getattr(node, "uuid", ""),
                        "name": getattr(node, "name", ""),
                        "labels": getattr(node, "labels", []),
                        "summary": getattr(node, "summary", ""),
                    })
                    if hasattr(node, "summary") and node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")
            return {
                "facts": facts,
                "edges": edges,
                "nodes": nodes,
                "query": query,
                "total_count": len(facts),
            }
        except Exception as e:
            logger.warning("Zep search failed, fallback to local: %s", e)
            return self._local_search(graph_id, query, limit, scope)

    def _local_search(
        self,
        graph_id: str,
        query: str,
        limit: int,
        scope: str,
    ) -> Dict[str, Any]:
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(",", " ").replace("，", " ").split() if len(w.strip()) > 1]

        def score(text: str) -> int:
            if not text:
                return 0
            t = text.lower()
            if query_lower in t:
                return 100
            return sum(10 for k in keywords if k in t)

        facts = []
        edges = []
        nodes = []
        if scope in ("edges", "both"):
            all_edges = self.get_all_edges(graph_id)
            scored = [(score(e.get("fact", "") or "") + score(e.get("name", "") or ""), e) for e in all_edges]
            scored.sort(key=lambda x: x[0], reverse=True)
            for _, e in scored[:limit]:
                if e.get("fact"):
                    facts.append(e["fact"])
                edges.append(e)
        if scope in ("nodes", "both"):
            all_nodes = self.get_all_nodes(graph_id)
            scored = [(score(n.get("name", "") or "") + score(n.get("summary", "") or ""), n) for n in all_nodes]
            scored.sort(key=lambda x: x[0], reverse=True)
            for _, n in scored[:limit]:
                nodes.append(n)
                if n.get("summary"):
                    facts.append(f"[{n.get('name')}]: {n['summary']}")
        return {"facts": facts, "edges": edges, "nodes": nodes, "query": query, "total_count": len(facts)}

    def add_episode_text(self, graph_id: str, text: str) -> None:
        self.client.graph.add(graph_id=graph_id, type="text", data=text)

    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        nodes = fetch_all_nodes(self.client, graph_id)
        edges = fetch_all_edges(self.client, graph_id)
        node_map = {getattr(n, "uuid_", None) or getattr(n, "uuid", ""): n.name or "" for n in nodes}
        nodes_data = []
        for node in nodes:
            created_at = getattr(node, "created_at", None)
            nodes_data.append({
                "uuid": getattr(node, "uuid_", None) or getattr(node, "uuid", ""),
                "name": node.name,
                "labels": node.labels or [],
                "summary": node.summary or "",
                "attributes": node.attributes or {},
                "created_at": str(created_at) if created_at else None,
            })
        edges_data = []
        for edge in edges:
            edges_data.append(self._edge_to_dict(edge, node_map))
        return {
            "graph_id": graph_id,
            "nodes": nodes_data,
            "edges": edges_data,
            "node_count": len(nodes_data),
            "edge_count": len(edges_data),
        }

    def delete_graph(self, graph_id: str) -> None:
        self.client.graph.delete(graph_id=graph_id)
