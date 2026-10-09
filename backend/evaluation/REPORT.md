# Evaluation report — cascaded retrieval (RAPTOR → GraphRAG → HippoRAG)

Executed 2026-10-09 by Claude Opus 5.5 (Claude Code) following `../EVALUATION_PLAN.md`.
Every number below is computed by committed code from committed run files; the evidence for each
is listed next to it. The chronological record is `RUN_LOG.md`; money is in `budget_ledger.md`.

## 1. Summary

**Claim tested:** each component of the cascade helps on the question type it was designed for,
and the full system is no worse than plain dense retrieval elsewhere.

**Result:** not supported. In one controlled setup (gpt-oss-20b, nomic-embed-text, the same
context budget for every system), no component produced a statistically significant gain after
the pre-registered Holm–Bonferroni correction, and on real cross-document news (MultiHop-RAG) the
full system was **significantly worse** than dense retrieval (−10.3 points of accuracy). The one
positive signal is community summaries on broad "summarise" questions (GraphRAG-Bench, +7.4
coverage points, p = 0.038 before correction, 0.113 after), which is the only place the plan
expected them to help.

| | Hypothesis | Dataset (test) | Comparison | Metric | Result | 95 % CI | p | p (Holm) |
|---|---|---|---|---|---|---|---|---|
| H1 | Graph ranking helps multi-hop retrieval | MuSiQue, 800 q | S2 vs S0 | Recall@5 | 0.507 vs 0.495 (+0.011) | [−0.001, +0.024] | 0.121 | 0.242 |
| H2 | RAPTOR summaries help long documents | QuALITY, 722 q | S3 vs S2 | accuracy | 0.600 vs 0.597 (+0.003) | [−0.010, +0.015] | 0.824 | 0.824 |
| H3 | Community summaries help broad questions | GraphRAG-Bench Novel, 86 q | S4 vs S2 | coverage (judge) | 0.928 vs 0.855 (+0.074) | [+0.012, +0.141] | 0.038 | 0.113 |
| H4 | Full system ≥ dense on real cross-doc questions | MultiHop-RAG, 400 q | S5 vs S0 | accuracy (repo scorer) | 0.453 vs 0.555 (**−0.103**) | [−0.148, −0.055] | 2.5e−5 | **1.0e−4** |

Tests: H1, H3 Wilcoxon signed-rank (two-sided); H2, H4 exact McNemar. CIs: paired bootstrap,
10,000 resamples, seed 42. Evidence: `results_summary.json`, `benchmarks/*/results.json`.

**What this means for the project:** the cascade is a working, explainable system, but in this
setup it does not retrieve better than plain dense search, and spending a fixed context budget
on community summaries and graph facts can crowd out the passages that hold the answer (§4.4).
These results are about **this implementation with these models**; they are not evidence that
the published methods do not work with stronger models (see §7).

## 2. Setup

- **Generator:** `openai/gpt-oss-20b` via OpenRouter, reasoning effort low, **temperature 0**,
  no chat history; every call cached on disk (`llm_cache/`, rule 3).
- **Embedder:** Ollama `nomic-embed-text` (768-d), strict mode (an embedding failure stops the
  run instead of using random vectors).
- **Code under test:** pipeline in `backend/pipeline/`, frozen per dataset (commits in §6).
- **Systems** (one shared index per dataset, `harness/systems.py`):

| ID | Retrieval | Context |
|---|---|---|
| C0 | none (closed book; prompt asks for the model's own knowledge) | — |
| S0 | dense cosine over chunks ("Standard" mode of the app) | passages |
| S1 | BM25 (`rank_bm25`) over the same chunks | passages |
| S2 | dense + Personalized PageRank, fused by reciprocal rank (weight w) | passages |
| S3 | S2 + up to m RAPTOR summaries | passages + summaries |
| S4 | S2 + community summaries and knowledge-graph facts | passages + graph extras (≤ 35 % of budget) |
| S5 | full cascade ("Refined" mode of the app) | all of the above |

- **Context budget B** (characters, same for every system): MuSiQue 4,000; QuALITY and
  MultiHop-RAG 8,000; GraphRAG-Bench 12,000.
- **Tuned on dev only:** w on MuSiQue (Recall@5) → 0.25 and on MultiHop-RAG (Hits@4) → 0.5;
  m on QuALITY (accuracy) → 1. QuALITY used MuSiQue's w; GraphRAG-Bench used w = 0.5, m = 1.
- **Sanity test (plan §2):** with w = 0 and m = 0, S2's ranking was identical to S0's on 50/50
  dev questions, on MuSiQue (pilot and full index) and MultiHop-RAG
  (`benchmarks/*/tuning/sanity_S2w0_equals_S0.json`).
- **Judge:** Claude (independent sub-agents, one per batch of ≤ 50 items, given only the rubric
  and the blinded batch). Grades were validated and **sha256-hashed before the key was opened**
  (`benchmarks/*/judging/*_ALL.sha256`). No second judge and no human audit were performed
  (owner's decision, 2026-10-10); the blinded files are kept so they can be added later
  (`JUDGING_HANDOFF.md`).

## 3. Datasets (as downloaded; hashes in `benchmarks/*/data_hashes.txt`)

| Dataset | Source | Corpus indexed | Questions used | Split |
|---|---|---|---|---|
| MuSiQue (answerable) | HF `osunlp/HippoRAG_2` | 11,656 passages (as released) | 1,000 | 200 dev / 800 test |
| QuALITY | `nyu-mll/quality` v1.0.1 dev (htmlstripped) | 50 of 115 articles (seed 42) | 915 | 10 / 40 articles (193 / 722 q) |
| MultiHop-RAG | HF `yixuantt/MultiHopRAG`, ODC-BY | all 609 articles | 500 of 2,556 (stratified, seed 42) | 100 / 400 |
| GraphRAG-Bench Novel | HF `GraphRAG-Bench/GraphRAG-Bench`, MIT | **6 of 20 novels** (seed-42 order, budget) | 108 test q of levels 3–4 | 20 % / 80 % (stratified) |

Index sizes (`benchmarks/*/index/full/build_summary.json`):

| Dataset | Chunks | RAPTOR summaries | Entities | Relationships | Community summaries | Index cost |
|---|---|---|---|---|---|---|
| MuSiQue | 11,656 | 0 (one passage per document) | 106,867 | 124,350 | 1,588 | $3.387 |
| QuALITY | 975 | 253 | 9,797 | 11,114 | 227 | $0.356 |
| MultiHop-RAG | 5,406 | 1,665 | 55,076 | 71,765 | 776 | $2.551 |
| GraphRAG-Bench (6 novels) | 1,152 | 297 | 15,529 | 14,697 | 670 | $0.491 |

## 4. Results per dataset

### 4.1 MuSiQue — H1 (`benchmarks/musique/results.json`)

| System | Recall@2 | Recall@5 | MRR@10 | EM | F1 |
|---|---|---|---|---|---|
| C0 | — | — | — | 0.071 | 0.131 |
| S0 dense | 0.367 | 0.495 | **0.723** | **0.205** | **0.277** |
| S1 BM25 | 0.317 | 0.407 | 0.669 | 0.163 | 0.221 |
| S2 graph (w = 0.25) | 0.358 | **0.507** | 0.708 | 0.200 | 0.270 |
| S3 (= S2, no summaries exist) | 0.358 | 0.507 | 0.708 | 0.200 | 0.270 |
| S4 / S5 (see note) | — | — | — | 0.198 | 0.268 |

- H1: S2 − S0 Recall@5 = +0.011 [−0.001, +0.024], W/T/L 94/640/66, p = 0.121.
- By hop type (Recall@5, S0 → S2): 2-hop 0.610 → 0.615; 3-hop 0.451 → 0.460 and 0.530 → 0.560;
  4-hop 0.195 → 0.204, 0.190 → 0.286, 0.106 → 0.144 (small groups; exploratory).
- Claude judge (200 q): S2 0.273 vs S0 0.293, −0.020 [−0.07, +0.03], p = 0.51.
- Dev tuning: more graph weight lowered Recall@5 monotonically (w = 0.25 → 2: 0.500 → 0.381;
  dense 0.501) — `tuning/ppr_weight.json`.
- **Note (defect, §6):** on MuSiQue, S4/S5 received almost no graph extras (6/800 contexts),
  because community labels were too long for the budget; their numbers are effectively S2/S3.

### 4.2 QuALITY — H2 (`benchmarks/quality/results.json`)

| System | Accuracy | Hard subset (372 q) |
|---|---|---|
| C0 | 0.414 | 0.363 |
| S0 | **0.601** | **0.497** |
| S1 | 0.582 | 0.487 |
| S2 | 0.597 | 0.497 |
| S3 | 0.600 | 0.497 |
| S4 | 0.596 | 0.473 |
| S5 | 0.591 | 0.470 |

- H2: S3 − S2 = +0.003 [−0.010, +0.015], discordant 11 vs 9, p = 0.824. With m = 1, a RAPTOR
  summary reached the context for only 77 of 722 questions.
- Exploratory: S5 vs S0 −0.010 [−0.039, +0.019], p = 0.58; every retrieval system is ≈ 19 points
  above closed book (S0 vs C0 +0.187, p < 1e−6). Unparseable replies: 0–1 per system.

### 4.3 GraphRAG-Bench Novel — H3 (`benchmarks/graphrag_bench_novel/results.json`)

- **H3 coverage** (benchmark's own fact-coverage metric, run by the blinded judge; 324 reference
  facts over 86 Contextual Summarize questions): S4 0.928 vs S2 0.855, +0.074 [+0.012, +0.141],
  W/T/L 16/65/5, Wilcoxon p = 0.038; Holm-adjusted p = 0.113.
- **Pairwise** (108 level 3–4 questions, each judged in both A/B orders; a win must hold in both):
  S4 33 wins, S2 20 wins, 55 ties; sign test p = 0.098; position consistency 0.80.
- ROUGE-L against the reference (exploratory): Contextual Summarize C0 0.157, S0 0.201, S2 0.198,
  S4 0.192, S5 0.195; Creative Generation 0.141–0.167 for all systems. ROUGE does not separate
  the systems.

### 4.4 MultiHop-RAG — H4 (`benchmarks/multihop_rag/results.json`, `diagnosis_H4.json`)

| System | Accuracy (repo scorer) | Normalised scorer | Hits@4 | Hits@10 | MRR@10 |
|---|---|---|---|---|---|
| C0 | 0.505 | 0.525 | — | — | — |
| S0 | **0.555** | **0.608** | 0.587 | 0.780 | 0.724 |
| S1 BM25 | 0.548 | 0.585 | **0.618** | **0.805** | **0.781** |
| S2 | 0.520 | 0.548 | 0.555 | 0.799 | 0.673 |
| S3 | 0.533 | 0.565 | 0.554 | 0.795 | 0.671 |
| S4 | 0.453 | 0.493 | — | — | — |
| S5 | 0.453 | 0.500 | — | — | — |

- H4: S5 − S0 = −0.103 [−0.148, −0.055], discordant 26 vs 67, p = 2.5e−5 (Holm 1.0e−4).
- Claude judge (200 q) agrees: S5 0.498 vs S0 0.608, −0.110 [−0.182, −0.040], p = 0.002.
- Per type (accuracy S0 → S5): inference 0.852 → 0.844, comparison 0.448 → 0.254,
  temporal 0.440 → 0.231, null 0.277 → 0.383 (the full system says "insufficient" more often,
  which is right for null questions and wrong elsewhere).
- **Why (exploratory diagnosis):** with a fixed 8,000-character budget, the graph extras take up
  to 35 %; S5 fits 3.0 passages vs S0's 4.6, the share of gold articles in the context falls from
  0.526 to 0.389, community summaries are often off-topic, and "Insufficient information" on
  answerable questions rises from 63 to 102. The graph ranking itself (S2) is also below dense
  (−0.035, p = 0.11) and BM25 is the best retriever on this news corpus.
- C0 is 0.505: half of the questions (largely yes/no) are answerable without retrieval; gains
  over C0 are therefore small for every system.

## 5. Costs

Total evaluation spend **$8.05** of a $9.00 key limit (guard $8.50 not crossed), read from the
OpenRouter key's own usage counter before and after each step (`budget_ledger.md`). Indexing was
85 % of the spend. Answering costs ≈ $0.00009–0.00011 per question at 4–8k characters of context.
Query latency is not reported as a result: answers ran 16 at a time and many were cache hits, so
measured times reflect contention, not the systems (`run_stats` in each `results.json` has them).

## 6. Deviations from the plan (all logged when they happened)

1. **Owner sign-off at the [STOP] points was delegated** by the owner's instruction to run the plan
   end to end; each protocol records this.
2. **Budget re-scoping (§7):** the pilot showed indexing ≈ 2× the estimate (actual MuSiQue index
   1.4× the pilot rate), so **2Wiki, the official HippoRAG 2 baseline and the extra seeds were
   dropped**, and GraphRAG-Bench was cut to **6 of 20 novels** and to level 3–4 test questions.
3. **MuSiQue / 2Wiki format:** the release has no `idx` field; gold passages were matched by exact
   (title, text) — all 2,648 MuSiQue supporting paragraphs matched.
4. **Two pipeline defects found during the evaluation and fixed between datasets** (commit
   `b49276a`, with regression tests; rule 9):
   - RAPTOR summaries were never selected under the rank fusion (one RRF term against a chunk's
     two). Found on QuALITY **dev**; fixed before QuALITY was frozen. MuSiQue has no summaries, so
     it was unaffected.
   - Community-summary blocks named every source document (labels up to 79k characters), so on
     MuSiQue they never fit the budget and S4/S5 ran without graph extras. Found after the
     MuSiQue test run; MuSiQue's S4/S5 numbers are reported as run (they do not enter any
     hypothesis). Fixed before QuALITY, MultiHop-RAG and GraphRAG-Bench.
5. **Network incident:** the owner's Wi-Fi dropped during the MuSiQue test answers; 10 calls
   (S4 5, S5 5) timed out. Rule 6 scores them 0 (primary). They were retried with the frozen code
   (`runs/test/S4_retry.jsonl`, `S5_retry.jsonl`); with retries S4/S5 F1 is 0.271 instead of 0.268.
6. **w for QuALITY** was carried over from MuSiQue (one gold document per question gives no
   passage-level signal to tune on); **no tuning on GraphRAG-Bench**.
7. **GraphRAG-Bench's LLM metrics** (coverage, pairwise) were run by the blinded Claude judge with
   the benchmark's own prompts instead of an API model.
8. **Single judge:** the plan's second LLM judge (Codex) and the owner's 100-item human check
   were not carried out (owner's decision, 2026-10-10), so judge agreement (κ) is not reported.
   All judge numbers rest on one blinded LLM judge; they are secondary everywhere except H3.
   The blinded files and `JUDGING_HANDOFF.md` remain if this is done later.

Frozen protocols and code: MuSiQue `5f1dc24`; QuALITY `fe5ae6d` (code `b49276a`); MultiHop-RAG
`95ccc7e`; GraphRAG-Bench `9f9e2aa`. Each `protocol.md` lists its own deviations.

## 7. Limitations

- One generator (gpt-oss-20b at low reasoning effort) and one embedder; published GraphRAG /
  HippoRAG / RAPTOR gains use stronger models (GPT-4o-mini, Llama-3.3-70B, NV-Embed-v2).
- Question entities are found by keyword matching, not LLM NER, so PageRank seeds are weak on
  paraphrased questions.
- A fixed character budget makes every addition (summaries, graph facts) compete with passages;
  a different design (extras in addition to the passages) would test a different question.
- GraphRAG-Bench: 6 of 20 novels and 86 questions for H3 — low power.
- MuSiQue S4/S5 ran with the label defect (§6.4).
- LLM judges can prefer longer answers; mitigated (directness criterion, A/B swapping, blinded
  sub-agents), not removed; only one judge, no human check.
- C0 is high on MultiHop-RAG (0.505), so that benchmark's headroom is limited.

## 8. Where the evidence is

| What | Where |
|---|---|
| Plan | `../EVALUATION_PLAN.md` |
| Chronological log | `RUN_LOG.md` |
| Spend per step | `budget_ledger.md` |
| Harness code + tests | `harness/`, `../tests/test_eval_*.py` |
| Frozen protocols | `benchmarks/<dataset>/protocol.md`, `frozen_config.json` |
| Raw data hashes, dataset stats, splits | `benchmarks/<dataset>/data_hashes.txt`, `dataset_stats.json`, `splits/` |
| Tuning | `benchmarks/<dataset>/tuning/` |
| Every answer (question, context, passages, prompt hash, cache hit, timing) | `benchmarks/<dataset>/runs/<split>/<system>.jsonl` |
| Rankings for retrieval metrics | `benchmarks/<dataset>/runs/test/rank_<system>.jsonl` |
| Blinded judge files, grades, hashes, keys | `benchmarks/<dataset>/judging/` |
| Per-run logs (DEBUG) | `benchmarks/<dataset>/logs/` |
| Results | `benchmarks/<dataset>/results.json`, `results_summary.json` |
