# Protocol — GraphRAG-Bench (Novel), H3

Status: **FROZEN 2026-10-09** (committed before the GraphRAG-Bench indexes and any test answer).
Code under test: commit `b49276a`.

## Data (verified 2026-10-09)

- Source: Hugging Face `GraphRAG-Bench/GraphRAG-Bench` (`Datasets/Corpus/novel.json`,
  `Datasets/Questions/novel_questions.json`, `Evaluation/`); licence MIT (`data/README.md`).
  sha256 in `data_hashes.txt`.
- **20 novels, 839,608 words; 2,010 questions**: Fact Retrieval 971, Complex Reasoning 610,
  Contextual Summarize 362, Creative Generation 67. Split stratified by type (seed 42):
  401 dev / 1,609 test.
- Each novel is its own knowledge base, as in the benchmark: **one index per novel**
  (chunked 250/40, RAPTOR tree, graph and communities per novel).

## Subsample (budget, plan §3.5 and §7)

The money left after MuSiQue, QuALITY and MultiHop-RAG allows indexing only part of the corpus.
Novels are taken in a seed-42 order (`random.Random(42).sample(sorted(novel ids), 20)`, recorded in
`subsample.json`), and the first **6** are used. Questions: the **test** questions of
those novels (Novel-10762, Novel-10146, Novel-30752, Novel-29973, Novel-54537, Novel-10356; 241,387 words; 86 Contextual Summarize + 22 Creative Generation test questions) of the two levels this hypothesis is about — **Contextual Summarize** (H3) and
**Creative Generation** (pairwise, exploratory). Fact Retrieval / Complex Reasoning are not run
(no budget; they are not part of H3).

## Systems and settings

C0, S0–S5 (`systems.py`). Budget **B = 12,000 characters**. `ppr_weight` **w = 0.5** and
`max_summaries` **m = 1**, carried over from MultiHop-RAG / QuALITY dev tuning (no tuning on this
dataset). Prompt `FREE_QA` / `FREE_QA_CLOSED` (`prompts.py`, ≤ 150 words); temperature 0.

## Hypothesis H3 (confirmatory)

**Community summaries help broad questions:** S4 vs S2 on the Contextual Summarize test questions
of the subsample.
- Metric: the benchmark's **coverage** score (share of reference facts covered by the answer),
  computed with the benchmark's own two prompts (fact extraction, then fact coverage —
  `Evaluation/metrics/coverage.py`), executed by the blinded Claude judge (`rubrics/coverage_facts.md`,
  `rubrics/coverage.md`). Reference facts are extracted once per question and shared by both systems.
- Test: Wilcoxon signed-rank, two-sided; α = 0.05, Holm–Bonferroni across H1–H4.
- Also reported: mean difference, 95 % paired-bootstrap CI, win/tie/loss.

## Secondary / exploratory

- Pairwise preference S4 vs S2 on Contextual Summarize + Creative Generation test questions, each pair
  judged twice with A/B swapped (a win counts only if it holds in both orders); position consistency.
- ROUGE-L of every system against the reference answer, per level.
- C0 for contamination (these are public-domain novels, so C0 may be high).

## Sign-off

[STOP: owner sign-off] — delegated by the owner's instruction of 2026-10-09 (see `RUN_LOG.md`).

## Deviations

1. Subsample (above): only 6 of 20 novels, and only levels 3–4 test questions.
2. No dev tuning on this dataset; w and m carried over.
3. The benchmark's LLM-based metrics are run by the blinded Claude judge with the benchmark's own
   prompts instead of an API model (plan §3.5, §6).
