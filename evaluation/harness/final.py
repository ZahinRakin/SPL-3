"""
Cross-dataset summary (EVALUATION_PLAN.md §5, §6): the four primary hypotheses with
Holm–Bonferroni, and judge agreement (Cohen's κ) once a second judge's or the owner's grades exist.

Reads benchmarks/<dataset>/results.json (written by analyze.py) and, if present,
judging/grades_codex_<name>_*.jsonl and judging/grades_owner_<name>.jsonl.
Writes evaluation/results_summary.json. Re-run after Codex / the owner have graded.

Usage:  python -m evaluation.harness.final
"""
import json
from typing import Dict, List, Optional

from evaluation.harness import metrics as M
from evaluation.harness.common import EVAL_DIR, bench, now, read_jsonl, run_log

PRIMARY = {
    "H1": ("musique", "H1"),
    "H2": ("quality", "H2"),
    "H3": ("graphrag_bench_novel", "H3"),
    "H4": ("multihop_rag", "H4"),
}

# Graded sets that a second judge (Codex) and the owner can grade; correctness and coverage use
# per-item scores, pairwise uses winners.
JUDGED_SETS = [
    ("musique", "correctness_S2_S0", "score"),
    ("multihop_rag", "correctness_S5_S0", "score"),
    ("graphrag_bench_novel", "coverage_S4_S2", "coverage"),
    ("graphrag_bench_novel", "pairwise_S4_S2", "winner"),
]


def _grades(dataset: str, name: str, judge: str, kind: str) -> Dict[str, object]:
    jdir = bench(dataset) / "judging"
    files = sorted(jdir.glob(f"grades_{judge}_{name}_*.jsonl"))
    if judge == "owner":
        files = [jdir / f"grades_owner_{name}.jsonl"]
    out = {}
    for p in files:
        if not p.exists():
            continue
        for g in read_jsonl(p):
            if kind == "score":
                out[g["bid"]] = float(g["score"])
            elif kind == "coverage":
                att = g["attributed"]
                out[g["bid"]] = round(sum(att) / len(att), 4) if att else 0.0
            else:
                out[g["bid"]] = g["winner"]
    return out


def agreement(dataset: str, name: str, kind: str) -> Dict:
    judges = {j: _grades(dataset, name, j, kind) for j in ("claude", "codex", "owner")}
    res = {"n": {j: len(v) for j, v in judges.items()}}
    for a, b in (("claude", "codex"), ("claude", "owner"), ("codex", "owner")):
        common = sorted(set(judges[a]) & set(judges[b]))
        if len(common) < 10:
            continue
        x = [judges[a][k] for k in common]
        y = [judges[b][k] for k in common]
        if kind == "winner":
            res[f"kappa_{a}_{b}"] = {"kappa": M.cohen_kappa(x, y), "n": len(common), "weights": "none"}
        elif kind == "coverage":
            res[f"agreement_{a}_{b}"] = {"mean_abs_diff": sum(abs(p - q) for p, q in zip(x, y)) / len(x),
                                         "n": len(common)}
        else:
            res[f"kappa_{a}_{b}"] = {"kappa": M.cohen_kappa(x, y, "quadratic"), "n": len(common),
                                     "weights": "quadratic"}
    return res


def main() -> Dict:
    hyps, pvals = {}, {}
    for h, (ds, key) in PRIMARY.items():
        p = bench(ds) / "results.json"
        r = json.loads(p.read_text(encoding="utf-8")).get(key) if p.exists() else None
        if not r:
            hyps[h] = {"dataset": ds, "status": "not run"}
            continue
        hyps[h] = {"dataset": ds, "hypothesis": r["hypothesis"], "n": r["n"], "mean_a": r["mean_a"],
                   "mean_b": r["mean_b"], "mean_diff": r["mean_diff"], "ci95": [r["ci_low"], r["ci_high"]],
                   "wtl": r["wtl"], "test": r["test"], "p": r["stat"]["p"]}
        pvals[h] = r["stat"]["p"]
    for h, v in M.holm(pvals).items():
        hyps[h]["p_holm"] = v["p_holm"]
        hyps[h]["significant_after_holm"] = v["reject"]
        d = hyps[h]["mean_diff"]
        hyps[h]["direction"] = "favours the cascade component" if d > 0 else ("against it" if d < 0 else "none")
    agree = {f"{ds}/{name}": agreement(ds, name, kind) for ds, name, kind in JUDGED_SETS}
    out = {"created_at": now(), "alpha": 0.05, "correction": "Holm–Bonferroni over H1–H4",
           "hypotheses": hyps, "judge_agreement": agree}
    (EVAL_DIR / "results_summary.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    run_log("final summary written (`results_summary.json`): " + ", ".join(
        f"{h} p={v.get('p', float('nan')):.3g} p_holm={v.get('p_holm', float('nan')):.3g}" for h, v in hyps.items()))
    return out


if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
