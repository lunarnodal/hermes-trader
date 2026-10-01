# Causality Analysis Report — 2026-09-25

Run: 9:00 AM ET Friday. Window: last 48h signals (created since 2026-09-23 ~13:00 UTC); verified outcome analysis covers 09-22 → 09-23 predictions (09-24/09-25 predictions not yet verified — next-day drift pending).

## Pipeline Health (pre-check)
- inference_rules active: 309; indirect_dependencies: 291; parse errors: 0 (month)
- Qdrant signals: 1,370 (1d) / 3,746 (3d) / 6,339 (7d)
- Closed trades last 7d: 0 (no executions; gates blocking everything)
- 7-day prediction win rate: 38% (26 verified); 48h verified subset: 5/17 ≈ 29%

## Verified Outcomes vs Critic Verdict (last ~2 trading days, 17 verified predictions)
| Verdict | n | correct | win rate |
|---|---|---|---|
| reject | 4 | 4 | 100% |
| challenge | 6 | 2 | 33% |
| approve | 6 | 1 | 17% |

The critic verdict ranking is now fully inverted vs the pre-fix assumption: **approve is the worst bucket, reject is the best.** This matches the 09-23 re-verification baseline (approve 29.6% / challenge 26.3% / reject 46.7% over 301 contaminated predictions) and sharpens it on the clean window. High-confidence approves specifically failed: Tech bullish 0.63 and 0.7 (both wrong), Healthcare bullish 0.75 (wrong), Financials bearish 0.65 (wrong). Only Financials bearish 0.75 approve was correct.

Action: no new card — `critic-verdict-quality-measurement` (2026-09-22 report) already covers this; 48h data is added evidence for that card. Do NOT flip the critic gate on this sample alone (n=17), consistent with the baseline note that recalibration decisions must use the full re-verified set.

## Sector Patterns (48h)
- **Technology bullish persists in failure**: 09-22 bullish 0.7 wrong, 09-23 bullish 0.63 wrong; 09-24 (0.75) and 09-25 (0.7) bullish issued again and unverified. Two more data points reinforce existing card `technology-bullish-gate-5-violation` — no new card.
- **Energy: chronic low-confidence miss**: every energy prediction in window was mixed/bearish at conf 0.3–0.55 and wrong where verified; 09-24/09-25 issued at 0.3 mixed (hard-block territory). Existing cards `bearish-macro-energy-rotation` and `crude-surge-market-rule` cover the causal side; nothing new.
- **Financials bearish**: 09-23 correct (conf 0.75), 09-22 wrong (conf 0.65) — no stable signal yet.
- **Industrials**: 09-22 bullish correct, 09-23 mixed wrong — mixed signal, no rule change warranted at n=2.

## New Causal Findings (2-source verified)

### Finding 1 — Generic violence/conflict headlines auto-tagged to a fixed 5-sector bearish set
Two independent signals in the 48h window carry the **identical** sector set (commodities, defense, energy, industrials, materials) at bearish 0.75:
- "Barefoot and terrified, Yemenis flee the Houthi advance" (finnhub-general) — plausibly relevant (Red Sea shipping → energy)
- "Three Dead in Another School Shooting in Southern Philippines" (bloomberg-markets) — no plausible transmission to any of those sectors

The identical 5-sector set across unrelated conflict events indicates a static keyword-based "conflict" mapping rather than economic relevance analysis. These false-positive bearish tags flow into sector scoring for energy/defense/materials and contaminate the signal base used by the 09-24/09-25 outlook queries (e.g., energy, materials, industrials).

Cross-check vs existing indirect_dependencies: no existing rule covers violence-headline tagging; existing geopolitics rules (Iran tensions → biotech, US-China → semis) are specific and unaffected. Not a duplicate.

### Finding 2 — Neutral-direction predictions (merger arb) are structurally excluded from the learning loop
- predictions table: 7-day merger-arb neutral win rate 0% (n=2; 09-22 neutral 0.35 approved, actual direction bearish, scored was_correct=0). Neutral can never match a directional outcome, so it is **always** scored wrong.
- signal_ledger (3 days): all 3 merger-arb entries (09-23, 09-24 ×2) blocked at `direction_confidence` with adj_confidence 0.6.

Causal chain: neutral predictions are (a) killed by the direction gate when they clear confidence, or (b) recorded as permanently wrong when they don't pass — so the merger-arb signal class generates **zero usable learning signal** and drags the overall win rate down as a structural artifact rather than a skill deficit.

Cross-check vs existing rules/cards: no existing card covers neutral-direction verification (manifest checked; `merger-arb-energy-2nd-occurrence` is a different rule). Not a duplicate.

## Observations (insufficient for a card — single data point, monitor next run)
- Ticker extraction contamination: "LPP shares surge on strong profit growth" (investing-com, bullish 0.85) tagged tickers LPP, **TGT, TRON** — TGT and TRON (a crypto token) have no connection to a Polish retail group. If confirmed on a second example, candidate card for investing-com ticker extraction.
- "The High-Stake Trump-Xi Meeting: Brace For Volatility" (neutral 0.65) spans 7 sectors. Existing rule "us-china geopolitics / trade policy → semiconductors & data centers" covers the semis leg; no new rule for summit-event volatility at n=1.
- Insider sale signal (Planet Labs CFO, neutral 0.75) correctly neutral — consistent with prior insider-trading consistency work.

## Signal Classification Notes
All 50 signal_ledger entries in the 3-day window carry event_type='other' — expected, since `fix-signal-ledger-sector-attribution` (manifest) covers population of these fields and the Sunday classification review is a separate scheduled job. The conflict-headline mis-tagging (Finding 1) is upstream of event_type (news→sector tagging in the feed), so it is handled here, not in the Sunday job.

## Kanban Cards to Create
1. `conflict-headline-sector-tag-false-positives` — **Investigate static 5-sector auto-tagging of generic violence/conflict headlines** (Risk: low | Sectors: signal-quality, pipeline | Owner: coder)
   - Evidence: 48h signals window: bloomberg-markets "school shooting, Southern Philippines" and finnhub-general "Houthi advance" both auto-tagged the identical set commodities/defense/energy/industrials/materials at bearish 0.75; the shooting has no economic transmission to any tagged sector, proving a static keyword mapping.
   - Expected impact: remove false-positive bearish risk-off tags from energy/defense/materials scoring; fewer contaminated inputs to daily outlook queries.
2. `neutral-direction-prediction-verification` — **Fix neutral-direction prediction verification and gating** (Risk: low | Sectors: pipeline, verification | Owner: coder)
   - Evidence: predictions table 7d merger-arb neutral win rate 0 of 2 with 09-22 row scored wrong against a directional outcome; signal_ledger 3d: all 3 merger-arb entries blocked at direction_confidence (adj 0.6). Neutral predictions are either gate-killed or permanently scored wrong, so the class produces zero learning signal.
   - Expected impact: exclude neutral from directional win-rate calibration or add a neutral verification class; removes a structural drag on the 38% win-rate baseline.
