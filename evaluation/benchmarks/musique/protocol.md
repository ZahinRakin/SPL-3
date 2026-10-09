# Protocol — MuSiQue (answerable), H1

Status: **DRAFT** (becomes FROZEN when committed, before any test-set answer or test ranking exists).
Plan: `EVALUATION_PLAN.md` (repo root). Code under test: commit recorded in the freeze commit.

## Data (verified 2026-10-09)

- Source: Hugging Face `osunlp/HippoRAG_2`, files `musique.json`, `musique_corpus.json`
  (sha256 in `data_hashes.txt`).
- **1,000 questions, 11,656 passages** (matches the plan). All answerable. Hop types: 2hop 518,
  3hop1 243, 3hop2 73, 4hop1 108, 4hop2 27, 4hop3 31. 276 questions have answer aliases.
- File format differs from the plan's description: the corpus has no `idx`, and question
  paragraphs use `paragraph_text`. Gold passages are matched to the corpus by exact
  (title, text); all 2,648 supporting paragraphs were found. `doc_id` = position in the corpus file.
- Indexing: no re-chunking. One passage = one chunk, text = `title + "\n" + text`. RAPTOR builds
  no tree (each document is one passage), so **S3 ≡ S2 and S5 ≡ S4 on this dataset**; that is
  expected (plan §3.1, §10) and reported, not hidden.
- Split (seed 42): 200 dev / 800 test (`splits/dev.txt`, `splits/test.txt`, sha256 in
  `dataset_stats.json`).

## Systems (plan §2), one shared index

C0 closed book · S0 dense · S1 BM25 · S2 dense + PageRank (RRF) · S3 S2 + RAPTOR summaries ·
S4 S2 + community summaries and graph facts · S5 full system. Definitions in
`evaluation/harness/systems.py`.

Frozen settings:
- Context budget **B = 4,000 characters** for every system (plan §4).
- `ppr_weight` **w\* = {W_STAR}**, chosen on dev Recall@5 from {0.25, 0.5, 1, 2}
  (`tuning/ppr_weight.json`; tie rule: closest to 1.0).
- `max_summaries` **m\* = 2** (the app default). It has no effect here (no summaries exist). The
  plan picks m\* on QuALITY dev; QuALITY runs after this dataset.
- Answer prompt: `SHORT_QA` / closed-book `SHORT_QA_CLOSED` in `evaluation/harness/prompts.py`
  (short-answer JSON); temperature 0; gpt-oss-20b, reasoning effort low; no chat history.

## Hypothesis H1 (confirmatory)

**Graph ranking helps multi-hop retrieval:** S2 vs S0 on the 800 test questions.
- Metric: Recall@5 of supporting passages, per question (gold = all supporting paragraphs).
- Test: Wilcoxon signed-rank, two-sided, zero differences dropped; α = 0.05, Holm–Bonferroni
  across H1–H4.
- Also reported: mean difference, 95 % paired-bootstrap CI (10,000 resamples, seed 42),
  win/tie/loss.

## Secondary and exploratory (no claims)

- Recall@2, MRR@10 for S0–S3; per hop type.
- QA: EM and token F1 (SQuAD normalisation, max over aliases) for all systems.
- Judge (Claude, blinded; Codex later): reference-based correctness (0 / 0.5 / 1) on 200 test
  questions (stratified by hop type, seed 42) × {S2, S0}.
- Costs, mean context characters, mean latency (uncached calls only).
- C0 shows how much gpt-oss-20b knows without retrieval (contamination check).

## Sign-off

[STOP: owner sign-off] — delegated: on 2026-10-09 the owner instructed the agent to execute the
plan without stopping ("execute it … you do it alone"). See `evaluation/RUN_LOG.md`.

## Deviations

(none yet)
