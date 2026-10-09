# Instructions for an LLM judge (Claude or Codex)

You are an independent judge for a blinded evaluation (EVALUATION_PLAN.md §6). Follow these
steps exactly. They are the same for every judge, so the judges' grades can be compared.

## What you may read

- The rubric file named in your task (one of `correctness.md`, `pairwise.md`,
  `coverage_facts.md`, `coverage.md` in this folder).
- The one blinded batch file named in your task: `evaluation/benchmarks/<dataset>/judging/blinded_*.jsonl`
  (or `facts_*.jsonl` for coverage stage 1).

## What you must NOT read or do

- Do not open `key_*.json`, `runs/`, `logs/`, `results*`, `protocol.md`, the harness code, or any
  other judge's grade file (`grades_*`, `facts_*_<otherjudge>`).
- Do not try to work out which system produced an answer.
- Do not use tools to look anything up; the gold/reference answer is correct by definition.
- Do not change grades you have already written, in this batch or an earlier one.

## Output

Write your grades for batch `blinded_<name>_<NNN>.jsonl` to
`judging/grades_<judge>_<name>_<NNN>.jsonl` (judge = `claude` or `codex`), one JSON object per
input line, in the input order, in the format the rubric gives. For coverage stage 1
(`blinded_facts_<NNN>.jsonl`), write `judging/facts_<judge>_<NNN>.jsonl`.

Every input line must get exactly one output line with the same `bid` (or `fid`). Valid JSON
only; no commentary in the file.
