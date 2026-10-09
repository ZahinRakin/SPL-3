"""
Controlled comparison of Standard RAG vs Refined (cascaded) RAG.

The experiment folder holds the pre-registered protocol and every artefact:

  protocol.md                       hypotheses, method, decision rule (written before the run)
  qa/*.json                         the question set, one file per document
  qa_set.json                       merged + validated question set          (validate)
  run.log                           full log of indexing and querying        (run)
  index_stats.json, index_snapshot.json                                       (run)
  results.jsonl                     one record per question per system       (run)
  judging/blinded_answers.jsonl     answers shuffled, system label removed    (run)
  judging/key.json                  blinded id → (question, system)           (run)
  judging/grades.jsonl              the grader's scores (written by the grader)
  per_question.csv, stats.json, report.md                                     (analyze)

Usage:
  python -m backend.evaluation.compare validate --exp backend/evaluation/experiments/2026-10-08_standard_vs_refined
  python -m backend.evaluation.compare run      --exp ... [--reuse-index]
  python -m backend.evaluation.compare analyze  --exp ...
"""
import argparse
import asyncio
import csv
import json
import logging
import random
import re
import string
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from rouge_score import rouge_scorer  # type: ignore
from scipy import stats  # type: ignore

from backend.core.logger import logger
from backend.pipeline.document_processor import extract_text
from backend.pipeline.graphrag_indexer import GraphRAGIndexer
from backend.pipeline.hippo_retriever import HippoRetriever
from backend.pipeline.llm_provider import provider_info
from backend.pipeline.query_engine import QueryEngine
from backend.pipeline.raptor_runner import RaptorRunner
from backend.pipeline.vectors import set_strict_embeddings

# ── settings fixed by the protocol ────────────────────────────────────────────

SEED = 42
TOP_K = 6
METHODS = ("standard", "refined")
QUESTION_TYPES = ("fact", "relational", "summary")
GRADES = (0.0, 0.5, 1.0)
MAX_ATTEMPTS = 3            # 1 try + 2 retries
ALPHA = 0.05
BOOTSTRAP_RESAMPLES = 10_000
DOCS_DIR = Path(__file__).resolve().parents[2] / "data/input/sample_cases"
_ERROR_PREFIXES = ("Error generating answer", "Error parsing LLM response")


# ── helpers ───────────────────────────────────────────────────────────────────

def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _norm_text(text: str) -> str:
    """Lowercase; unify the dash and quote variants LLMs emit (e.g. U+2011 hyphens)."""
    text = text.lower()
    text = re.sub(r"[‐‑‒–—−]", "-", text)
    text = re.sub(r"[‘’]", "'", text)
    text = re.sub(r"[“”]", '"', text)
    return _norm_ws(text)


def key_fact_recall(key_facts: List[str], text: str) -> float:
    """Share of key facts found in `text`; a fact may list variants separated by '|'."""
    if not key_facts:
        return 1.0
    hay = _norm_text(text)
    hits = sum(1 for kf in key_facts if any(_norm_text(v) in hay for v in kf.split("|") if v.strip()))
    return hits / len(key_facts)


def _squad_tokens(text: str) -> List[str]:
    text = _norm_text(text)
    text = "".join(ch for ch in text if ch not in set(string.punctuation))
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    return text.split()


def token_f1(prediction: str, reference: str) -> float:
    pred, ref = _squad_tokens(prediction), _squad_tokens(reference)
    common = Counter(pred) & Counter(ref)
    overlap = sum(common.values())
    if not pred or not ref or overlap == 0:
        return 0.0
    precision, recall = overlap / len(pred), overlap / len(ref)
    return 2 * precision * recall / (precision + recall)


def _read_jsonl(path: Path) -> List[Dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _attach_file_log(exp: Path) -> None:
    handler = logging.FileHandler(exp / "run.log", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    for h in logger.handlers:
        if not isinstance(h, logging.FileHandler):
            h.setLevel(logging.INFO)   # keep the console readable; the file gets everything


# ── validate ──────────────────────────────────────────────────────────────────

def validate(exp: Path) -> List[Dict]:
    """Merge qa/*.json into qa_set.json after checking every item (protocol §4)."""
    questions: List[Dict] = []
    for f in sorted((exp / "qa").glob("*.json")):
        questions.extend(json.loads(f.read_text(encoding="utf-8")))

    problems: List[str] = []
    seen = set()
    docs: Dict[str, str] = {}
    required = ("id", "doc", "type", "question", "reference", "evidence", "key_facts")
    for q in questions:
        qid = q.get("id", "?")
        missing = [k for k in required if not q.get(k)]
        if missing:
            problems.append(f"{qid}: missing {missing}")
            continue
        if qid in seen:
            problems.append(f"{qid}: duplicate id")
        seen.add(qid)
        if q["type"] not in QUESTION_TYPES:
            problems.append(f"{qid}: unknown type {q['type']!r}")
        if q["doc"] not in docs:
            path = DOCS_DIR / q["doc"]
            if not path.exists():
                problems.append(f"{qid}: document {q['doc']!r} not found")
                continue
            docs[q["doc"]] = _norm_ws(path.read_text(encoding="utf-8"))
        for quote in q["evidence"]:
            if _norm_ws(quote) not in docs[q["doc"]]:
                problems.append(f"{qid}: evidence not verbatim in {q['doc']}: {quote[:70]!r}")

    if problems:
        for p in problems:
            print("INVALID", p)
        raise SystemExit(f"{len(problems)} problem(s); qa_set.json not written.")

    (exp / "qa_set.json").write_text(json.dumps(questions, indent=1, ensure_ascii=False), encoding="utf-8")
    by_type = Counter(q["type"] for q in questions)
    by_doc = Counter(q["doc"] for q in questions)
    print(f"OK: {len(questions)} questions, all evidence verbatim. By type: {dict(by_type)}")
    for d, n in sorted(by_doc.items()):
        print(f"  {n:3d}  {d}")
    return questions


# ── run ───────────────────────────────────────────────────────────────────────

async def _build_index(exp: Path, docs: List[str], reuse: bool) -> Tuple[GraphRAGIndexer, RaptorRunner]:
    graphrag, raptor = GraphRAGIndexer(), RaptorRunner()
    snapshot = exp / "index_snapshot.json"
    if reuse and snapshot.exists():
        state = json.loads(snapshot.read_text(encoding="utf-8"))
        graphrag.load_state(state["graphrag"])
        raptor.load_state(state["raptor"])
        logger.info(f"Index reused from {snapshot.name}: {len(graphrag.entities)} entities, {len(raptor.nodes)} passages")
        return graphrag, raptor

    per_doc = []
    t_all = time.perf_counter()
    for name in docs:
        t0 = time.perf_counter()
        text = await extract_text(str(DOCS_DIR / name))
        doc_id = Path(name).stem
        # Same cascade as backend/api/documents.py: RAPTOR first, then the graph over
        # chunks + summaries.
        chunks = graphrag.chunk_text(text, doc_id)
        await raptor.build_tree(chunks)
        passages = raptor.passages(doc_id) or chunks
        await graphrag.index_document(doc_id, text, passages)
        secs = round(time.perf_counter() - t0, 1)
        per_doc.append({"doc": name, "words": len(text.split()), "chunks": len(chunks),
                        "summaries": len(passages) - len(chunks), "seconds": secs})
        logger.info(f"Indexed {name}: {len(chunks)} chunks, {len(passages) - len(chunks)} summaries, {secs}s")

    index_stats = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "seconds_total": round(time.perf_counter() - t_all, 1),
        "documents": per_doc,
        "graph": graphrag.get_stats(),
        "raptor": raptor.get_stats(),
        "models": provider_info(),
    }
    (exp / "index_stats.json").write_text(json.dumps(index_stats, indent=2), encoding="utf-8")
    snapshot.write_text(json.dumps({"graphrag": graphrag.export_state(), "raptor": raptor.export_state()}),
                        encoding="utf-8")
    logger.info(f"Index built in {index_stats['seconds_total']}s: {index_stats['graph']}")
    return graphrag, raptor


async def _ask(engine: QueryEngine, question: str, method: str) -> Tuple[Dict, float, int]:
    """One question in one system, history cleared, with the protocol's retry rule."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        engine.load_state({"history": []})   # every question is answered independently
        t0 = time.perf_counter()
        try:
            res = await engine.query(question, method=method, top_k=TOP_K)
        except Exception as exc:
            logger.warning(f"query raised ({method}, attempt {attempt}): {exc!r}", exc_info=True)
            res = {"answer": f"Error generating answer: {exc}", "entities": [], "sources": [],
                   "confidence": 0.0, "reasoning": "", "context": ""}
        latency = time.perf_counter() - t0
        if not res["answer"].startswith(_ERROR_PREFIXES):
            return res, latency, attempt
        logger.warning(f"failed answer ({method}, attempt {attempt}): {res['answer'][:120]!r}")
    return res, latency, MAX_ATTEMPTS


async def run(exp: Path, reuse_index: bool) -> None:
    _attach_file_log(exp)
    questions = json.loads((exp / "qa_set.json").read_text(encoding="utf-8"))
    docs = sorted({q["doc"] for q in questions})
    logger.info(f"=== run started: {len(questions)} questions, {len(docs)} documents, models={provider_info()}")

    graphrag, raptor = await _build_index(exp, docs, reuse_index)
    engine = QueryEngine(graphrag=graphrag, raptor=raptor, hippo=HippoRetriever(graphrag=graphrag, raptor=raptor))

    results_path = exp / "results.jsonl"
    done = {(r["qid"], r["method"]) for r in _read_jsonl(results_path)}
    if done:
        logger.info(f"Resuming: {len(done)} records already in results.jsonl")

    rng = random.Random(SEED)
    order = list(questions)
    rng.shuffle(order)
    plan = [(q, rng.sample(METHODS, k=len(METHODS))) for q in order]   # random system order per question

    with results_path.open("a", encoding="utf-8") as out:
        for i, (q, methods) in enumerate(plan, 1):
            for slot, method in enumerate(methods):
                if (q["id"], method) in done:
                    continue
                res, latency, attempts = await _ask(engine, q["question"], method)
                record = {
                    "qid": q["id"], "type": q["type"], "doc": q["doc"], "method": method,
                    "position": i, "slot": slot, "question": q["question"],
                    "answer": res["answer"], "confidence": res.get("confidence", 0.0),
                    "reasoning": res.get("reasoning", ""), "entities": res.get("entities", []),
                    "sources": res.get("sources", []), "context": res.get("context", ""),
                    "latency_s": round(latency, 3), "attempts": attempts,
                    "failed": res["answer"].startswith(_ERROR_PREFIXES),
                }
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
                logger.info(f"[{i:3d}/{len(plan)}] {q['id']:<12} {method:<8} {latency:5.1f}s attempts={attempts}")

    _write_blinded(exp)
    logger.info("=== run finished")


def _write_blinded(exp: Path) -> None:
    """Shuffle every answer, replace the system with a random id (protocol §6)."""
    questions = {q["id"]: q for q in json.loads((exp / "qa_set.json").read_text(encoding="utf-8"))}
    records = _read_jsonl(exp / "results.jsonl")
    rng = random.Random(SEED + 1)
    rng.shuffle(records)
    judging = exp / "judging"
    judging.mkdir(exist_ok=True)
    key: Dict[str, Dict[str, str]] = {}
    with (judging / "blinded_answers.jsonl").open("w", encoding="utf-8") as out:
        for r in records:
            bid = uuid.UUID(int=rng.getrandbits(128)).hex[:10]
            key[bid] = {"qid": r["qid"], "method": r["method"]}
            q = questions[r["qid"]]
            out.write(json.dumps({
                "bid": bid, "question": q["question"], "reference": q["reference"],
                "evidence": q["evidence"], "answer": r["answer"],
            }, ensure_ascii=False) + "\n")
    (judging / "key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    logger.info(f"Blinded file written: {len(records)} answers")


# ── analyze ───────────────────────────────────────────────────────────────────

def _holm(pvalues: Dict[str, float]) -> Dict[str, float]:
    """Holm–Bonferroni adjusted p-values (step-down, monotone)."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m, running, adjusted = len(items), 0.0, {}
    for i, (name, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        adjusted[name] = running
    return adjusted


def _rank_biserial(d: np.ndarray) -> float:
    """Matched-pairs rank-biserial correlation; zero differences dropped (as in 'wilcox')."""
    nz = d[d != 0]
    if nz.size == 0:
        return 0.0
    ranks = stats.rankdata(np.abs(nz))
    pos, neg = ranks[nz > 0].sum(), ranks[nz < 0].sum()
    return float((pos - neg) / (pos + neg))


def _wilcoxon(d: np.ndarray, zero_method: str = "wilcox") -> Dict[str, Any]:
    nz = int(np.count_nonzero(d))
    if nz == 0:
        return {"statistic": None, "p_value": 1.0, "n_nonzero": 0, "note": "all differences are zero"}
    res = stats.wilcoxon(d, zero_method=zero_method, alternative="two-sided")
    return {"statistic": float(res.statistic), "p_value": float(res.pvalue), "n_nonzero": nz}


def _bootstrap_ci(d: np.ndarray) -> Tuple[float, float]:
    rng = np.random.default_rng(SEED)
    means = rng.choice(d, size=(BOOTSTRAP_RESAMPLES, d.size), replace=True).mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def _paired_summary(s: np.ndarray, r: np.ndarray) -> Dict[str, Any]:
    d = r - s
    wins, losses = int((d > 0).sum()), int((d < 0).sum())
    sign = stats.binomtest(wins, wins + losses, 0.5) if wins + losses else None
    return {
        "n": int(d.size),
        "mean_standard": float(s.mean()), "mean_refined": float(r.mean()),
        "mean_difference": float(d.mean()), "median_difference": float(np.median(d)),
        "bootstrap_95ci_mean_difference": _bootstrap_ci(d),
        "wilcoxon": _wilcoxon(d, "wilcox"),
        "wilcoxon_pratt": _wilcoxon(d, "pratt"),
        "rank_biserial_r": _rank_biserial(d),
        "refined_wins": wins, "ties": int((d == 0).sum()), "refined_losses": losses,
        "sign_test_p": float(sign.pvalue) if sign else 1.0,
    }


def analyze(exp: Path) -> None:
    questions = {q["id"]: q for q in json.loads((exp / "qa_set.json").read_text(encoding="utf-8"))}
    records = _read_jsonl(exp / "results.jsonl")
    key = json.loads((exp / "judging" / "key.json").read_text(encoding="utf-8"))
    grades = {g["bid"]: g for g in _read_jsonl(exp / "judging" / "grades.jsonl")}

    missing = sorted(set(key) - set(grades))
    bad = [b for b, g in grades.items() if float(g["score"]) not in GRADES]
    if missing or bad:
        raise SystemExit(f"Grades incomplete: {len(missing)} missing, {len(bad)} invalid scores.")

    correctness = {(v["qid"], v["method"]): float(grades[b]["score"]) for b, v in key.items()}
    scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=True)
    rows: Dict[str, Dict[str, Dict[str, float]]] = {}
    failures = []
    for r in records:
        q = questions[r["qid"]]
        rows.setdefault(r["qid"], {})[r["method"]] = {
            "correctness": correctness[(r["qid"], r["method"])],
            "token_f1": token_f1(r["answer"], q["reference"]),
            "rouge_l": scorer.score(q["reference"], r["answer"])["rougeL"].fmeasure,
            "answer_key_fact_recall": key_fact_recall(q["key_facts"], r["answer"]),
            "context_key_fact_recall": key_fact_recall(q["key_facts"], r["context"]),
            "latency_s": r["latency_s"],
            "context_chars": len(r["context"]),
        }
        if r["failed"]:
            failures.append({"qid": r["qid"], "method": r["method"], "answer": r["answer"][:200]})
    qids = sorted(q for q in rows if all(m in rows[q] for m in METHODS))
    if len(qids) != len(questions):
        raise SystemExit(f"Results incomplete: {len(qids)} of {len(questions)} questions have both systems.")

    def arr(metric: str, method: str, subset: Optional[List[str]] = None) -> np.ndarray:
        return np.array([rows[q][method][metric] for q in (subset or qids)], dtype=float)

    # primary (confirmatory)
    primary = _paired_summary(arr("correctness", "standard"), arr("correctness", "refined"))
    reject = primary["wilcoxon"]["p_value"] < ALPHA

    # secondary 1: per question type, Holm across types
    per_type = {}
    for t in QUESTION_TYPES:
        sub = [q for q in qids if questions[q]["type"] == t]
        per_type[t] = _paired_summary(arr("correctness", "standard", sub), arr("correctness", "refined", sub))
    adj = _holm({t: v["wilcoxon"]["p_value"] for t, v in per_type.items()})
    for t in per_type:
        per_type[t]["holm_p"] = adj[t]

    # secondary 2: automatic metrics, Holm across metrics
    auto_metrics = ("token_f1", "rouge_l", "answer_key_fact_recall", "context_key_fact_recall")
    secondary = {m: _paired_summary(arr(m, "standard"), arr(m, "refined")) for m in auto_metrics}
    adj = _holm({m: v["wilcoxon"]["p_value"] for m, v in secondary.items()})
    for m in secondary:
        secondary[m]["holm_p"] = adj[m]
    descriptive = {
        m: {meth: {"mean": float(arr(m, meth).mean()), "median": float(np.median(arr(m, meth)))} for meth in METHODS}
        for m in ("latency_s", "context_chars")
    }

    result = {
        "analysed_at": datetime.now(timezone.utc).isoformat(),
        "alpha": ALPHA, "n_questions": len(qids),
        "primary_correctness": primary,
        "decision": "reject H0" if reject else "fail to reject H0",
        "per_type_correctness": per_type,
        "secondary_metrics": secondary,
        "descriptive": descriptive,
        "failures": failures,
        "grade_distribution": {m: dict(Counter(correctness[(q, m)] for q in qids)) for m in METHODS},
    }
    (exp / "stats.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    with (exp / "per_question.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        cols = ("correctness", "token_f1", "rouge_l", "answer_key_fact_recall", "context_key_fact_recall", "latency_s")
        w.writerow(["qid", "type", "doc"] + [f"{c}_{m}" for c in cols for m in METHODS] + ["correctness_diff"])
        for q in qids:
            w.writerow([q, questions[q]["type"], questions[q]["doc"]]
                       + [round(rows[q][m][c], 4) for c in cols for m in METHODS]
                       + [rows[q]["refined"]["correctness"] - rows[q]["standard"]["correctness"]])

    _write_report(exp, result)
    print(f"Decision: {result['decision']} (Wilcoxon p = {primary['wilcoxon']['p_value']:.4g}). "
          f"See {exp / 'report.md'}")


def _fmt_p(p: float) -> str:
    return "< 0.001" if p < 0.001 else f"{p:.3f}"


def _write_report(exp: Path, r: Dict) -> None:
    p = r["primary_correctness"]
    lo, hi = p["bootstrap_95ci_mean_difference"]
    lines = [
        "# Results: Standard RAG vs Refined (cascaded) RAG", "",
        f"Generated by `backend/evaluation/compare.py analyze` on {r['analysed_at'][:19]} UTC from the files in this folder.",
        "Protocol: `protocol.md` (pre-registered). Raw data: `results.jsonl`, `judging/`, `per_question.csv`.", "",
        "## Primary result (confirmatory)", "",
        f"- Questions: **{r['n_questions']}** (paired; each answered by both systems over the same index).",
        f"- Mean correctness: Standard **{p['mean_standard']:.3f}**, Refined **{p['mean_refined']:.3f}**.",
        f"- Mean paired difference (Refined − Standard): **{p['mean_difference']:+.3f}**, "
        f"95% bootstrap CI [{lo:+.3f}, {hi:+.3f}].",
        f"- Wilcoxon signed-rank (two-sided, zero_method=wilcox): W = {p['wilcoxon']['statistic']}, "
        f"n(non-zero) = {p['wilcoxon']['n_nonzero']}, **p = {_fmt_p(p['wilcoxon']['p_value'])}**.",
        f"- Effect size (matched-pairs rank-biserial r): **{p['rank_biserial_r']:+.3f}**.",
        f"- Wins / ties / losses for Refined: {p['refined_wins']} / {p['ties']} / {p['refined_losses']}; "
        f"exact sign test p = {_fmt_p(p['sign_test_p'])}; Wilcoxon (Pratt) p = {_fmt_p(p['wilcoxon_pratt']['p_value'])}.",
        "", f"**Decision at α = {r['alpha']}: {r['decision']}.**", "",
        "## Secondary: correctness by question type (exploratory)", "",
        "| Type | n | Standard | Refined | Diff | 95% CI | Wilcoxon p | Holm p | r | W/T/L |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t, v in r["per_type_correctness"].items():
        a, b = v["bootstrap_95ci_mean_difference"]
        lines.append(f"| {t} | {v['n']} | {v['mean_standard']:.3f} | {v['mean_refined']:.3f} | "
                     f"{v['mean_difference']:+.3f} | [{a:+.3f}, {b:+.3f}] | {_fmt_p(v['wilcoxon']['p_value'])} | "
                     f"{_fmt_p(v['holm_p'])} | {v['rank_biserial_r']:+.2f} | "
                     f"{v['refined_wins']}/{v['ties']}/{v['refined_losses']} |")
    lines += ["", "## Secondary: automatic metrics (exploratory)", "",
              "| Metric | Standard | Refined | Diff | 95% CI | Wilcoxon p | Holm p | r |",
              "|---|---|---|---|---|---|---|---|"]
    for m, v in r["secondary_metrics"].items():
        a, b = v["bootstrap_95ci_mean_difference"]
        lines.append(f"| {m} | {v['mean_standard']:.3f} | {v['mean_refined']:.3f} | {v['mean_difference']:+.3f} | "
                     f"[{a:+.3f}, {b:+.3f}] | {_fmt_p(v['wilcoxon']['p_value'])} | {_fmt_p(v['holm_p'])} | "
                     f"{v['rank_biserial_r']:+.2f} |")
    d = r["descriptive"]
    lines += ["", "## Cost", "",
              "| | Standard | Refined |", "|---|---|---|",
              f"| Mean latency (s) | {d['latency_s']['standard']['mean']:.2f} | {d['latency_s']['refined']['mean']:.2f} |",
              f"| Median latency (s) | {d['latency_s']['standard']['median']:.2f} | {d['latency_s']['refined']['median']:.2f} |",
              f"| Mean context length (chars) | {d['context_chars']['standard']['mean']:.0f} | {d['context_chars']['refined']['mean']:.0f} |",
              "", "## Grade distribution", "",
              f"- Standard: {r['grade_distribution']['standard']}",
              f"- Refined: {r['grade_distribution']['refined']}",
              "", "## Failures", "",
              (f"{len(r['failures'])} answer(s) failed after retries (scored 0): "
               + ", ".join(f"{f['qid']}/{f['method']}" for f in r["failures"])) if r["failures"] else "None.",
              ""]
    discussion = exp / "discussion.md"
    if discussion.exists():
        lines += [discussion.read_text(encoding="utf-8")]
    (exp / "report.md").write_text("\n".join(lines), encoding="utf-8")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Standard vs Refined RAG experiment")
    parser.add_argument("command", choices=["validate", "run", "analyze"])
    parser.add_argument("--exp", required=True, help="Experiment folder (holds protocol.md and qa/)")
    parser.add_argument("--reuse-index", action="store_true", help="run: load index_snapshot.json instead of re-indexing")
    args = parser.parse_args()
    exp = Path(args.exp)
    if args.command == "validate":
        validate(exp)
    elif args.command == "run":
        set_strict_embeddings(True)   # a random-vector fallback would silently corrupt the results
        asyncio.run(run(exp, args.reuse_index))
    else:
        analyze(exp)


if __name__ == "__main__":
    main()
