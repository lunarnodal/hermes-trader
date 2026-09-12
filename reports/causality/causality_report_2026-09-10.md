# Causality Analysis Report — 2026-09-10

## Pipeline Health
- Inference rules active: 273
- Indirect dependencies: 244 (DB)
- Qdrant signals (last 7 days): 5,979 — steady flow
- Parse errors this month: 153 — all-time total now equals monthly total, indicating new error source introduced this month

## Signal Inventory (Last 48h)

30 signals retrieved. Key signals by sector:

| Signal | Sector(s) | Sentiment | Confidence |
|---|---|---|---|
| AbbVie +$11B Apogee acquisition | healthcare, biotech, pharma | bullish | 0.85 |
| Soitec +15% after Q2 outlook boost | semiconductors, photonics, ai_infra | bullish | 0.85 |
| Snowflake +23% on earnings beat | software, cloud, ai_infra | bullish | 0.91 |
| Globant MuleSoft AI Pod | enterprise_software, ai_infra | bullish | 0.75 |
| Broadcom reiteration (AI revenue) | semiconductors, ai_infra | bullish | 0.75 |
| RBC RingCentral outperform | communications, software | bullish | 0.75 |
| Voltalia -16% on capacity target cut | energy, renewables | bearish | 0.75 |
| BOJ quarter-point hike leaning | macro, financials | bullish | 0.75 |
| Europe Energy-Bond feedback loop | energy, financials | bearish | 0.75 |
| Lutnick Iran war comment | geopolitics, energy, defense | neutral | 0.65 |
| Amazon Health Benefits Connector | healthcare, ai_infra, consumer | bullish | 0.75 |

## Recent Predictions (Last 2 Days)

**20 predictions retrieved.** Direction/accuracy summary:

| Direction | Correct/Total | Win Rate | Avg Confidence |
|---|---|---|---|
| bullish | 0/7 | 0% | ~58% |
| bearish | 1/3 | 33% | ~50% |
| mixed | 0/3 | 0% | ~30% |

**Overall 7-day win rate: 12% (2/16 correct)** — significantly below the 35% hard-block threshold. All sectors at 0% except Financials (50%) and Overall Market (50%).

## Prediction Accuracy Concerns

7-day accuracy breakdown reveals a systematic issue:

- **Healthcare bullish** (2 predictions, 0 correct, avg conf 72%) — highest avg confidence sector, worst performance
- **Technology bullish** (2 predictions, 0 correct, avg conf 68%)
- **Consumer bearish** (2 predictions, 0 correct, avg conf 55%)
- **Industrials bullish** (2 predictions, 0 correct, avg conf 45%)
- **Materials bullish** (2 predictions, 0 correct, avg conf 43%)
- **Energy bullish** (1 prediction, 0 correct, avg conf 80%)

The pipeline is issuing high-confidence bullish predictions that are universally wrong. This pattern suggests either:
1. Sector-level sentiment signals are not being properly calibrated against actual price data
2. The critic model is not catching directional bias (pipeline gate 6 is not compensating)
3. Macro context (Fed, geopolitical) is overriding sector-specific signals

**No new indirect dependencies can be reliably proposed** — the current prediction accuracy is too poor to attribute outcomes to specific causal chains with confidence.

## Trade History Context (30 days)

5 closed trades: 4 wins, 1 loss. Win rate 80%, total P+L +$300.70.

| Ticker | Sector | P&L | Exit Reason | Outcome |
|---|---|---|---|---|
| INHD | energy | +23.5% | profit_tier_3 | WIN |
| XOM | energy | +7.2% | time_exit | WIN |
| XLB | materials | +3.2% | time_exit | WIN |
| XLV | healthcare | +2.5% | time_exit (VIX=15.3) | WIN |
| CRM | technology | -3.4% | stop_loss | LOSS |

Notable: CRM loss (+3.4% stop) occurred despite Globant/Salesforce AI pod news being strongly bullish. XLB materials win with no identifiable signal catalyst. INHD energy win (short hold, 2 days) is the standout — could be tied to geopolitical/oil price signals but not captured in the indirect dependency graph.

## Existing Indirect Dependencies (Sample)

Top rules by confidence:
- Rule 225: \`ai compute demand surge → NVDA AMD INTC\` (0.90)
- Rule 226: \`ai compute demand surge → TSMC Samsung ASML\` (0.88)
- Rule 240: \`hyperscaler capex increase → ai infra supply chain\` (0.88)
- Rule 228: \`data center construction → VRT ETN HUBB\` (0.88)
- Rule 231: \`data center power demand → NEE CEG VST NRG\` (0.87)

All 30 existing rules are AI/data-center related chains. No rules exist for:
- Pharma M&A (AbbVie/Apogee)
- Earnings-driven software price moves (Snowflake +23%)
- Renewables capacity misses (Voltalia -16%)
- BOJ monetary policy effects on EM/financials

## Key Findings

### Finding 1: Parse Error Spike
Parse errors this month = 153, equal to all-time total (153). This means all parse errors occurred in September 2026. Likely cause: new signal source format or schema change. Requires investigation.

### Finding 2: Systematic Bullish Prediction Failure
12% win rate over 7 days. High-confidence bullish predictions in healthcare, technology, and energy are all failing. This points to a calibration or gate failure, not a signal quality issue. Current indirect dependency rules (AI/data-center chains) do not cover these failing sectors.

### Finding 3: M&A Signal Coverage Gap
AbbVie +$11B Apogee acquisition is the highest-conviction signal (0.85) with direct tickers (ABBV, APGE). No indirect dependency rule exists for pharma/biotech M&A → sector price move. Rule 244 exists for merger_arb_signal → energy_sector but pharma M&A is not captured.

### Finding 4: Short-Hold Energy Wins Not in Rules
INHD energy +23.5% in 2 days (profit_tier_3 exit) — shortest hold of any winning trade. This rapid energy sector move is not captured by any of the 244 indirect dependencies. Possible signal: geopolitical, short-cover, or sector rotation.

### Finding 5: No Rule for Snowflake-Type Earnings Beats
Snowflake +23% is the highest-conviction signal (0.91). Software/cloud earnings beat → short-term price move is not in the dependency graph. Rule 239 covers NVDA earnings beat but there is no generalization to other software/cloud names.

## Recommendations

1. **Investigate parse error source** — all 153 errors in September points to a new signal format issue. Check predict.log for entries after Sept 1.
2. **Flag healthcare/technology bullish predictions as low-confidence until calibration is restored** — the 0% win rate in these sectors over 7 days should trigger automatic confidence reduction via sector_trim.
3. **Add pharma M&A rule** — AbbVie/Apogee is a textbook case. Trigger: large-cap pharma M&A announcement → biotech/pharma sector momentum.
4. **Add software earnings beat rule** — generalize from Snowflake: software/cloud earnings beat → sector up move within 1-2 trading days.
5. **Investigate INHD energy short-hold win** — identify which signal preceded the +23.5% move in 2 days.

## Kanban Cards to Create

1. \`parse-error-september-2026\` — **Investigate September parse error spike (153 errors = all-time total)** (Risk: medium | Sectors: pipeline | Owner: coder)
2. \`healthcare-tech-calibration-failure\` — **Calibration failure: 0% win rate on bullish healthcare/tech predictions, 7-day window** (Risk: high | Sectors: pipeline, rules | Owner: coder)
3. \`pharma-ma-signal-rule\` — **Add pharma/biotech M&A indirect dependency rule** (Risk: low | Sectors: rules | Owner: coder)
4. \`software-earnings-beat-rule\` — **Generalize NVDA earnings rule to cover Snowflake-type software earnings beats** (Risk: low | Sectors: rules | Owner: coder)
5. \`inhd-energy-short-win-signal\` — **Identify signal that preceded INHD +23.5% in 2 days** (Risk: low | Sectors: rules | Owner: coder)
