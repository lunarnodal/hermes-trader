# Causality Analysis Report — 2026-09-07

## Data Sources
- Recent signals: 5 (last 48h, via `get_recent_signals`)
- Prediction accuracy: 24 predictions, 7-day window (via DB query)
- Indirect dependencies: 30 rules (via `get_indirect_dependencies`)
- Active inference rules: 272 (via `get_pipeline_health`)
- Pipeline logs: predict.log (last 50 lines)

---

## Finding 1: Macro Overall Market Outlook — 0% Win Rate (3/3 Incorrect)

**Signal pattern:** "Overall market outlook — S&P 500, macro environment, geopolitical risks, Fed"  
**Direction:** bearish (×3 on Sep 1, 2, 3)  
**Outcome:** 0 correct out of 3  
**Avg confidence:** 38%

**Cross-check against existing rules:**
- `war conflict military strikes sanctions` → energy/defense/commodities/oil_gas (0.9 static) already captured
- `iran saudi arabia opec` → energy/oil_gas (0.9 static) already captured
- `federal reserve interest rates inflation` → financials/real_estate/utilities (0.9 static) already captured

**Gap:** No cross-sector macro compositing rule exists. The "overall market outlook" predictor is generating directional calls without composing sector-level signals into a coherent market view. It is essentially guessing directionally on macro themes without aggregating the signal-level evidence from sector predictions.

**Evidence:** All three bearish macro calls failed. The underlying signals (Iran strikes, European gas spike) were correctly routed to energy/defense/commodities sectors individually — but no causal rule chains them to "overall market direction."

**Recommendation:** Create a rule that composes sector-level signal aggregations into a market-level confidence score before issuing macro direction calls. The predictor should weight the most-confident sector signals, not apply a blanket bearish sentiment.

---

## Finding 2: Confidence Does Not Predict Accuracy — Energy Sector

**Signal:** "Energy sector outlook as of 2026-09-02/03"  
**Direction:** bullish (×2)  
**Outcome:** 1/2 correct on individual days — but composite 3-prediction win rate is 33%  
**Avg confidence:** 68% (highest among all sectors)

**Cross-check against existing indirect dependencies:**
- ID 97: `geopolitical events (iran)` → `energy sector` (0.7, 3 occurrences, last_seen 2026-09-05) — correctly active
- ID 150: `geopolitical events in the middle east` → `energy sector` (0.7, 2 occurrences) — correctly active
- ID 1: `china's oil purchase` → `energy sector` (0.7, 1 occurrence) — correctly active

**Gap:** The confidence score (68%) is high but the accuracy is low. The model is overweighting geopolitical risk premium in energy despite mixed outcomes. The signal "Depleted US oil stash loses potency as Iran war grinds on" (confidence 0.85, bearish) and "European gas spikes after US strikes on Iranian missile sites" (confidence 0.85, bearish) both target energy — yet energy sector predictions were split bullish/neutral. The predictor is not coherently resolving conflicting high-confidence signals within the same sector.

**Recommendation:** Energy sector predictor needs a conflict-resolution step. When multiple signals with >0.70 confidence point in opposite directions for the same sector, the predictor should either (a) reduce confidence or (b) split into directional sub-predictions rather than issuing a single consensus call.

---

## Finding 3: 28.8% of Signals Tagged "Other" — New Event Types Needed

**Pattern:** 576 / 2000 sampled signals classified as "other" over 7 days  
**Notable misclassifications in sample:**

| Title | Should Be Tagged |
|---|---|
| Rosenblatt initiates Airbnb stock coverage with buy rating | `analyst_initiation` |
| Bloom Energy, Illumina, Everpure to Join S&P 500 in September | `index_inclusion` |
| DA Davidson maintains Malibu Boats Neutral rating on industry caution | `analyst_rating_maintenance` |
| Citizens maintains Global Net Lease stock rating on portfolio progress | `analyst_rating_maintenance` |
| AAR appoints Sanjay Sood as chief digital and technology officer | `leadership_transition` (was corporate_governance) |
| Medalist Diversified CFO Winn buys $120 in company stock | `insider_trading` |
| Nurix CSO Gwenn Hansen sells $36,982 in stock | `insider_trading` |
| GrafTech: A Call Option With A Catalyst | `options_analysis` |

**Gap:** These are recurring signal patterns that should be automatically classified. Currently they fall through to "other," reducing signal quality for sector predictors. The system has `analyst_rating_maintenance`, `analyst_initiation`, `index_inclusion` event types needed — and `insider_trading` is inconsistently applied (some CFO buys/sells tagged, others not).

**Recommendation:** Add signal classification rules for: `analyst_initiation`, `analyst_rating_maintenance`, `index_inclusion`, `ceo_transition` (separate from leadership_transition), `cfo_resignation` (separate from leadership_transition — historically more bearish per existing rule ID 220).

---

## Existing Rules Correctly Active (No Action Needed)

| Rule | Status |
|---|---|
| `geopolitical events (iran)` → energy sector (0.7, 3occ) | ✓ Active, last seen 2026-09-05 |
| `ai compute demand surge` → NVDA/AMD/INTC (0.9, 1occ) | ✓ Active, last seen 2026-09-04 |
| `data center construction` → VRT/ETN/HUBB (0.88, 1occ) | ✓ Active, last seen 2026-09-04 |
| `cfo resignation` → company stock (0.8, 1occ) | ✓ Active, last seen 2026-09-02 |
| `ceo transition` → affected company stock (0.75, 1occ) | ✓ Active, last seen 2026-09-02 |

---

## Kanban Cards to Create

1. **`causality-20260907-macro-compositing`** — "Add macro market compositing rule"  
   Risk: low | Sectors: pipeline, rules | Owner: coder/applier/verifier

2. **`causality-20260907-energy-conflict-resolution`** — "Add signal conflict-resolution step in energy sector predictor"  
   Risk: low | Sectors: pipeline, rules | Owner: coder/applier/verifier

3. **`causality-20260907-signal-classification`** — "Add new signal event types: analyst_initiation, analyst_rating_maintenance, index_inclusion, cfo_resignation, ceo_transition"  
   Risk: low | Sectors: signal-quality | Owner: coder/applier/verifier

4. **`causality-20260907-insider-trading-consistency`** — "Audit and fix inconsistent insider_trading tagging (some CFO buys/sells still falling to 'other')"  
   Risk: low | Sectors: signal-quality | Owner: coder/applier/verifier

---

*Report generated: 2026-09-07 09:00 AM ET*  
*Next scan scheduled: 2026-09-08 09:00 AM ET via cron job `0679a8e2116a`*
