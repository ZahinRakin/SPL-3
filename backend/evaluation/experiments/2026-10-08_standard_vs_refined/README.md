# Experiment 2026-10-08: Standard RAG vs Refined (cascaded) RAG

A pre-registered, paired, blinded comparison of the two answering modes of the GraphRAG
Investigations system, on 200 questions over 12 case documents.

**Read in this order:** `protocol.md` (what was decided before the run) → `report.md` (results
and conclusion) → the raw files below if you want to check anything.

## Files

| File | What it is | Written by |
|---|---|---|
| `protocol.md` | Research question, systems, hypotheses (H₀/H₁), metrics, statistical tests, decision rule, limitations. **Frozen before any answer was generated.** | author, before the run |
| `qa/*.json` | The 200 questions, one file per source document | author, before the run |
| `qa_set.json` | Merged question set; every evidence quote checked verbatim against its document | `compare.py validate` |
| `index_stats.json` | Indexing time per document, graph and RAPTOR sizes, model names | `compare.py run` |
| `index_snapshot.json` | The exact index both systems queried (graph + passages + embeddings) | `compare.py run` |
| `run.log` | Full debug log of indexing and all 400 queries | `compare.py run` |
| `run_attempt1_crashed.log` | Log of the first run, which crashed during indexing before any answer was generated (see Deviations in `report.md`) | `compare.py run` |
| `results.jsonl` | One record per question per system: answer, retrieved context, entities, sources, latency, retries | `compare.py run` |
| `judging/blinded_answers.jsonl` | All 400 answers shuffled, system label replaced by a random id | `compare.py run` |
| `judging/key.json` | Random id → (question, system). Not opened by the grader before grading was saved | `compare.py run` |
| `judging/grades.jsonl` | The grader's score (0 / 0.5 / 1) and a short note for each blinded answer | grader |
| `judging/grading_completed_at.txt` | Time and SHA-256 of `grades.jsonl`, recorded before the key was opened | grader |
| `per_question.csv` | Every metric for every question and both systems (for plots or re-analysis) | `compare.py analyze` |
| `stats.json` | All test statistics, p-values, CIs, effect sizes | `compare.py analyze` |
| `discussion.md` | Interpretation, deviations from the protocol, limitations (appended to the report) | author, after analysis |
| `report.md` | The results write-up | `compare.py analyze` |

## Reproduce

From `graphrag-project/`, with Ollama running and `LLM_API_KEY` set:

```powershell
python -m evaluation.compare validate --exp evaluation/experiments/2026-10-08_standard_vs_refined
python -m evaluation.compare run      --exp evaluation/experiments/2026-10-08_standard_vs_refined --reuse-index
# grade judging/blinded_answers.jsonl → judging/grades.jsonl  ({"bid", "score", "note"} per line)
python -m evaluation.compare analyze  --exp evaluation/experiments/2026-10-08_standard_vs_refined
```

`--reuse-index` re-queries the saved `index_snapshot.json`, so a re-run uses exactly the same
index. Without it the corpus is re-indexed, and LLM extraction is not deterministic, so the graph
will differ slightly. Answers are generated at temperature 0.3, so a re-run will not reproduce
them word for word. `analyze` on the saved files is fully deterministic (seed 42).

To re-run into a fresh folder, copy `protocol.md` and `qa/` to a new folder and point `--exp` at it.
