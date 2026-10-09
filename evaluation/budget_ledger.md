# Budget ledger (EVALUATION_PLAN.md §1 rule 4, §7)

Spend is read from the OpenRouter key's own usage counter (`GET /api/v1/key`, field `usage`,
cumulative USD) before and after each step, after it stops changing. The key has a hard limit of
**$9.00** set on OpenRouter. Usage before the evaluation: **$0.4575** (earlier development work).
Guard: stop and ask the owner if cumulative evaluation spend would exceed **$8.50**.

Prices (OpenRouter, `openai/gpt-oss-20b`): cheapest provider $0.018 / M input, $0.09 / M output.
`LLM_PROVIDER_SORT=throughput` routes to faster, pricier hosts, so estimates assume ~3×
($0.054 / M input, $0.27 / M output). Hidden reasoning tokens are billed as output.

| # | Step | Estimate (USD) | Key usage before | Key usage after | Actual (USD) | Cumulative eval spend | Notes |
|---|---|---|---|---|---|---|---|
| 1 | MuSiQue pilot index (200 passages) | 0.04 (≤ 0.10) | | | | | 200 extractions × (~330 in + ~700 out tokens) + community summaries |

Row 1 actual: $0.5113 − $0.4575 = **$0.0538** for 353 LLM calls (200 extractions + 153 community
summaries) → ≈ $0.00020 per extraction, ≈ $0.00009 per community summary (split estimated from
token counts). The usage counter lagged > 60 s; the harness now waits ≥ 150 s for it to settle.

### Re-estimate after the pilot (2026-10-09)

| Step | Plan estimate | Pilot-based estimate | Basis |
|---|---|---|---|
| MuSiQue index (11,656 passages) | $1–2 | **~$2.8** | 11,656 × $0.0002 + ~5,000 community summaries × $0.00009 |
| MuSiQue answers (C0 + S0–S5 on 800 test) | $0.5–1 | ~$0.7 | 5,600 calls × ~1.3k in / 0.2k out tokens |
| QuALITY index + dev tuning + test answers | $0.5–1.5 | ~$1.3 | ~1,900 index calls; ~5,600 answer calls at 8k chars |
| MultiHop-RAG index + answers | $1–2 | ~$2.0 | ~5,000 chunks + ~1,500 RAPTOR summaries + communities; 2,800 answers |
| GraphRAG-Bench Novel, all 20 novels, all 1,609 test questions × 7 systems | $1–3 | ~$4.5 | 7,500 index calls + 11,300 answers at 12k chars |
| 2Wiki / official HippoRAG 2 / extra seeds | $2.5–4 | — | |

Total ≈ $11.3 > $8.44 remaining, and the MuSiQue index is ≈ 2× the plan's midpoint, so the plan's
§7 rule applies: **drop priorities 5–7 (2Wiki, official HippoRAG 2, extra seeds)**, then
**subsample GraphRAG-Bench** (decided when it is reached, from the money left). Order of work
unchanged (MuSiQue → QuALITY → MultiHop-RAG → GraphRAG-Bench). The $8.50 guard is checked before
every step.

| # | Step | Estimate (USD) | Key usage before | Key usage after | Actual (USD) | Cumulative eval spend | Notes |
|---|---|---|---|---|---|---|---|
| 1 | MuSiQue pilot index (200 passages) | 0.04 | 0.4575 | 0.5113 | 0.0538 | 0.0538 | see above |
| 2 | MuSiQue full index (11,656 passages) | 2.80 | | | | | estimate from pilot; cumulative after ≈ 2.85 ≤ 8.50 |
