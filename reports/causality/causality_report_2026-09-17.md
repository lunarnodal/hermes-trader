# Causality Analysis Report — 2026-09-17

## 1. Data Scope
- Predictions: 09-15 12:xx batch verified 09-16 10:00 UTC (OLD mapping, 3/8 correct); 09-16 12:xx batch (9) and 09-17 12:xx batch (8) unverified — first verification under the NEW mapping runs 09-17 16:00 UTC
- Signals: 10 most recent high-confidence signals; signal_ledger 72 entries in 48h (66/72 sector=unknown); 2,000-sample event breakdown (5% other)
- Closed trades (30d): 6 — 4W/2L, +$155.97, 67% (SPCX +5.1%, AMZN -1.1%, CRM -3.4%, XLV +2.5%, XLB +3.2%, XOM +7.2%)
- Pipeline health: 285 active inference rules, 258 indirect_dependencies, 0 parse errors, Qdrant 1,503 signals/1d
- Prior report status: BOTH 09-16 cards implemented — t_c79f41b4 (ETF fix, commit 4b76ffb 09-17 01:48 ET) done; t_8e09e11f (crude_oil_surge to broad_market, conf 0.5) done, rule row confirmed present in lessons.db

## 2. 48h Signal to Move Cross-Reference
No new verified 2+ independent-source corroboration pairs for causal rules in the 48h window. Candidates examined and rejected:
- **Bab el-Mandeb Strait** (finnhub-general, neutral 0.65) — single source, no corroborating move signal; existing rules #97/#180/#196 already encode geopolitical tension to energy/defense volatility. A strait-specific rule would duplicate the pattern with no outcome evidence yet.
- **CAMP/CWH consumer bearish** (seeking-alpha 0.75) — single source, ticker-specific; consumer-bearish direction bias already enforced by critic (wrong 6 of last 8) and sector_trim. No new entity-level causal relationship.
- **CRH/ARCOS data-center industrials bullish** (seeking-alpha 0.75) — single source; hyperscaler-capex to ai_infrastructure and to energy rules already exist in the 258-rule set. No new link.
- 09-15 batch (verified 09-16 10:00): 3/8 correct — still contaminated (healthcare via XLK, consumer/materials via AIQ per 09-16 Finding A); excluded from causal interpretation.
- "mixed" direction: 9 predictions in 14d, 0% correct, all gate-blocked by design (direction_confidence < 0.70). Not a causal pattern.

## 3. Finding A — Residual keyword shadowing in sector_etf.py (data-integrity, BLOCKING for tonight verification)

The 09-17 fix (4b76ffb) resolved healthcare/consumer/materials, but live-testing get_sector_etf() against all 9 production query templates shows 3 still map to the WRONG ETF:

| Production query | Maps to | Should be | Root cause |
|------------------|---------|-----------|------------|
| Overall market outlook ... Fed | XLF | SPY | "fed" (financials block) matches; there is NO macro/"market" keyword anywhere in KEYWORDS |
| Financial sector outlook ... real estate, Fed policy | VNQ | XLF | "real estate" (real_estate to VNQ) block sits ABOVE the financials block |
| Technology and AI ... semiconductors, ai infrastructure | AIQ | SOXX (arguable) | plural "semiconductors" fails the singular "semiconductor" word-boundary regex; "ai" to ai_infra shadows technology |

Evidence (two independent sources):
1. Live get_sector_etf() output run 2026-09-17 13:05 UTC on airig against all 9 production query templates (health to XLV OK, consumer to XLP OK, materials to XLB OK, industrials to XLI OK, energy to XLE OK, merger-arb to SPY OK, market to XLF WRONG, financials to VNQ WRONG, tech to AIQ WRONG)
2. KEYWORDS table order inspection: no (market/s&p/macro) entry exists; real-estate block precedes financials block

Impact: the 09-16 12:xx batch verifies at 09-17 16:00 UTC under the NEW mapping — market (SPY) and financials (XLF) win rates will be re-contaminated (XLF for market, VNQ for financials) for exactly the sectors sitting near the 35% hard-block threshold. Tonight's "first clean verification" will be clean for 6/8 sectors only.

## 4. Finding B — Sector attribution still 81-100% unknown
signal_ledger sector=unknown: 09-16 18/18, 09-15 48/54, 09-14 16/18, 09-11 66/80; 7-day event_type aggregate returns only "other" (188/188). Unresolved since the 09-16 report — the upstream attribution fix is still pending. No new card (tracked in existing queue rationale); re-flagged because sector-keyed win-rate lookups (hard-block, critic, trim) remain degraded while attribution is broken.

## 5. Sectors Reviewed — No New Causal Rules
- **Healthcare**: 75% bullish 7d win rate is an artifact of XLK contamination — do NOT propose momentum rules until first clean verification (09-17 16:00 UTC).
- **Technology**: 6 hard-blocks in 48h (win_rate 28-31% < 35%, conf 60% < 65%) — gate working as designed; consistent with critic bias flag and SPCX/AMZN/CRM trade outcomes. No new rule.
- **Crude-oil-surge to broad-market** rule now exists (provisional conf 0.5) — no duplicate proposed.
- **Trades**: no uncaptured signal to outcome pattern; all 30d outcomes consistent with existing calibration.

## Kanban Cards to Create
1. `fix-verification-etf-keyword-shadowing` — **Fix residual keyword shadowing in sector_etf.py: add macro keywords (market, s&p) BEFORE financials block, move real-estate block below financials, add plural semiconductors alias** (Risk: low | Sectors: pipeline, risk | Owner: coder)
   - Evidence: live get_sector_etf() run 09-17 13:05 UTC (market to XLF, financials to VNQ, tech to AIQ) + KEYWORDS table order inspection — two independent sources
   - Urgency: blocks clean verification of the 09-16 batch at 09-17 16:00 UTC; tonight would re-contaminate 2/8 sectors under the "fixed" mapping
   - Expected impact: restores valid market/financials win rates, protecting the 35% hard-block gate and sector_trim feedback for those sectors
   - Verification: unit check that all 9 production query templates map to SPY/XLF/SOXX-AIQ/XLV/XLP/XLB/XLI/XLE; then confirm 09-17 16:00 verify.log shows "Verified via SPY" / "Verified via XLF" for market/financials rows
