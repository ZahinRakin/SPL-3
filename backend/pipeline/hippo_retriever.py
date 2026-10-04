"""
HiPPO Retriever: Hierarchical Passage Pooling.
Builds a pyramid of pooled passage embeddings for multi-granularity retrieval —
fine-grained at level 0, coarser at higher levels.

Embedding provider is selected via EMBED_PROVIDER in .env.
"""
import asyncio
import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

import numpy as np

from backend.core.logger import logger
from .vectors import EmbeddingIndex, embed_or_fallback

_LEVEL_PENALTY = 0.02   # per pyramid level, preferring fine-grained passages


@dataclass
class HippoNode:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    text: str = ""
    embedding: Optional[List[float]] = None
    level: int = 0
    parent_id: Optional[str] = None
    child_ids: List[str] = field(default_factory=list)
    doc_id: str = ""
    chunk_idx: int = 0


class HippoRetriever:
    def __init__(self, api_key: str = "", pool_size: int = 2, max_levels: int = 4):
        self.pool_size = pool_size
        self.max_levels = max_levels
        self.nodes: Dict[str, HippoNode] = {}
        self.levels: Dict[int, List[str]] = {}
        self._index: Optional[EmbeddingIndex] = None

    # ── embedding ─────────────────────────────────────────────────────────────

    async def _embed(self, text: str) -> List[float]:
        return await embed_or_fallback(text, owner="HippoRetriever")

    def _embedding_index(self) -> EmbeddingIndex:
        # Built lazily, dropped whenever nodes change (build/index/load).
        if self._index is None:
            self._index = EmbeddingIndex.build({nid: n.embedding for nid, n in self.nodes.items()})
        return self._index

    # ── pooling ───────────────────────────────────────────────────────────────

    @staticmethod
    def _mean_pool(embeddings: List[List[float]]) -> List[float]:
        arr = np.array(embeddings, dtype=float)
        pooled = arr.mean(axis=0)
        norm = np.linalg.norm(pooled)
        return (pooled / norm).tolist() if norm > 0 else pooled.tolist()

    # ── indexing ──────────────────────────────────────────────────────────────

    async def index_passages(self, passages: List[Dict]) -> Dict:
        if not passages:
            logger.warning("HippoRetriever.index_passages called with empty passages list")
            return {}

        logger.info(f"HippoRetriever.index_passages: {len(passages)} passages, max_levels={self.max_levels}")
        self._index = None
        embs = await asyncio.gather(
            *[self._embed(p["text"]) for p in passages], return_exceptions=True
        )
        level0: List[str] = []
        for i, (p, emb) in enumerate(zip(passages, embs)):
            if isinstance(emb, Exception):
                logger.warning(f"Embedding failed for passage index={i}, using fallback: {emb}")
                emb = await self._embed("")
            node = HippoNode(
                text=p["text"],
                embedding=emb,
                level=0,
                doc_id=p.get("doc_id", ""),
                chunk_idx=i,
            )
            self.nodes[node.id] = node
            level0.append(node.id)

        self.levels[0] = level0
        logger.debug(f"HippoRetriever: {len(level0)} level-0 nodes created")
        current = level0

        for level in range(1, self.max_levels + 1):
            if len(current) <= 1:
                logger.debug(f"HippoRetriever: stopping at level={level}, only {len(current)} node(s) remain")
                break
            next_level: List[str] = []
            for i in range(0, len(current), self.pool_size):
                group = current[i : i + self.pool_size]
                child_embs = [self.nodes[nid].embedding for nid in group]
                pooled = self._mean_pool(child_embs)
                preview = " | ".join(self.nodes[nid].text[:80] for nid in group)
                parent = HippoNode(
                    text=preview,
                    embedding=pooled,
                    level=level,
                    child_ids=group,
                    doc_id=self.nodes[group[0]].doc_id if group else "",
                )
                self.nodes[parent.id] = parent
                for cid in group:
                    self.nodes[cid].parent_id = parent.id
                next_level.append(parent.id)
            self.levels[level] = next_level
            logger.debug(f"HippoRetriever: level={level} built with {len(next_level)} nodes")
            current = next_level

        self._index = None
        stats = {"total_nodes": len(self.nodes), "levels": {k: len(v) for k, v in self.levels.items()}}
        logger.info(f"HippoRetriever.index_passages complete: {stats}")
        return stats

    # ── retrieval ─────────────────────────────────────────────────────────────

    def retrieve(self, query_embedding: List[float], top_k: int = 5, level: int = 0) -> List[Dict]:
        target = self.levels.get(level, self.levels.get(0, []))
        if not target:
            logger.warning(f"HippoRetriever.retrieve: no nodes at level={level}")
        ids, sims = self._embedding_index().cosine(query_embedding, target)
        scored = [(float(sim), nid) for nid, sim in zip(ids, sims)]
        scored.sort(reverse=True)
        results = [
            {
                "id": nid,
                "text": self.nodes[nid].text,
                "level": self.nodes[nid].level,
                "score": round(s, 4),
                "doc_id": self.nodes[nid].doc_id,
            }
            for s, nid in scored[:top_k]
        ]
        logger.debug(f"HippoRetriever.retrieve: level={level}, top_k={top_k}, returned={len(results)}")
        return results

    def retrieve_multi_level(self, query_embedding: List[float], top_k: int = 5) -> List[Dict]:
        """Retrieve across all levels and deduplicate, preferring fine-grained hits."""
        if not self.nodes:
            logger.warning("HippoRetriever.retrieve_multi_level: no nodes indexed")
            return []
        ids, sims = self._embedding_index().cosine(query_embedding)
        scored = [
            (float(sim) - self.nodes[nid].level * _LEVEL_PENALTY, nid)
            for nid, sim in zip(ids, sims)
        ]
        scored.sort(reverse=True)
        results = [
            {
                "id": nid,
                "text": self.nodes[nid].text,
                "level": self.nodes[nid].level,
                "score": round(s, 4),
                "doc_id": self.nodes[nid].doc_id,
            }
            for s, nid in scored[:top_k]
        ]
        logger.debug(f"HippoRetriever.retrieve_multi_level: top_k={top_k}, returned={len(results)}")
        return results

    def get_stats(self) -> Dict:
        return {
            "total_nodes": len(self.nodes),
            "levels": {k: len(v) for k, v in self.levels.items()},
        }

    # ── state (persistence) ───────────────────────────────────────────────────

    def export_state(self) -> Dict:
        return {
            "nodes": [asdict(n) for n in self.nodes.values()],
            "levels": {str(k): list(v) for k, v in self.levels.items()},
        }

    def load_state(self, state: Dict) -> None:
        self.nodes = {}
        for n in state.get("nodes", []):
            node = HippoNode(**n)
            self.nodes[node.id] = node
        self.levels = {int(k): list(v) for k, v in state.get("levels", {}).items()}
        self._index = None
        logger.debug(f"HippoRetriever.load_state: nodes={len(self.nodes)}, levels={len(self.levels)}")
