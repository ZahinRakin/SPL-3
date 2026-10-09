"""
Answers for every system (EVALUATION_PLAN.md §8 step 3): frozen budget, temperature 0, no chat
history (each question is answered with a fresh prompt; QueryEngine.history is never used).

Runs are append-only and resumable: runs/<split>/<system>.jsonl gets one line per question, and a
re-run skips questions already answered. A failed call is recorded with its error and scores 0
(rule 6). Every LLM call goes through the on-disk cache (rule 3); `cache_hit` says whether the
answer came from it (latency is then not a real measurement).

On the test split the settings must match frozen_config.json (written when the protocol is
frozen), so a test run can't silently use different settings.

Usage:
  python -m backend.evaluation.harness.answer --dataset musique --split test --systems C0,S0,S1,S2,S3,S4,S5
  python -m backend.evaluation.harness.answer --dataset quality --split dev --systems S3 --w 1.0 --m 2 --tag m2
"""
import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from backend.core.logger import logger
from backend.pipeline import llm_provider
from backend.evaluation.harness.common import (
    append_jsonl, bench, key_usage, now, read_jsonl, run_log, settled_usage, setup,
)
from backend.evaluation.harness.metrics import parse_option
from backend.evaluation.harness.prompts import PROMPTS, render
from backend.evaluation.harness.retrieve import load_questions, load_retrieval, query_embeddings
from backend.evaluation.harness.systems import Retrieval, System, ladder

ANSWER_TEMPERATURE = 0.0
_CONCURRENCY = 16


def frozen_config(dataset: str) -> Optional[Dict]:
    p = bench(dataset) / "frozen_config.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def parse_answer(dataset: str, raw: str, json_mode: bool) -> str:
    text = raw.strip()
    if json_mode:
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "answer" in data:
                return str(data["answer"]).strip()
        except json.JSONDecodeError:
            pass
        return text
    if dataset == "quality":
        return parse_option(text) or ""
    return text


def is_cached(prompt: str, json_mode: bool) -> bool:
    key = llm_provider._cache_key(prompt, json_mode, ANSWER_TEMPERATURE, llm_provider._DEFAULT_MAX_TOKENS)
    return llm_provider._cache_path(key).exists()


async def answer_one(dataset: str, r: Optional[Retrieval], system: System, q: Dict,
                     q_emb: List[float], budget: int, split: str, tag: str) -> Dict:
    json_mode = PROMPTS[dataset][2]
    t0 = time.perf_counter()
    context, passages = ("", []) if system.kind == "closed" else await r.context(system, q["question"], q_emb, budget)
    t1 = time.perf_counter()
    prompt = render(dataset, system.kind == "closed", q, context)
    cache_hit = is_cached(prompt, json_mode)
    raw, error = "", None
    try:
        raw = await llm_provider.generate(prompt, json_mode=json_mode, temperature=ANSWER_TEMPERATURE)
    except Exception as exc:   # rule 6: recorded, scores 0
        error = f"{type(exc).__name__}: {exc}"
        logger.error(f"[answer {dataset}/{system.sid}] {q['qid']}: {error}")
    t2 = time.perf_counter()
    ctx_docs: List[str] = []
    for p in passages:
        for d in p.get("doc_ids", []):
            if d not in ctx_docs:
                ctx_docs.append(d)
    return {
        "qid": q["qid"], "system": system.sid, "tag": tag, "split": split, "type": q["type"],
        "question": q["question"], "gold_answers": q["answers"], "gold_doc_ids": q["gold_doc_ids"],
        "answer": parse_answer(dataset, raw, json_mode), "raw": raw, "error": error,
        "settings": {"ppr_weight": system.ppr_weight, "max_summaries": system.max_summaries,
                     "graph_extras": system.graph_extras, "budget": budget, "temperature": ANSWER_TEMPERATURE},
        "context_chars": len(context), "context": context,
        "passages": [{"id": p["id"], "level": p["level"], "doc_ids": p.get("doc_ids", [])} for p in passages],
        "context_doc_ids": ctx_docs,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_chars": len(prompt), "cache_hit": cache_hit,
        "retrieval_s": round(t1 - t0, 3), "generation_s": round(t2 - t1, 3),
        "answered_at": now(),
    }


async def main(args) -> None:
    root = bench(args.dataset)
    setup("answer", root / "logs" / f"answer_{args.split}_{args.tag or 'main'}_{time.strftime('%Y%m%d_%H%M%S')}.log")
    frozen = frozen_config(args.dataset)
    if args.split == "test":
        if frozen is None:
            raise SystemExit("No frozen_config.json: freeze the protocol before any test answer (rule 1-2).")
        w, m, budget = frozen["ppr_weight"], frozen["max_summaries"], frozen["budget"]
        if (args.w, args.m, args.budget) != (None, None, None) and (args.w, args.m, args.budget) != (w, m, budget):
            raise SystemExit(f"Test settings must be the frozen ones: w={w} m={m} budget={budget}")
    else:
        w, m, budget = args.w, args.m, args.budget
        if None in (w, m, budget):
            raise SystemExit("--w, --m and --budget are required on dev")
    systems = ladder(w, m)
    questions = load_questions(args.dataset, args.split)
    if args.only_types:
        questions = [q for q in questions if q["type"] in args.only_types.split(",")]
    if args.only_docs:
        keep = set(args.only_docs.split(","))
        questions = [q for q in questions if q["gold_doc_ids"][0] in keep]
    if args.limit:
        questions = questions[: args.limit]
    embs = await query_embeddings(args.dataset, args.split, load_questions(args.dataset, args.split))
    indexes: Dict[str, Retrieval] = {}

    def retrieval_for(q: Dict) -> Retrieval:
        # GraphRAG-Bench has one index per novel; every other dataset has one index.
        name = f"{args.index}/{q['gold_doc_ids'][0]}" if args.per_doc else args.index
        if name not in indexes:
            indexes[name] = load_retrieval(args.dataset, name)
        return indexes[name]

    usage0 = key_usage()["usage"]
    run_log(f"answers `{args.dataset}` {args.split} {args.tag or ''} started: systems {args.systems}, "
            f"{len(questions)} questions, w={w} m={m} budget={budget}, key usage ${usage0:.4f}")
    out_dir = root / "runs" / args.split
    summary = {}
    for sid in args.systems.split(","):
        system = systems[sid]
        out = out_dir / f"{sid}{'_' + args.tag if args.tag else ''}.jsonl"
        done = {row["qid"] for row in read_jsonl(out)} if out.exists() else set()
        todo = [q for q in questions if q["qid"] not in done]
        if args.retry_errors:
            # Only the questions whose answer failed in the main run file; the original rows stay.
            failed = {row["qid"] for row in read_jsonl(out_dir / f"{sid}.jsonl") if row["error"]}
            todo = [q for q in todo if q["qid"] in failed]
        sem = asyncio.Semaphore(_CONCURRENCY)
        t0 = time.perf_counter()

        async def run(q: Dict) -> Dict:
            async with sem:
                r = None if system.kind == "closed" else retrieval_for(q)
                row = await answer_one(args.dataset, r, system, q, embs[q["qid"]], budget, args.split, args.tag)
                append_jsonl(out, row)
                return row

        # Per-document indexes: group by document so each index is loaded once and in order.
        rows = await asyncio.gather(*[run(q) for q in todo])
        errors = sum(1 for r_ in rows if r_["error"])
        summary[sid] = {"answered": len(rows), "skipped_done": len(done), "errors": errors,
                        "seconds": round(time.perf_counter() - t0, 1)}
        logger.info(f"[answer {args.dataset}/{sid}] {summary[sid]}")
    usage1 = settled_usage() if not args.no_settle else key_usage()["usage"]
    run_log(f"answers `{args.dataset}` {args.split} {args.tag or ''} finished: {summary}, "
            f"cost ${usage1 - usage0:.4f} (key ${usage0:.4f} → ${usage1:.4f})")
    print(json.dumps({"summary": summary, "cost": round(usage1 - usage0, 4)}, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--split", required=True, choices=["dev", "test"])
    ap.add_argument("--systems", required=True)
    ap.add_argument("--index", default="full")
    ap.add_argument("--per_doc", action="store_true")
    ap.add_argument("--w", type=float)
    ap.add_argument("--m", type=int)
    ap.add_argument("--budget", type=int)
    ap.add_argument("--tag", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only_types", default="")
    ap.add_argument("--only_docs", default="", help="comma-separated gold document ids (GraphRAG-Bench novels)")
    ap.add_argument("--no_settle", action="store_true")
    ap.add_argument("--retry_errors", action="store_true",
                    help="answer only questions that failed in runs/<split>/<system>.jsonl (use with --tag)")
    asyncio.run(main(ap.parse_args()))
