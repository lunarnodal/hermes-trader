# Causality Analysis Report — 2026-09-30

## Executive Summary

- 26 predictions in the trailing 7d window: **23% overall win rate** (pipeline tool). The last verified batch (09-24 → 09-28, 18 rows) is 44% (8/18).
- Direction breakdown (verified 09-24→09-28): **bullish 4/10 (40%), bearish 1/4 (25%), mixed 0/6 (0%), neutral 3/3 (100%)**.
- Critic discrimination re-measured: **approve 6/10 (60%) vs reject 1/7 (14%) vs challenge 0/3 (0%)** — reject remains the worst bucket, reconfirming card `critic-verdict-quality-measurement` (t_1854b679).
- **One new causal finding** → card below: single-signal individual-stock entries produced the CLS -6.7% stop-out despite the sector call being verified correct.
- No new cross-sector causal rules found; 4 existing cards are corroborated by fresh data, none contradicted. Signal "other" classification is healthy (3/2000 sampled).

## 48-Hour Prediction & Signal Review

Unverified (09-29, 09-30 batches, 18 rows): today's notable calls — Financials bearish 0.80 (approve), Technology bullish 0.60 (approve), Consumer bearish 0.55 (approve). All 09-29 and 09-30 predictions are pending verification; no outcome analysis possible yet.

Verified batch (09-24 → 09-28, 18 rows):

| Sector/direction | Calls | Correct | Notes |
|---|---|---|---|
| Tech/AI bullish | 2 | 2 | avg conf 0.75 — only consistently-correct sector direction in window |
| Merger arb neutral | 3 | 3 | avg conf 0.65, all critic-approved |
| Healthcare bullish | 3 | 0 | actual: bearish/neutral/neutral; now critic-reject (09-29, 09-30) |
| Materials bullish | 3 | 0 | all critic-reject |
| Financials bearish | 3 | 1 | correct only 09-28; 09-24/09-25 wrong (market flat/bullish) |
| Mixed (any sector) | 6 | 0 | energy, industrials, consumer, market — includes a 0.5-conf call |

### Signal types preceding price moves

- **Tech rally (09-24, 09-28, 09-30)** preceded by ai_infrastructure cluster: "Why is IonQ stock surging" (investing-com, bullish, conf 0.75), "Top 10 things to watch Wednesday" (META/MSFT), hyperscaler capex narratives. Fits existing rule `stock-surge-explainer-momentum-rule` (t_7066998e) — now 3rd occurrence (IONQ). Rule reinforced, no new card.
- **Financials dip 09-28** preceded by Fed-policy/rate-regime cluster (financials bearish 0.75 approve 09-27/09-28). Only correct financials bearish call of the week.
- **Commodities soft**: "Brent Drops Below $100" signal (09-30) — inverse of the `crude-surge-market-rule` candidate; insufficient evidence either direction. No action.
- **Static multi-sector tagging persists**: "OECD: UK economy will grow less than expected" tagged commodities/energy/materials/utilities; "Ethiopian Airlines Cancels Tigray Flights" tagged africa/aviation/industrials/transportation. Both are the generic-headline static 5-sector tagging pattern covered by existing card `conflict-headline-sector-tag-false-positives` (t_41c665ca). Corroborated, not new.

## Trade Forensics: CLS −6.7% (hackathon account)

- Entry 2026-09-24 19:35 UTC @ $378.03, 10 shares, **signal_count=1, avg conf 75%, score 0.938** (portfolio.db recommendation #426, ai_infrastructure).
- Stop-out 2026-09-28 @ $352.69, **−$253.40 (−6.7%)**.
- Key finding: the 09-24 Technology/AI sector prediction (bullish 0.75, approved) was **verified correct the same day** (actual bullish) and again 09-28 — the sector call was right, the single-stock pick was not. The prediction's own reasoning_summary explicitly stated "a broad sector ETF is preferred to mitigate idiosyncratic stock risk," yet selection proceeded with one signal and one stock. The model's risk-reduction reasoning was not honored by the stock selector.

## Critic Verdict Discrimination (re-measured, 09-24 → 09-28)

- approve: 6/10 = 60% | reject: 1/7 = 14% | challenge: 0/3 = 0%
- Reject bucket contains the only correct call that was killed (Industrials bullish 0.5, 09-28). Same pattern as the 09-22 report — reject is the worst-discriminating bucket while it is an active portfolio gate. Reinforces `critic-verdict-quality-measurement` (t_1854b679). No new card.

## Cross-Reference vs Existing Indirect Dependencies

30 rules reviewed (incl. top 20 newest from lessons.db). Findings:
- No proposed new rule duplicates an existing one.
- Corroborated: `stock-surge-explainer-momentum-rule` (IONQ, 3rd occurrence), `conflict-headline-sector-tag-false-positives` (2 new static-tag examples 09-30), `critic-verdict-quality-measurement` (fresh bucket data), `mixed-direction-hard-block-gate` (mixed 0/5 in 7d incl. a 0.5-conf call — the 0.5-conf wrong mixed call is inside that card's <55% scope).
- Contradicted: none. The 09-25 neutral/flat-market day (all actual_direction=neutral) again supports `low-vol-regime-direction-decay` (t_7f098efa) — 5 directional calls that day, 0 correct.

## Signal Quality

- 7-day event-type breakdown (2000 sampled): "other" down to **3/2000** (0.15%) — classifier healthy. The 3 "other" samples (PU Prime Argentina FX expo, Europa EG-08 farm-out extension, Stockport mill restoration) are one-offs for Sunday's classification review; no systematic pattern.
- editorial_opinion is 23% of all ingested signals (459/2000) — highest-volume event type and a standing noise source; no card (existing scope, low priority).

## Kanban Cards to Create
1. `single-signal-stock-entry-concentration` — **Enforce minimum signal count or honor ETF-preference reasoning for individual-stock entries** (Risk: medium | Sectors: pipeline, risk | Owner: coder)
   - Evidence: CLS stop-out −6.7% ($−253.40, 2026-09-24→28) entered on signal_count=1 (avg conf 75%, score 0.938) while the 09-24 Tech/AI bullish sector call was verified correct that day and again 09-28; prediction #705 reasoning_summary explicitly states "a broad sector ETF is preferred to mitigate idiosyncratic stock risk" yet selection took the single stock — two independent sources: portfolio.db transactions/recommendations #426 + paper_trading.db predictions verified outcomes
   - Expected impact: single-signal setups route to the sector ETF (or require ≥2 corroborating signals for a stock entry), eliminating idiosyncratic stop-outs when the sector direction is already verified right; protects both accounts' LLM trade flow
