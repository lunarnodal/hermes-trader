# Causality Analysis Report — 2026-09-22

## Summary

Today's (09-22) and yesterday's (09-21) predictions are still unverified (was_correct NULL for all 18 rows in the 48h window), so causal evidence this run comes from the **09-15→09-18 verified window** re-sliced with a rolling 7-day cut, the live 48h signal feed, signal_ledger rejections, and sector_trim state. All three cards proposed in the 09-21 report (defense-budget rule, FDA-catalyst discount, Fed-hike-fear provisional) were created and the corresponding rules are now present in `indirect_dependencies` — no re-proposal needed. Two new findings this run concern **penalty layers over-firing against verified winners** (industrials trim/gate) and **critic verdicts having near-zero discriminating power**.

## Verified outcome breakdown (rolling 7d, 09-15→09-18, n=32)

| Bucket | dir | n | wins | avg conf |
|---|---|---|---|---|
| industrials | bullish | 4 | 3 | 0.46 |
| merger_arb | neutral | 4 | 4 | 0.49 |
| tech | bullish | 4 | 3 | 0.67 |
| financials | bearish | 4 | 2 | 0.49 |
| healthcare | bullish | 4 | 1 | 0.80 |
| consumer | bearish | 4 | 0 | 0.68 |
| market | bearish | 3 | 0 | 0.48 |
| materials | bullish/mixed/neutral | 4 | 0 | 0.37 |
| energy | bearish/mixed | 4 | 1 | 0.41 |

## Findings

### 1. Industrials: deepest LTFT penalty in the system vs 75% verified win rate (STRONG, new)

**Signal type:** earnings-beat / infrastructure-demand catalysts in industrials (Caterpillar Q2 beat, defense budget cluster).

**Evidence:**
- Source 1 — predictions table (7d verified): industrials bullish **3/4 correct at avg conf 0.46**, the system's best hit-rate bucket alongside merger-arb, yet it carries the **lowest confidence** — the penalty stack is suppressing the winner.
- Source 2 — sector_trim + signal_ledger: industrials LTFT = **-0.343, the most negative trim of all 9 sectors** (vs tech -0.10, healthcare -0.05). Yesterday the Caterpillar earnings-beat signal (bullish raw 0.55) was knocked by meta_correction to **adj 0.361 and rejected** ("raw=55% adj=36% < 0.70 after sector penalty") — i.e. the pipeline hard-blocked a signal in the bucket that has since verified 3/4.
- Cross-reference: existing rules encode defense→industrials *bullish causality* (rule added 09-21) but nothing reconciles **trim staleness**: LTFT learned from an older losing regime is now contradicting two consecutive verified winning weeks. The `technology-bullish-gate-5-violation` card (t_3f8bb465) covers gates *failing to fire*; this is the inverse failure mode (penalty *over*-firing) and is unaddressed.
- 09-21 critic data corroborates: industrials critic conf 38% vs 100% realized (over-challenging the winner).

### 2. Critic verdicts barely discriminate outcomes (MEDIUM, new)

**Evidence:**
- Source 1 — predictions table (10d verified, n=45): approve 7/16 correct (44%), challenge 5/12 (42%), reject 6/17 (**35%**). The critic's *reject* bucket is the **worst-performing** bucket — a verdict that carries no reliable information (and marginally negative signal when it rejects).
- Source 2 — per-sector critic conf vs realized (09-15→09-18 window, first flagged in 09-21 report): healthcare critic conf 76% vs 25% realized win rate (rubber-stamping the loser); industrials 38% vs 100% (blocking the winner). Directionally consistent across two independent sector slices.
- Cross-reference: manifest has cards for ETF mapping, ledger attribution, gate-5 non-firing — **no card measures critic verdict quality before critic verdicts gate trades** (critic_verdict_reject is an active gate — it killed the Materials 0.58 bullish yesterday). The gate's input is currently unmeasured.

### 3. Consumer bearish continues inverted — consistent with pending card, no new card

Consumer bearish now **0/4 in rolling 7d** (avg conf 0.68, critic rejected 3 of 4 anyway). This is a further data point for the pending `consumer-staples-defensive-rotation` card (t_18d521e3), not a new rule.

## Signal-feed notes (48h, for future rule mining — no evidence yet)

- **Gulf/Hormuz/Red Sea geopolitical cluster** (Houthi attack, Reuters conf 0.85; Egypt-Saudi Red Sea security conf 0.75): feeds directly into the pending `crude-surge-market-rule` card (t_3fbca761) — watch Brent and energy/market verification tomorrow.
- **UK payrolls miss + sterling drop** (Bloomberg/investing-com, bearish conf 0.75 each): UK-macro→EU-financials channel; only a stale UK-jobs→healthcare rule (id 12) exists. Zero verified outcomes yet — watch 24-48h.
- **AI capex bill narratives split** (Meta "$279B bill" bearish 0.75 vs Microsoft "AI bear case cracks" bullish 0.75): divergent same-theme signals; no outcome yet. Watch whether tech bullish verifies 3-for-4 again.
- **Merger-arb neutral bucket 4/4 correct, avg conf 0.49**: the most reliable bucket in the system; already handled by existing Form-8.3 logic, no action.

## Candidates REJECTED (no card)

- **Defense→industrials bullish** — rule + card landed 09-21, already in DB.
- **FDA-catalyst healthcare discount** — carded 09-21; today's healthcare bullish 0.75 approved by critic again feeds that card's evidence.
- **Fed-hike-fear priced-in** — provisional rule exists; awaiting occurrence #3.
- **Crude/Hormuz stress** — pending card t_3fbca761.
- **Consumer defensive rotation** — pending card t_18d521e3.
- **signal_ledger event_type 100% "other"** — attribution blocker, already carded (t_7d2aba5a).

## Pipeline Health Notes

- 09-21 + 09-22 runs: all 18 predictions unverified at query time; verification lag confirmed but progressing (09-18 rows verified).
- sector_trim STFT = 0.0 for all sectors: expected while the newest day is unverified, but monitor alongside t_edb6fbca (verification cron lock starvation) — if STFT stays 0.0 two more run-days while verifications exist, that card gains evidence.
- Trade history 7d: 0 closed trades. All yesterday's candidates died at gates (5× direction_confidence, 2× meta_correction, 1× hard_block_bullish, 1× critic_reject) — the two new cards below target the two gate families doing the most over-blocking.

## Kanban Cards to Create
1. `industrials-trim-overpenalty-reconcile` — **Reconcile industrials LTFT trim (-0.343, deepest penalty) with verified outcomes: industrials bullish 3/4 correct at avg conf 0.46 in the 7d window; Caterpillar earnings beat rejected 09-21 via meta_correction (raw 55% → adj 36%). Add a freshness/contradiction check: when trailing-7d verified win rate for a direction exceeds 60% while LTFT penalty > 25%, clamp or re-derive trim from the recent window instead of legacy regime** (Risk: medium | Sectors: calibration, pipeline | Owner: coder)
2. `critic-verdict-quality-measurement` — **Measure critic verdict discriminating power before critic gates carry weight: 10d verified shows approve 44%, challenge 42%, reject 35% correct — reject is the WORST bucket and critic_verdict_reject is an active portfolio gate (killed Materials bullish 0.58 09-21). Log per-verdict Brier/hit-rate into rule_performance and down-weight or recalibrate the critic gate if discrimination stays < 55%** (Risk: medium | Sectors: calibration, pipeline, rules | Owner: coder)
