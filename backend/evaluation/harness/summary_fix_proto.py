"""Prototype (dev only): RAPTOR summaries inherit the graph rank of their best descendant chunk.

Monkeypatches HippoRetriever.retrieve in this process only; the repository file is untouched while
the MuSiQue test run is in progress (plan rule 9). Measures, on QuALITY dev:
  - summaries in the top-8 ranking and in the 8k-char context,
  - S3 dev accuracy for m in {1, 2, 4} (LLM calls, cached).
"""
import asyncio
import json
import statistics as st
import sys
from typing import Dict, List

from backend.evaluation.harness.common import setup, bench, now, read_jsonl

setup("summary-fix prototype")

from backend.core.logger import logger
from backend.pipeline import hippo_retriever as H
from backend.evaluation.harness import metrics as M
from backend.evaluation.harness.answer import answer_one
from backend.evaluation.harness.retrieve import load_retrieval, load_questions, query_embeddings
from backend.evaluation.harness.systems import System


def _leaves(self, pid: str) -> List[str]:
    cache = self.__dict__.setdefault("_leaf_cache", {})
    if pid not in cache:
        node = self.raptor.nodes[pid]
        cache[pid] = [pid] if node.level == 0 else [l for c in node.children if c in self.raptor.nodes
                                                     for l in _leaves(self, c)]
    return cache[pid]


async def retrieve(self, question: str, query_embedding: List[float], top_k: int = 6) -> Dict:
    hits = self.raptor.retrieve(query_embedding, top_k=len(self.raptor.nodes))
    chunk_hits = [h for h in hits if h["level"] == 0]
    summary_hits = [h for h in hits if h["level"] > 0][: max(self.max_summaries, 0)]
    chunk_entities = self._chunk_entities()
    seeds = self._seeds(question, chunk_hits[: self.seed_passages], chunk_entities)
    ppr = await self._pagerank(seeds)
    candidates = sorted(chunk_hits + summary_hits, key=lambda h: h["score"], reverse=True)
    sim_rank = {h["id"]: r for r, h in enumerate(candidates, start=1)}
    graph_score = {pid: sum(ppr.get(eid, 0.0) for eid in ents) for pid, ents in chunk_entities.items()}
    graph_ranked = sorted(((s, pid) for pid, s in graph_score.items() if s > 0), reverse=True)
    graph_rank = {pid: r for r, (_, pid) in enumerate(graph_ranked, start=1)}
    # THE CHANGE: a summary covers its descendant chunks, so it takes the best graph rank among them.
    for h in summary_hits:
        ranks = [graph_rank[l] for l in _leaves(self, h["id"]) if l in graph_rank]
        if ranks:
            graph_rank[h["id"]] = min(ranks)

    def fused(pid: str) -> float:
        score = 1.0 / (H._RRF_K + sim_rank[pid])
        if pid in graph_rank:
            score += self.ppr_weight / (H._RRF_K + graph_rank[pid])
        return score

    scored = sorted(((fused(h["id"]), h["id"]) for h in candidates), reverse=True)
    passages = [self._passage(pid, s) for s, pid in scored[:top_k]]
    cutoff = max(ppr.values(), default=0.0) * H._MIN_ENTITY_SHARE
    top_entities = [eid for eid, sc in sorted(ppr.items(), key=lambda kv: kv[1], reverse=True)[:H._TOP_ENTITIES]
                    if sc > cutoff]
    community_mass: Dict[int, float] = {}
    for eid, sc in ppr.items():
        cid = self.graphrag.graph.nodes[eid].get("community", -1)
        if cid in self.graphrag.community_summaries:
            community_mass[cid] = community_mass.get(cid, 0.0) + sc
    return {"passages": passages, "entities": top_entities, "communities": community_mass}


H.HippoRetriever.retrieve = retrieve


async def main() -> None:
    r = load_retrieval("quality", "full")
    qs = load_questions("quality", "dev")
    embs = await query_embeddings("quality", "dev", qs)
    report = {}
    for m in (1, 2, 4):
        s = System("S3", "graph", 0.25, m, False)
        top8, ctx = [], []
        rows = []
        sem = asyncio.Semaphore(16)

        async def one(q):
            async with sem:
                return await answer_one("quality", r, s, q, embs[q["qid"]], 8000, "dev", f"proto_m{m}")
        for q in qs:
            ps = await r.ranked(s, q["question"], embs[q["qid"]], 8)
            top8.append(sum(1 for p in ps if p["level"] > 0))
        rows = await asyncio.gather(*[one(q) for q in qs])
        ctx = [sum(1 for p in row["passages"] if p["level"] > 0) for row in rows]
        acc = st.mean(0.0 if row["error"] else M.mc_correct(row["answer"], row["gold_answers"][0]) for row in rows)
        report[f"m{m}"] = {"summaries_top8": round(st.mean(top8), 3), "summaries_in_context": round(st.mean(ctx), 3),
                           "questions_with_summary_in_context": sum(c > 0 for c in ctx),
                           "accuracy": round(acc, 4), "errors": sum(1 for x in rows if x["error"])}
        out = bench("quality") / "runs" / "dev" / f"S3_proto_m{m}.jsonl"
        with open(out, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(m, report[f"m{m}"], flush=True)
    (bench("quality") / "tuning" / "summary_fix_prototype.json").write_text(
        json.dumps({"created_at": now(), "change": "summaries inherit best descendant chunk graph rank",
                    "ppr_weight": 0.25, "budget": 8000, "split": "dev", "results": report}, indent=1))


asyncio.run(main())
