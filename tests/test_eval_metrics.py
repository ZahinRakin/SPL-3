"""evaluation/harness/metrics.py against hand-computed cases (EVALUATION_PLAN.md §8 step 3)."""
import math

import pytest

from evaluation.harness import metrics as m


def test_recall_and_hits():
    ranked = ["a", "x", "a", "b", "y", "c"]          # duplicates count once
    assert m.recall_at_k(ranked, ["a", "b"], 2) == 0.5   # top-2 distinct: a, x
    assert m.recall_at_k(ranked, ["a", "b"], 3) == 1.0   # a, x, b
    assert m.recall_at_k(ranked, ["a", "b", "c", "z"], 5) == 0.75
    assert math.isnan(m.recall_at_k(ranked, [], 5))
    # distinct order a, x, b, y, c: c is 5th
    assert m.hits_at_k(ranked, ["c"], 4) == 0.0
    assert m.hits_at_k(ranked, ["c"], 5) == 1.0


def test_mrr():
    assert m.mrr_at_k(["x", "y", "g"], ["g"], 10) == pytest.approx(1 / 3)
    assert m.mrr_at_k(["x", "x", "g"], ["g"], 10) == pytest.approx(1 / 2)   # dedupe first
    assert m.mrr_at_k(["x", "y", "g"], ["g"], 2) == 0.0


def test_em_and_f1():
    assert m.exact_match("The Eiffel Tower.", ["eiffel tower"]) == 1.0
    assert m.exact_match("Paris, France", ["Paris"]) == 0.0
    # pred tokens: paris france; gold: paris → P=1/2, R=1 → F1=2/3
    assert m.token_f1("Paris, France", ["Paris"]) == pytest.approx(2 / 3)
    assert m.token_f1("Lyon", ["Paris", "Lyon"]) == 1.0      # max over aliases
    assert m.token_f1("", ["Paris"]) == 0.0


def test_option_parsing_is_strict():
    for ok in ["2", " 2 ", "2.", "(2)", "Option 2", "option 2)"]:
        assert m.parse_option(ok) == "2", ok
    for bad in ["2 or 3", "The answer is 2", "5", "", "two"]:
        assert m.parse_option(bad) is None, bad
    assert m.mc_correct("3", "3") == 1.0 and m.mc_correct("I think 3", "3") == 0.0


def test_multihop_rag_scorer_matches_repo():
    assert m.multihop_rag_correct("Sam Bankman-Fried", "Sam Bankman-Fried") == 1.0
    assert m.multihop_rag_correct("Yes.", "Yes") == 0.0          # repo keeps punctuation
    assert m.multihop_rag_correct_normalized("Yes.", "Yes") == 1.0
    assert m.multihop_rag_correct('The answer to the question is "Google"', "Google") == 1.0
    assert m.multihop_rag_correct("Insufficient Information", "Insufficient information.") == 1.0


def test_mcnemar_exact():
    a = [1, 1, 1, 1, 0, 0]
    b = [0, 0, 0, 1, 0, 1]
    r = m.mcnemar_exact(a, b)
    # discordant: a-only 3, b-only 1 → binomial(3 of 4, 0.5) two-sided p = 0.625
    assert (r["a_only"], r["b_only"]) == (3, 1)
    assert r["p"] == pytest.approx(0.625)
    assert m.mcnemar_exact([1, 0], [1, 0])["p"] == 1.0


def test_wilcoxon():
    a = [0.5, 0.75, 1.0, 1.0, 0.25]
    b = [0.5, 0.25, 0.5, 0.0, 0.0]
    r = m.wilcoxon(a, b)
    assert r["n_nonzero"] == 4
    # differences 0.5, 0.5, 1.0, 0.25 all positive → W- = 0, exact p = 2/2^4 = 0.125
    assert r["p"] == pytest.approx(0.125)
    assert m.wilcoxon([1, 1], [1, 1])["p"] == 1.0


def test_bootstrap_and_wtl():
    a, b = [1, 1, 0, 1], [0, 1, 0, 0]
    ci = m.paired_bootstrap_ci(a, b, resamples=2000)
    assert ci["mean_diff"] == pytest.approx(0.5)
    assert ci["ci_low"] <= 0.5 <= ci["ci_high"]
    assert m.win_tie_loss(a, b) == {"win": 2, "tie": 2, "loss": 0}


def test_holm():
    r = m.holm({"H1": 0.01, "H2": 0.04, "H3": 0.03, "H4": 0.5})
    # sorted 0.01, 0.03, 0.04, 0.5 → ×4, ×3, ×2, ×1 = 0.04, 0.09, 0.08→0.09 (monotone), 0.5
    assert r["H1"]["p_holm"] == pytest.approx(0.04) and r["H1"]["reject"]
    assert r["H3"]["p_holm"] == pytest.approx(0.09) and not r["H3"]["reject"]
    assert r["H2"]["p_holm"] == pytest.approx(0.09)
    assert r["H4"]["p_holm"] == pytest.approx(0.5)


def test_cohen_kappa():
    # 2x2: agree on 3 of 4; marginals r1 = (2 yes, 2 no), r2 = (1 yes, 3 no)
    r1, r2 = [1, 1, 0, 0], [1, 0, 0, 0]
    # po = 0.75, pe = 0.5*0.25 + 0.5*0.75 = 0.5 → kappa = 0.5
    assert m.cohen_kappa(r1, r2) == pytest.approx(0.5)
    assert m.cohen_kappa([0, 0.5, 1], [0, 0.5, 1], "quadratic") == pytest.approx(1.0)
    # one-step disagreement is penalised less than a two-step one with quadratic weights
    near = m.cohen_kappa([0, 0.5, 1, 1], [0, 0.5, 0.5, 1], "quadratic")
    far = m.cohen_kappa([0, 0.5, 1, 1], [0, 0.5, 0, 1], "quadratic")
    assert near > far
