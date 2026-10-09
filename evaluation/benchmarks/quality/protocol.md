# Protocol — QuALITY (long documents), H2

Status: **FROZEN 2026-10-09** (committed before any QuALITY test-set answer was generated).
Code under test: commit `b49276a` (includes the two fixes in Deviations 2–3).

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
- `ppr_weight` **w\* = 0.25** — carried over from MuSiQue dev tuning (QuALITY has one gold
  document per question, so passage-level retrieval recall cannot tune it; plan §4 tunes w on
  MuSiQue / 2Wiki / MultiHop-RAG).
- `max_summaries` **m\* = 1**, chosen from {1, 2, 4} by S3 dev accuracy (plan §4; tie →
  the smaller value). Dev results: `tuning/max_summaries.json`.
- Prompt: `QUALITY_MC` / `QUALITY_MC_CLOSED` (`prompts.py`): the four options are listed; the
  model must reply with only the option number. Parsing is strict (`metrics.parse_option`); an
  unparseable reply scores 0. Temperature 0, no chat history.

## Dev observations recorded at freeze time (not results)

`tuning/max_summaries.json` (before fix 2): S2 0.674; S3 m = 1/2/4 0.668 (no summary ever entered
the context). `tuning/summary_fix_prototype.json` (with fix 2): S3 m = 1/2/4 = 0.632 / 0.627 /
0.627; 26/193 dev questions get a summary into the context. m\* = 1 (highest; tie rule → smaller).
On dev, adding summaries lowered accuracy by ~4 points; H2 stays two-sided as planned.

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

1. `ppr_weight` is not tuned on QuALITY (one gold document per question gives no passage-level
   retrieval signal); MuSiQue's w\* = 0.25 is used.
2. **Code fix before freezing (rule 9):** with the RRF fusion, RAPTOR summaries had one RRF term
   against a chunk's two and were never selected (dev: 0 summaries per context for every m), which
   would make H2 a test of two identical systems. A summary now inherits the best graph rank of its
   descendant chunks (`hippo_retriever.py`, commit `b49276a`). Found and prototyped on dev only.
3. **Code fix before freezing:** community block labels are capped at 3 documents and oversized
   extras are skipped (`query_engine.py`, same commit). Affects S4/S5 only, not H2.
4. Budget: 2Wiki, official HippoRAG 2 and extra seeds are dropped (`budget_ledger.md`).
