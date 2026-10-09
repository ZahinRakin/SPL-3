"""
HippoRAG ranker: Personalized PageRank over the GraphRAG knowledge graph.

Stage 3 of the cascade (RAPTOR → GraphRAG → HippoRAG), after Gutiérrez et al.,
"HippoRAG: Neurobiologically Inspired Long-Term Memory for LLMs" (NeurIPS 2024).

1. Seed the graph: entities named in the question (strong), plus the entities of the few
   chunks most similar to the question (weak, so questions that name nothing still work).
   Each seed is divided by how many chunks mention it (the paper's node specificity).
2. Personalized PageRank spreads relevance from the seeds along the graph's edges.
3. Two rankings are fused with weighted Reciprocal Rank Fusion (Cormack et al., 2009):
   - the similarity ranking of every passage (chunks and RAPTOR summaries), and
   - the graph ranking of chunks by their entities' PageRank mass.
   score(p) = 1 / (K + rank_sim(p)) + w · 1 / (K + rank_graph(p))
   Ranks, not raw scores, are fused: cosine scores of the top candidates differ by a few
   hundredths while a normalised PageRank score spans 0-1, so adding raw scores let the
   graph decide the order on its own. Only chunks get a graph rank; summaries compete on
   similarity alone and take at most `max_summaries` of the slots.

Ablation switches (constructor):
   ppr_weight=0, max_summaries=0  → exactly the Standard (similarity-only) chunk ranking
   ppr_weight>0, max_summaries=0  → similarity + PageRank over chunks (HippoRAG-style)
   ppr_weight>0, max_summaries>0  → the full cascade

It stores nothing of its own: it reads the graph and the RAPTOR nodes at query time.
"""
import asyncio
from typing import Dict, List, Optional, Tuple

import networkx as nx

from backend.core.logger import logger
from .graphrag_indexer import GraphRAGIndexer
from .raptor_runner import RaptorRunner

_DAMPING = 0.5              # the paper's value: chance of following an edge vs jumping back to a seed
_QUESTION_SEED_WEIGHT = 1.0
_PASSAGE_SEED_WEIGHT = 0.3  # total seed mass one similar chunk spreads over its entities
_SEED_PASSAGES = 3
_RRF_K = 60                 # the usual RRF constant; larger flattens the head of each ranking
_PPR_WEIGHT = 1.0           # w: weight of the graph ranking next to the similarity ranking
_MAX_SUMMARIES = 2          # summary passages allowed in the returned list
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
        ppr_weight: float = _PPR_WEIGHT,
        max_summaries: int = _MAX_SUMMARIES,
    ):
        self.graphrag = graphrag
        self.raptor = raptor
        self.damping = damping
        self.seed_passages = seed_passages
        self.ppr_weight = ppr_weight
        self.max_summaries = max_summaries
        self._links: Dict[str, List[str]] = {}
        self._links_key: Optional[Tuple] = None

    # ── chunk ↔ entity links ──────────────────────────────────────────────────

    def _chunk_entities(self) -> Dict[str, List[str]]:
        """Chunk id → ids of the entities extracted from it. Only RAPTOR leaves (level 0).
        Cached until the index changes: load_state replaces the dicts (new ids) and indexing
        adds entities and nodes (new sizes)."""
        key = (
            id(self.graphrag.entities), len(self.graphrag.entities),
            id(self.raptor.nodes), len(self.raptor.nodes), len(self.graphrag.relationships),
        )
        if key == self._links_key:
            return self._links
        links: Dict[str, List[str]] = {}
        for eid, ent in self.graphrag.entities.items():
            for pid in ent.source_chunks:
                node = self.raptor.nodes.get(pid)
                if node is not None and node.level == 0:
                    links.setdefault(pid, []).append(eid)
        self._links, self._links_key = links, key
        return links

    # ── seeds ─────────────────────────────────────────────────────────────────

    def _seeds(
        self, question: str, similar_chunks: List[Dict], chunk_entities: Dict[str, List[str]]
    ) -> Dict[str, float]:
        seeds: Dict[str, float] = {}
        for eid, overlap in self.graphrag.match_entities(question).items():
            seeds[eid] = seeds.get(eid, 0.0) + _QUESTION_SEED_WEIGHT * overlap
        for hit in similar_chunks:
            ents = chunk_entities.get(hit["id"], [])
            for eid in ents:
                seeds[eid] = seeds.get(eid, 0.0) + _PASSAGE_SEED_WEIGHT * max(hit["score"], 0.0) / len(ents)

        mentions: Dict[str, int] = {}
        for ents in chunk_entities.values():
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

    async def _pagerank(self, seeds: Dict[str, float]) -> Dict[str, float]:
        if not seeds or self.ppr_weight <= 0:
            if self.ppr_weight > 0:
                logger.warning("HippoRetriever: no seed entities, using similarity ranking")
            return {}
        try:
            return await asyncio.to_thread(
                nx.pagerank, self.graphrag.graph, alpha=self.damping,
                personalization=seeds, weight="weight",
            )
        except Exception as exc:
            logger.warning(f"HippoRetriever: PageRank failed, using similarity ranking: {exc}", exc_info=True)
            return {}

    async def retrieve(self, question: str, query_embedding: List[float], top_k: int = 6) -> Dict:
        """Top-k passages by fused similarity + PageRank rank, the highest-ranked entities, and
        each community's PageRank mass ({passages, entities, communities}).
        With no seeds (or ppr_weight=0, or a PageRank failure) the ranking is plain similarity (D6)."""
        # Every node's similarity in one pass; RAPTOR already subtracts its level penalty.
        hits = self.raptor.retrieve(query_embedding, top_k=len(self.raptor.nodes))
        chunk_hits = [h for h in hits if h["level"] == 0]
        summary_hits = [h for h in hits if h["level"] > 0][: max(self.max_summaries, 0)]

        chunk_entities = self._chunk_entities()
        seeds = self._seeds(question, chunk_hits[: self.seed_passages], chunk_entities)
        ppr = await self._pagerank(seeds)

        # Similarity rank among the passages that can be returned (chunks + allowed summaries).
        candidates = sorted(chunk_hits + summary_hits, key=lambda h: h["score"], reverse=True)
        sim_rank = {h["id"]: r for r, h in enumerate(candidates, start=1)}
        graph_score = {
            pid: sum(ppr.get(eid, 0.0) for eid in ents) for pid, ents in chunk_entities.items()
        }
        graph_ranked = sorted(
            ((s, pid) for pid, s in graph_score.items() if s > 0), reverse=True
        )
        graph_rank = {pid: r for r, (_, pid) in enumerate(graph_ranked, start=1)}

        def fused(pid: str) -> float:
            score = 1.0 / (_RRF_K + sim_rank[pid])
            if pid in graph_rank:
                score += self.ppr_weight / (_RRF_K + graph_rank[pid])
            return score

        scored = sorted(((fused(h["id"]), h["id"]) for h in candidates), reverse=True)
        passages = [self._passage(pid, s) for s, pid in scored[:top_k]]

        cutoff = max(ppr.values(), default=0.0) * _MIN_ENTITY_SHARE
        top_entities = [
            eid for eid, score in sorted(ppr.items(), key=lambda kv: kv[1], reverse=True)[:_TOP_ENTITIES]
            if score > cutoff
        ]
        # PageRank mass per summarised community, for ranking the community summaries.
        community_mass: Dict[int, float] = {}
        for eid, score in ppr.items():
            cid = self.graphrag.graph.nodes[eid].get("community", -1)
            if cid in self.graphrag.community_summaries:
                community_mass[cid] = community_mass.get(cid, 0.0) + score
        logger.debug(
            f"HippoRetriever.retrieve: seeds={len(seeds)}, chunks_with_entities={len(chunk_entities)}, "
            f"graph_ranked={len(graph_rank)}, summaries={sum(1 for p in passages if p['level'] > 0)}, "
            f"returned={len(passages)}"
        )
        return {"passages": passages, "entities": top_entities, "communities": community_mass}