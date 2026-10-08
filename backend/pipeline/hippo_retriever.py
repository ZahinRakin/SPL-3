"""
HippoRAG ranker: Personalized PageRank over the GraphRAG knowledge graph.

Stage 3 of the cascade (RAPTOR → GraphRAG → HippoRAG), after Gutiérrez et al.,
"HippoRAG: Neurobiologically Inspired Long-Term Memory for LLMs" (NeurIPS 2024).

1. Seed the graph: entities named in the question (strong), plus the entities of the few
   passages most similar to the question (weak, so questions that name nothing still work).
   Each seed is divided by how many passages mention it (the paper's node specificity).
2. Personalized PageRank spreads relevance from the seeds along the graph's edges.
3. Every passage (a RAPTOR chunk or summary) scores the PageRank mass of its entities,
   and only the top-k passages reach the LLM.

It stores nothing of its own: it reads the graph and the RAPTOR nodes at query time.
"""
import asyncio
from typing import Dict, List, Optional

import networkx as nx

from backend.core.logger import logger
from .graphrag_indexer import GraphRAGIndexer
from .raptor_runner import RaptorRunner

_DAMPING = 0.5              # the paper's value: chance of following an edge vs jumping back to a seed
_QUESTION_SEED_WEIGHT = 1.0
_PASSAGE_SEED_WEIGHT = 0.3  # total seed mass one similar passage spreads over its entities
_SEED_PASSAGES = 3
_TOP_ENTITIES = 8           # highest-PageRank entities described in the answer prompt
_MIN_ENTITY_SHARE = 0.01    # ...if they hold at least this share of the top entity's score


class HippoRetriever:
    def __init__(
        self,
        api_key: str = "",
        graphrag: Optional[GraphRAGIndexer] = None,
        raptor: Optional[RaptorRunner] = None,
        damping: float = _DAMPING,
        seed_passages: int = _SEED_PASSAGES,
    ):
        self.graphrag = graphrag
        self.raptor = raptor
        self.damping = damping
        self.seed_passages = seed_passages

    # ── passage ↔ entity links ────────────────────────────────────────────────

    def _passage_entities(self) -> Dict[str, List[str]]:
        """Passage id → ids of the entities extracted from it. Mentions that are not RAPTOR
        nodes (plain chunk ids, when RAPTOR failed for a document) are skipped."""
        links: Dict[str, List[str]] = {}
        for eid, ent in self.graphrag.entities.items():
            for pid in ent.source_chunks:
                if pid in self.raptor.nodes:
                    links.setdefault(pid, []).append(eid)
        return links

    # ── seeds ─────────────────────────────────────────────────────────────────

    def _seeds(
        self, question: str, query_embedding: List[float], passage_entities: Dict[str, List[str]]
    ) -> Dict[str, float]:
        seeds: Dict[str, float] = {}
        for eid, overlap in self.graphrag.match_entities(question).items():
            seeds[eid] = seeds.get(eid, 0.0) + _QUESTION_SEED_WEIGHT * overlap
        for hit in self.raptor.retrieve(query_embedding, top_k=self.seed_passages):
            ents = passage_entities.get(hit["id"], [])
            for eid in ents:
                seeds[eid] = seeds.get(eid, 0.0) + _PASSAGE_SEED_WEIGHT * max(hit["score"], 0.0) / len(ents)

        mentions: Dict[str, int] = {}
        for ents in passage_entities.values():
            for eid in ents:
                mentions[eid] = mentions.get(eid, 0) + 1
        specific = {
            eid: w / max(mentions.get(eid, 1), 1)
            for eid, w in seeds.items()
            if w > 0 and eid in self.graphrag.graph
        }
        logger.debug(f"HippoRetriever._seeds: {len(specific)} seed entities")
        return specific

    # ── retrieval ─────────────────────────────────────────────────────────────

    def _passage(self, pid: str, score: float) -> Dict:
        node = self.raptor.nodes[pid]
        return {"id": pid, "text": node.text, "level": node.level, "score": round(score, 6),
                "doc_ids": node.doc_ids}

    async def retrieve(self, question: str, query_embedding: List[float], top_k: int = 6) -> Dict:
        """Top-k passages ranked by Personalized PageRank, and the highest-ranked entities.
        Degrades to plain similarity ranking when the graph gives nothing to rank (D6)."""
        passage_entities = self._passage_entities()
        seeds = self._seeds(question, query_embedding, passage_entities)

        ppr: Dict[str, float] = {}
        if seeds:
            try:
                ppr = await asyncio.to_thread(
                    nx.pagerank, self.graphrag.graph, alpha=self.damping,
                    personalization=seeds, weight="weight",
                )
            except Exception as exc:
                logger.warning(f"HippoRetriever: PageRank failed, using similarity ranking: {exc}", exc_info=True)
        else:
            logger.warning("HippoRetriever: no seed entities, using similarity ranking")

        scores = {
            pid: sum(ppr.get(eid, 0.0) for eid in ents)
            for pid, ents in passage_entities.items()
        }
        ranked = sorted((s, pid) for pid, s in scores.items() if s > 0)[::-1]
        passages = [self._passage(pid, s) for s, pid in ranked[:top_k]]

        if len(passages) < top_k:
            taken = {p["id"] for p in passages}
            for hit in self.raptor.retrieve(query_embedding, top_k=top_k):
                if len(passages) >= top_k:
                    break
                if hit["id"] not in taken:
                    passages.append(self._passage(hit["id"], 0.0))
            logger.debug(f"HippoRetriever: filled to {len(passages)} passages with similarity hits")

        cutoff = max(ppr.values(), default=0.0) * _MIN_ENTITY_SHARE
        top_entities = [
            eid for eid, score in sorted(ppr.items(), key=lambda kv: kv[1], reverse=True)[:_TOP_ENTITIES]
            if score > cutoff
        ]
        logger.debug(
            f"HippoRetriever.retrieve: seeds={len(seeds)}, ranked_passages={len(ranked)}, "
            f"returned={len(passages)}"
        )
        return {"passages": passages, "entities": top_entities}
