"""
RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval.
Clusters document chunks, summarises each cluster, and recurses until a
single root summary remains.  Retrieval traverses the tree top-down.

LLM / embedding provider is selected via LLM_PROVIDER / EMBED_PROVIDER in .env.
"""
import asyncio
import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

import numpy as np
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import normalize

from backend.core.logger import logger
from .llm_provider import generate
from .vectors import EmbeddingIndex, embed_many_or_fallback

_LEVEL_PENALTY = 0.03   # per tree level, so summaries don't crowd out leaf passages


@dataclass
class RaptorNode:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    text: str = ""
    level: int = 0
    children: List[str] = field(default_factory=list)
    parent: Optional[str] = None
    embedding: Optional[List[float]] = None
    doc_ids: List[str] = field(default_factory=list)


class RaptorRunner:
    def __init__(self, api_key: str = "", max_levels: int = 3, target_cluster_size: int = 5):
        self.max_levels = max_levels
        self.target_cluster_size = target_cluster_size
        self.nodes: Dict[str, RaptorNode] = {}
        self.root_ids: List[str] = []
        self._index: Optional[EmbeddingIndex] = None

    # ── embedding index ───────────────────────────────────────────────────────

    def _embedding_index(self) -> EmbeddingIndex:
        # Built lazily, dropped whenever nodes change (build/index/load).
        if self._index is None:
            self._index = EmbeddingIndex.build({nid: n.embedding for nid, n in self.nodes.items()})
        return self._index

    # ── summarisation ─────────────────────────────────────────────────────────

    async def _summarise(self, texts: List[str], level: int) -> str:
        joined = "\n\n---\n\n".join(t[:600] for t in texts[:6])
        depth = "high-level abstract" if level > 1 else "detailed"
        prompt = (
            f"Write a {depth} summary of these related passages. "
            "Preserve key facts, entities, and relationships. Be concise.\n\n"
            f"{joined[:3500]}"
        )
        try:
            return await generate(prompt, temperature=0.2)
        except Exception as exc:
            logger.warning(f"RaptorRunner._summarise failed at level={level}: {exc}", exc_info=True)
            return " ".join(texts[0].split()[:80]) + "…"

    # ── clustering ────────────────────────────────────────────────────────────

    def _cluster(self, embeddings: List[List[float]], n_clusters: int) -> List[int]:
        n = len(embeddings)
        n_clusters = max(2, min(n_clusters, n // 2))
        X = normalize(np.array(embeddings))
        try:
            gmm = GaussianMixture(
                n_components=n_clusters, covariance_type="full",
                random_state=42, max_iter=100,
            )
            gmm.fit(X)
            return gmm.predict(X).tolist()
        except Exception as exc:
            logger.warning(
                f"GaussianMixture clustering failed (n={n}, k={n_clusters}), "
                f"falling back to round-robin: {exc}",
                exc_info=True,
            )
            return [i % n_clusters for i in range(n)]

    # ── tree construction ─────────────────────────────────────────────────────

    async def build_tree(self, chunks: List[Dict]) -> Dict:
        if not chunks:
            logger.warning("RaptorRunner.build_tree called with empty chunks list")
            return {}

        logger.info(f"RaptorRunner.build_tree: {len(chunks)} chunks, max_levels={self.max_levels}")
        self._index = None
        embs = await embed_many_or_fallback([c["text"] for c in chunks], owner="RaptorRunner")
        leaf_ids = []
        for chunk, emb in zip(chunks, embs):
            node = RaptorNode(
                text=chunk["text"],
                level=0,
                embedding=emb,
                doc_ids=[chunk.get("doc_id", "")],
            )
            self.nodes[node.id] = node
            leaf_ids.append(node.id)

        logger.debug(f"RaptorRunner: {len(leaf_ids)} leaf nodes created")
        current = leaf_ids
        for level in range(1, self.max_levels + 1):
            if len(current) <= 1:
                logger.debug(f"RaptorRunner: stopping at level={level}, only {len(current)} node(s) remain")
                break
            n_clusters = max(2, len(current) // self.target_cluster_size)
            logger.debug(f"RaptorRunner: level={level}, nodes={len(current)}, clusters={n_clusters}")
            cur_embs = [self.nodes[nid].embedding for nid in current]
            labels = self._cluster(cur_embs, n_clusters)

            clusters: Dict[int, List[str]] = {}
            for nid, lbl in zip(current, labels):
                clusters.setdefault(lbl, []).append(nid)

            texts_per_cluster = [[self.nodes[nid].text for nid in ids] for ids in clusters.values()]
            summaries = await asyncio.gather(
                *[self._summarise(txts, level) for txts in texts_per_cluster],
                return_exceptions=True,
            )
            new_embs = await embed_many_or_fallback(
                [s if not isinstance(s, Exception) else "" for s in summaries], owner="RaptorRunner"
            )

            new_level: List[str] = []
            for (cluster_ids, _), summary, emb in zip(
                ((ids, None) for ids in clusters.values()), summaries, new_embs
            ):
                if isinstance(summary, Exception):
                    logger.warning(f"Summary exception at level={level}: {summary}")
                    summary = "Summary unavailable."
                all_docs = list({d for nid in cluster_ids for d in self.nodes[nid].doc_ids})
                parent = RaptorNode(
                    text=summary,
                    level=level,
                    children=cluster_ids,
                    embedding=emb,
                    doc_ids=all_docs,
                )
                self.nodes[parent.id] = parent
                for cid in cluster_ids:
                    self.nodes[cid].parent = parent.id
                new_level.append(parent.id)

            current = new_level

        self.root_ids = current
        self._index = None
        stats = self.get_stats()
        logger.info(f"RaptorRunner.build_tree complete: {stats}")
        return stats

    # ── retrieval ─────────────────────────────────────────────────────────────

    def retrieve(self, query_embedding: List[float], top_k: int = 6) -> List[Dict]:
        if not self.nodes:
            logger.warning("RaptorRunner.retrieve called but no nodes in tree")
            return []
        ids, sims = self._embedding_index().cosine(query_embedding)
        scored = [
            (float(sim) - self.nodes[nid].level * _LEVEL_PENALTY, nid, self.nodes[nid].level)
            for nid, sim in zip(ids, sims)
        ]
        scored.sort(reverse=True)
        results = [
            {
                "id": nid,
                "text": self.nodes[nid].text,
                "level": lvl,
                "score": round(s, 4),
                "doc_ids": self.nodes[nid].doc_ids,
            }
            for s, nid, lvl in scored[:top_k]
        ]
        logger.debug(f"RaptorRunner.retrieve: top_k={top_k}, returned={len(results)}")
        return results

    def get_stats(self) -> Dict:
        lvl_counts: Dict[int, int] = {}
        for n in self.nodes.values():
            lvl_counts[n.level] = lvl_counts.get(n.level, 0) + 1
        return {"total_nodes": len(self.nodes), "levels": lvl_counts}

    # ── state (persistence) ───────────────────────────────────────────────────

    def export_state(self) -> Dict:
        return {
            "nodes": [asdict(n) for n in self.nodes.values()],
            "root_ids": list(self.root_ids),
        }

    def load_state(self, state: Dict) -> None:
        self.nodes = {}
        for n in state.get("nodes", []):
            node = RaptorNode(**n)
            self.nodes[node.id] = node
        self.root_ids = list(state.get("root_ids", []))
        self._index = None
        logger.debug(f"RaptorRunner.load_state: nodes={len(self.nodes)}")
