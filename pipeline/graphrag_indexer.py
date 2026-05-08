"""
GraphRAG Indexer: extracts entities + relationships from document chunks,
builds a NetworkX knowledge graph, detects communities with Louvain,
and generates community summaries for global-context retrieval.

LLM provider (Gemini / Groq) is selected via LLM_PROVIDER in .env.
"""
import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import networkx as nx

from core.logger import logger
from .llm_provider import generate


@dataclass
class Entity:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    type: str = "OTHER"
    description: str = ""
    source_chunks: List[str] = field(default_factory=list)


@dataclass
class Relationship:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str = ""
    target_id: str = ""
    relation_type: str = "RELATED_TO"
    description: str = ""
    weight: float = 1.0


_ENTITY_TYPES = {
    "PERSON", "ORGANIZATION", "LOCATION", "DATE", "EVENT",
    "CONCEPT", "PRODUCT", "LAW", "DISEASE", "DRUG", "OTHER",
}

_EXTRACT_PROMPT = """Extract ALL named entities and their relationships from the text below.
Be thorough — capture every meaningful entity regardless of domain.

TEXT:
{text}

Return ONLY valid JSON (no markdown fences, no explanation):
{{
  "entities": [
    {{"name": "...", "type": "PERSON|ORGANIZATION|LOCATION|DATE|EVENT|CONCEPT|PRODUCT|LAW|DISEASE|DRUG|OTHER", "description": "one sentence"}}
  ],
  "relationships": [
    {{"source": "entity name", "target": "entity name", "relation": "VERB_PHRASE", "description": "one sentence"}}
  ]
}}"""


class GraphRAGIndexer:
    def __init__(self, api_key: str = "", chunk_size: int = 500, overlap: int = 80):
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.graph: nx.Graph = nx.Graph()
        self.entities: Dict[str, Entity] = {}
        self.relationships: List[Relationship] = []
        self.community_summaries: Dict[int, str] = {}
        self._name_to_id: Dict[str, str] = {}

    # ── chunking ──────────────────────────────────────────────────────────────

    def chunk_text(self, text: str, doc_id: str) -> List[Dict]:
        words = text.split()
        chunks, i, idx = [], 0, 0
        while i < len(words):
            chunk_words = words[i : i + self.chunk_size]
            chunks.append({
                "id": f"{doc_id}_c{idx}",
                "text": " ".join(chunk_words),
                "doc_id": doc_id,
            })
            i += self.chunk_size - self.overlap
            idx += 1
        logger.debug(f"chunk_text: doc_id={doc_id!r}, words={len(words)}, chunks={len(chunks)}")
        return chunks

    # ── LLM extraction ────────────────────────────────────────────────────────

    async def _extract(self, chunk: Dict) -> Tuple[List[Dict], List[Dict]]:
        prompt = _EXTRACT_PROMPT.format(text=chunk["text"])
        try:
            raw = await generate(prompt, json_mode=True, temperature=0.1)
            data = json.loads(raw)
            entities = data.get("entities", [])
            rels = data.get("relationships", [])
            logger.debug(
                f"_extract chunk={chunk['id']!r}: {len(entities)} entities, {len(rels)} relationships"
            )
            return entities, rels
        except json.JSONDecodeError as exc:
            logger.warning(f"JSON decode failed for chunk {chunk['id']!r}: {exc}")
            return [], []
        except Exception as exc:
            logger.error(f"Entity extraction failed for chunk {chunk['id']!r}: {exc}", exc_info=True)
            return [], []

    # ── entity deduplication ──────────────────────────────────────────────────

    def _normalise(self, name: str) -> str:
        return re.sub(r"\s+", " ", name.strip()).title()

    def _upsert_entity(self, name: str, etype: str, desc: str, chunk_id: str) -> str:
        norm = self._normalise(name)
        key = norm.lower()
        if key in self._name_to_id:
            eid = self._name_to_id[key]
            e = self.entities[eid]
            if chunk_id not in e.source_chunks:
                e.source_chunks.append(chunk_id)
            return eid
        e = Entity(
            name=norm,
            type=etype.upper() if etype.upper() in _ENTITY_TYPES else "OTHER",
            description=desc,
            source_chunks=[chunk_id],
        )
        self.entities[e.id] = e
        self._name_to_id[key] = e.id
        return e.id

    # ── indexing ──────────────────────────────────────────────────────────────

    async def index_document(self, doc_id: str, text: str, chunks: Optional[List[Dict]] = None) -> Dict:
        if chunks is None:
            chunks = self.chunk_text(text, doc_id)
        logger.info(f"index_document: doc_id={doc_id!r}, chunks={len(chunks)}")
        results = await asyncio.gather(
            *[self._extract(c) for c in chunks], return_exceptions=True
        )

        new_entities = 0
        new_rels = 0
        for chunk, result in zip(chunks, results):
            if isinstance(result, Exception):
                logger.error(
                    f"Extraction task raised exception for chunk {chunk['id']!r}: {result}",
                    exc_info=result,
                )
                continue
            entities_raw, rels_raw = result
            local_map: Dict[str, str] = {}

            for e in entities_raw:
                eid = self._upsert_entity(
                    e.get("name", ""),
                    e.get("type", "OTHER"),
                    e.get("description", ""),
                    chunk["id"],
                )
                local_map[e.get("name", "").lower()] = eid
                if not self.graph.has_node(eid):
                    ent = self.entities[eid]
                    self.graph.add_node(eid, name=ent.name, type=ent.type)
                    new_entities += 1

            for r in rels_raw:
                src_key = r.get("source", "").lower()
                tgt_key = r.get("target", "").lower()
                src_id = local_map.get(src_key)
                tgt_id = local_map.get(tgt_key)
                if src_id and tgt_id and src_id != tgt_id:
                    rel = Relationship(
                        source_id=src_id,
                        target_id=tgt_id,
                        relation_type=r.get("relation", "RELATED_TO"),
                        description=r.get("description", ""),
                    )
                    self.relationships.append(rel)
                    if self.graph.has_edge(src_id, tgt_id):
                        self.graph[src_id][tgt_id]["weight"] += 1
                    else:
                        self.graph.add_edge(
                            src_id, tgt_id,
                            weight=1,
                            relation=r.get("relation", "RELATED_TO"),
                        )
                        new_rels += 1

        logger.info(
            f"index_document complete: doc_id={doc_id!r}, new_entities={new_entities}, "
            f"new_relationships={new_rels}, graph_nodes={len(self.graph.nodes)}"
        )
        await self._detect_communities()
        return {
            "doc_id": doc_id,
            "chunks": len(chunks),
            "entities_total": len(self.entities),
            "relationships_total": len(self.relationships),
        }

    # ── community detection ───────────────────────────────────────────────────

    async def _detect_communities(self):
        if len(self.graph.nodes) < 3:
            logger.debug("_detect_communities: skipping, fewer than 3 nodes in graph")
            return
        logger.info(f"_detect_communities: running Louvain on {len(self.graph.nodes)} nodes")
        try:
            communities = await asyncio.to_thread(
                nx.community.louvain_communities, self.graph, seed=42
            )
            for n in self.graph.nodes:
                self.graph.nodes[n]["community"] = -1
            tasks = []
            for cid, comm in enumerate(communities):
                for n in comm:
                    self.graph.nodes[n]["community"] = cid
                if len(comm) >= 3:
                    names = [self.entities[n].name for n in comm if n in self.entities]
                    tasks.append((cid, names))
            logger.info(f"_detect_communities: {len(communities)} communities found, {len(tasks)} to summarise")
            summaries = await asyncio.gather(
                *[self._summarise_community(cid, names) for cid, names in tasks],
                return_exceptions=True,
            )
            for (cid, _), summary in zip(tasks, summaries):
                if isinstance(summary, Exception):
                    logger.warning(f"Community summary failed for cid={cid}: {summary}")
                else:
                    self.community_summaries[cid] = summary
        except Exception as exc:
            logger.error(f"_detect_communities failed: {exc}", exc_info=True)

    async def _summarise_community(self, cid: int, names: List[str]) -> str:
        excerpt = ", ".join(names[:20])
        prompt = (
            f"In 2-3 sentences, describe the thematic cluster formed by these entities "
            f"and how they relate to each other: {excerpt}"
        )
        try:
            return await generate(prompt, temperature=0.3)
        except Exception as exc:
            logger.warning(f"_summarise_community failed for cid={cid}: {exc}", exc_info=True)
            return f"Cluster of {len(names)} entities including: {excerpt}"

    # ── serialisation helpers ─────────────────────────────────────────────────

    def get_graph_data(self) -> Dict:
        nodes = []
        for nid, data in self.graph.nodes(data=True):
            ent = self.entities.get(nid)
            if not ent:
                continue
            nodes.append({
                "id": nid,
                "label": ent.name,
                "type": ent.type.lower(),
                "description": ent.description,
                "community": data.get("community", -1),
                "degree": self.graph.degree(nid),
                "doc_count": len(ent.source_chunks),
            })
        edges = [
            {
                "source": s,
                "target": t,
                "relation": d.get("relation", "RELATED_TO"),
                "weight": d.get("weight", 1),
            }
            for s, t, d in self.graph.edges(data=True)
        ]
        return {
            "nodes": nodes,
            "edges": edges,
            "communities": {str(k): v for k, v in self.community_summaries.items()},
        }

    def get_stats(self) -> Dict:
        return {
            "total_entities": len(self.entities),
            "total_relationships": len(self.relationships),
            "total_communities": len(self.community_summaries),
            "density": round(nx.density(self.graph), 4) if len(self.graph.nodes) > 1 else 0,
            "components": nx.number_connected_components(self.graph),
        }

    # ── graph-context lookup for query engine ─────────────────────────────────

    def get_context_for_query(self, query: str, max_entities: int = 15) -> str:
        words = {w for w in query.lower().split() if len(w) > 3}
        matched: List[Tuple[int, str]] = []
        for eid, ent in self.entities.items():
            score = sum(1 for w in words if w in ent.name.lower() or w in ent.description.lower())
            if score:
                matched.append((score, eid))
        matched.sort(reverse=True)
        logger.debug(f"get_context_for_query: query={query[:60]!r}, matched_entities={len(matched)}")
        lines = []
        seen_comms = set()
        for _, eid in matched[:max_entities]:
            ent = self.entities[eid]
            nbrs = [
                self.entities[n].name
                for n in self.graph.neighbors(eid)
                if n in self.entities
            ][:5]
            lines.append(
                f"- {ent.name} [{ent.type}]: {ent.description}"
                + (f" | connected to: {', '.join(nbrs)}" if nbrs else "")
            )
            comm = self.graph.nodes[eid].get("community", -1)
            if comm >= 0 and comm not in seen_comms and comm in self.community_summaries:
                lines.append(f"  (community context) {self.community_summaries[comm]}")
                seen_comms.add(comm)
        return "\n".join(lines) if lines else "No matching entities found in the knowledge graph."
