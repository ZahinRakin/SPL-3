"""
Results per dataset (EVALUATION_PLAN.md §5): reads runs/test/*.jsonl, rankings and (unblinded)
judge grades, writes results.json and results.md.

Every comparison reports the mean difference, a 95 % paired-bootstrap CI (10,000 resamples,
seed 42), win/tie/loss and the pre-registered test. Only the dataset's primary hypothesis is
confirmatory; everything else is labelled exploratory.

Usage:  python -m evaluation.harness.analyze --dataset musique
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np

from evaluation.harness import metrics as M
from evaluation.harness.common import bench, now, read_jsonl, run_log

SYSTEMS = ["C0", "S0", "S1", "S2", "S3", "S4", "S5"]


def load_runs(dataset: str, split: str = "test", tag: str = "") -> Dict[str, Dict[str, Dict]]:
    out = {}
    for s in SYSTEMS:
        p = bench(dataset) / "runs" / split / f"{s}{'_' + tag if tag else ''}.jsonl"
        if p.exists():
            out[s] = {r["qid"]: r for r in read_jsonl(p)}
    return out


def with_retries(dataset: str, runs: Dict[str, Dict[str, Dict]], split: str = "test") -> Dict[str, Dict[str, Dict]]:
    """A copy of runs where a failed answer is replaced by its retry (runs/<split>/<sys>_retry.jsonl),
    if one exists. Primary results follow rule 6 (failure = 0); this is the sensitivity check."""
    merged = {}
    for s, rows in runs.items():
        p = bench(dataset) / "runs" / split / f"{s}_retry.jsonl"
        retry = {r["qid"]: r for r in read_jsonl(p)} if p.exists() else {}
        merged[s] = {q: (retry[q] if r.get("error") and q in retry else r) for q, r in rows.items()}
    return merged


def load_ranks(dataset: str, split: str = "test") -> Dict[str, Dict[str, Dict]]:
    out = {}
    for s in ("S0", "S1", "S2", "S3"):
        p = bench(dataset) / "runs" / split / f"rank_{s}.jsonl"
        if p.exists():
            out[s] = {r["qid"]: r for r in read_jsonl(p)}
    return out


def score_rows(rows: Dict[str, Dict], fn: Callable[[Dict], float]) -> Dict[str, float]:
    """Per-question score; a failed query (error) scores 0 (rule 6)."""
    return {q: (0.0 if r.get("error") else fn(r)) for q, r in rows.items()}


def compare(a: Dict[str, float], b: Dict[str, float], test: str) -> Dict:
    qids = sorted(set(a) & set(b))
    x = [a[q] for q in qids]
    y = [b[q] for q in qids]
    res = {"n": len(qids), "mean_a": float(np.mean(x)), "mean_b": float(np.mean(y)),
           **M.paired_bootstrap_ci(x, y), "wtl": M.win_tie_loss(x, y), "test": test}
    res["stat"] = M.mcnemar_exact(x, y) if test == "mcnemar" else M.wilcoxon(x, y)
    return res


def table(rows: List[List], header: List[str]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def fmt(x: Optional[float], d: int = 3) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}f}"


def describe(c: Dict) -> str:
    p = c["stat"]["p"]
    return (f"mean {fmt(c['mean_a'])} vs {fmt(c['mean_b'])}, diff {c['mean_diff']:+.3f} "
            f"[95% CI {c['ci_low']:+.3f}, {c['ci_high']:+.3f}], W/T/L {c['wtl']['win']}/{c['wtl']['tie']}/"
            f"{c['wtl']['loss']}, {c['test']} p = {p:.4g}, n = {c['n']}")


# ── run statistics shared by all datasets ─────────────────────────────────────

def run_stats(runs: Dict[str, Dict[str, Dict]]) -> Dict[str, Dict]:
    out = {}
    for s, rows in runs.items():
        r = list(rows.values())
        fresh = [x for x in r if not x["cache_hit"]]
        out[s] = {
            "n": len(r), "errors": sum(1 for x in r if x["error"]),
            "empty_answers": sum(1 for x in r if not x["error"] and not str(x["answer"]).strip()),
            "mean_context_chars": float(np.mean([x["context_chars"] for x in r])) if r else 0,
            "mean_retrieval_s": float(np.mean([x["retrieval_s"] for x in r])) if r else 0,
            "mean_generation_s_uncached": float(np.mean([x["generation_s"] for x in fresh])) if fresh else None,
            "uncached_answers": len(fresh),
        }
    return out


# ── judge grades (after unblinding) ───────────────────────────────────────────

def unblind(dataset: str, name: str, judge: str = "claude") -> Dict[str, Dict[str, float]]:
    """system → qid → score, from grades_<judge>_<name>_*.jsonl and key_<name>.json.
    Must only be called after the grade files are hashed (blind.hash_grades)."""
    jdir = bench(dataset) / "judging"
    if not (jdir / f"grades_{judge}_{name}_ALL.sha256").exists():
        raise SystemExit(f"grades_{judge}_{name} not hashed yet: hash before unblinding (rule 5)")
    key = json.loads((jdir / f"key_{name}.json").read_text(encoding="utf-8"))["key"]
    out: Dict[str, Dict[str, float]] = defaultdict(dict)
    for p in sorted(jdir.glob(f"grades_{judge}_{name}_*.jsonl")):
        for g in read_jsonl(p):
            k = key[g["bid"]]
            if "score" in g:
                out[k["system"]][k["qid"]] = float(g["score"])
            elif "attributed" in g:
                att = [int(v) for v in g["attributed"]]
                out[k["system"]][k["qid"]] = float(np.mean(att)) if att else float("nan")
    return out


def unblind_pairwise(dataset: str, name: str, judge: str = "claude") -> Dict:
    jdir = bench(dataset) / "judging"
    if not (jdir / f"grades_{judge}_{name}_ALL.sha256").exists():
        raise SystemExit("hash before unblinding (rule 5)")
    meta_key = json.loads((jdir / f"key_{name}.json").read_text(encoding="utf-8"))
    key, (sys_a, sys_b) = meta_key["key"], meta_key["meta"]["systems"]
    verdicts: Dict[str, List[str]] = defaultdict(list)   # qid → winners (system id or "tie"), both orders
    for p in sorted(jdir.glob(f"grades_{judge}_{name}_*.jsonl")):
        for g in read_jsonl(p):
            k = key[g["bid"]]
            w = g["winner"]
            verdicts[k["qid"]].append("tie" if w == "tie" else k[w])
    per_q, consistent = {}, 0
    for q, v in verdicts.items():
        if len(v) == 2 and v[0] == v[1]:
            consistent += 1
            per_q[q] = v[0]
        else:
            per_q[q] = "tie"   # a win counts only if it holds in both orders (§6)
    wins_a = sum(1 for v in per_q.values() if v == sys_a)
    wins_b = sum(1 for v in per_q.values() if v == sys_b)
    n_dec = wins_a + wins_b
    from scipy import stats
    p = 1.0 if n_dec == 0 else float(stats.binomtest(wins_a, n_dec, 0.5).pvalue)
    return {"systems": [sys_a, sys_b], "pairs": len(per_q), f"wins_{sys_a}": wins_a, f"wins_{sys_b}": wins_b,
            "ties": len(per_q) - n_dec, "position_consistency": consistent / max(len(per_q), 1),
            "sign_test_p": p, "per_question": per_q}


# ── per dataset ───────────────────────────────────────────────────────────────

def _retrieval_section(dataset: str, metric_keys: List[str]) -> Dict:
    ranks = load_ranks(dataset)
    out: Dict = {"systems": {}, "comparisons": {}}
    for s, rows in ranks.items():
        out["systems"][s] = {k: float(np.nanmean([r[k] for r in rows.values() if k in r])) for k in metric_keys}
    for a, b in [("S2", "S0"), ("S2", "S1"), ("S0", "S1"), ("S3", "S2")]:
        if a in ranks and b in ranks:
            for k in metric_keys:
                sa = {q: r[k] for q, r in ranks[a].items() if k in r and not np.isnan(r[k])}
                sb = {q: r[k] for q, r in ranks[b].items() if k in r and not np.isnan(r[k])}
                out["comparisons"][f"{a}_vs_{b}:{k}"] = compare(sa, sb, "wilcoxon")
    return out


def _judge_section(dataset: str, name: str, pair: List[str]) -> Optional[Dict]:
    jdir = bench(dataset) / "judging"
    if not (jdir / f"grades_claude_{name}_ALL.sha256").exists():
        return None
    g = unblind(dataset, name)
    a, b = pair
    return {"mean": {s: float(np.nanmean(list(v.values()))) for s, v in g.items()},
            "n": {s: len(v) for s, v in g.items()},
            "comparison": compare(g[a], g[b], "wilcoxon"), "scores": g}


def _by_type(runs: Dict[str, Dict[str, Dict]], scores: Dict[str, Dict[str, float]]) -> Dict:
    out: Dict = defaultdict(dict)
    for s, d in scores.items():
        groups = defaultdict(list)
        for q, v in d.items():
            groups[runs[s][q]["type"]].append(v)
        for t, v in groups.items():
            out[t][s] = float(np.mean(v))
    return dict(out)


def analyze_musique(dataset: str = "musique") -> Dict:
    runs = load_runs(dataset)
    res: Dict = {"dataset": dataset, "analyzed_at": now()}
    res["retrieval"] = _retrieval_section(dataset, ["recall@2", "recall@5", "mrr@10"])
    em = {s: score_rows(r, lambda x: M.exact_match(x["answer"], x["gold_answers"])) for s, r in runs.items()}
    f1 = {s: score_rows(r, lambda x: M.token_f1(x["answer"], x["gold_answers"])) for s, r in runs.items()}
    res["qa"] = {s: {"EM": float(np.mean(list(em[s].values()))), "F1": float(np.mean(list(f1[s].values())))}
                 for s in runs}
    rr = with_retries(dataset, runs)
    res["qa_with_retries"] = {
        s: {"EM": float(np.mean([M.exact_match(x["answer"], x["gold_answers"]) if not x["error"] else 0.0 for x in r.values()])),
            "F1": float(np.mean([M.token_f1(x["answer"], x["gold_answers"]) if not x["error"] else 0.0 for x in r.values()])),
            "errors_left": sum(1 for x in r.values() if x["error"])}
        for s, r in rr.items()}
    res["qa_comparisons"] = {f"{a}_vs_{b}:{m}": compare(d[a], d[b], "wilcoxon")
                             for a, b in [("S2", "S0"), ("S5", "S0"), ("S4", "S2"), ("S2", "S1"), ("S0", "C0")]
                             if a in runs and b in runs for m, d in (("EM", em), ("F1", f1))}
    ranks = load_ranks(dataset)
    if "S2" in ranks and "S0" in ranks:
        a = {q: r["recall@5"] for q, r in ranks["S2"].items()}
        b = {q: r["recall@5"] for q, r in ranks["S0"].items()}
        res["H1"] = {"hypothesis": "Graph ranking helps multi-hop retrieval (S2 vs S0, Recall@5, test)",
                     **compare(a, b, "wilcoxon")}
    types = defaultdict(list)
    for q, r in ranks.get("S0", {}).items():
        types[r["type"]].append(q)
    res["by_type_recall@5"] = {t: {s: float(np.mean([ranks[s][q]["recall@5"] for q in qs])) for s in ranks}
                               for t, qs in sorted(types.items())}
    res["by_type_F1"] = _by_type(runs, f1)
    res["run_stats"] = run_stats(runs)
    res["judge"] = _judge_section(dataset, "correctness_S2_S0", ["S2", "S0"])
    return res


def analyze_quality() -> Dict:
    runs = load_runs("quality")
    acc = {s: score_rows(r, lambda x: M.mc_correct(x["answer"], x["gold_answers"][0])) for s, r in runs.items()}
    res: Dict = {"dataset": "quality", "analyzed_at": now()}
    res["accuracy"] = {s: float(np.mean(list(v.values()))) for s, v in acc.items()}
    hard = {s: {q: v for q, v in d.items() if runs[s][q]["type"] == "hard"} for s, d in acc.items()}
    res["accuracy_hard"] = {s: float(np.mean(list(v.values()))) for s, v in hard.items()}
    res["unparseable"] = {s: sum(1 for x in r.values() if not x["error"] and not x["answer"]) for s, r in runs.items()}
    if "S3" in acc and "S2" in acc:
        res["H2"] = {"hypothesis": "RAPTOR summaries help long documents (S3 vs S2, accuracy, test)",
                     **compare(acc["S3"], acc["S2"], "mcnemar")}
    pairs = [("S2", "S0"), ("S5", "S0"), ("S4", "S2"), ("S0", "S1"), ("S0", "C0"), ("S5", "S3")]
    res["comparisons"] = {f"{a}_vs_{b}": compare(acc[a], acc[b], "mcnemar") for a, b in pairs if a in acc and b in acc}
    res["comparisons_hard"] = {f"{a}_vs_{b}": compare(hard[a], hard[b], "mcnemar")
                               for a, b in [("S3", "S2"), ("S5", "S0")] if a in hard and b in hard}
    res["run_stats"] = run_stats(runs)
    return res


def analyze_multihop_rag() -> Dict:
    ds = "multihop_rag"
    runs = load_runs(ds)
    off = {s: score_rows(r, lambda x: M.multihop_rag_correct(x["answer"], x["gold_answers"][0])) for s, r in runs.items()}
    norm = {s: score_rows(r, lambda x: M.multihop_rag_correct_normalized(x["answer"], x["gold_answers"][0]))
            for s, r in runs.items()}
    res: Dict = {"dataset": ds, "analyzed_at": now()}
    res["retrieval"] = _retrieval_section(ds, ["hits@4", "hits@10", "mrr@10"])
    res["accuracy_official"] = {s: float(np.mean(list(v.values()))) for s, v in off.items()}
    res["accuracy_normalized"] = {s: float(np.mean(list(v.values()))) for s, v in norm.items()}
    res["accuracy_by_type"] = _by_type(runs, off)
    if "S5" in off and "S0" in off:
        res["H4"] = {"hypothesis": "Full system ≥ dense on real cross-doc questions (S5 vs S0, accuracy, test)",
                     **compare(off["S5"], off["S0"], "mcnemar")}
    pairs = [("S2", "S0"), ("S3", "S2"), ("S4", "S2"), ("S0", "S1"), ("S0", "C0")]
    res["comparisons"] = {f"{a}_vs_{b}": compare(off[a], off[b], "mcnemar") for a, b in pairs if a in off and b in off}
    res["run_stats"] = run_stats(runs)
    res["judge"] = _judge_section(ds, "correctness_S5_S0", ["S5", "S0"])
    return res


def analyze_novel() -> Dict:
    ds = "graphrag_bench_novel"
    runs = load_runs(ds)
    res: Dict = {"dataset": ds, "analyzed_at": now()}
    from rouge_score import rouge_scorer   # already an evaluation dependency
    scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=True)
    rouge = {s: score_rows(r, lambda x: scorer.score(x["gold_answers"][0], x["answer"])["rougeL"].fmeasure)
             for s, r in runs.items()}
    res["rougeL_by_type"] = _by_type(runs, rouge)
    cov = _judge_section(ds, "coverage_S4_S2", ["S4", "S2"])
    if cov:
        res["H3"] = {"hypothesis": "Community summaries help broad questions (S4 vs S2, judge coverage, "
                                   "Contextual Summarize, test)", **cov["comparison"]}
        res["coverage"] = {k: v for k, v in cov.items() if k != "scores"}
    if (bench(ds) / "judging" / "grades_claude_pairwise_S4_S2_ALL.sha256").exists():
        pw = unblind_pairwise(ds, "pairwise_S4_S2")
        res["pairwise_S4_vs_S2"] = {k: v for k, v in pw.items() if k != "per_question"}
    res["run_stats"] = run_stats(runs)
    return res


ANALYZERS = {"musique": analyze_musique, "quality": analyze_quality,
             "multihop_rag": analyze_multihop_rag, "graphrag_bench_novel": analyze_novel}


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.floating, np.integer, np.bool_)):
        return x.item()
    if isinstance(x, float) and np.isnan(x):
        return None
    return x


def main(dataset: str) -> Dict:
    res = _jsonable(ANALYZERS[dataset]())
    (bench(dataset) / "results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    run_log(f"analysis `{dataset}` written to results.json")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    out = main(ap.parse_args().dataset)
    print(json.dumps({k: v for k, v in out.items() if k not in ("judge", "run_stats")}, indent=1)[:8000])
