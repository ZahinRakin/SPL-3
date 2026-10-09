# Protocol: Standard RAG vs Refined (cascaded) RAG

**Status: pre-registered.** This protocol was written on 2026-10-08, before any answer from either
system was generated or seen. Any later change is listed under *Deviations* in `report.md`.

## 1. Research question

Does the cascaded retrieval pipeline ("Refined RAG": RAPTOR → GraphRAG → HippoRAG Personalized
PageRank) produce more correct answers than a plain retrieval-augmented generation baseline
("Standard RAG") on questions about a collection of investigation documents?

## 2. Systems under test

Both systems share **everything except context selection**:

| | Standard RAG | Refined RAG |
|---|---|---|
| Index | the same index, built once | the same index, built once |
| Context | top-k = 6 chunks (RAPTOR level 0) by cosine similarity to the question | top-k = 6 passages (chunks or RAPTOR summaries) ranked by HippoRAG Personalized PageRank over the GraphRAG entity graph, plus a short block of graph facts about the highest-ranked entities |
| Generator | `openai/gpt-oss-20b` (OpenRouter), temperature 0.3, same prompt | same |
| Embeddings | `nomic-embed-text` (Ollama) | same |
| Chat history | cleared before every question | cleared before every question |

Implementation: `backend/pipeline/query_engine.py` (`method="standard"` / `"refined"`), run by
`evaluation/compare.py`.

## 3. Corpus

The 12 English sample cases in `data/input/sample_cases/`, indexed **together** as one
collection (so retrieval must also reject passages from unrelated documents). `dhaka.txt` is
excluded: it is in Bengali and only 110 words long.

## 4. Question set

- **200 questions**, written by the grader (Claude, Anthropic) from the source documents
  **before** either system produced an answer.
- Three pre-defined types, about one third each:
  - **fact**: the answer is stated in one place in one document.
  - **relational**: the answer needs two or more facts from different places to be linked
    (multi-hop: a person → an organisation → an event, etc.).
  - **summary**: the answer synthesises a theme spread over a document.
- Each question records: `id`, `doc`, `type`, `question`, `reference` (reference answer),
  `evidence` (one or more **verbatim** quotes from the document) and `key_facts` (short strings,
  such as names, numbers and dates, that a correct answer must contain).
- `evaluation/compare.py validate` checks that every evidence quote appears verbatim in its
  document. Questions that fail are fixed before the run, not dropped silently.

## 5. Hypotheses

Let *cᵢ(S)* and *cᵢ(R)* be the correctness scores of Standard and Refined on question *i*, and
*dᵢ = cᵢ(R) − cᵢ(S)* the paired difference.

- **H₀ (null):** the distribution of *dᵢ* is symmetric about zero. Refined and Standard are
  equally correct (median paired difference = 0).
- **H₁ (alternative, two-sided):** the distribution of *dᵢ* is not symmetric about zero. One
  system is more correct than the other.

Significance level **α = 0.05** (two-sided).

## 6. Primary outcome: correctness

Each answer is graded **0, 0.5 or 1** against the reference answer and evidence:

| Score | Rule |
|---|---|
| **1** | Contains the substance of the reference (all its key facts, or for summary questions all its main points) and contradicts nothing in the evidence. Extra correct detail is not penalised. |
| **0.5** | Partly correct: some but not all key facts or main points, **or** fully correct but with one minor factual error. |
| **0** | Wrong, contradicts the evidence, answers a different question, or says the information is not available. |

**Blinding.** `compare.py run` writes `judging/blinded_answers.jsonl`: every answer of both
systems in a random order (seed 42), each with a random id and **no system label**, next to its
question, reference and evidence. The id → system key is in `judging/key.json`. The grader
grades from the blinded file only and saves `judging/grades.jsonl` **before** opening
`results.jsonl`, the key or the run log.

## 7. Statistical analysis

All tests use `scipy.stats`, and every random number uses seed 42.

**Primary test (confirmatory).** Wilcoxon signed-rank test on the 200 paired differences *dᵢ*,
two-sided, `zero_method="wilcox"` (zero differences are excluded from the ranking, the textbook
default). Reject H₀ if *p* < 0.05. The direction of an effect is read from the mean of *dᵢ*.

**Reported with the primary test:**
- **Effect size:** matched-pairs rank-biserial correlation *r* (−1 to 1; positive favours Refined).
- **95% confidence interval** for the mean paired difference: paired bootstrap, 10,000 resamples.
- **Robustness check:** exact two-sided sign test (binomial, p = 0.5) on the number of questions
  Refined wins vs loses (ties excluded); and Wilcoxon with `zero_method="pratt"`.
- Win / tie / loss counts.

**Secondary analyses (exploratory).**
1. Per question type: Wilcoxon signed-rank on *dᵢ* within each type, p-values adjusted with
   **Holm–Bonferroni** over the 3 types.
2. Automatic metrics, each tested with Wilcoxon signed-rank and Holm-adjusted across metrics:
   - token-level F1 against the reference (SQuAD-style),
   - ROUGE-L F-measure against the reference,
   - **answer key-fact recall:** the share of `key_facts` found in the answer (case-insensitive),
   - **context key-fact recall:** the share of `key_facts` found in the retrieved context. This
     measures retrieval separately from generation,
   - latency (seconds) and context length (characters), reported descriptively.

Only the primary test decides the conclusion. Secondary results are reported as exploratory.

**Power (approximate).** For a paired test at α = 0.05 (two-sided) with 80% power, n ≈
(1.96 + 0.84)² / d_z² pairs are needed. With n = 200 that is d_z ≈ 0.20, a small standardised
effect; Wilcoxon's asymptotic relative efficiency (≈ 0.95) changes this only slightly.

## 8. Procedure

1. Write and freeze this protocol.
2. Write `qa_set.json`; run `compare.py validate`.
3. `compare.py run`: index the corpus once; shuffle the question order (seed 42); for each
   question run both systems in a random order (seed 42), clearing chat history each time; log
   everything to `run.log`; save `results.jsonl` (full records), the blinded file and the key.
4. Grade the blinded file → `judging/grades.jsonl`.
5. `compare.py analyze`: unblind, compute the metrics and tests → `stats.json`, `report.md`.

**Failures.** A query that raises an error is retried up to 2 times. If it still fails, it gets
correctness 0 and is listed in the report (it stays in the analysis, so failures count against
the system that had them).

## 9. Known limitations, stated in advance

- The grader also wrote the question set and the system. Blinding hides the system label, but
  answer style can sometimes reveal it.
- One LLM grader, no second human annotator, so there is no inter-rater agreement figure.
- One run per question. Generation at temperature 0.3 is not deterministic, so a re-run can
  differ slightly.
- One generator model (gpt-oss-20b) and one corpus of 12 synthetic case files. The results may
  not generalise to other models or real evidence.
