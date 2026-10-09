"""
The ablation ladder (EVALUATION_PLAN.md §2), defined once for retrieve.py and answer.py.

| ID | Retrieval                                   | Pipeline settings                                    |
|----|---------------------------------------------|------------------------------------------------------|
| C0 | none (closed book)                          | —                                                    |
| S0 | dense (cosine over chunks)                  | method="standard"                                    |
| S1 | BM25 over the same chunks (rank_bm25)       | harness-only; same block format and budget           |
| S2 | dense + PageRank (RRF)                      | ppr_weight=w*, max_summaries=0, graph_extras=False   |
| S3 | S2 + RAPTOR summaries                       | ppr_weight=w*, max_summaries=m*, graph_extras=False  |
| S4 | S2 + community summaries (+ graph facts)    | ppr_weight=w*, max_summaries=0, graph_extras=True    |
| S5 | full system                                 | ppr_weight=w*, max_summaries=m*, graph_extras=True   |

All systems share one index. Contexts are built by the app's own QueryEngine._build_context with
the frozen context budget B, so every system fills the same number of characters.
"""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
from rank_bm25 import BM25Okapi

from backend.pipeline.graphrag_indexer import GraphRAGIndexer
from backend.pipeline.hippo_retriever import HippoRetriever
from backend.pipeline.query_engine import QueryEngine
from backend.pipeline.raptor_runner import RaptorRunner

RANK_DEPTH = 20   # ranked passages kept per question for retrieval metrics (Recall@5, MRR@10, …)


@dataclass(frozen=True)
class System:
    sid: str
    kind: str                  # "closed" | "dense" | "bm25" | "graph"
    ppr_weight: float = 0.0
    max_summaries: int = 0
    graph_extras: bool = False


def ladder(w: float, m: int) -> Dict[str, System]:
    return {
        "C0": System("C0", "closed"),
        "S0": System("S0", "dense"),
        "S1": System("S1", "bm25"),
        "S2": System("S2", "graph", w, 0, False),
        "S3": System("S3", "graph", w, m, False),
        "S4": System("S4", "graph", w, 0, True),
        "S5": System("S5", "graph", w, m, True),
    }


_TOKEN = re.compile(r"[a-z0-9]+")


def bm25_tokens(text: str) -> List[str]:
    return _TOKEN.findall(text.lower())


class Retrieval:
    """One loaded index plus everything the systems need to rank and build contexts."""

    def __init__(self, graphrag: GraphRAGIndexer, raptor: RaptorRunner, doc_names: Dict[str, str]):
        self.graphrag = graphrag
        self.raptor = raptor
        self.hippo = HippoRetriever(graphrag=graphrag, raptor=raptor)
        self.engine = QueryEngine(graphrag=graphrag, raptor=raptor, hippo=self.hippo)
        self.engine.doc_names = doc_names
        self.chunk_ids = [nid for nid, n in raptor.nodes.items() if n.level == 0]
        self._bm25: Optional[BM25Okapi] = None

    def configure(self, system: System) -> None:
        self.hippo.ppr_weight = system.ppr_weight
        self.hippo.max_summaries = system.max_summaries

    # ── BM25 (S1) ─────────────────────────────────────────────────────────────

    def bm25(self) -> BM25Okapi:
        if self._bm25 is None:
            self._bm25 = BM25Okapi([bm25_tokens(self.raptor.nodes[c].text) for c in self.chunk_ids])
        return self._bm25

    def bm25_ranked(self, question: str, depth: int) -> List[Dict]:
        scores = self.bm25().get_scores(bm25_tokens(question))
        order = np.argsort(-scores, kind="stable")[:depth]
        return [self._passage(self.chunk_ids[i], float(scores[i])) for i in order]

    def _passage(self, pid: str, score: float) -> Dict:
        n = self.raptor.nodes[pid]
        return {"id": pid, "text": n.text, "level": n.level, "doc_ids": n.doc_ids, "score": score}

    # ── ranking (retrieval metrics) ───────────────────────────────────────────

    async def ranked(self, system: System, question: str, q_emb: List[float], depth: int = RANK_DEPTH) -> List[Dict]:
        if system.kind == "dense":
            return self.raptor.retrieve(q_emb, top_k=depth, level=0)
        if system.kind == "bm25":
            return self.bm25_ranked(question, depth)
        if system.kind == "graph":
            self.configure(system)
            return (await self.hippo.retrieve(question, q_emb, top_k=depth))["passages"]
        return []

    # ── context (answers) ─────────────────────────────────────────────────────

    async def context(self, system: System, question: str, q_emb: List[float], budget: int) -> Tuple[str, List[Dict]]:
        """The context string and the passages in it, with the same budget for every system."""
        if system.kind == "closed":
            return "", []
        if system.kind == "bm25":
            # Same rule as QueryEngine._build_context for a budget: passages in rank order,
            # the top one always kept, stop at the first that doesn't fit.
            chosen, used = [], 0
            for p in self.bm25_ranked(question, 40):
                cost = len(self.engine._passage_block(p)) + 2
                if chosen and used + cost > budget:
                    break
                chosen.append(p)
                used += cost
            ctx = "\n\n".join(self.engine._passage_block(p) for p in chosen)
            return ctx, [{"id": p["id"], "level": 0, "doc_ids": p["doc_ids"], "score": p["score"]} for p in chosen]
        self.configure(system)
        method = "standard" if system.kind == "dense" else "refined"
        ctx, _, used = await self.engine._build_context(
            question, q_emb, method, top_k=6, context_budget=budget, graph_extras=system.graph_extras,
        )
        return ctx, used


def doc_ids_of(passages: List[Dict]) -> List[str]:
    """Ranked passages → ranked distinct document ids (a summary contributes its documents)."""
    out: List[str] = []
    for p in passages:
        for d in p.get("doc_ids", []):
            if d not in out:
                out.append(d)
    return out
