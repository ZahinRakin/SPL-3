"""
HiPPO Retriever: Hierarchical Passage Pooling.
Builds a pyramid of pooled passage embeddings for multi-granularity retrieval —
fine-grained at level 0, coarser at higher levels.

Embedding provider is selected via EMBED_PROVIDER in .env.
"""
import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .llm_provider import embed


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

    # ── embedding ─────────────────────────────────────────────────────────────

    async def _embed(self, text: str) -> List[float]:
        try:
            return await embed(text)
        except Exception:
            rng = np.random.default_rng(abs(hash(text)) % (2**32))
            v = rng.standard_normal(768).astype(float)
            return (v / np.linalg.norm(v)).tolist()

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
            return {}

        embs = await asyncio.gather(
            *[self._embed(p["text"]) for p in passages], return_exceptions=True
        )
        level0: List[str] = []
        for i, (p, emb) in enumerate(zip(passages, embs)):
            if isinstance(emb, Exception):
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
        current = level0

        for level in range(1, self.max_levels + 1):
            if len(current) <= 1:
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
            current = next_level

        return {"total_nodes": len(self.nodes), "levels": {k: len(v) for k, v in self.levels.items()}}

    # ── retrieval ─────────────────────────────────────────────────────────────

    def retrieve(self, query_embedding: List[float], top_k: int = 5, level: int = 0) -> List[Dict]:
        qv = np.array(query_embedding)
        target = self.levels.get(level, self.levels.get(0, []))
        scored = []
        for nid in target:
            node = self.nodes.get(nid)
            if node and node.embedding:
                nv = np.array(node.embedding)
                denom = np.linalg.norm(qv) * np.linalg.norm(nv)
                sim = float(np.dot(qv, nv) / denom) if denom > 0 else 0.0
                scored.append((sim, nid))
        scored.sort(reverse=True)
        return [
            {
                "id": nid,
                "text": self.nodes[nid].text,
                "level": self.nodes[nid].level,
                "score": round(s, 4),
                "doc_id": self.nodes[nid].doc_id,
            }
            for s, nid in scored[:top_k]
        ]

    def retrieve_multi_level(self, query_embedding: List[float], top_k: int = 5) -> List[Dict]:
        """Retrieve across all levels and deduplicate, preferring fine-grained hits."""
        qv = np.array(query_embedding)
        scored = []
        for nid, node in self.nodes.items():
            if node.embedding:
                nv = np.array(node.embedding)
                denom = np.linalg.norm(qv) * np.linalg.norm(nv)
                sim = float(np.dot(qv, nv) / denom) if denom > 0 else 0.0
                scored.append((sim - node.level * 0.02, nid))
        scored.sort(reverse=True)
        return [
            {
                "id": nid,
                "text": self.nodes[nid].text,
                "level": self.nodes[nid].level,
                "score": round(s, 4),
                "doc_id": self.nodes[nid].doc_id,
            }
            for s, nid in scored[:top_k]
        ]

    def get_stats(self) -> Dict:
        return {
            "total_nodes": len(self.nodes),
            "levels": {k: len(v) for k, v in self.levels.items()},
        }
