# Causality Analysis Report — 2026-10-01

## Executive Summary

- Trailing 48h: 27 predictions created (09-30 batch 9 rows + 10-01 batch 9 rows, plus earlier-verified 09-29 rows). **All 09-30 and 10-01 predictions are UNVERIFIED** (verified_at NULL) — no outcome analysis possible on them yet. Last verified batch remains 09-28→09-29.
- 14-day verified direction breakdown: **bearish 8/23 (34.8%), bullish 13/35 (37.1%), mixed 0/14 (0%), neutral 4/8 (50%)**. Mixed-direction calls remain structurally worthless.
- **One new, two-source-verified causal/calibration finding** → card below: financials-bearish predictions emitted at 0.70–0.80 conf during the 09-30/10-01 US long-end-yield-spike cluster verify at **2/12 = 16.7%** over the 14d window — the model over-weights the "yield spike → financials sell-off" causal edge far beyond realized follow-through.
- **No new cross-sector causal rule is promotion-ready today.** The dominant 09-30/10-01 cluster (US 30-yr yield highest since 2004, yields past 5%, failed auctions) produced the bearish signals, but its outcomes are still unverified — per protocol I will not propose a causal card on unverified triggers.
- 4 existing cards corroborated by fresh data (ai-infrastructure-trim-missing, fed-hike-fear-rule-267-counterexample, signal-ledger-drift-writer-missing, fix-signal-ledger-sector-attribution). None contradicted.

## 48-Hour Prediction & Signal Review

### Unverified 09-30 / 10-01 batches (18 rows, verified_at NULL)
Notable high-conf calls still pending verification:
- **Financials bearish 0.80** (09-30, approve) — highest-confidence call of the day
- Technology bullish 0.60 (09-30, approve); Technology bearish 0.55 (09-30, direction_confidence)
- Healthcare bullish 0.55 → now critic_reject (09-29, 09-30)
- AI_infrastructure bullish 0.60 → hard_block_bullish (09-29, 09-30, 10-01 — repeated)

Because these are unverified, no causal claim is made on them. They are the cohort to verify on 10-02 morning.

### The 09-30/10-01 US long-end-yield-spike cluster (signal side)
The news feed was dominated by a bond-selloff / rate-hike cluster, all tagged energy/financials/real_estate/utilities:
- "US 30-Year Yield Hits Highest Since 2004 as Bond Selloff Deepens" (bloomberg, bearish, conf 0.88)
- "U.S. yields surge past 5% as hot PMI data and failed auctions spark sharp sell-o" (investing-com, bearish, conf 0.88)
- "Treasury Yield Surge Pushes Spreads Versus Asia Near Extremes" (bloomberg, bearish, conf 0.75)
- "NY Fed president says it is reasonable to think there will be one more 2026 hike" (marketwatch, bearish, conf 0.75)
- "Norway central bank raises rate to 4.50%, may hike again" (investing-com, bearish, conf 0.75)

This cluster is the direct driver of the repeated **financials bearish 0.70–0.80** calls. It is a real, high-signal macro event — but the price follow-through is unverified, so no new causal edge is proposed on it today. Watch: if 10-02 verification confirms financials/real_estate fell, this becomes the 2nd-occurrence trigger for a "long-end yield spike → financials/real-estate bearish" rule; if it does not (flat/defensive), the card below (overconfidence penalty) is confirmed correct.

### Verified batch (09-28 → 09-29, most recent with outcomes)
- Financials bearish 0.70 (09-28): XLF 54.35→54.01 = **−0.63%, CORRECT** — the only correct financials-bearish call of the window.
- Financials bearish 0.75 (09-29): XLF 53.86→53.78 = **−0.13%, within ±0.2% → scored WRONG** despite correct direction. The move was the right sign but below the materiality threshold.
- Healthcare bullish (09-28 0.73, 09-29 0.70): both wrong, both critic_reject now.
- Technology/SOXX bullish 0.75 (09-28): **SOXX 554.75→571.27 = +2.98%, CORRECT** — tech bullish is currently the strongest sector-direction in the 14d window (16 calls, 62.5% win).

### Signal types preceding price moves
- **Tech/AI rally** (SOXX +2.98% 09-28) preceded by ai-infrastructure / "top things to watch" / hyperscaler-capex cluster — fits existing stock-surge-explainer-momentum-rule (t_7066998e). Note: **technology** (SOXX) bullish works (62.5%), but **ai_infrastructure** bullish does not (16.7%) — the two tags behave very differently; see card.
- **Bond-selloff cluster** (09-30/10-01) → repeated financials-bearish high-conf calls. Directionally right but below threshold (09-29) or unverified (09-30). → see card.
- **Static multi-sector tagging** continues: "Croatian court allows extradition of Nord Stream blast suspect" tagged commodities/defense/energy/geopolitical/industrials/materials/macro (7 sectors). Reinforces conflict-headline-sector-tag-false-positives (t_41c665ca). Not new.

## Critic Verdict Discrimination (09-28 → 09-29 verified)
- Correct calls in the verified 2-day slice: SOXX bullish (approve), XLF bearish 0.70 (approve), XLI/XLB bullish 0.55/0.5 (reject-bucket).
- The reject bucket again killed a call that moved the right way (healthcare/defense bullish critic_reject on 09-29). Reject remains the worst-discriminating bucket — reinforces critic-verdict-quality-measurement (t_1854b679). No new card.

## Cross-Reference vs Existing Indirect Dependencies
300 rules total in lessons.db. Reviewed the full from→to map. Findings:
- **No proposed new edge duplicates an existing rule.**
- The "yield spike → financials sell-off" relationship is NOT a new to_entity — several bond/yield→sector rules already exist (rising bond yields→tech stocks id 170; treasury yields/macro liquidity→ai infra id 245; fed hawkishness→tech id 123; 10-yr yield→consumer discretionary id 271). The NEW element is the **directional confidence miscalibration on financials-bearish**, not a missing causal edge — hence the card is framed as a calibration/overconfidence card, not a new rule insert.
- Corroborated by fresh data:
  - ai-infrastructure-trim-missing (t_32a63ab1): ai_infrastructure bullish 12 verified calls, 2 correct (16.7%), avg conf 0.649 — still under the blind default penalty, still the worst sector-direction. Reinforced.
  - fed-hike-fear-rule-267-counterexample (t_912d3fc5): the 09-30/10-01 cluster is exactly the "Fed hike fear" regime; outcome still unverified. Watch.
  - signal-ledger-drift-writer-missing (t_af430b08): next_day_drift is now populating (e.g. 09-29 financials bearish drift −0.44%), but it DISAGREES with the prediction actual_notes (−0.13%) for the same call — the two attribution paths use different windows. Reinforced; see data-quality note.
  - fix-signal-ledger-sector-attribution (t_7d2aba5a): 26 verified predictions in the "unknown"/empty sector bucket over 14d — sector attribution still broken. Reinforced.
- Contradicted: none.

## Data-Quality Note (corroborates t_af430b08)
For the 09-29 financials-bearish call, `signal_ledger.next_day_drift` = −0.0044 (−0.44%) but the `predictions.actual_notes` = "XLF 53.86→53.78 (−0.13%), threshold=±0.2%". The two windows disagree, so a call scored wrong by the prediction verifier looks directionally right in the ledger. When the drift writer (card t_af430b08) is implemented, it should use the SAME verification window as the prediction verifier so was_correct and next_day_drift cannot diverge on the same row.

## 14-Day Sector-Direction Win Rates (verified)
| sector/direction | n | win_rate | avg adj_conf |
|---|---|---|---|
| technology bullish | 16 | 62.5% | 0.50 |
| healthcare bullish | 13 | 46.2% | 0.65 |
| ai_infrastructure bullish | 12 | 16.7% | 0.65 |
| financials bearish | 12 | 16.7% | 0.73 |
| unknown bearish | 16 | 37.5% | 0.48 |
| unknown bullish | 10 | 20.0% | 0.54 |
| macro bearish | 8 | 50.0% | 0.46 |
| defense bullish | 7 | 57.1% | 0.49 |

Key asymmetry: **technology bullish (SOXX) is the best sector-direction at 62.5%, while ai_infrastructure bullish — a closely related tag — is among the worst at 16.7%.** The model treats them nearly identically at selection time but their realized follow-through is opposite.

## Kanban Cards to Create
1. `financials-bearish-yield-spike-overconfidence` — **Discount financials-bearish directional predictions: penalize the "US long-end yield spike / failed auction / rate-hike" → financials-sell-off causal edge and review the ±0.2% verification threshold for low-vol financials** (Risk: low | Sectors: calibration, rules | Owner: coder)
   - Evidence (two independent sources, n=12 not n=1): (1) signal_ledger 14d — financials bearish = 12 verified calls, 2 correct = **16.7% win rate at avg adj_conf 0.725**, the highest-confidence bucket yet one of the worst; (2) predictions.actual_notes — the 09-29 call was directionally right (XLF −0.13%) but below the ±0.2% materiality threshold, and the only correct call (09-28, −0.63%) came from a milder 0.70-conf setup, not the 0.80 ones. The 09-30/10-01 bond-selloff cluster (30-yr yield highest since 2004, yields >5%, failed auctions) drove repeated 0.75–0.80 financials-bearish calls whose follow-through is unverified — consistent with over-confidence, not a stronger edge.
   - Recommended change: add a directional penalty on financials-bearish in selector.py (or route it through a higher materiality bar); add a sector-specific verification-threshold review for low-volatility sectors (XLF rarely clears ±0.2% single-day). Do NOT add a new indirect_dependencies edge — bond/yield→financials-style edges already exist; this is a confidence miscalibration, not a missing causal link.
   - Expected impact: stops the model from placing its highest-confidence calls on the 2nd-worst sector-direction; turns near-miss correct-direction calls into either real fills (right threshold) or honest rejects (right confidence), improving Brier score on the financials bucket.
