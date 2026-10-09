# Protocol — QuALITY (long documents), H2

Status: **DRAFT** (becomes FROZEN when committed, before any test-set answer exists).

## Data (verified 2026-10-09)

- Source: `github.com/nyu-mll/quality`, `data/v1.0.1/QuALITY.v1.0.1.htmlstripped.dev`
  (sha256 in `data_hashes.txt`). Licence: see `data/README.md` (articles are Project Gutenberg,
  Slate and open-access texts; questions CC BY 4.0 per the QuALITY release).
- The dev file has 230 question sets over **115 unique articles** (each article appears in two sets
  with identical text); questions of both sets are merged per article.
- Subsample: **50 articles** (seed 42) → **915 questions** (457 easy, 458 hard per the
  `difficult` flag). Split by article (seed 42): 10 dev articles (193 questions) / 40 test articles
  (722 questions).
- Indexing: one article = one document; `GraphRAGIndexer.chunk_text` (250 words, 40 overlap);
  RAPTOR tree per article (§3.3).

## Systems and settings

C0, S0–S5 as in `evaluation/harness/systems.py`.
- Context budget **B = 8,000 characters** for every system.
- `ppr_weight` **w\* = {W_STAR}** — carried over from MuSiQue dev tuning (QuALITY has one gold
  document per question, so passage-level retrieval recall cannot tune it; plan §4 tunes w on
  MuSiQue / 2Wiki / MultiHop-RAG).
- `max_summaries` **m\* = {M_STAR}**, chosen from {1, 2, 4} by S3 dev accuracy (plan §4; tie →
  the smaller value). Dev results: `tuning/max_summaries.json`.
- Prompt: `QUALITY_MC` / `QUALITY_MC_CLOSED` (`prompts.py`): the four options are listed; the
  model must reply with only the option number. Parsing is strict (`metrics.parse_option`); an
  unparseable reply scores 0. Temperature 0, no chat history.

## Hypothesis H2 (confirmatory)

**RAPTOR summaries help long documents:** S3 vs S2 on the 722 test questions.
- Metric: accuracy (0/1) per question.
- Test: exact McNemar (binomial on discordant pairs), two-sided; α = 0.05, Holm–Bonferroni across H1–H4.
- Also reported: mean difference, 95 % paired-bootstrap CI, win/tie/loss.

## Secondary / exploratory

Accuracy of every system; accuracy on the hard subset; S5 vs S0, S4 vs S2, S0 vs S1, S0 vs C0;
unparseable replies per system; costs, context characters, latency.

## Sign-off

[STOP: owner sign-off] — delegated by the owner's instruction of 2026-10-09 (see `RUN_LOG.md`).

## Deviations

(none yet)
