"""
GraphRAG Indexer: extracts entities + relationships from passages,
builds a NetworkX knowledge graph, detects communities with Louvain,
and generates community summaries.

Stage 2 of the cascade (RAPTOR → GraphRAG → HippoRAG). Entities are extracted from the
RAPTOR leaves (the original chunks) only: HippoRAG ranks chunks by their entities, and a
summary can state things its chunks don't (an LLM paraphrase, sometimes a wrong one), which
would put unsupported edges in the graph. Set extract_summaries=True to also extract from
summaries. Entity.source_chunks holds the ids of the passages an entity came from.

Entities are keyed by (name, type), so "Ashford" the person and "Ashford" the county stay
two nodes. Likely aliases of one entity ("Quantum" / "Quantum Financial Services, Inc.")
are linked by a SAME_AS edge instead of being merged, but only when the alias is
unambiguous: "Meridian" next to both "Meridian Chemical" and "Meridian Healthcare" gets no
edge, since linking it to both would let PageRank leak between two different companies.

Communities: index_document(update_communities=False) skips Louvain and the community
summaries, so a batch of documents can be indexed first and update_communities() called once.

LLM provider: OpenAI-compatible API (LLM_API_KEY / LLM_BASE_URL / LLM_MODEL in .env).
"""
import asyncio
import difflib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

import networkx as nx

from backend.core.logger import logger
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

_MIN_NAME_OVERLAP = 0.5   # share of an entity's name words the question must contain
_STOPWORDS = {
    "the", "and", "for", "with", "what", "who", "whom", "how", "are", "was", "were", "does",
    "did", "this", "that", "these", "those", "from", "into", "about", "which", "when", "where",
    "why", "between", "there", "their", "have", "has", "been", "any", "all", "its", "his", "her",
}


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOPWORDS}


# ── aliases ───────────────────────────────────────────────────────────────────
# Relationship endpoints and repeated entities are often written differently from the
# entity name ("Quantum" vs "Quantum Financial Services, Inc."). These rules decide when
# two names probably refer to the same thing.

_NAME_NOISE = {"the", "inc", "ltd", "llc", "co", "corp", "plc", "dr", "mr", "mrs", "ms", "prof"}
_FUZZY_CUTOFF = 0.85          # difflib ratio for spelling variants of an endpoint name
_ALIAS_EXCLUDED_TYPES = {"DATE"}   # "March" vs "March 2021" are not aliases
_UNTYPED = "OTHER"            # endpoints the LLM didn't list get this type; they may alias any type
_MIN_ALIAS_CHARS = 4          # "No" is not an alias of "No Significant Tumor Lysis"
# The LLM mixes typographic and ASCII punctuation ("Bcr‑Abl1" / "Bcr-Abl1"); fold it so
# the same name gives the same entity.
_PUNCT_FOLD = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
                             "‘": "'", "’": "'", "“": '"', "”": '"'})
_ALIAS_RELATION = "SAME_AS"


def _name_tokens(name: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in _NAME_NOISE]


def _is_alias(a: List[str], b: List[str], allow_suffix: bool = False) -> bool:
    """The shorter token list starts (or, for people, ends) the longer one."""
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len("".join(short)) < _MIN_ALIAS_CHARS:
        return False
    if long_[: len(short)] == short:
        return True
    return allow_suffix and long_[-len(short):] == short


def _clean_extraction(data: Dict, chunk_id: str) -> Tuple[List[Dict], List[Dict]]:
    """The LLM sometimes breaks the requested shape: a bare string instead of an entity
    object, or non-list fields. Keep what is usable, drop the rest (decision D6)."""
    if not isinstance(data, dict):
        logger.warning(f"Extraction for {chunk_id!r} is not a JSON object; ignored")
        return [], []
    raw_entities = data.get("entities", [])
    raw_rels = data.get("relationships", [])
    raw_entities = raw_entities if isinstance(raw_entities, list) else []
    raw_rels = raw_rels if isinstance(raw_rels, list) else []

    entities = []
    for e in raw_entities:
        if isinstance(e, str):
            e = {"name": e}
        if isinstance(e, dict) and isinstance(e.get("name"), str) and e["name"].strip():
            entities.append(e)
    rels = [r for r in raw_rels if isinstance(r, dict)]

    reshaped = sum(1 for e in raw_entities if isinstance(e, str) and e.strip())
    dropped = len(raw_entities) - len(entities) + len(raw_rels) - len(rels)
    if reshaped or dropped:
        logger.warning(
            f"Extraction for {chunk_id!r} was malformed: {reshaped} bare-string entities "
            f"converted, {dropped} items dropped"
        )
    return entities, rels


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

_COMMUNITY_PROMPT = """Below are entities from one cluster of a knowledge graph, with their descriptions
and the relationships between them. In 2-4 sentences, describe what this cluster is about and
how its main entities are connected. Use only the information given.

ENTITIES:
{entities}

RELATIONSHIPS:
{relationships}"""
_COMMUNITY_MAX_ENTITIES = 20
_COMMUNITY_MAX_RELATIONS = 25
_EXTRACT_MAX_TOKENS = 4096    # hidden reasoning counts too; caps a runaway extraction


def entity_key(name: str, etype: str) -> str:
    """Identity of an entity in a case: its normalised name and its type.
    Also the `entities.normalized_key` column (services/index_store.py)."""
    return f"{name.lower()}|{etype}"


class GraphRAGIndexer:
    # Chunks of ~250 words: the GraphRAG paper finds smaller chunks give better extraction
    # recall, and they also keep each embedding focused on one topic.
    def __init__(
        self, api_key: str = "", chunk_size: int = 250, overlap: int = 40,
        extract_summaries: bool = False,
    ):
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.extract_summaries = extract_summaries
        self.graph: nx.Graph = nx.Graph()
        self.entities: Dict[str, Entity] = {}
        self.relationships: List[Relationship] = []
        self.community_summaries: Dict[int, str] = {}
        self._name_to_id: Dict[str, str] = {}           # entity_key → id
        self._ids_by_name: Dict[str, List[str]] = {}    # lower-case name → ids (all types)
        self._reset_alias_index()

    def _reset_alias_index(self) -> None:
        self._alias_tokens: Dict[str, List[str]] = {}       # entity id → name tokens
        self._by_first_token: Dict[str, set] = {}           # first name token → entity ids
        self._by_last_token: Dict[str, set] = {}            # last name token → entity ids

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
            raw = await generate(prompt, json_mode=True, temperature=0.1, max_tokens=_EXTRACT_MAX_TOKENS)
            data = json.loads(raw)
            entities, rels = _clean_extraction(data, chunk["id"])
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
        return re.sub(r"\s+", " ", name.translate(_PUNCT_FOLD).strip()).title()

    def _upsert_entity(self, name: str, etype: str, desc: str, chunk_id: str) -> str:
        norm = self._normalise(name)
        etype = str(etype).upper()
        etype = etype if etype in _ENTITY_TYPES else "OTHER"
        key = entity_key(norm, etype)
        if key in self._name_to_id:
            eid = self._name_to_id[key]
            e = self.entities[eid]
            if chunk_id not in e.source_chunks:
                e.source_chunks.append(chunk_id)
            if not e.description and desc:
                e.description = desc
            return eid
        e = Entity(name=norm, type=etype, description=desc, source_chunks=[chunk_id])
        self.entities[e.id] = e
        self._name_to_id[key] = e.id
        self._ids_by_name.setdefault(norm.lower(), []).append(e.id)
        self._index_alias_tokens(e.id)
        return e.id

    def _resolve_endpoint(self, name: str, local_map: Dict[str, str]) -> Tuple[Optional[str], str]:
        """The entity a relationship endpoint refers to, and how it was found:
        "exact" / "fuzzy" (this passage's entities), "case" (a same-named entity elsewhere in
        the case), or None with "missing" / "ambiguous"."""
        key = self._normalise(name).lower()
        if key in local_map:
            return local_map[key], "exact"
        tokens = _name_tokens(key)
        aliases = {eid for k, eid in local_map.items() if _is_alias(tokens, _name_tokens(k))}
        if len(aliases) == 1:
            return aliases.pop(), "fuzzy"
        close = difflib.get_close_matches(key, list(local_map), n=1, cutoff=_FUZZY_CUTOFF)
        if close:
            return local_map[close[0]], "fuzzy"
        same_name = self._ids_by_name.get(key, [])
        if len(same_name) == 1:
            return same_name[0], "case"
        return None, "ambiguous" if same_name else "missing"

    def _add_edge(self, src_id: str, tgt_id: str, relation: str, description: str) -> bool:
        """Record a relationship; True if it created a new graph edge."""
        self.relationships.append(Relationship(
            source_id=src_id, target_id=tgt_id, relation_type=relation, description=description,
        ))
        if self.graph.has_edge(src_id, tgt_id):
            self.graph[src_id][tgt_id]["weight"] += 1
            return False
        self.graph.add_edge(src_id, tgt_id, weight=1, relation=relation)
        return True

    def _index_alias_tokens(self, eid: str) -> None:
        """Index an entity by its first and last name token. An alias shares the first token
        (prefix match) or, for people, the last one, so only these entities need comparing."""
        ent = self.entities[eid]
        if ent.type in _ALIAS_EXCLUDED_TYPES:
            return
        tokens = _name_tokens(ent.name)
        if not tokens:
            return
        self._alias_tokens[eid] = tokens
        self._by_first_token.setdefault(tokens[0], set()).add(eid)
        self._by_last_token.setdefault(tokens[-1], set()).add(eid)

    def _alias_matches(self, eid: str) -> List[str]:
        """Entities this entity's name is probably an alias of (or that are aliases of it).
        Types must agree, except that an untyped (OTHER) entity — an endpoint the LLM didn't
        list — may alias any type."""
        tokens = self._alias_tokens.get(eid)
        if not tokens:
            return []
        ent = self.entities[eid]
        is_person = ent.type == "PERSON"
        candidates = set(self._by_first_token.get(tokens[0], ()))
        if is_person:
            candidates |= self._by_last_token.get(tokens[-1], set())
        matches = []
        for other in candidates:
            if other == eid:
                continue
            other_type = self.entities[other].type
            if ent.type != other_type and _UNTYPED not in (ent.type, other_type):
                continue
            if _is_alias(tokens, self._alias_tokens[other], allow_suffix=is_person):
                matches.append(other)
        return matches

    def _link_aliases(self, new_ids: List[str]) -> int:
        """SAME_AS edges between a new entity and its one likely alias (HippoRAG's synonym
        edges, matched on name tokens instead of embeddings). Edges, not merges, so a wrong
        guess costs a little PageRank leakage rather than a corrupted entity.

        Ambiguity is checked from both sides: no edge when the new name fits more than one
        entity, or when the matched entity's own name fits more than one entity (e.g. an
        existing "Meridian" when "Meridian Chemical" and "Meridian Healthcare" both exist)."""
        added, ambiguous = 0, 0
        for eid in new_ids:
            matches = self._alias_matches(eid)
            if not matches:
                continue
            if len(matches) > 1:
                ambiguous += 1
                continue
            other = matches[0]
            # The other side: is `other` a short name that fits several entities?
            if len(self._alias_matches(other)) > 1:
                ambiguous += 1
                continue
            if not self.graph.has_edge(eid, other):
                self._add_edge(eid, other, _ALIAS_RELATION, "Probably the same entity (name match)")
                added += 1
        if ambiguous:
            logger.debug(f"_link_aliases: {ambiguous} ambiguous aliases left unlinked")
        return added

    # ── indexing ──────────────────────────────────────────────────────────────

    async def index_document(
        self, doc_id: str, text: str, chunks: Optional[List[Dict]] = None,
        update_communities: bool = True,
    ) -> Dict:
        """Extract entities and relationships from a document's passages into the graph.
        Passages above level 0 (RAPTOR summaries) are skipped unless extract_summaries is on;
        a passage without a "level" counts as a chunk. update_communities=False defers
        Louvain and the community summaries to one update_communities() call after a batch."""
        if chunks is None:
            chunks = self.chunk_text(text, doc_id)
        if not self.extract_summaries:
            skipped = sum(1 for c in chunks if c.get("level", 0) > 0)
            chunks = [c for c in chunks if c.get("level", 0) == 0]
            if skipped:
                logger.debug(f"index_document: doc_id={doc_id!r}, {skipped} summaries not extracted")
        logger.info(f"index_document: doc_id={doc_id!r}, passages={len(chunks)}")
        results = await asyncio.gather(
            *[self._extract(c) for c in chunks], return_exceptions=True
        )

        new_ids: List[str] = []
        new_rels = 0
        # How relationship endpoints were resolved; anything but a match used to vanish silently.
        endpoint_counts = {"exact": 0, "fuzzy": 0, "case": 0, "created": 0}
        dropped = {"self_loop": 0, "ambiguous": 0, "empty": 0}
        for chunk, result in zip(chunks, results):
            if isinstance(result, Exception):
                logger.error(
                    f"Extraction task raised exception for chunk {chunk['id']!r}: {result}",
                    exc_info=result,
                )
                continue
            entities_raw, rels_raw = result
            local_map: Dict[str, str] = {}   # normalised lower-case name → entity id

            for e in entities_raw:
                eid = self._upsert_entity(
                    e.get("name", ""),
                    e.get("type", "OTHER"),
                    e.get("description", ""),
                    chunk["id"],
                )
                local_map.setdefault(self.entities[eid].name.lower(), eid)
                if not self.graph.has_node(eid):
                    ent = self.entities[eid]
                    self.graph.add_node(eid, name=ent.name, type=ent.type)
                    new_ids.append(eid)

            for r in rels_raw:
                ends = [r.get("source"), r.get("target")]
                if not all(isinstance(end, str) and end.strip() for end in ends):
                    dropped["empty"] += 1
                    continue
                ids, hows = [], []
                for end in ends:
                    eid, how = self._resolve_endpoint(end, local_map)
                    if how == "missing":
                        # The LLM named something it didn't list as an entity: keep it.
                        eid, how = self._upsert_entity(end, "OTHER", "", chunk["id"]), "created"
                        local_map.setdefault(self.entities[eid].name.lower(), eid)
                        if not self.graph.has_node(eid):
                            self.graph.add_node(eid, name=self.entities[eid].name, type="OTHER")
                            new_ids.append(eid)
                    ids.append(eid)
                    hows.append(how)
                src_id, tgt_id = ids
                if src_id is None or tgt_id is None:
                    dropped["ambiguous"] += 1
                    continue
                for how in hows:
                    endpoint_counts[how] += 1
                if src_id == tgt_id:
                    dropped["self_loop"] += 1
                    continue
                new_rels += self._add_edge(
                    src_id, tgt_id,
                    str(r.get("relation") or "RELATED_TO"),
                    str(r.get("description") or ""),
                )

        aliases = self._link_aliases(new_ids)
        logger.info(
            f"index_document complete: doc_id={doc_id!r}, new_entities={len(new_ids)}, "
            f"new_relationships={new_rels}, alias_edges={aliases}, graph_nodes={len(self.graph.nodes)}"
        )
        logger.info(
            f"index_document endpoints: doc_id={doc_id!r}, resolved={endpoint_counts}, "
            f"relationships_dropped={dropped}"
        )
        if update_communities:
            await self._detect_communities()
        return {
            "doc_id": doc_id,
            "chunks": len(chunks),
            "entities_total": len(self.entities),
            "relationships_total": len(self.relationships),
        }

    # ── community detection ───────────────────────────────────────────────────

    async def update_communities(self) -> None:
        """Louvain + community summaries over the whole graph. Call once after a batch of
        index_document(update_communities=False) calls."""
        await self._detect_communities()

    async def _detect_communities(self):
        if len(self.graph.nodes) < 3:
            logger.debug("_detect_communities: skipping, fewer than 3 nodes in graph")
            return
        logger.info(f"_detect_communities: running Louvain on {len(self.graph.nodes)} nodes")
        try:
            communities = await asyncio.to_thread(
                nx.community.louvain_communities, self.graph, seed=42
            )
            # Louvain renumbers communities on every run, so summaries are keyed by
            # membership: a community whose members haven't changed keeps its summary
            # (no LLM call), and summaries of communities that no longer exist are dropped.
            previous = self._summaries_by_members()
            for n in self.graph.nodes:
                self.graph.nodes[n]["community"] = -1
            new_summaries: Dict[int, str] = {}
            tasks: List[Tuple[int, List[str]]] = []
            for cid, comm in enumerate(communities):
                for n in comm:
                    self.graph.nodes[n]["community"] = cid
                if len(comm) < 3:
                    continue
                reused = previous.get(frozenset(comm))
                if reused is not None:
                    new_summaries[cid] = reused
                else:
                    tasks.append((cid, [n for n in comm if n in self.entities]))
            logger.info(
                f"_detect_communities: {len(communities)} communities found, "
                f"{len(new_summaries)} summaries reused, {len(tasks)} to summarise"
            )
            rel_desc: Dict[frozenset, str] = {}
            if tasks:
                for rel in self.relationships:
                    rel_desc.setdefault(frozenset((rel.source_id, rel.target_id)), rel.description)
            summaries = await asyncio.gather(
                *[self._summarise_community(cid, members, rel_desc) for cid, members in tasks],
                return_exceptions=True,
            )
            for (cid, _), summary in zip(tasks, summaries):
                if isinstance(summary, Exception):
                    logger.warning(f"Community summary failed for cid={cid}: {summary}")
                else:
                    new_summaries[cid] = summary
            self.community_summaries = new_summaries
        except Exception as exc:
            logger.error(f"_detect_communities failed: {exc}", exc_info=True)

    def _summaries_by_members(self) -> Dict[frozenset, str]:
        """Current summaries keyed by the set of entity ids in their community."""
        members: Dict[int, set] = {}
        for n, data in self.graph.nodes(data=True):
            cid = data.get("community", -1)
            if cid in self.community_summaries:
                members.setdefault(cid, set()).add(n)
        return {frozenset(ids): self.community_summaries[cid] for cid, ids in members.items()}

    async def _summarise_community(
        self, cid: int, members: List[str], rel_desc: Dict[frozenset, str]
    ) -> str:
        # Built from what the graph knows (descriptions and relations), not names alone,
        # so the summary describes the cluster instead of guessing a theme.
        top = sorted(members, key=self.graph.degree, reverse=True)[:_COMMUNITY_MAX_ENTITIES]
        entity_lines = "\n".join(
            f"- {self.entities[e].name} [{self.entities[e].type}]: {self.entities[e].description}"
            for e in top
        )
        rel_lines = []
        for s, t, d in self.graph.subgraph(top).edges(data=True):
            desc = rel_desc.get(frozenset((s, t)), "")
            rel_lines.append(
                f"- {self.entities[s].name} —{d.get('relation', 'RELATED_TO')}→ {self.entities[t].name}"
                + (f": {desc}" if desc else "")
            )
        prompt = _COMMUNITY_PROMPT.format(
            entities=entity_lines,
            relationships="\n".join(rel_lines[:_COMMUNITY_MAX_RELATIONS]) or "(none)",
        )
        try:
            return await generate(prompt, temperature=0.3)
        except Exception as exc:
            logger.warning(f"_summarise_community failed for cid={cid}: {exc}", exc_info=True)
            names = ", ".join(self.entities[e].name for e in top)
            return f"Cluster of {len(members)} entities including: {names}"

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

    # ── lookups for the query engine ──────────────────────────────────────────

    def match_entities(self, question: str) -> Dict[str, float]:
        """Entities named in the question (keyword match, decision D8): entity id → fraction
        of the entity's name words that appear in the question. These seed HippoRAG's PageRank."""
        q_words = _words(question)
        matches: Dict[str, float] = {}
        for eid, ent in self.entities.items():
            name_words = _words(ent.name)
            if not name_words:
                continue
            overlap = len(name_words & q_words) / len(name_words)
            if overlap >= _MIN_NAME_OVERLAP:
                matches[eid] = overlap
        logger.debug(f"match_entities: question={question[:60]!r}, matched={len(matches)}")
        return matches

    def community_members(self) -> Dict[int, List[str]]:
        """Community id → entity ids, for the communities that have a summary."""
        members: Dict[int, List[str]] = {}
        for n, data in self.graph.nodes(data=True):
            cid = data.get("community", -1)
            if cid in self.community_summaries:
                members.setdefault(cid, []).append(n)
        return members

    def describe_entities(self, entity_ids: List[str], max_relations: int = 4) -> str:
        """Short graph facts for the answer prompt: each entity, its type, description,
        and a few of its relations."""
        lines = []
        for eid in entity_ids:
            ent = self.entities.get(eid)
            if ent is None:
                continue
            rels = [
                f"{self.graph[eid][n].get('relation', 'RELATED_TO')} {self.entities[n].name}"
                for n in self.graph.neighbors(eid)
                if n in self.entities
            ][:max_relations]
            lines.append(
                f"- {ent.name} [{ent.type}]: {ent.description}"
                + (f" | {'; '.join(rels)}" if rels else "")
            )
        return "\n".join(lines)

    # ── state (persistence) ───────────────────────────────────────────────────
    # Plain dicts only; the pipeline never knows about the database (decision D12).

    def export_state(self) -> Dict:
        return {
            "entities": [asdict(e) for e in self.entities.values()],
            "relationships": [asdict(r) for r in self.relationships],
            "communities": dict(self.community_summaries),
            "node_community": {n: d.get("community", -1) for n, d in self.graph.nodes(data=True)},
        }

    def load_state(self, state: Dict) -> None:
        self.graph = nx.Graph()
        self.entities = {}
        self.relationships = []
        self._name_to_id = {}
        self._ids_by_name = {}
        self._reset_alias_index()
        node_community = state.get("node_community", {})
        for e in state.get("entities", []):
            ent = Entity(**e)
            self.entities[ent.id] = ent
            # ent.name is already _normalise()d, so this is the same key _upsert_entity uses.
            self._name_to_id[entity_key(ent.name, ent.type)] = ent.id
            self._ids_by_name.setdefault(ent.name.lower(), []).append(ent.id)
            self._index_alias_tokens(ent.id)
            self.graph.add_node(
                ent.id, name=ent.name, type=ent.type, community=node_community.get(ent.id, -1)
            )
        # Replaying in the original order gives the same weights and "first relation"
        # per edge as index_document produced.
        for r in state.get("relationships", []):
            rel = Relationship(**r)
            self.relationships.append(rel)
            if self.graph.has_edge(rel.source_id, rel.target_id):
                self.graph[rel.source_id][rel.target_id]["weight"] += 1
            else:
                self.graph.add_edge(rel.source_id, rel.target_id, weight=1, relation=rel.relation_type)
        self.community_summaries = {int(k): v for k, v in state.get("communities", {}).items()}
        logger.debug(
            f"GraphRAGIndexer.load_state: entities={len(self.entities)}, "
            f"relationships={len(self.relationships)}, communities={len(self.community_summaries)}"
        )