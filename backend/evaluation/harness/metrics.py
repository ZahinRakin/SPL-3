"""
Metrics and statistics (EVALUATION_PLAN.md §5, §8 step 3). Pure functions, tested in
backend/tests/test_eval_metrics.py against hand-computed cases.

Retrieval: recall_at_k, hits_at_k, mrr_at_k.  QA: exact_match, token_f1 (SQuAD normalisation,
max over gold aliases), mc_correct, multihop_rag_correct (the repo's own scorer).
Paired statistics: mcnemar_exact, wilcoxon, paired_bootstrap_ci, win_tie_loss, holm.
Agreement: cohen_kappa (unweighted / quadratic).
"""
import math
import re
import string
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import stats

# ── retrieval ─────────────────────────────────────────────────────────────────

def _dedupe(ids: Sequence[str]) -> List[str]:
    return list(dict.fromkeys(ids))


def recall_at_k(ranked: Sequence[str], gold: Sequence[str], k: int) -> float:
    """Share of gold docs among the first k distinct ranked docs."""
    gold = set(gold)
    if not gold:
        return float("nan")
    return len(set(_dedupe(ranked)[:k]) & gold) / len(gold)


def hits_at_k(ranked: Sequence[str], gold: Sequence[str], k: int) -> float:
    """MultiHop-RAG's Hits@k: share of evidence docs found in the top k (same as recall@k)."""
    return recall_at_k(ranked, gold, k)


def mrr_at_k(ranked: Sequence[str], gold: Sequence[str], k: int) -> float:
    """Reciprocal rank of the first gold doc within the top k distinct docs (0 if none)."""
    gold = set(gold)
    if not gold:
        return float("nan")
    for i, d in enumerate(_dedupe(ranked)[:k], start=1):
        if d in gold:
            return 1.0 / i
    return 0.0


# ── QA ────────────────────────────────────────────────────────────────────────

def normalize_answer(s: str) -> str:
    """SQuAD normalisation: lower case, no punctuation, no articles, single spaces."""
    s = s.lower()
    s = "".join(ch for ch in s if ch not in set(string.punctuation))
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    return " ".join(s.split())


def exact_match(pred: str, golds: Sequence[str]) -> float:
    p = normalize_answer(pred)
    return float(any(p == normalize_answer(g) for g in golds))


def _f1(pred: str, gold: str) -> float:
    p, g = normalize_answer(pred).split(), normalize_answer(gold).split()
    common = Counter(p) & Counter(g)
    same = sum(common.values())
    if not p or not g:
        return float(p == g)
    if same == 0:
        return 0.0
    precision, recall = same / len(p), same / len(g)
    return 2 * precision * recall / (precision + recall)


def token_f1(pred: str, golds: Sequence[str]) -> float:
    return max(_f1(pred, g) for g in golds)


_OPTION = re.compile(r"^\s*(?:option\s*)?\(?([1-4])\)?\s*[.):]?\s*$", re.IGNORECASE)


def parse_option(text: str) -> Optional[str]:
    """Strict: the reply must be just an option number 1-4 (optionally 'Option 2', '(2)', '2.')."""
    m = _OPTION.match(str(text))
    return m.group(1) if m else None


def mc_correct(reply: str, gold_label: str) -> float:
    """QuALITY: 1 if the strictly parsed option number equals the gold label; unparseable = 0."""
    return float(parse_option(reply) == str(gold_label))


def multihop_rag_correct(pred: str, gold: str) -> float:
    """The MultiHop-RAG repository's scorer (qa_evaluate.py), verbatim logic: correct if the
    lower-cased prediction and gold share any whitespace-separated token."""
    m = re.search(r'The answer to the question is "(.*?)"', pred)
    pred = m.group(1) if m else pred
    return float(bool(set(pred.lower().split()).intersection(gold.lower().split())))


def multihop_rag_correct_normalized(pred: str, gold: str) -> float:
    """Exploratory variant: the same token-overlap rule after SQuAD normalisation, so that
    'Yes.' matches 'Yes' (the official scorer counts punctuation as part of the token)."""
    return float(bool(set(normalize_answer(pred).split()) & set(normalize_answer(gold).split())))


# ── paired statistics ─────────────────────────────────────────────────────────

def win_tie_loss(a: Sequence[float], b: Sequence[float]) -> Dict[str, int]:
    """Per item, does a beat b?"""
    a, b = np.asarray(a, float), np.asarray(b, float)
    return {"win": int((a > b).sum()), "tie": int((a == b).sum()), "loss": int((a < b).sum())}


def mcnemar_exact(a: Sequence[float], b: Sequence[float]) -> Dict[str, float]:
    """Exact (binomial) McNemar test for paired 0/1 outcomes, two-sided."""
    a, b = np.asarray(a, int), np.asarray(b, int)
    n01 = int(((a == 1) & (b == 0)).sum())   # a right, b wrong
    n10 = int(((a == 0) & (b == 1)).sum())
    n = n01 + n10
    p = 1.0 if n == 0 else float(stats.binomtest(n01, n, 0.5, alternative="two-sided").pvalue)
    return {"a_only": n01, "b_only": n10, "p": p}


def wilcoxon(a: Sequence[float], b: Sequence[float]) -> Dict[str, float]:
    """Wilcoxon signed-rank, two-sided, zero differences dropped (Wilcox method);
    p = 1 when every pair ties."""
    d = np.asarray(a, float) - np.asarray(b, float)
    d = d[~np.isnan(d)]
    nz = int((d != 0).sum())
    if nz == 0:
        return {"statistic": 0.0, "p": 1.0, "n_nonzero": 0}
    res = stats.wilcoxon(d, zero_method="wilcox", alternative="two-sided")
    return {"statistic": float(res.statistic), "p": float(res.pvalue), "n_nonzero": nz}


def paired_bootstrap_ci(
    a: Sequence[float], b: Sequence[float], resamples: int = 10_000, seed: int = 42, level: float = 0.95
) -> Dict[str, float]:
    """Mean of (a - b) and its percentile bootstrap CI over items."""
    d = np.asarray(a, float) - np.asarray(b, float)
    d = d[~np.isnan(d)]
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(resamples, len(d)))
    means = d[idx].mean(axis=1)
    lo, hi = np.percentile(means, [(1 - level) / 2 * 100, (1 + level) / 2 * 100])
    return {"mean_diff": float(d.mean()), "ci_low": float(lo), "ci_high": float(hi), "n": int(len(d))}


def holm(pvalues: Dict[str, float], alpha: float = 0.05) -> Dict[str, Dict[str, float]]:
    """Holm–Bonferroni: adjusted p-values (monotone) and reject decisions."""
    order = sorted(pvalues, key=lambda k: pvalues[k])
    m = len(order)
    out, running = {}, 0.0
    for i, k in enumerate(order):
        adj = min(1.0, (m - i) * pvalues[k])
        running = max(running, adj)
        out[k] = {"p": pvalues[k], "p_holm": running, "reject": running < alpha}
    return out


# ── agreement ─────────────────────────────────────────────────────────────────

def cohen_kappa(r1: Sequence[float], r2: Sequence[float], weights: Optional[str] = None) -> float:
    """Cohen's kappa over the categories present in either rater. weights=None (nominal) or
    'quadratic' (ordered categories, e.g. 0 / 0.5 / 1)."""
    cats = sorted(set(r1) | set(r2))
    if len(cats) == 1:
        return 1.0
    ix = {c: i for i, c in enumerate(cats)}
    k = len(cats)
    obs = np.zeros((k, k))
    for x, y in zip(r1, r2):
        obs[ix[x], ix[y]] += 1
    obs /= obs.sum()
    exp = np.outer(obs.sum(axis=1), obs.sum(axis=0))
    if weights == "quadratic":
        w = np.array([[(i - j) ** 2 for j in range(k)] for i in range(k)]) / (k - 1) ** 2
    else:
        w = 1 - np.eye(k)
    denom = (w * exp).sum()
    return 1.0 if denom == 0 else float(1 - (w * obs).sum() / denom)


def summarize(values: Sequence[float]) -> Dict[str, float]:
    v = np.asarray(values, float)
    v = v[~np.isnan(v)]
    return {"mean": float(v.mean()) if len(v) else float("nan"), "n": int(len(v)),
            "sd": float(v.std(ddof=1)) if len(v) > 1 else float("nan")}
