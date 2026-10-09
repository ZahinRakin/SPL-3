"""
Dataset adapters (EVALUATION_PLAN.md §3, §8 step 2): raw downloads → one common format.

  corpus.jsonl     {doc_id, title, text}
  questions.jsonl  {qid, question, answers[], gold_doc_ids[], type, options?}
  splits/dev.txt, splits/test.txt   question ids (QuALITY: by article), seed 42

Usage:  python -m backend.evaluation.harness.adapters [dataset ...]
"""
import ast
import json
import random
import sys
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

from backend.evaluation.harness.common import SEED, bench, run_log, sha256_file, write_jsonl

Rows = Tuple[List[Dict], List[Dict]]


def _load(path) -> object:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── MuSiQue / 2Wiki (HippoRAG 2 release) ─────────────────────────────────────
# The corpus has no ids; a passage's id is its position. Gold passages are matched to the
# corpus by exact (title, text), as HippoRAG 2's own evaluation does.

def _hipporag_corpus(name: str, file: str) -> Tuple[List[Dict], Dict[Tuple[str, str], str]]:
    raw = _load(bench(name) / "data" / f"{file}_corpus.json")
    corpus = [{"doc_id": str(i), "title": p["title"], "text": p["text"]} for i, p in enumerate(raw)]
    return corpus, {(p["title"], p["text"]): p["doc_id"] for p in corpus}


def musique() -> Rows:
    corpus, by_text = _hipporag_corpus("musique", "musique")
    questions = []
    for q in _load(bench("musique") / "data" / "musique.json"):
        gold = [by_text[(p["title"], p["paragraph_text"])] for p in q["paragraphs"] if p["is_supporting"]]
        questions.append({
            "qid": q["id"], "question": q["question"],
            "answers": [q["answer"]] + list(q.get("answer_aliases") or []),
            "gold_doc_ids": gold, "type": q["id"].split("__")[0],
        })
    return corpus, questions


def twowiki() -> Rows:
    corpus, by_text = _hipporag_corpus("2wiki", "2wikimultihopqa")
    questions = []
    for q in _load(bench("2wiki") / "data" / "2wikimultihopqa.json"):
        supporting = {title for title, _ in q["supporting_facts"]}
        # Sentences are joined without a separator in most passages and with a space in some.
        gold = [by_text.get((t, "".join(s))) or by_text[(t, " ".join(s))]
                for t, s in q["context"] if t in supporting]
        questions.append({
            "qid": q["_id"], "question": q["question"], "answers": [q["answer"]],
            "gold_doc_ids": gold, "type": q["type"],
        })
    return corpus, questions


# ── QuALITY ───────────────────────────────────────────────────────────────────
# Each dev article appears in two question sets with the same text; questions are merged.

_QUALITY_ARTICLES = 50


def quality() -> Rows:
    path = bench("quality") / "data" / "QuALITY.v1.0.1.htmlstripped.dev"
    sets = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    articles: Dict[str, Dict] = {}
    qs: Dict[str, List[Dict]] = defaultdict(list)
    for s in sets:
        articles.setdefault(s["article_id"], s)
        qs[s["article_id"]].extend(s["questions"])
    chosen = sorted(random.Random(SEED).sample(sorted(articles), _QUALITY_ARTICLES))
    corpus = [{"doc_id": a, "title": articles[a]["title"], "text": articles[a]["article"]} for a in chosen]
    questions = []
    for a in chosen:
        for q in qs[a]:
            questions.append({
                "qid": q["question_unique_id"], "question": q["question"],
                "options": q["options"], "answers": [str(q["gold_label"])],   # 1-based option number
                "gold_doc_ids": [a], "type": "hard" if q["difficult"] else "easy",
            })
    return corpus, questions


# ── MultiHop-RAG ──────────────────────────────────────────────────────────────

_MH_SAMPLE = 500


def multihop_rag() -> Rows:
    raw_corpus = _load(bench("multihop_rag") / "data" / "corpus.json")
    corpus = [{"doc_id": str(i), "title": c["title"], "text": c["body"],
               "published_at": c.get("published_at"), "source": c.get("source")}
              for i, c in enumerate(raw_corpus)]
    by_title = {c["title"]: c["doc_id"] for c in corpus}
    raw_q = _load(bench("multihop_rag") / "data" / "MultiHopRAG.json")
    by_type: Dict[str, List[int]] = defaultdict(list)
    for i, q in enumerate(raw_q):
        by_type[q["question_type"]].append(i)
    # Stratified by type, proportional (largest remainder), seed 42.
    total = len(raw_q)
    quota = {t: _MH_SAMPLE * len(ix) / total for t, ix in by_type.items()}
    counts = {t: int(v) for t, v in quota.items()}
    for t in sorted(quota, key=lambda t: quota[t] - counts[t], reverse=True)[: _MH_SAMPLE - sum(counts.values())]:
        counts[t] += 1
    rng = random.Random(SEED)
    picked = sorted(i for t in sorted(by_type) for i in rng.sample(by_type[t], counts[t]))
    questions = [{
        "qid": f"mh{i}", "question": raw_q[i]["query"], "answers": [raw_q[i]["answer"]],
        "gold_doc_ids": list(dict.fromkeys(by_title[e["title"]] for e in raw_q[i]["evidence_list"])),
        "type": raw_q[i]["question_type"],
    } for i in picked]
    return corpus, questions


# ── GraphRAG-Bench (Novel) ────────────────────────────────────────────────────

def graphrag_bench_novel() -> Rows:
    base = bench("graphrag_bench_novel") / "data" / "Datasets"
    corpus = [{"doc_id": c["corpus_name"], "title": c["corpus_name"], "text": c["context"]}
              for c in _load(base / "Corpus" / "novel.json")]
    questions = []
    for q in _load(base / "Questions" / "novel_questions.json"):
        evidence = q.get("evidence")
        if isinstance(evidence, str):
            try:
                evidence = ast.literal_eval(evidence)
            except (ValueError, SyntaxError):
                evidence = [evidence]
        questions.append({
            "qid": q["id"], "question": q["question"], "answers": [q["answer"]],
            "gold_doc_ids": [q["source"]], "type": q["question_type"], "evidence": evidence,
        })
    return corpus, questions


ADAPTERS = {
    "musique": musique, "2wiki": twowiki, "quality": quality,
    "multihop_rag": multihop_rag, "graphrag_bench_novel": graphrag_bench_novel,
}


# ── splits (§4) ───────────────────────────────────────────────────────────────

def make_splits(name: str, questions: List[Dict]) -> Tuple[List[str], List[str]]:
    rng = random.Random(SEED)
    if name == "quality":
        # Split by article: 10 dev, 40 test.
        docs = sorted({q["gold_doc_ids"][0] for q in questions})
        dev_docs = set(rng.sample(docs, 10))
        dev = [q["qid"] for q in questions if q["gold_doc_ids"][0] in dev_docs]
        test = [q["qid"] for q in questions if q["gold_doc_ids"][0] not in dev_docs]
        return dev, test
    if name in ("musique", "2wiki"):
        ids = [q["qid"] for q in questions]
        dev = set(rng.sample(ids, 200))
        return [i for i in ids if i in dev], [i for i in ids if i not in dev]
    # Stratified by type: MultiHop-RAG 100 of 500 dev; GraphRAG-Bench 20% dev.
    share = 0.2
    by_type: Dict[str, List[str]] = defaultdict(list)
    for q in questions:
        by_type[q["type"]].append(q["qid"])
    dev_ids = set()
    for t in sorted(by_type):
        dev_ids |= set(rng.sample(by_type[t], round(len(by_type[t]) * share)))
    ids = [q["qid"] for q in questions]
    return [i for i in ids if i in dev_ids], [i for i in ids if i not in dev_ids]


# ── checks (§8 step 2: gold ids exist, answers non-empty) ─────────────────────

def check(name: str, corpus: List[Dict], questions: List[Dict]) -> List[str]:
    ids = {c["doc_id"] for c in corpus}
    problems = []
    if len(ids) != len(corpus):
        problems.append("duplicate doc ids")
    for q in questions:
        if not q["question"].strip():
            problems.append(f"{q['qid']}: empty question")
        if not q["answers"] or not all(str(a).strip() for a in q["answers"][:1]):
            problems.append(f"{q['qid']}: empty answer")
        if q["type"] != "null_query" and not q["gold_doc_ids"]:
            problems.append(f"{q['qid']}: no gold docs")
        for g in q["gold_doc_ids"]:
            if g not in ids:
                problems.append(f"{q['qid']}: gold {g} not in corpus")
        if name == "quality" and (len(q["options"]) != 4 or q["answers"][0] not in "1234"):
            problems.append(f"{q['qid']}: bad options/label")
    return problems


def build(name: str) -> Dict:
    corpus, questions = ADAPTERS[name]()
    problems = check(name, corpus, questions)
    if problems:
        raise SystemExit(f"{name}: {len(problems)} problems, e.g. {problems[:5]}")
    out = bench(name)
    write_jsonl(out / "corpus.jsonl", corpus)
    write_jsonl(out / "questions.jsonl", questions)
    dev, test = make_splits(name, questions)
    (out / "splits").mkdir(exist_ok=True)
    (out / "splits" / "dev.txt").write_text("\n".join(dev) + "\n", encoding="utf-8")
    (out / "splits" / "test.txt").write_text("\n".join(test) + "\n", encoding="utf-8")
    stats = {
        "dataset": name, "corpus_docs": len(corpus),
        "corpus_words": sum(len(c["text"].split()) for c in corpus),
        "questions": len(questions), "dev": len(dev), "test": len(test),
        "types": dict(Counter(q["type"] for q in questions)),
        "dev_types": dict(Counter(q["type"] for q in questions if q["qid"] in set(dev))),
        "corpus_sha256": sha256_file(out / "corpus.jsonl"),
        "questions_sha256": sha256_file(out / "questions.jsonl"),
        "dev_sha256": sha256_file(out / "splits" / "dev.txt"),
        "test_sha256": sha256_file(out / "splits" / "test.txt"),
    }
    (out / "dataset_stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    run_log(f"adapter `{name}`: {stats['corpus_docs']} docs, {stats['questions']} questions "
            f"(dev {stats['dev']}, test {stats['test']}), splits sha256 dev={stats['dev_sha256'][:12]} "
            f"test={stats['test_sha256'][:12]}")
    return stats


if __name__ == "__main__":
    for n in sys.argv[1:] or list(ADAPTERS):
        print(json.dumps(build(n), indent=1))
