# Causality Analysis Report — 2026-09-15

## Data Sources Reviewed
- Predictions DB (last 48h): 20 predictions across 9 sectors
- Signal ledger (last 7 days via get_recent_signals): 5 signals
- Trade history (last 30 days via get_trade_history): 6 trades
- Indirect dependencies: 254 total, 30 active rules reviewed
- Prediction accuracy: 7-day window, 35 predictions, 34% overall win rate
- Pipeline health: 282 inference rules, 2,540 parse errors all-time (0 this month)

## Signal → Outcome Cross-Reference

### 1. Recent Signals (last 48h)
| Signal | Sentiment | Conf | Sectors | Tickers |
|--------|-----------|------|---------|---------|
| Citi fading investor conviction | bearish | 0.75 | equities, financials | — |
| Rothschild initiates Tempus AI at Neutral | neutral | 0.75 | ai_infrastructure, healthcare, technology | TEM |
| Barclays upgrades Sensata on auto content | bullish | 0.75 | automotive, consumer, semiconductors, technology | BCS, SST |
| Telecom Italia rally | neutral | 0.65 | energy, telecom, utilities | TIM |
| Barclays initiates JBT Marel at Overweight | bullish | 0.75 | agriculture, materials, technology | BCS, JBT, MAREL |

No corresponding predictions for Telecom Italia or JBT/Marel in the last 48h. These signals entered Qdrant but did not generate sector-level predictions — possible gap in signal-to-prediction coverage.

### 2. Recent Trade Outcomes (last 30 days)
| Ticker | Sector | Entry | Exit | P&L | Outcome |
|--------|--------|-------|------|-----|---------|
| SPCX | technology | $140.97 | $148.17 | +$129.60 (+5.1%) | WIN |
| AMZN | technology | $257.81 | $255.02 | -$64.17 (-1.1%) | LOSS |
| CRM | technology | $252.86 | $244.27 | -$128.85 (-3.4%) | LOSS |
| XLV | healthcare | $167.23 | $171.40 | +$33.36 (+2.5%) | WIN |
| XLB | materials | $51.12 | $52.78 | +$43.16 (+3.2%) | WIN |
| XOM | energy | $153.70 | $164.69 | +$142.87 (+7.2%) | WIN |

**Observations:**
- Energy sector (XOM): +7.2% — aligns with Sep 14 merger arb prediction (neutral, conf 0.65) and Sep 15 overall market bearish signal. Energy winning in bearish macro is not yet captured as a rule.
- Technology (SPCX win, AMZN/CRM loss): Sector-level bullish predictions on Sep 14/15 conf 0.4–0.6 failed to produce positive outcomes. Technology sector bullish predictions at low confidence (avg 59%) are 25% accurate (1/4 correct) — well below the 35% hard-block threshold, yet they were generated.
- Healthcare (XLV win): Matches bullish prediction Sep 14 conf 0.70 (75% accuracy this period).

### 3. Prediction Accuracy by Sector (7-day)
| Sector | Direction | Win Rate | Avg Conf | Avg Critic | Issue |
|--------|-----------|----------|----------|------------|-------|
| Merger arb | neutral | **100%** (3/3) | 53% | 53% | Already captured |
| Financial | bearish | 67% (2/3) | 42% | 42% | Existing rule covers it |
| Healthcare | bullish | **75%** (3/4) | 63% | 51% | Existing rule covers it |
| Industrials | bullish | 50% (1/2) | 45% | 45% | Low confidence, mixed direction |
| Consumer | bearish | **25%** (1/4) | 62% | 50% | bearish direction consistently misses |
| Technology | bullish | **25%** (1/4) | 59% | 49% | bullish predictions at conf <65% failing |
| Overall market | bearish | **0%** (0/4) | 48% | 38% | bearish predictions consistently wrong |
| Energy | bullish | 33% (1/3) | 47% | 47% | Low conf, mixed direction |
| Materials | various | 0% (0/4) | 32% | 32% | All directions failing |

### 4. Indirect Dependencies Gap Analysis

Existing rules cover:
- AI data center → supply chain (hardware, power, cooling, networking, EDA)
- Hyperscaler capex → energy sector
- NVDA earnings → AI infrastructure supply chain
- Geopolitical → energy/defense
- Bond yields → growth stocks
- CFO/CEO transitions → company stock

**Not yet captured (inferred from data):**

#### Finding 1: Telecom Italia Merger Arb Signal Not Tracked
**Signal:** "Why is Telecom Italia stock rallying today?" (investing-com, neutral, conf 0.65, Sep 15)
**Prediction gap:** No corresponding merger arb prediction generated
**Rule gap:** indirect_dependencies has no Telecom Italia or European telecom merger arb rule
**Evidence:** TIM rally signal was in Qdrant but did not produce a merger arb prediction. Rule ID 244 (merger_arb_signal → energy_sector, conf 0.75) only triggers on spread > 15% energy situations.

#### Finding 2: Barclays Initiating Coverage as Alpha Signal
**Signal:** Barclays initiates JBT Marel at Overweight (bullish, conf 0.75)
**Rule gap:** No rule links analyst initiating coverage (Barclays specifically or generic "analyst initiates coverage with overweight rating") → short-term bullish price move (1–5 days)
**Evidence:** Multiple indirect dependency rules reference analyst actions (e.g., cfo_resignation, ceo_transition), but initiating coverage is absent. With only 4 occurrences for the highest-confidence existing rules, this is a low-confidence gap, but the pattern is widely documented in literature.

#### Finding 3: Bearish Macro → Energy Outperformance
**Signal:** Overall market outlook bearish (conf 0.48–0.55) + Financial bearish (conf 0.42–0.75)
**Trade evidence:** XOM +7.2% in same period as market bearish predictions failed (0% win rate)
**Rule gap:** No rule captures "bearish macro / market uncertainty → capital rotation into energy sector (defensive/hard asset)"
**Note:** Energy sector has existing rules (geopolitical, hyperscaler capex, merger arb) but not this macro-rotation mechanism.

#### Finding 4: Consumer Sector Bearish Failure Mode
**Prediction evidence:** Consumer bearish direction 25% win rate (1/4 correct), avg critic confidence 50%
**Rule gap:** Consumer sector may benefit from defensive spending characteristics during bearish macro calls — no rule captures consumer staples defensive rotation
**Evidence:** Consumer bearish win rate is 25% despite high avg confidence (62%) — suggests the calibration penalizes consumer more than warranted during macro uncertainty.

#### Finding 5: Technology Bullish Predictor Failure
**Prediction evidence:** Technology bullish 25% win rate (1/4 correct), avg conf 59%, avg critic 49%
**Trade evidence:** AMZN and CRM losses in technology sector within this window
**Rule gap:** Existing rules do not penalize low-confidence bullish technology predictions (conf <65% + direction=bullish). Gate 5 (hard-block: win_rate <35% + bullish + adj_conf <65% → auto-reject) should fire for Technology (25% WR, bullish, conf 59%) — either it did not fire or the prediction still generated despite the gate.

---

## Kanban Cards to Create
1. `barclays-initiating-coverage-signal` — **Add rule: analyst initiating coverage with overweight/buy rating predicts 1-5 day bullish price move** (Risk: low | Sectors: pipeline, rules | Owner: coder)
2. `telecom-italia-merger-arb-signal` — **Track Telecom Italia merger arb signal and add TIM to merger arb coverage universe** (Risk: low | Sectors: signal-quality | Owner: coder)
3. `bearish-macro-energy-rotation` — **Add rule: bearish macro/market outlook triggers capital rotation into energy sector as defensive/hard asset play** (Risk: medium | Sectors: rules | Owner: coder)
4. `consumer-staples-defensive-rotation` — **Add rule: bearish macro predictions should penalize consumer sector less (consumer staples defensive spending during uncertainty)** (Risk: medium | Sectors: rules, calibration | Owner: coder)
5. `technology-bullish-gate-5-violation` — **Investigate why Technology bullish predictions (25% WR, conf 59%) did not trigger Gate 5 auto-reject** (Risk: medium | Sectors: pipeline, risk | Owner: coder)

