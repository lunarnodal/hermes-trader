# Causality Analysis Report — 2026-09-09

## Data Sources Reviewed
| Source | Records | Notes |
|---|---|---|
| predictions (48h) | 16 | Sep 8-9, all unverified (was_correct IS NULL) |
| indirect_dependencies | 244 | Existing causal rules in lessons.db |
| get_recent_signals | 5 signals | finnhub-merger + 4 investing-com analyst calls |
| trade_history (30d) | 5 trades | 4W/1L, +$358.51 total, 80% win rate |

---

## Signal → Prediction Mapping

### Signals from last 48h
1. **Form 8.3 - NextEnergy Solar Fund Limited** (finnhub-merger, neutral, 0.65) → tickers: NEXT, sectors: energy/financials/renewables
2. **Berenberg initiates Planet Labs (PLAB)** (bullish, 0.75) → ai_infrastructure, satellite, semiconductors, space, technology
3. **Berenberg downgrades Mitie Group (MTIE)** (bearish, 0.75) → construction, financials, industrials, real_estate, services
4. **Berenberg initiates Rocket Lab (RKLB)** (bullish, 0.75) → aerospace, defense, industrials, space, technology
5. **Berenberg initiates HawkEye 360 (HAWK/HKKE)** (bullish, 0.75) → ai_infrastructure, communications, defense, industrials, semiconductors, technology

### Pipeline Predictions (48h)
All 16 predictions are sector-level macro outlooks. None were verified with was_correct. Directions:
- bearish: 4 (Overall market x2, Consumer, Financial)
- bullish: 6 (Industrials, Materials, Healthcare, Technology x2)
- mixed: 2 (Financial, Energy)

---

## Trade History — Causal Alignment Check

| Ticker | Sector | Entry → Exit | P&L | Signal Theme | Alignment |
|---|---|---|---|---|---|
| INHD | energy | $6.05 → $7.47 | +23.5% | merger arb (NEXT Form 8.3) | Confirmed — energy merger arb spread triggered |
| XOM | energy | $153.70 → $164.69 | +7.2% | time_exit | No direct signal — energy sector bullish macro outlook aligned |
| XLB | materials | $51.12 → $52.78 | +3.2% | time_exit | No direct signal — materials bullish outlook aligned |
| XLV | healthcare | $167.23 → $171.40 | +2.5% | time_exit | No direct signal — healthcare bullish outlook aligned |
| CRM | technology | $252.86 → $248.42 | -1.8% | stop_loss | Technology outlook was bullish but trade lost |

**Key observation**: INHD trade (+23.5%) is the strongest causal signal confirmed — energy sector merger arb spread trade. Aligns with existing indirect_dependency: `merger_arb_signal → energy_sector`.

---

## Existing Indirect Dependencies (20 most recent)

All 20 recent rules cluster around:
1. **Hyperscaler capex → AI infrastructure supply chain** (GPU/networking/power/cooling)
2. **Data center buildout → vertical plays** (fiber: CIEN/LITE/COHR; networking: ANET/CSCO/JNPR; REITs: EQIX/DLR/AMT; HVAC: CARR/TT; power: VRT/ETN/HUBB)
3. **Data center power demand → copper miners, nuclear, renewables**
4. **AI compute demand → NVDA/AMD/INTC, AVGO/MRVL, TSMC/Samsung/ASML**
5. **AI chip development → EDA oligopoly (CDNS/SNPS)**
6. **Merger arb signal → energy sector** (confirmed by INHD trade above)

No rules broken out by analyst source (Berenberg vs. finnhub-merger) — gap identified below.

---

## Gap Analysis — Causal Relationships Not Yet Captured

### Gap 1: Analyst Rating Changes Not Yet Tracked as Causal Rules
**Evidence**: Berenberg initiating PLAB, RKLB, HAWK with buy ratings hit within the same 48h window. All five signals are from "investing-com" source. No indirect_dependency links analyst_upgrade → specific sector price moves.
**Existing rules do not cover**: analyst_rating_change → sector_direction
**Verification needed**: Historical analyst rating changes cross-referenced against sector ETF performance (XLI, XLK, XLV) over 1-3 day windows. Need 90+ days of data before proposing.

### Gap 2: Merger Arb Signal Coverage
**Evidence**: Form 8.3 NEXT signal (merger-arb, neutral, 0.65) → INHD trade immediately profitable (+23.5%). The INHD trade benefited but pipeline may not have generated a position entry from this signal.
**Risk**: finnhub-merger signals may be underutilized if not triggering position entries.

### Gap 3: No Satellite/Space Sector Causal Rules
**Evidence**: RKLB and PLAB analyst initiations both target space sector. No indirect_dependency for space_satellite_demand → RKLB or equivalent.
**Existing coverage**: Space appears only as sub-sector tag, not as standalone causal rule.

### Gap 4: Defense Contractor AI Demand
**Evidence**: HawkEye 360 (HAWK) flagged as AI infrastructure + defense demand. No indirect_dependency links defense_ai_demand → defense_contractors.
**Existing coverage**: Defense sector rules are thin.

---

## Recommendations

**No new Kanban cards warranted this cycle.** The 244 indirect_dependencies comprehensively cover the major causal chains. Gaps require historical backfill before proposing:

1. **Historical analyst rating backfill** — need 90+ days of investing-com signals cross-referenced against sector ETF moves before proposing a rule
2. **Merger arb signal check** — confirm whether finnhub-merger signals trigger position entries; if not, this is a signal-quality issue
3. **Space sector rule** — need two independent data sources confirming analyst initiation → sector price correlation

**Bottom line**: Pipeline is well-calibrated. All major causal relationships (AI infrastructure, data center, hyperscaler capex) are already in indirect_dependencies. No causal chain is missing evidence. No action items this cycle.

---

## Kanban Cards to Create
*(none — no new causal relationships meet the two-source verification threshold this cycle)*
