# Second judge (Codex) and owner human check — what is left to do

The Claude judge has graded everything (EVALUATION_PLAN.md §6). Two gradings remain, both blind,
on **exactly the same files** the Claude judge saw. Nothing here costs API money.

## 1. Codex (second LLM judge)

Give Codex `backend/evaluation/harness/rubrics/JUDGE_INSTRUCTIONS.md` and ask it to follow it, with
judge name **`codex`**, one batch at a time, writing one output file per batch:

| Dataset folder (`backend/evaluation/benchmarks/…`) | Rubric | Batches to grade | Output files |
|---|---|---|---|
| `musique/judging/` | `correctness.md` | `blinded_correctness_S2_S0_001…008.jsonl` | `grades_codex_correctness_S2_S0_001…008.jsonl` |
| `multihop_rag/judging/` | `correctness.md` | `blinded_correctness_S5_S0_001…008.jsonl` | `grades_codex_correctness_S5_S0_001…008.jsonl` |
| `graphrag_bench_novel/judging/` | `coverage.md` | `blinded_coverage_S4_S2_001…004.jsonl` | `grades_codex_coverage_S4_S2_001…004.jsonl` |
| `graphrag_bench_novel/judging/` | `pairwise.md` | `blinded_pairwise_S4_S2_001…005.jsonl` | `grades_codex_pairwise_S4_S2_001…005.jsonl` |

Note for coverage: the reference facts inside `blinded_coverage_*` were extracted by the Claude
judge (stage 1, `facts_claude_*.jsonl`); both judges score coverage against the same facts, so
their scores are comparable. (Optionally Codex can also redo stage 1 on `blinded_facts_*.jsonl`
→ `facts_codex_*.jsonl`; that is not needed for agreement.)

Codex must not open `key_*.json`, `runs/`, `logs/`, `results*`, `protocol.md`, or any
`grades_claude_*` / `facts_claude_*` file. Prompt to use, per batch:

> Read `backend/evaluation/harness/rubrics/JUDGE_INSTRUCTIONS.md` and `backend/evaluation/harness/rubrics/<rubric>`.
> Grade `backend/evaluation/benchmarks/<dataset>/judging/<batch>` and write
> `backend/evaluation/benchmarks/<dataset>/judging/grades_codex_<name>_<NNN>.jsonl`. Open no other file.

After Codex has finished all batches, run (from `graphrag-project/`):

```powershell
python -m backend.evaluation.harness.blind validate --dataset musique --name correctness_S2_S0 --judge codex
python -m backend.evaluation.harness.blind validate --dataset multihop_rag --name correctness_S5_S0 --judge codex
python -m backend.evaluation.harness.blind validate --dataset graphrag_bench_novel --name coverage_S4_S2 --judge codex
python -m backend.evaluation.harness.blind validate --dataset graphrag_bench_novel --name pairwise_S4_S2 --judge codex
# hash before anything reads the key (rule 5):
python -m backend.evaluation.harness.blind hash --dataset musique --pattern "grades_codex_correctness_S2_S0_*.jsonl"
python -m backend.evaluation.harness.blind hash --dataset multihop_rag --pattern "grades_codex_correctness_S5_S0_*.jsonl"
python -m backend.evaluation.harness.blind hash --dataset graphrag_bench_novel --pattern "grades_codex_coverage_S4_S2_*.jsonl"
python -m backend.evaluation.harness.blind hash --dataset graphrag_bench_novel --pattern "grades_codex_pairwise_S4_S2_*.jsonl"
python -m backend.evaluation.harness.final      # adds Claude–Codex agreement (κ) to results_summary.json
```

## 2. Owner human check (100 items per correctness set)

Files: `musique/judging/owner_correctness_S2_S0.jsonl` and
`multihop_rag/judging/owner_correctness_S5_S0.jsonl` (100 random items each, seed 42, already
blinded — same `bid`s as the judge files). Grade with `rubrics/correctness.md` (1 / 0.5 / 0) and
save `grades_owner_correctness_S2_S0.jsonl` / `grades_owner_correctness_S5_S0.jsonl` in the same
folder, one line per item: `{"bid": "...", "score": 1, "reason": "..."}`. Then re-run
`python -m backend.evaluation.harness.final`. Plan rule: if κ(owner, a judge) < 0.6 on a dataset, that
judge's numbers there are not used as evidence.
