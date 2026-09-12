# Causality Analysis Report — 2026-09-11

## Pipeline Health Summary

| Metric | Value |
|--------|-------|
| Active inference rules | 273 |
| Indirect dependencies | 244 (30 in active set) |
| Signals (last 1d) | 1,603 |
| Signals (last 7d) | 6,095 |
| Parse errors (all-time) | 153 (all this month) |

---

## Signal → Prediction Cross-Reference (Last 48h)

### Recent Signals (from get_recent_signals)

| Title | Source | Sentiment | Confidence | Sectors |
|-------|--------|-----------|------------|---------|
| CleanSpark: The AI Story Is Real, The Payday Awaits | seeking-alpha | bullish | 0.85 | ai_infrastructure, data_center, energy, semiconductors, utilities |
| Record Cash Freezes India's Key Funding Market for 90 Minutes | bloomberg-markets | neutral | 0.65 | commodities, emerging_markets, financials, india, materials |
| The Zacks Analyst Blog Highlights SpaceX, NASA, Rocket Lab, ARKX, UFO and WARP | finnhub-spcx | bullish | 0.75 | ai_infrastructure, defense, energy, industrials, space |
| SpaceX Spent $18.4 Billion in a Single Quarter — $15.8B on AI | finnhub-spcx | bullish | 0.91 | aerospace, ai_infrastructure, data_center, energy, semiconductors, technology |
| SoundHound AI Completes Acquisition of LivePerson | finnhub-merger | bullish | 0.85 | ai_infrastructure, consumer, financials, software, technology |

### Recent Predictions (last 2 days, from DB)

| Query | Direction | was_correct | Confidence |
|-------|-----------|------------|------------|
| Overall market outlook 2026-09-09 | bearish | 0 | 0.45 |
| Consumer sector outlook 2026-09-09 | bearish | 1 | 0.60 |
| Industrials sector outlook 2026-09-09 | bullish | 0 | 0.45 |
| Materials and mining sector outlook 2026-09-09 | bullish | 0 | 0.58 |
| Healthcare and biotech sector outlook 2026-09-09 | bullish | 0 | 0.55 |
| Financial sector outlook 2026-09-09 | mixed | 0 | 0.30 |
| Technology and AI sector outlook 2026-09-09 | bullish | 0 | 0.65 |
| Energy sector outlook as of 2026-09-09 | mixed | 0 | 0.30 |
| Overall market outlook 2026-09-10 | bearish | 0 | 0.45 |
| Consumer sector outlook 2026-09-10 | bearish | 1 | 0.60 |
| Energy sector outlook as of 2026-09-10 | bullish | null | 0.30 |

Note: was_correct=null for 2026-09-10 predictions = unverified (today's briefing run).

---

## Prediction Accuracy (7-Day Window)

| Sector | Direction | Total | Correct | Win Rate | Avg Confidence |
|--------|-----------|-------|---------|----------|---------------|
| Consumer | bearish | 2 | 1 | 50% | 52% |
| Overall market | bearish | 2 | 1 | 50% | 45% |
| Financial | bearish | 1 | 1 | 100% | 45% |
| Energy | mixed | 2 | 0 | 0% | 38% |
| Healthcare | bullish | 2 | 0 | 0% | 62% |
| Industrials | bullish | 2 | 0 | 0% | 45% |
| Materials | bullish | 2 | 0 | 0% | 49% |
| Technology | bullish | 2 | 0 | 0% | 65% |
| **Overall** | — | **16** | **3** | **19%** | — |

Key observation: 0-for-8 on bullish predictions (excluding financials). 3-for-3 on bearish.

---

## Trade History (30-Day Window)

| Ticker | Sector | P&L | Exit Reason | Outcome |
|--------|--------|-----|-------------|---------|
| CRM | technology | $-128.85 (-3.4%) | stop_loss | LOSS |
| XLV | healthcare | +$33.36 (+2.5%) | time_exit | WIN |
| XLB | materials | +$43.16 (+3.2%) | time_exit | WIN |
| XOM | energy | +$142.87 (+7.2%) | time_exit | WIN |
| INHD | energy | +$210.16 (+23.5%) | profit_tier_3 | WIN |

Win rate: 80% (4/5), total P&L: +$300.70.

---

## Indirect Dependencies Review

Existing 30 active rules are heavily clustered around AI infrastructure buildout:

```
ai compute demand surge → NVDA AMD INTC
ai compute demand surge → TSMC Samsung ASML
ai compute demand surge → AVGO MRVL
data center construction → VRT ETN HUBB
data center construction → EQIX DLR AMT
data center construction → CARR TT
data center power demand → NEE CEG VST NRG
data center power demand → nuclear energy stocks
data center power demand → copper miners
ai data center buildout → ANET CSCO JNPR
ai data center buildout → CIEN LITE COHR
hyperscaler capex increase → ai infrastructure supply chain
hyperscaler capex increase → energy sector
NVDA earnings beat → ai infrastructure supply chain
ai chip development → CDNS SNPS
ai infrastructure expansion → industrial gases sector
```

Gaps identified: M&A-driven signals, space-sector AI tailwinds, India/EM macro, merger arb spreads outside energy.

---

## Key Findings

### Finding 1 — Bullish Direction Systematic Failure
Evidence: 0-for-8 on bullish predictions (excluding financials); 3-for-3 on bearish.
Pattern: Meta-correction penalty (-15%) applied to bullish predictions may be compounding with inflated base confidence from noisy AI infrastructure signals. The system generates bullish at higher raw confidence but they fail 100%.
Existing mechanism: direction calibration: bullish -15%, bearish -25%, mixed hard-blocked (inference rule, not yet indirect dependency).

### Finding 2 — SpaceX AI Spend Signal Not Captured as Rule
Evidence: "SpaceX Spent $18.4 Billion — $15.8B on AI" (confidence 0.91, finnhub-spcx) not matched to any active rule. SpaceX absent from all 30 indirect dependency chains.
Signal type: ai_infrastructure + aerospace + energy
Potential rule: hyperscaler-equivalent AI capex → space/defense AI supply chain (confidence ~0.75)

### Finding 3 — M&A Signals Have No Systematic Downstream Rules
Evidence: SoundHound AI/LivePerson merger signal and merger arbitrage signals in feed. ONE rule exists (merger_arb → energy, id 244) but no rules for M&A spread compression → technology/ai_infrastructure sector moves.
Signal type: merger / acquisition
Potential rule: M&A spread compression → target sector re-rating (needs sector-specific breakdown)

### Finding 4 — India/EM Macro Signal Has No Causal Rule
Evidence: "Record Cash Freezes India's Key Funding Market" (confidence 0.65) with emerging_markets, india, financials, materials sectors. No indirect dependency links India liquidity events to US materials or emerging market ETFs.
Signal type: emerging_markets / india / macro
Potential rule: India liquidity stress → EM ETF pressure → materials sector headwind (low confidence, requires second data source)

---

## Kanban Cards to Create

1. bullish-dir-calibration-overcorrection — Investigate bullish direction over-correction — 0-for-8 win rate on bullish predictions (Risk: medium | Sectors: pipeline, rules | Owner: coder)
2. spacex-ai-spend-rule — Add indirect dependency: SpaceX AI capex → space/defense AI supply chain tickers (Risk: low | Sectors: rules | Owner: coder)
3. ma-spread-compression-tech-rule — Add indirect dependency: M&A spread compression → technology sector re-rating (Risk: low | Sectors: rules | Owner: coder)
4. india-liquidity-stress-materials-rule — Investigate India liquidity stress → materials sector causal link (Risk: low | Sectors: pipeline, rules | Owner: coder)
