"""backend/evaluation/harness/blind.py: the judge files must not leak which system wrote an answer."""
import json

import pytest

from backend.evaluation.harness import blind, common
from backend.evaluation.harness.common import read_jsonl, write_jsonl


@pytest.fixture
def ds(tmp_path, monkeypatch):
    """A fake dataset with two systems' runs on 6 questions."""
    monkeypatch.setattr(common, "BENCH_DIR", tmp_path)
    monkeypatch.setattr(common, "RUN_LOG", tmp_path / "RUN_LOG.md")
    monkeypatch.setattr(blind, "bench", lambda name: tmp_path / name)
    monkeypatch.setattr(blind, "run_log", lambda entry: None)
    root = tmp_path / "toy"
    qs = [{"qid": f"q{i}", "question": f"question {i}", "answers": [f"gold {i}"], "type": "t1" if i < 3 else "t2",
           "evidence": [f"ev {i}"]} for i in range(6)]
    write_jsonl(root / "questions.jsonl", qs)
    for s in ("S0", "S2"):
        write_jsonl(root / "runs" / "test" / f"{s}.jsonl", [
            {"qid": q["qid"], "question": q["question"], "gold_answers": q["answers"], "type": q["type"],
             "answer": f"{s} says {i}", "error": None} for i, q in enumerate(qs)])
    return root


def _items(root, prefix):
    return [r for p in sorted((root / "judging").glob(f"blinded_{prefix}_*.jsonl")) for r in read_jsonl(p)]


def test_correctness_files_hide_the_system(ds):
    blind.correctness("toy", ["S2", "S0"], sample=4)
    items = _items(ds, "correctness_S2_S0")
    assert len(items) == 8                                  # 4 questions × 2 systems
    assert set(items[0]) == {"bid", "question", "gold_answers", "answer", "null_query"}
    key = json.loads((ds / "judging" / "key_correctness_S2_S0.json").read_text())["key"]
    assert set(key) == {i["bid"] for i in items}
    # Stratified: 2 of each type.
    types = [k["type"] for k in key.values()]
    assert types.count("t1") == types.count("t2") == 4


def test_pairwise_has_both_orders(ds):
    blind.pairwise("toy", ["S2", "S0"], ["t1", "t2"])
    items = _items(ds, "pairwise_S2_S0")
    key = json.loads((ds / "judging" / "key_pairwise_S2_S0.json").read_text())["key"]
    assert len(items) == 12
    orders = {}
    for k in key.values():
        orders.setdefault(k["qid"], set()).add((k["A"], k["B"]))
    assert all(v == {("S2", "S0"), ("S0", "S2")} for v in orders.values())
    assert not any("S2" in json.dumps({k: v for k, v in i.items() if k not in ("answer_A", "answer_B")})
                   for i in items)


def test_same_seed_same_files(ds):
    blind.correctness("toy", ["S2", "S0"], sample=4, name="a")
    blind.correctness("toy", ["S2", "S0"], sample=4, name="b")
    assert _items(ds, "a") == _items(ds, "b")
