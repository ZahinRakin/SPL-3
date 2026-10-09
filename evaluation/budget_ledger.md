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
| 1b | (pilot, late billing) | — | 0.5113 | 0.5261 | 0.0148 | 0.0686 | the pilot's charges kept arriving after the first reading; pilot total = **$0.0686** |
| 2 | MuSiQue full index (11,656 passages) | 2.80 | 0.5261 | 3.9131 | **3.3870** | **3.4556** | 13,043 new LLM responses (11,654 extractions + 1,588 community summaries − 199 pilot hits); ≈ $0.00028 per extraction, 1.4× the pilot rate; 1,390 s |
| 3 | MuSiQue dev answer-cost probe (S0, 50 dev questions) | 0.01 | | | | | measures the real per-answer cost before re-planning |

Row 3 actual: **$0.0054** for 50 answers (S0, B = 4,000 chars) → **$0.000108 per answer**.
Cumulative ≈ **$3.461**.

### Re-plan after the MuSiQue index (measured rates: extraction $0.00028, answer @4k chars $0.000108)

| Step | Estimate | Running total |
|---|---|---|
| MuSiQue test answers: C0, S0, S1, S2, S4 × 800 (S3 ≡ S2 and S5 ≡ S4 here: no summaries exist, so their prompts are identical and come from the cache at no cost) | $0.39 | $3.85 |
| QuALITY index (≈ 990 chunks, ≈ 280 RAPTOR summaries) + dev tuning of m (S2 + S3×3 on 193 q) + test (7 × 722) | $1.22 | $5.07 |
| MultiHop-RAG index (whole corpus, ≈ 5,300 chunks, ≈ 1,500 summaries) + test (7 × 400) | $2.40 | $7.47 |
| GraphRAG-Bench Novel: **subsample** to what is left under $8.20 (a $0.30 margin below the $8.50 guard): ≈ 8 of 20 novels, levels 3–4 test questions | ≈ $0.7 | ≈ $8.2 |

Decision: continue in plan order; the GraphRAG-Bench subsample size is fixed when it is reached,
from the money actually left.
| 4 | MuSiQue test: rankings (free) + answers C0,S0,S1,S2,S3,S4,S5 × 800, + retry of 10 timeouts | 0.39 | 4.3118 | 4.6193 | **0.3075** | **4.1618** | answer run reported $0.307; 10 retries $0.0005; S3 ≡ S2 and (by a defect) S4 ≈ S2 → cache hits |
| 5 | QuALITY index (50 articles) | 0.40 | 3.9185 | 4.2748 | **0.3564** | **3.8173** | 975 chunks, 253 RAPTOR summaries, 9,797 entities, 227 community summaries; ran while MuSiQue test rankings (no LLM) ran |
| 6 | QuALITY dev tuning of m: S2 + S3 (m = 1, 2, 4) × 193 dev q, then the summary-fix prototype S3 (m = 1, 2, 4) × 193 | 0.11 | 4.2748 | 4.3118 | **0.0370** | **3.8543** | 1,351 dev answers ≈ $0.000027 each (many short MC replies; S3 ≡ S2 before the fix → cache hits) |
| 7 | QuALITY test: C0, S0–S5 × 722 | 0.30 | 4.6193 | | | | dev MC answers cost ≈ /usr/bin/bash.00003–0.00011 each |
| 9 | MultiHop-RAG test: C0, S0–S5 × 400 | 0.30 | 7.6125 | 7.8912 | **0.2787** | **7.4337** | 2,800 answers, 0 errors. Left under the guard: $1.066 |
| 10 | GraphRAG-Bench: 6 novels (241k words) per-novel index + C0, S0–S5 × 108 test q | 0.68 | 7.8912 | | | | subsample sized to the money left with a $0.30 margin (`subsample.json`) |
