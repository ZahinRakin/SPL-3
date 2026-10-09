"""
Ranking only, no LLM calls (EVALUATION_PLAN.md §2 sanity test, §4 tuning, §8 step 3).

  sanity  S2 with ppr_weight=0, max_summaries=0 must rank exactly like S0 (50 dev questions).
  tune    dev retrieval metrics for every ppr_weight in the grid → tuning/ppr_weight.json
  rank    ranked doc ids for one system on a split → runs/rank_<system>_<split>.jsonl

Query embeddings are computed once per question and stored in query_emb/<split>.npz.

Usage:
  python -m evaluation.harness.retrieve sanity --dataset musique --index full
  python -m evaluation.harness.retrieve tune   --dataset musique --index full
"""
import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Dict, List

import numpy as np

from backend.core.logger import logger
from backend.pipeline.vectors import embed_many_or_fallback
from evaluation.harness import metrics as M
from evaluation.harness.common import bench, now, read_jsonl, read_split, run_log, setup
from evaluation.harness.index import load_index
from evaluation.harness.systems import RANK_DEPTH, Retrieval, System, doc_ids_of

PPR_GRID = [0.25, 0.5, 1.0, 2.0]
SANITY_N = 50
_RETRIEVAL_METRIC = {"musique": ("recall@5", 5), "2wiki": ("recall@5", 5),
                     "multihop_rag": ("hits@4", 4), "quality": ("recall@5", 5),
                     "graphrag_bench_novel": ("recall@5", 5)}


def load_questions(dataset: str, split: str) -> List[Dict]:
    by_id = {q["qid"]: q for q in read_jsonl(bench(dataset) / "questions.jsonl")}
    return [by_id[i] for i in read_split(dataset, split)]


def load_retrieval(dataset: str, index_name: str) -> Retrieval:
    root = bench(dataset)
    graphrag, raptor = load_index(root / "index" / index_name)
    names = {d["doc_id"]: d["title"] for d in read_jsonl(root / "corpus.jsonl")}
    return Retrieval(graphrag, raptor, names)


async def query_embeddings(dataset: str, split: str, questions: List[Dict]) -> Dict[str, List[float]]:
    path = bench(dataset) / "query_emb" / f"{split}.npz"
    if path.exists():
        data = np.load(path)
        if list(data["qids"]) == [q["qid"] for q in questions]:
            return {qid: v.astype(float).tolist() for qid, v in zip(data["qids"], data["emb"])}
    embs = await embed_many_or_fallback([q["question"] for q in questions], owner="harness",
                                        task_type="retrieval_query")
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, qids=np.array([q["qid"] for q in questions]), emb=np.array(embs, dtype=np.float32))
    # Reload through float32 so the first run and later runs use identical vectors.
    data = np.load(path)
    return {qid: v.astype(float).tolist() for qid, v in zip(data["qids"], data["emb"])}


def retrieval_scores(ranked_docs: List[str], gold: List[str]) -> Dict[str, float]:
    return {
        "recall@2": M.recall_at_k(ranked_docs, gold, 2), "recall@5": M.recall_at_k(ranked_docs, gold, 5),
        "hits@4": M.hits_at_k(ranked_docs, gold, 4), "hits@10": M.hits_at_k(ranked_docs, gold, 10),
        "mrr@10": M.mrr_at_k(ranked_docs, gold, 10),
    }


async def rank_system(r: Retrieval, system: System, questions: List[Dict], embs: Dict[str, List[float]]) -> List[Dict]:
    rows = []
    for q in questions:
        t0 = time.perf_counter()
        passages = await r.ranked(system, q["question"], embs[q["qid"]], RANK_DEPTH)
        docs = doc_ids_of(passages)
        rows.append({
            "qid": q["qid"], "system": system.sid, "ppr_weight": system.ppr_weight,
            "max_summaries": system.max_summaries, "type": q["type"],
            "ranked_passages": [p["id"] for p in passages], "ranked_doc_ids": docs,
            "gold_doc_ids": q["gold_doc_ids"], "seconds": round(time.perf_counter() - t0, 3),
            **({} if not q["gold_doc_ids"] else retrieval_scores(docs, q["gold_doc_ids"])),
        })
    return rows


def mean(rows: List[Dict], key: str) -> float:
    vals = [r[key] for r in rows if key in r and not np.isnan(r[key])]
    return float(np.mean(vals)) if vals else float("nan")


# ── commands ──────────────────────────────────────────────────────────────────

async def sanity(args) -> None:
    r = load_retrieval(args.dataset, args.index)
    qs = load_questions(args.dataset, "dev")[:SANITY_N]
    embs = await query_embeddings(args.dataset, "dev", load_questions(args.dataset, "dev"))
    s0 = await rank_system(r, System("S0", "dense"), qs, embs)
    s2 = await rank_system(r, System("S2-w0", "graph", 0.0, 0, False), qs, embs)
    mismatches = [(a["qid"], a["ranked_passages"][:5], b["ranked_passages"][:5])
                  for a, b in zip(s0, s2) if a["ranked_passages"] != b["ranked_passages"]]
    out = {"dataset": args.dataset, "index": args.index, "questions": len(qs), "depth": RANK_DEPTH,
           "identical": len(qs) - len(mismatches), "mismatches": mismatches[:10], "checked_at": now()}
    path = bench(args.dataset) / "tuning" / "sanity_S2w0_equals_S0.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    verdict = "PASS" if not mismatches else "FAIL"
    run_log(f"sanity test `{args.dataset}`: S2(w=0,m=0) ranking == S0 on {out['identical']}/{len(qs)} "
            f"dev questions (top {RANK_DEPTH}) → **{verdict}** (`{path.relative_to(bench(args.dataset).parent.parent)}`)")
    print(verdict, json.dumps(out)[:600])
    if mismatches:
        raise SystemExit("Sanity test failed: stop and report (plan §2).")


async def tune(args) -> None:
    r = load_retrieval(args.dataset, args.index)
    qs = load_questions(args.dataset, "dev")
    embs = await query_embeddings(args.dataset, "dev", qs)
    metric, _ = _RETRIEVAL_METRIC[args.dataset]
    systems = [System("S0", "dense"), System("S1", "bm25")] + [
        System(f"S2-w{w}", "graph", w, 0, False) for w in PPR_GRID]
    results, per_question = {}, {}
    for s in systems:
        t0 = time.perf_counter()
        rows = await rank_system(r, s, qs, embs)
        per_question[s.sid] = rows
        results[s.sid] = {k: mean(rows, k) for k in ("recall@2", "recall@5", "hits@4", "hits@10", "mrr@10")}
        results[s.sid]["seconds_per_q"] = round((time.perf_counter() - t0) / len(qs), 3)
        logger.info(f"[tune {args.dataset}] {s.sid}: {results[s.sid]}")
    best_w = max(PPR_GRID, key=lambda w: (results[f"S2-w{w}"][metric], -abs(w - 1.0)))
    out = {"dataset": args.dataset, "index": args.index, "split": "dev", "questions": len(qs),
           "selection_metric": metric, "grid": PPR_GRID, "results": results,
           "chosen_ppr_weight": best_w,
           "tie_rule": "highest dev metric; ties → the value closest to 1.0", "tuned_at": now()}
    tdir = bench(args.dataset) / "tuning"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "ppr_weight.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    with open(tdir / "ppr_weight_per_question.jsonl", "w", encoding="utf-8") as f:
        for sid, rows in per_question.items():
            for row in rows:
                f.write(json.dumps(row) + "\n")
    run_log(f"tuning `{args.dataset}` (dev, {len(qs)} q, by {metric}): "
            + ", ".join(f"{k} {v[metric]:.4f}" for k, v in results.items())
            + f" → chosen ppr_weight = **{best_w}**")
    print(json.dumps(out, indent=1))


async def rank(args) -> None:
    """Test-split rankings for every ranking system with the frozen settings (no LLM calls)."""
    from evaluation.harness.answer import frozen_config
    from evaluation.harness.systems import ladder
    frozen = frozen_config(args.dataset)
    if frozen is None:
        raise SystemExit("Freeze the protocol first (frozen_config.json).")
    r = load_retrieval(args.dataset, args.index)
    qs = load_questions(args.dataset, args.split)
    embs = await query_embeddings(args.dataset, args.split, qs)
    systems = ladder(frozen["ppr_weight"], frozen["max_summaries"])
    out_dir = bench(args.dataset) / "runs" / args.split
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for sid in ("S0", "S1", "S2", "S3"):   # S4/S5 rank like S2/S3; they differ only in context extras
        rows = await rank_system(r, systems[sid], qs, embs)
        with open(out_dir / f"rank_{sid}.jsonl", "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        summary[sid] = {k: round(mean(rows, k), 4) for k in ("recall@2", "recall@5", "hits@4", "mrr@10")}
        summary[sid]["seconds_per_q"] = round(float(np.mean([x["seconds"] for x in rows])), 3)
    run_log(f"rankings `{args.dataset}` {args.split}: {summary}")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["sanity", "tune", "rank"])
    ap.add_argument("--split", default="test")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--index", default="full")
    a = ap.parse_args()
    setup(f"retrieve {a.command}", bench(a.dataset) / "logs" / f"retrieve_{a.command}_{time.strftime('%Y%m%d_%H%M%S')}.log")
    asyncio.run({"sanity": sanity, "tune": tune, "rank": rank}[a.command](a))
