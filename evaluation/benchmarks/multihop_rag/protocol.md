# Protocol — MultiHop-RAG (real news, cross-document), H4

Status: **DRAFT** (becomes FROZEN when committed, before any test-set answer exists).

## Data (verified 2026-10-09)

- Source: Hugging Face `yixuantt/MultiHopRAG` (`MultiHopRAG.json`, `corpus.json`), scorer from
  `github.com/yixuantt/MultiHop-RAG` (`qa_evaluate.py`, saved as `data/repo_qa_evaluate.py`).
  Licence ODC-BY (`data/repo_README.md`). sha256 in `data_hashes.txt`.
- **2,556 queries** (inference 816, comparison 856, temporal 583, null 301) and **609 articles**
  (1,063,319 words). Evidence titles all match corpus titles.
- Subsample: **500 queries, stratified by type, seed 42** (inference 160, comparison 167,
  temporal 114, null 59). Split stratified by type: 100 dev / 400 test. The whole corpus is indexed.
- Indexing: one article = one document, text = title + "\n" + body, chunked (250/40), RAPTOR tree
  per article.

## Systems and settings

C0, S0–S5 (`systems.py`). Budget **B = 8,000 characters**. `ppr_weight` **w\* = {W_STAR}**
(re-tuned on this dataset's dev split by Hits@4 from {0.25, 0.5, 1, 2}; `tuning/ppr_weight.json`).
`max_summaries` **m\* = {M_STAR}** (from QuALITY dev). Prompt: the repository's own instruction
(`MULTIHOP_RAG` in `prompts.py`, verbatim from `qa_llama.py`) with our labelled context blocks;
temperature 0.

## Hypothesis H4 (confirmatory)

**The full system is at least as good as dense retrieval on real cross-document questions:**
S5 vs S0 on the 400 test queries.
- Metric: answer accuracy (0/1) with the repository's own scorer (`metrics.multihop_rag_correct`,
  same logic as `qa_evaluate.py`: correct if prediction and gold share a whitespace token,
  lower-cased; punctuation is not stripped).
- Test: exact McNemar, two-sided; α = 0.05, Holm–Bonferroni across H1–H4.
  "≥" is read as: a significant difference in favour of S0 would refute H4; a significant
  difference in favour of S5 supports "better"; no significant difference is reported with its CI
  (it is not evidence of equivalence).

## Secondary / exploratory

Hits@4, Hits@10, MRR@10 of evidence articles (S0–S3); accuracy per query type, with the null type
reported separately (correct behaviour: say the information is missing); accuracy with a
punctuation-normalised variant of the scorer; Claude judge correctness on 200 test queries
(stratified, seed 42) × {S5, S0}; costs, context characters, latency.

## Sign-off

[STOP: owner sign-off] — delegated by the owner's instruction of 2026-10-09 (see `RUN_LOG.md`).

## Deviations

(none yet)
