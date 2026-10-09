# Evaluation run log

Chronological record of every step of the evaluation in `EVALUATION_PLAN.md` (repo root:
`../EVALUATION_PLAN.md`). Each line is written by the harness or by the agent running it
(Claude Opus 5.5, Claude Code), with a UTC timestamp. Detailed logs per run are in
`evaluation/benchmarks/<dataset>/logs/`.

## Decisions taken before any data was touched

- 2026-10-09 — **Owner instruction (verbatim excerpt):** "take look at this evaluation plan and execute it. there codex is also put as a judge but you do it alone. cause i will have to ask codex later after you are done. … log everything so that when i write paper i can give proofs". Interpreted as: run the whole plan end to end; the owner's sign-off at the plan's **[STOP]** points is delegated to this instruction (each protocol records this); Claude is the only judge for now, Codex grades the same blinded files later.
- 2026-10-09 — The Claude judge runs as **separate sub-agents** that are given only the blinded batch file and the rubric (rule 5). The orchestrating agent knows the system key, so it never grades.
- 2026-10-09 — Code baseline committed before any evaluation work: `6d49d07` (+ `.gitignore` fix `57a044a`). OpenRouter key: hard limit $9.00, usage before the evaluation $0.4575 (so the evaluation can spend at most ~$8.54; the plan's guard is $8.50).

## Log
- 2026-10-09 05:35:40 UTC — adapter `musique`: 11656 docs, 1000 questions (dev 200, test 800), splits sha256 dev=c7d6362f0e6e test=bc2f4ba2b97e
- 2026-10-09 05:35:51 UTC — adapter `2wiki`: 6119 docs, 1000 questions (dev 200, test 800), splits sha256 dev=de88ffceebc7 test=e553ed3b6d89
- 2026-10-09 05:35:51 UTC — adapter `quality`: 50 docs, 915 questions (dev 193, test 722), splits sha256 dev=b428fa0439c8 test=decdfca759cb
- 2026-10-09 05:35:51 UTC — adapter `multihop_rag`: 609 docs, 500 questions (dev 100, test 400), splits sha256 dev=81d0de20985b test=2e7f0cf19d61
- 2026-10-09 05:35:51 UTC — adapter `graphrag_bench_novel`: 20 docs, 2010 questions (dev 401, test 1609), splits sha256 dev=9752311c5d42 test=582769a498b1
- 2026-10-09 05:38:06 UTC — index `musique` / `pilot200` started: 200 docs, key usage $0.4575, cache entries 0, log `benchmarks\musique\logs\index_pilot200_20261009_113806.log`
- 2026-10-09 05:39:09 UTC — index `musique` / `pilot200` finished: cost $0.0000, 353 new LLM responses, 200 chunks, 0 summaries, 2511 entities, 2072 relationships, 153 community summaries, 52.1 s
- 2026-10-09 05:41 UTC — Pilot cost settled: **$0.0538** for 200 MuSiQue passages (353 LLM calls). Re-estimate in `budget_ledger.md`: full plan ≈ $11.3 > $8.44 left; MuSiQue indexing ≈ 2× the plan's estimate → per plan §7, **2Wiki, official HippoRAG 2 and extra seeds are dropped**, GraphRAG-Bench will be subsampled. [STOP: owner sign-off after pilot] — delegated by the owner's instruction (see top).
- 2026-10-09 05:41:45 UTC — sanity test `musique`: S2(w=0,m=0) ranking == S0 on 50/50 dev questions (top 20) → **PASS** (`benchmarks\musique\tuning\sanity_S2w0_equals_S0.json`)
- 2026-10-09 05:41:55 UTC — index `musique` / `full` started: 11656 docs, key usage $0.5261, cache entries 353, log `benchmarks\musique\logs\index_full_20261009_114154.log`
- 2026-10-09 06:08:20 UTC — index `musique` / `full` finished: cost $3.3870, 13043 new LLM responses, 11656 chunks, 0 summaries, 106867 entities, 124350 relationships, 1588 community summaries, 1390.2 s
- 2026-10-09 06:08 UTC — MuSiQue full index built: 11,656 passages, 106,867 entities, 124,350 relationships, 1,588 community summaries, 12,448 isolated entities, 15,287 components, 2 empty extractions; 1,390 s; **cost $3.387** (cumulative $3.456; the pilot's true cost was $0.0686 after late billing). Per-extraction cost is 1.4× the pilot estimate → remaining scope to be re-planned after measuring MuSiQue answer costs.
- 2026-10-09 06:09:32 UTC — answers `musique` dev costprobe started: systems S0, 50 questions, w=1.0 m=2 budget=4000, key usage $3.9131
- 2026-10-09 06:09:57 UTC — sanity test `musique`: S2(w=0,m=0) ranking == S0 on 50/50 dev questions (top 20) → **PASS** (`benchmarks\musique\tuning\sanity_S2w0_equals_S0.json`)
- 2026-10-09 06:12:43 UTC — answers `musique` dev costprobe finished: {'S0': {'answered': 50, 'skipped_done': 0, 'errors': 0, 'seconds': 10.6}}, cost $0.0054 (key $3.9131 → $3.9185)
- 2026-10-09 06:13 UTC — Answer-cost probe (S0, 50 MuSiQue dev q): $0.0054 → $0.000108/answer. Re-plan written to budget_ledger.md: MuSiQue, QuALITY, MultiHop-RAG in full; GraphRAG-Bench subsampled to what is left (≈ 8 novels).
- 2026-10-09 06:26:05 UTC — tuning `musique` (dev, 200 q, by recall@5): S0 0.5012, S1 0.4037, S2-w0.25 0.4996, S2-w0.5 0.4813, S2-w1.0 0.4350, S2-w2.0 0.3808 → chosen ppr_weight = **0.25**
