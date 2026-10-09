"""
Blinded judging files (EVALUATION_PLAN.md §6, rule 5).

The judge only ever sees judging/blinded_*.jsonl and the rubric. Item ids ("bid") are random
(seed 42) and items are shuffled, so neither the system nor the question order can be inferred.
The key (bid → qid, system) is written to judging/key.json, which the judge must never open; it is
read only by unblind(), after the grade files have been hashed.

Modes
  correctness  question, gold answer(s), one system answer          → score 0 / 0.5 / 1
  pairwise     question, reference + evidence, answers A and B, each pair twice with A/B swapped
  coverage     question, reference facts (extracted once per question), one system answer
               → GraphRAG-Bench's coverage metric (share of facts covered)

Usage:
  python -m evaluation.harness.blind correctness --dataset musique --systems S2,S0 --sample 200
  python -m evaluation.harness.blind pairwise --dataset graphrag_bench_novel --systems S4,S2 --types "Contextual Summarize,Creative Generation"
  python -m evaluation.harness.blind owner --dataset musique --n 100
"""
import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from evaluation.harness.common import SEED, bench, now, read_jsonl, run_log, sha256_file, write_jsonl

BATCH = 50


def _runs(dataset: str, split: str, sid: str, tag: str = "") -> Dict[str, Dict]:
    path = bench(dataset) / "runs" / split / f"{sid}{'_' + tag if tag else ''}.jsonl"
    rows = {r["qid"]: r for r in read_jsonl(path)}
    return rows


def _stratified_sample(qids: List[str], types: Dict[str, str], n: int, rng: random.Random) -> List[str]:
    """Proportional by type (largest remainder), seed 42."""
    if n >= len(qids):
        return sorted(qids)
    by_type: Dict[str, List[str]] = defaultdict(list)
    for q in sorted(qids):
        by_type[types[q]].append(q)
    quota = {t: n * len(v) / len(qids) for t, v in by_type.items()}
    counts = {t: int(x) for t, x in quota.items()}
    for t in sorted(quota, key=lambda t: quota[t] - counts[t], reverse=True)[: n - sum(counts.values())]:
        counts[t] += 1
    return sorted(q for t in sorted(by_type) for q in rng.sample(by_type[t], counts[t]))


def _bid(rng: random.Random, used: set) -> str:
    while True:
        b = "%08x" % rng.getrandbits(32)
        if b not in used:
            used.add(b)
            return b


def _write_batches(jdir: Path, prefix: str, items: List[Dict]) -> List[str]:
    files = []
    for i in range(0, len(items), BATCH):
        path = jdir / f"blinded_{prefix}_{i // BATCH + 1:03d}.jsonl"
        write_jsonl(path, items[i:i + BATCH])
        files.append(path.name)
    return files


def _save_key(jdir: Path, name: str, key: Dict, meta: Dict) -> None:
    (jdir / f"key_{name}.json").write_text(json.dumps({"meta": meta, "key": key}, indent=1), encoding="utf-8")


# ── correctness ───────────────────────────────────────────────────────────────

def correctness(dataset: str, systems: List[str], sample: int, split: str = "test", name: str = "") -> None:
    rng = random.Random(SEED)
    runs = {s: _runs(dataset, split, s) for s in systems}
    common = sorted(set.intersection(*(set(r) for r in runs.values())))
    types = {q: runs[systems[0]][q]["type"] for q in common}
    qids = _stratified_sample(common, types, sample, rng)
    items, key, used = [], {}, set()
    for q in qids:
        for s in systems:
            row = runs[s][q]
            b = _bid(rng, used)
            items.append({"bid": b, "question": row["question"], "gold_answers": row["gold_answers"],
                          "answer": row["answer"] if not row["error"] else "",
                          "null_query": row["type"] == "null_query"})
            key[b] = {"qid": q, "system": s, "type": row["type"]}
    rng.shuffle(items)
    name = name or f"correctness_{'_'.join(systems)}"
    jdir = bench(dataset) / "judging"
    jdir.mkdir(exist_ok=True)
    files = _write_batches(jdir, name, items)
    meta = {"mode": "correctness", "dataset": dataset, "split": split, "systems": systems,
            "questions": len(qids), "items": len(items), "batches": files, "created_at": now(), "seed": SEED}
    _save_key(jdir, name, key, meta)
    run_log(f"blinded `{dataset}` {name}: {len(qids)} questions × {len(systems)} systems = {len(items)} items "
            f"in {len(files)} batches")
    print(json.dumps(meta, indent=1))


# ── pairwise (GraphRAG-Bench levels 3–4) ─────────────────────────────────────

def pairwise(dataset: str, systems: List[str], types: List[str], split: str = "test", name: str = "",
             limit: int = 0) -> None:
    a_sys, b_sys = systems
    rng = random.Random(SEED)
    ra, rb = _runs(dataset, split, a_sys), _runs(dataset, split, b_sys)
    qs = {q["qid"]: q for q in read_jsonl(bench(dataset) / "questions.jsonl")}
    qids = sorted(q for q in set(ra) & set(rb) if ra[q]["type"] in types)
    if limit:
        qids = _stratified_sample(qids, {q: ra[q]["type"] for q in qids}, limit, rng)
    items, key, used = [], {}, set()
    for q in qids:
        for first, second in ((a_sys, b_sys), (b_sys, a_sys)):
            b = _bid(rng, used)
            runs = {a_sys: ra, b_sys: rb}
            items.append({"bid": b, "question": qs[q]["question"], "reference_answer": qs[q]["answers"][0],
                          "evidence": qs[q].get("evidence") or [],
                          "answer_A": runs[first][q]["answer"] if not runs[first][q]["error"] else "",
                          "answer_B": runs[second][q]["answer"] if not runs[second][q]["error"] else ""})
            key[b] = {"qid": q, "A": first, "B": second, "type": ra[q]["type"]}
    rng.shuffle(items)
    name = name or f"pairwise_{a_sys}_{b_sys}"
    jdir = bench(dataset) / "judging"
    jdir.mkdir(exist_ok=True)
    files = _write_batches(jdir, name, items)
    meta = {"mode": "pairwise", "dataset": dataset, "split": split, "systems": systems, "types": types,
            "questions": len(qids), "items": len(items), "batches": files, "created_at": now(), "seed": SEED}
    _save_key(jdir, name, key, meta)
    run_log(f"blinded `{dataset}` {name}: {len(qids)} pairs × 2 orders = {len(items)} items in {len(files)} batches")
    print(json.dumps(meta, indent=1))


# ── coverage (GraphRAG-Bench Contextual Summarize) ────────────────────────────

def coverage_facts(dataset: str, types: List[str], split: str = "test") -> None:
    """Stage 1: one file of (question, reference) per question for fact extraction. Contains no
    system output, so it needs no blinding; the extracted facts are reused for every system."""
    qs = [q for q in read_jsonl(bench(dataset) / "questions.jsonl") if q["type"] in types]
    split_ids = set(l.strip() for l in open(bench(dataset) / "splits" / f"{split}.txt", encoding="utf-8"))
    rows = [{"fid": q["qid"], "question": q["question"], "reference": q["answers"][0]}
            for q in qs if q["qid"] in split_ids]
    jdir = bench(dataset) / "judging"
    jdir.mkdir(exist_ok=True)
    files = _write_batches(jdir, "facts", rows)
    run_log(f"coverage stage 1 `{dataset}`: {len(rows)} references to split into facts, {len(files)} batches")
    print(files)


def coverage(dataset: str, systems: List[str], types: List[str], split: str = "test", name: str = "") -> None:
    """Stage 2: blinded (question, reference facts, answer) items for every system."""
    rng = random.Random(SEED)
    jdir = bench(dataset) / "judging"
    facts = {}
    for p in sorted(jdir.glob("facts_claude_*.jsonl")):
        for r in read_jsonl(p):
            facts[r["fid"]] = r["facts"]
    runs = {s: _runs(dataset, split, s) for s in systems}
    qids = sorted(q for q in set.intersection(*(set(r) for r in runs.values()))
                  if runs[systems[0]][q]["type"] in types and facts.get(q))
    items, key, used = [], {}, set()
    for q in qids:
        for s in systems:
            row = runs[s][q]
            b = _bid(rng, used)
            items.append({"bid": b, "question": row["question"], "reference_facts": facts[q],
                          "response": row["answer"] if not row["error"] else ""})
            key[b] = {"qid": q, "system": s, "type": row["type"]}
    rng.shuffle(items)
    name = name or f"coverage_{'_'.join(systems)}"
    files = _write_batches(jdir, name, items)
    meta = {"mode": "coverage", "dataset": dataset, "split": split, "systems": systems, "types": types,
            "questions": len(qids), "items": len(items), "batches": files, "created_at": now(), "seed": SEED}
    _save_key(jdir, name, key, meta)
    run_log(f"blinded `{dataset}` {name}: {len(qids)} questions × {len(systems)} systems = {len(items)} items")
    print(json.dumps(meta, indent=1))


# ── owner's human check ───────────────────────────────────────────────────────

def owner(dataset: str, name: str, n: int = 100) -> None:
    """A random 100 items (seed 42) of an existing blinded set, for the owner to grade blind."""
    jdir = bench(dataset) / "judging"
    items = [r for p in sorted(jdir.glob(f"blinded_{name}_*.jsonl")) for r in read_jsonl(p)]
    picked = random.Random(SEED).sample(items, min(n, len(items)))
    write_jsonl(jdir / f"owner_{name}.jsonl", picked)
    run_log(f"owner check `{dataset}` {name}: {len(picked)} items in judging/owner_{name}.jsonl")


# ── grade integrity ───────────────────────────────────────────────────────────

def hash_grades(dataset: str, pattern: str) -> str:
    """sha256 of every grade file matching pattern, written before the key is opened (rule 5)."""
    jdir = bench(dataset) / "judging"
    lines = [f"{sha256_file(p)}  {p.name}" for p in sorted(jdir.glob(pattern))]
    out = jdir / f"{pattern.replace('*', 'ALL').replace('.jsonl', '')}.sha256"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run_log(f"grades hashed `{dataset}` {pattern}: {len(lines)} files → `{out.name}` (before unblinding)")
    return out.name


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["correctness", "pairwise", "facts", "coverage", "owner", "hash"])
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--systems", default="")
    ap.add_argument("--sample", type=int, default=200)
    ap.add_argument("--types", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--pattern", default="")
    a = ap.parse_args()
    systems = [s for s in a.systems.split(",") if s]
    types = [t for t in a.types.split(",") if t]
    if a.mode == "correctness":
        correctness(a.dataset, systems, a.sample, name=a.name)
    elif a.mode == "pairwise":
        pairwise(a.dataset, systems, types, name=a.name, limit=a.limit)
    elif a.mode == "facts":
        coverage_facts(a.dataset, types)
    elif a.mode == "coverage":
        coverage(a.dataset, systems, types, name=a.name)
    elif a.mode == "owner":
        owner(a.dataset, a.name, a.n)
    else:
        print(hash_grades(a.dataset, a.pattern))
