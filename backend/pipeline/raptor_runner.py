"""
RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval.
Clusters document chunks, summarises each cluster, and recurses until a
single root summary remains (or max_levels is reached). Only clusters of two or more
nodes are summarised; a node left alone in its cluster moves up a level unchanged.

Stage 1 of the cascade (RAPTOR → GraphRAG → HippoRAG). The tree's nodes are the
case's passages: leaves are the chunks, higher levels are summaries. GraphRAG extracts
entities from all of them, and HippoRAG ranks them. The Standard (plain RAG) mode
searches the leaves directly.
"""
import asyncio
import math
import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

import numpy as np
from sklearn.decomposition import PCA
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import normalize

from backend.core.logger import logger
from .llm_provider import generate
from .vectors import EmbeddingIndex, embed_many_or_fallback

_LEVEL_PENALTY = 0.03          # per tree level, so summaries don't crowd out leaf passages
_MIN_CHUNKS_TO_SUMMARISE = 3   # a summary of 2 chunks is nearly a copy of them; above the
                               # leaves, 2 summaries still merge into the document's root
# GMM in 768 dimensions on a handful of points is underdetermined. The RAPTOR paper reduces
# dimensions first (UMAP, ~10-d); PCA does the same job without a new dependency.
_REDUCED_DIM = 10
_SUMMARY_MAX_CHARS = 24000     # safety cap on one summary prompt's input (~6K tokens)

_SUMMARY_PROMPT = """Write a {depth} summary of the related passages below.
Keep every specific fact: names, numbers, amounts, dates, places, and how the people,
organisations and events relate to each other. Do not add anything the passages do not state.

{passages}"""


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
        self._index: Optional[EmbeddingIndex] = None

    # ── embedding index ───────────────────────────────────────────────────────

    def _embedding_index(self) -> EmbeddingIndex:
        # Built lazily, dropped whenever nodes change (build/index/load).
        if self._index is None:
            self._index = EmbeddingIndex.build({nid: n.embedding for nid, n in self.nodes.items()})
        return self._index

    # ── summarisation ─────────────────────────────────────────────────────────

    async def _summarise(self, texts: List[str], level: int) -> str:
        # The summariser sees the children in full: the facts questions ask about (numbers,
        # dates) are often deep in a chunk, and a summary can't keep what it never saw.
        joined = "\n\n---\n\n".join(texts)
        if len(joined) > _SUMMARY_MAX_CHARS:
            logger.warning(
                f"RaptorRunner._summarise: level={level} input of {len(joined)} chars "
                f"cut to {_SUMMARY_MAX_CHARS}"
            )
            joined = joined[:_SUMMARY_MAX_CHARS]
        depth = "high-level" if level > 1 else "detailed"
        prompt = _SUMMARY_PROMPT.format(depth=depth, passages=joined)
        try:
            return await generate(prompt, temperature=0.2)
        except Exception as exc:
            logger.warning(f"RaptorRunner._summarise failed at level={level}: {exc}", exc_info=True)
            return " ".join(texts[0].split()[:80]) + "…"

    # ── clustering ────────────────────────────────────────────────────────────

    def _cluster(self, embeddings: List[List[float]]) -> List[List[int]]:
        """Groups of node indices. A level that fits in one cluster becomes one group (the
        root); otherwise GMM over PCA-reduced embeddings, about target_cluster_size per group."""
        n = len(embeddings)
        size = self.target_cluster_size
        if n <= size:
            return [list(range(n))]
        n_clusters = math.ceil(n / size)
        X = normalize(np.array(embeddings))
        try:
            X = PCA(n_components=min(_REDUCED_DIM, n - 1), random_state=42).fit_transform(X)
            gmm = GaussianMixture(
                n_components=n_clusters, covariance_type="diag",
                random_state=42, max_iter=100,
            )
            labels = gmm.fit(X).predict(X).tolist()
        except Exception as exc:
            logger.warning(
                f"GaussianMixture clustering failed (n={n}, k={n_clusters}), "
                f"falling back to consecutive groups: {exc}",
                exc_info=True,
            )
            return [list(range(i, min(i + size, n))) for i in range(0, n, size)]

        groups: Dict[int, List[int]] = {}
        for i, lbl in enumerate(labels):
            groups.setdefault(lbl, []).append(i)
        # GMM can lump most nodes into one component; split oversized groups (in document
        # order) so every summary prompt stays small enough to keep the details.
        result: List[List[int]] = []
        for g in groups.values():
            if len(g) > 2 * size:
                result.extend(g[i:i + size] for i in range(0, len(g), size))
            else:
                result.append(g)
        return result

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
            if len(current) < (_MIN_CHUNKS_TO_SUMMARISE if level == 1 else 2):
                logger.debug(f"RaptorRunner: stopping at level={level}, only {len(current)} node(s) remain")
                break
            cur_embs = [self.nodes[nid].embedding for nid in current]
            clusters = [[current[i] for i in group] for group in self._cluster(cur_embs)]
            to_summarise = [ids for ids in clusters if len(ids) > 1]
            logger.debug(
                f"RaptorRunner: level={level}, nodes={len(current)}, clusters={len(clusters)}, "
                f"summarised={len(to_summarise)}"
            )
            if not to_summarise:
                # Only singletons: another level would just re-summarise single nodes.
                logger.debug(f"RaptorRunner: stopping at level={level}, every cluster is a singleton")
                break

            summaries = await asyncio.gather(
                *[self._summarise([self.nodes[nid].text for nid in ids], level) for ids in to_summarise],
                return_exceptions=True,
            )
            new_embs = await embed_many_or_fallback(
                [s if not isinstance(s, Exception) else "" for s in summaries], owner="RaptorRunner"
            )
            parents: Dict[str, str] = {}   # first child id → parent id
            for cluster_ids, summary, emb in zip(to_summarise, summaries, new_embs):
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
                parents[cluster_ids[0]] = parent.id

            # A singleton moves up unchanged, so it can still join a cluster at the next level.
            current = [parents.get(ids[0], ids[0]) for ids in clusters]

        self._index = None
        stats = self.get_stats()
        logger.info(f"RaptorRunner.build_tree complete: {stats}")
        return stats

    # ── retrieval ─────────────────────────────────────────────────────────────

    def retrieve(
        self, query_embedding: List[float], top_k: int = 6, level: Optional[int] = None
    ) -> List[Dict]:
        """Cosine search over the whole tree, or over one level only (level=0: the chunks)."""
        if not self.nodes:
            logger.warning("RaptorRunner.retrieve called but no nodes in tree")
            return []
        candidates = None if level is None else [nid for nid, n in self.nodes.items() if n.level == level]
        ids, sims = self._embedding_index().cosine(query_embedding, candidates)
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

    def passages(self, doc_id: str) -> List[Dict]:
        """One document's chunks and summaries as passages for entity extraction.
        Each build_tree call builds a tree over one document, so its nodes carry only that doc id."""
        return [
            {"id": nid, "text": n.text, "doc_id": doc_id, "level": n.level}
            for nid, n in self.nodes.items()
            if n.doc_ids == [doc_id]
        ]

    def get_stats(self) -> Dict:
        lvl_counts: Dict[int, int] = {}
        for n in self.nodes.values():
            lvl_counts[n.level] = lvl_counts.get(n.level, 0) + 1
        return {"total_nodes": len(self.nodes), "levels": lvl_counts}

    # ── state (persistence) ───────────────────────────────────────────────────

    def export_state(self) -> Dict:
        return {"nodes": [asdict(n) for n in self.nodes.values()]}

    def load_state(self, state: Dict) -> None:
        self.nodes = {}
        for n in state.get("nodes", []):
            node = RaptorNode(**n)
            self.nodes[node.id] = node
        self._index = None
        logger.debug(f"RaptorRunner.load_state: nodes={len(self.nodes)}")
