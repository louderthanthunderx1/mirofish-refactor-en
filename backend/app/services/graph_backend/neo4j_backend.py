"""
Neo4j implementation of the graph backend.

Uses Neo4j to store nodes (Entity + type labels) and relationships (REL with edge_type).
Ingests text by calling an LLM to extract entities and relations from each chunk (ontology-aware).
"""

from __future__ import annotations

import json
import uuid as uuid_lib
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase

from ...config import Config
from ...utils.logger import get_logger
from ...utils.llm_client import LLMClient

from .base import IGraphBackend

logger = get_logger("mirofish.graph_backend.neo4j")

# LLM prompt for extracting entities and relations from text given ontology
_EXTRACTION_PROMPT = """You are a knowledge graph extractor. Given an ontology (entity types and relation types) and a text chunk, output a JSON object with two arrays.

Output format (valid JSON only, no other text):
{{
  "entities": [
    {{"type": "EntityTypeName", "name": "entity name", "summary": "short description", "attributes": {{"key": "value"}}}}
  ],
  "relations": [
    {{"type": "RELATION_TYPE", "source_name": "subject entity name", "target_name": "object entity name", "fact": "short fact description"}}
  ]
}}

Rules:
- Use only entity types and relation types from the ontology.
- "name" must match exactly how the entity appears or is clearly referred to in the text.
- "source_name" and "target_name" in relations must match the "name" of entities in the entities array.
- If the text has no clear entities or relations, return empty arrays.

Ontology:
{ontology}

Text chunk:
{text}
"""


def _parse_extraction_json(raw: str) -> Dict[str, Any]:
    """Parse JSON from LLM response; tolerate markdown wrapper and leading/trailing whitespace."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        raw = raw.strip()
    # Find first { and last } so we parse only the JSON object (handles leading newlines etc.)
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start : end + 1]
    return json.loads(raw)


def _normalize_extraction_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Get entities and relations from parsed data; accept keys with leading/trailing whitespace."""
    entities = []
    relations = []
    for k, v in (data or {}).items():
        key = (k or "").strip()
        if key == "entities" and isinstance(v, list):
            entities = v
            break
    for k, v in (data or {}).items():
        key = (k or "").strip()
        if key == "relations" and isinstance(v, list):
            relations = v
            break
    return {"entities": entities, "relations": relations}


def _extract_entities_relations(text: str, ontology: Dict[str, Any], llm_client: LLMClient) -> Dict[str, Any]:
    """Call LLM to extract entities and relations from text; returns {entities: [...], relations: [...]}."""
    ontology_str = json.dumps(ontology, ensure_ascii=False, indent=2)
    prompt = _EXTRACTION_PROMPT.format(ontology=ontology_str, text=text[:8000])
    try:
        # Use chat (not chat_json) + robust parse so we handle newline/brace issues from the API
        response = llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=4096,
        )
        data = _parse_extraction_json(response)
        return _normalize_extraction_data(data)
    except Exception as e:
        logger.warning("LLM extraction failed for chunk: %s", e)
        return {"entities": [], "relations": []}


class Neo4jGraphBackend(IGraphBackend):
    """Graph backend using Neo4j. Ingests text via LLM-based entity/relation extraction."""

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self._uri = uri or Config.NEO4J_URI
        self._user = user or Config.NEO4J_USER
        self._password = password or Config.NEO4J_PASSWORD
        if not self._password:
            raise ValueError("NEO4J_PASSWORD is required when GRAPH_BACKEND=neo4j")
        self._driver = GraphDatabase.driver(self._uri, auth=(self._user, self._password))
        self._llm = LLMClient()
        self._ontology_cache: Dict[str, Dict[str, Any]] = {}

    def close(self) -> None:
        self._driver.close()

    def _get_ontology(self, tx: Any, graph_id: str) -> Optional[Dict[str, Any]]:
        result = tx.run(
            "MATCH (g:GraphMetadata {id: $graph_id}) RETURN g.ontology AS ontology",
            graph_id=graph_id,
        )
        record = result.single()
        if record and record["ontology"]:
            return json.loads(record["ontology"]) if isinstance(record["ontology"], str) else record["ontology"]
        return None

    def create_graph(self, name: str) -> str:
        graph_id = f"mirofish_{uuid_lib.uuid4().hex[:16]}"
        with self._driver.session() as session:
            session.run(
                """
                CREATE (g:GraphMetadata {id: $graph_id, name: $name})
                """,
                graph_id=graph_id,
                name=name,
            )
        logger.info("Neo4j graph created: %s", graph_id)
        return graph_id

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]) -> None:
        with self._driver.session() as session:
            session.run(
                """
                MERGE (g:GraphMetadata {id: $graph_id})
                SET g.ontology = $ontology
                """,
                graph_id=graph_id,
                ontology=json.dumps(ontology, ensure_ascii=False),
            )
        self._ontology_cache[graph_id] = ontology

    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[str],
        batch_size: int = 3,
        progress_callback: Optional[Any] = None,
    ) -> List[str]:
        episode_uuids: List[str] = []
        ontology = self._ontology_cache.get(graph_id)
        with self._driver.session() as session:
            if ontology is None:
                ontology = session.execute_read(self._get_ontology, graph_id)
                if ontology:
                    self._ontology_cache[graph_id] = ontology
            if not ontology:
                logger.warning("No ontology for graph %s; skipping extraction", graph_id)
                return [str(i) for i in range(len(chunks))]

            for i, chunk in enumerate(chunks):
                if progress_callback:
                    progress_callback(f"Extracting from chunk {i + 1}/{len(chunks)}...", (i + 1) / len(chunks))
                try:
                    out = _extract_entities_relations(chunk, ontology, self._llm)
                    entities = out.get("entities") if isinstance(out.get("entities"), list) else []
                    relations = out.get("relations") if isinstance(out.get("relations"), list) else []
                    ep_id = f"ep_{graph_id}_{i}_{uuid_lib.uuid4().hex[:8]}"
                    episode_uuids.append(ep_id)
                    self._apply_extraction(session, graph_id, entities, relations)
                except Exception as chunk_err:
                    logger.warning("Chunk %s failed (skipping): %s", i + 1, chunk_err)
                    episode_uuids.append(f"ep_{graph_id}_{i}_skip_{uuid_lib.uuid4().hex[:8]}")

        return episode_uuids

    def _apply_extraction(
        self,
        session: Any,
        graph_id: str,
        entities: List[Dict[str, Any]],
        relations: List[Dict[str, Any]],
    ) -> None:
        """Write extracted entities and relations into Neo4j. Uses OpenAI text-embedding-3-small for entity vectors."""
        if not entities and not relations:
            return
        valid = []
        for e in (entities or []):
            if not isinstance(e, dict):
                continue
            name = (e.get("name") or "").strip()
            if not name:
                continue
            summary = (e.get("summary") or "").strip()
            text_to_embed = f"{name}. {summary}".strip() or name
            valid.append((e, text_to_embed))
        if not valid:
            return
        texts = [t for _, t in valid]
        try:
            entity_embeddings = self._llm.embed(texts)
        except Exception as err:
            logger.warning("Entity embedding failed, storing nodes without vectors: %s", err)
            entity_embeddings = [None] * len(texts)
        for (e, _), embedding in zip(valid, entity_embeddings):
            etype = (e.get("type") or "Entity").replace(" ", "_")
            name = (e.get("name") or "").strip()
            summary = e.get("summary") or ""
            attrs = e.get("attributes") or {}
            node_uuid = str(uuid_lib.uuid4())
            session.run(
                """
                MERGE (n:Entity {graph_id: $graph_id, name: $name})
                ON CREATE SET n.uuid = $uuid, n.summary = $summary, n.attributes = $attributes, n.entity_type = $type, n.embedding = $embedding
                ON MATCH SET n.summary = CASE WHEN n.summary IS NULL OR n.summary = '' THEN $summary ELSE n.summary END,
                             n.attributes = $attributes, n.embedding = $embedding
                """,
                graph_id=graph_id,
                name=name,
                uuid=node_uuid,
                summary=summary,
                attributes=json.dumps(attrs) if isinstance(attrs, dict) else "{}",
                type=etype,
                embedding=embedding,
            )
        # Build name -> uuid map for this graph (after all entities merged)
        result = session.run(
            "MATCH (n:Entity {graph_id: $graph_id}) WHERE n.uuid IS NOT NULL RETURN n.name AS name, n.uuid AS uuid",
            graph_id=graph_id,
        )
        name_to_uuid = {r["name"]: r["uuid"] for r in result if r["name"] and r["uuid"]}

        for rel in relations:
            src_name = (rel.get("source_name") or "").strip()
            tgt_name = (rel.get("target_name") or "").strip()
            if not src_name or not tgt_name:
                continue
            src_uuid = name_to_uuid.get(src_name)
            tgt_uuid = name_to_uuid.get(tgt_name)
            if not src_uuid or not tgt_uuid:
                continue
            fact = rel.get("fact") or ""
            rel_type = (rel.get("type") or "RELATED").replace(" ", "_")
            rel_uuid = str(uuid_lib.uuid4())
            # Create relationship; use generic :REL type and store edge type in property (avoids dynamic rel type)
            session.run(
                """
                MATCH (a:Entity {graph_id: $graph_id, uuid: $src_uuid})
                MATCH (b:Entity {graph_id: $graph_id, uuid: $tgt_uuid})
                MERGE (a)-[r:REL {graph_id: $graph_id, uuid: $rel_uuid}]->(b)
                ON CREATE SET r.fact = $fact, r.edge_type = $edge_type
                ON MATCH SET r.fact = $fact
                """,
                graph_id=graph_id,
                src_uuid=src_uuid,
                tgt_uuid=tgt_uuid,
                rel_uuid=rel_uuid,
                fact=fact,
                edge_type=rel_type,
            )

    def wait_for_episodes(
        self,
        episode_uuids: List[str],
        progress_callback: Optional[Any] = None,
        timeout: int = 600,
    ) -> None:
        # Neo4j ingestion is synchronous; no wait needed
        if progress_callback:
            progress_callback("Neo4j ingestion complete", 1.0)

    def get_graph_info(self, graph_id: str) -> Dict[str, Any]:
        with self._driver.session() as session:
            nc = session.run(
                "MATCH (n:Entity {graph_id: $graph_id}) RETURN count(n) AS c",
                graph_id=graph_id,
            ).single()
            node_count = (nc["c"] or 0) if nc else 0
            ec = session.run(
                """
                MATCH (a:Entity {graph_id: $graph_id})-[r:REL]->(b:Entity {graph_id: $graph_id})
                RETURN count(r) AS c
                """,
                graph_id=graph_id,
            ).single()
            edge_count = (ec["c"] or 0) if ec else 0
            types_result = session.run(
                "MATCH (n:Entity {graph_id: $graph_id}) WHERE n.entity_type IS NOT NULL RETURN DISTINCT n.entity_type AS t",
                graph_id=graph_id,
            )
            entity_types = [r["t"] for r in types_result if r["t"]]
        return {
            "graph_id": graph_id,
            "node_count": node_count,
            "edge_count": edge_count,
            "entity_types": entity_types,
        }

    def _node_record_to_dict(self, n: Any) -> Dict[str, Any]:
        if n is None:
            return {}
        # Store entity_type as property; expose as labels for compatibility with Zep shape
        etype = n.get("entity_type") or "Entity"
        labels = [etype, "Entity"] if etype != "Entity" else ["Entity"]
        attrs = n.get("attributes")
        if isinstance(attrs, str):
            try:
                attrs = json.loads(attrs) or {}
            except Exception:
                attrs = {}
        elif attrs is None:
            attrs = {}
        return {
            "uuid": n.get("uuid", ""),
            "name": n.get("name", ""),
            "labels": labels,
            "summary": n.get("summary", ""),
            "attributes": attrs,
        }

    def _edge_record_to_dict(self, r: Any, a: Any, b: Any) -> Dict[str, Any]:
        if r is None:
            return {}
        return {
            "uuid": r.get("uuid", ""),
            "name": r.get("edge_type", "") or "REL",
            "fact": r.get("fact", ""),
            "source_node_uuid": a.get("uuid", "") if a else "",
            "target_node_uuid": b.get("uuid", "") if b else "",
            "attributes": {},
        }

    def get_all_nodes(self, graph_id: str) -> List[Dict[str, Any]]:
        with self._driver.session() as session:
            result = session.run(
                "MATCH (n:Entity {graph_id: $graph_id}) RETURN n",
                graph_id=graph_id,
            )
            return [self._node_record_to_dict(r["n"]) for r in result]

    def get_all_edges(self, graph_id: str) -> List[Dict[str, Any]]:
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (a:Entity {graph_id: $graph_id})-[r:REL]->(b:Entity {graph_id: $graph_id})
                RETURN r, a, b
                """,
                graph_id=graph_id,
            )
            out = []
            for rec in result:
                r, a, b = rec["r"], rec["a"], rec["b"]
                d = self._edge_record_to_dict(r, a, b)
                d["source_node_name"] = a.get("name", "") if a else ""
                d["target_node_name"] = b.get("name", "") if b else ""
                out.append(d)
            return out

    def get_node(self, node_uuid: str) -> Optional[Dict[str, Any]]:
        with self._driver.session() as session:
            result = session.run("MATCH (n:Entity {uuid: $uuid}) RETURN n", uuid=node_uuid)
            rec = result.single()
            if rec and rec["n"]:
                return self._node_record_to_dict(rec["n"])
        return None

    def get_node_edges(self, node_uuid: str) -> List[Dict[str, Any]]:
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (n:Entity {uuid: $uuid})-[r:REL]-(m:Entity)
                RETURN r, startNode(r) AS a, endNode(r) AS b
                """,
                uuid=node_uuid,
            )
            out = []
            for rec in result:
                r, a, b = rec["r"], rec["a"], rec["b"]
                d = self._edge_record_to_dict(r, a, b)
                out.append(d)
            return out

    def search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges",
    ) -> Dict[str, Any]:
        q = query.strip().lower()
        if not q:
            return {"facts": [], "edges": [], "nodes": [], "query": query, "total_count": 0}
        facts = []
        edges = []
        nodes = []
        with self._driver.session() as session:
            if scope in ("edges", "both"):
                result = session.run(
                    """
                    MATCH (a:Entity {graph_id: $graph_id})-[r:REL]->(b:Entity {graph_id: $graph_id})
                    WHERE toLower(r.fact) CONTAINS $q OR toLower(r.edge_type) CONTAINS $q
                    RETURN r, a, b
                    LIMIT $limit
                    """,
                    graph_id=graph_id,
                    q=q,
                    limit=limit,
                )
                for rec in result:
                    r, a, b = rec["r"], rec["a"], rec["b"]
                    d = self._edge_record_to_dict(r, a, b)
                    edges.append(d)
                    if d.get("fact"):
                        facts.append(d["fact"])
            if scope in ("nodes", "both"):
                result = session.run(
                    """
                    MATCH (n:Entity {graph_id: $graph_id})
                    WHERE toLower(n.name) CONTAINS $q OR toLower(n.summary) CONTAINS $q
                    RETURN n
                    LIMIT $limit
                    """,
                    graph_id=graph_id,
                    q=q,
                    limit=limit,
                )
                for rec in result:
                    n = rec["n"]
                    d = self._node_record_to_dict(n)
                    nodes.append(d)
                    if d.get("summary"):
                        facts.append(f"[{d.get('name')}]: {d['summary']}")
        return {"facts": facts, "edges": edges, "nodes": nodes, "query": query, "total_count": len(facts)}

    def add_episode_text(self, graph_id: str, text: str) -> None:
        ontology = self._ontology_cache.get(graph_id)
        with self._driver.session() as session:
            if ontology is None:
                ontology = session.execute_read(self._get_ontology, graph_id)
                if ontology:
                    self._ontology_cache[graph_id] = ontology
            if not ontology:
                logger.warning("No ontology for graph %s; skipping episode", graph_id)
                return
            out = _extract_entities_relations(text, ontology, self._llm)
            self._apply_extraction(session, graph_id, out.get("entities", []), out.get("relations", []))

    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        nodes_data = self.get_all_nodes(graph_id)
        edges_data = self.get_all_edges(graph_id)
        return {
            "graph_id": graph_id,
            "nodes": nodes_data,
            "edges": edges_data,
            "node_count": len(nodes_data),
            "edge_count": len(edges_data),
        }

    def delete_graph(self, graph_id: str) -> None:
        with self._driver.session() as session:
            session.run(
                "MATCH (n {graph_id: $graph_id}) DETACH DELETE n",
                graph_id=graph_id,
            )
            session.run(
                "MATCH (g:GraphMetadata {id: $graph_id}) DELETE g",
                graph_id=graph_id,
            )
        self._ontology_cache.pop(graph_id, None)
        logger.info("Neo4j graph deleted: %s", graph_id)
