# Causality Analysis Report — 2026-09-23

## Window & Data Sources
- Predictions: 20 verified rows (2026-09-16 → 09-21) + unverified 09-22/09-23 batch, paper_trading.db
- Signals: 30 recent high-confidence signals from news pipeline (last 48h)
- Trades: 0 closed trades in 7d window (no execution — gate-blocked)
- Existing rules: 20 most recent indirect_dependencies rows (lessons.db)
- Critic stats (14d): approve 13/23 (57%), challenge 12/38 (32%), reject 10/27 (37%)

## Finding A — Pre-fix verification contamination still pollutes calibration
The t_7ec31cb6 keyword-shadowing fix landed 09-19 19:59 ET (commit 6c1d74d), but ~10 predictions
verified BEFORE the fix were scored against the WRONG ETF and their was_correct values still feed
sector win rates and LTFT trim:
- Financial sector outlook 09-16/09-17 verified via **VNQ** (real-estate ETF) — both scored "correct"
- Overall market outlook 09-16/09-17 verified via **XLF** ("Fed" keyword shadowed "market")
- Materials + Consumer outlook 09-14/09-15 verified via **AIQ** (AI-infrastructure ETF)
Post-fix spot check: 09-21 rows verify correctly (Financials→XLF, Materials→XLB, Tech→SOXX).
Residual risk: materials LTFT (-0.206) and financials LTFT (-0.186) are partially built on rows
measured against the wrong benchmark. The historical rows need re-verification with the fixed map.

## Finding B — Fed-hike-fear bearish cluster: third occurrence pending (rule 267)
Rule 267 (PROVISIONAL, conf 0.5, "promote after one more occurrence") is supported again:
- 09-21 overall-market bearish (conf 0.38, reasoning = "anticipated Fed tightening, elevated oil,
  volatility parallels") → WRONG, SPY +0.51% bullish. Fed-fear bearish record now 0/3 in 14d
  (09-17 conf 0.35 wrong, 09-18 conf 0.55 wrong, 09-21 conf 0.38 wrong).
- 09-22 overall-market bearish (conf 0.50, "unanimous Fed rate hike, hawkish guidance, rising
  yields") is the third occurrence — NOT yet verified. Promotion of rule 267 is blocked on this
  row verifying neutral/bullish. Do not promote today; recheck tomorrow.
No new card — `fed-hike-fear-priced-in-rule` (t_52a66e7b) already open.

## Finding C — NEW: single-stock "surging/rallying today" explainer signals → sector momentum continuation
Signal pattern: investing-com publishes "Why is <ticker> stock surging/rallying today?" articles
on intraday momentum days. Last 48h produced a semiconductor surge cluster:
- "Why is Intel stock surging today?" (bullish 0.75, semiconductors)
- "Why is SK hynix stock rallying today?" (bullish 0.75, semis/AI infra)
Outcome evidence: Technology predictions verified correct 2/2 in the window via SOXX
(09-17 +1.51%, 09-21 +1.46%) — the only sector with repeated verified directional wins.
Momentum explainer clusters precede continuation in the named sector within 1-5d.
This is distinct from existing rules (bofa risk warnings→tech, ai/tech momentum→e-commerce).
Mirror bearish form ("sliding/falling today" — Vodafone, Stellantis in window) is only tagged
neutral and has no outcome evidence yet — propose as bullish-side rule only.

## Finding D — NEW: flat-market sessions destroy directional accuracy
09-18 session: every sector ETF closed within ±0.07% (XLY +0.07, XLV -0.01, XLE +0.02, SOXX +0.00,
XLI -0.00, XLF +0.00, XLB +0.01, SPY -0.00). 8 of 9 directional predictions scored WRONG; only
the always-neutral merger-arb row scored. 09-17 was near-flat on 5 of 8 ETFs. Directional
confidence carries no signal when realized cross-sector movement collapses below the ±0.2%
verification threshold — the pipeline should decay directional calls toward neutral in
low-realized-vol regimes instead of spending them. Note: signal_ledger.vix_at_time is 100% NULL
(currently no populated vol regime feature exists to gate on).

## Finding E — Critic reject verdicts are anti-correlated with outcomes (existing card, updated evidence)
14d: reject-verdict predictions were correct 37% (10/27) and approve 57% (13/23). Two verified-wrong
calls were critic-approved, but both verified-WINNING tech/SOXX calls (09-16, 09-17) carry critic
verdict "reject" in the prediction rows — the reject is currently destroying the pipeline's best
performing sector. Feeds existing `critic-verdict-quality-measurement` (t_1854b679); no new card.

## Finding F — Bearish-macro→energy defensive rotation (rule 263): counter-evidence, keep provisional
09-21 was a bearish-macro day AND energy bearish verified correct (XLE -1.22%) — capital did NOT
rotate defensive that day. Against 09-14 window evidence (XOM +7.2% under bearish macro).
Rule 263 stays at conf 0.5, occurrences 1. No promotion, no new card
(`bearish-macro-energy-rotation` t_a3acb7c4 stays open).

## Kanban Cards to Create
1. `reverify-pre-fix-contaminated-predictions` — **Re-verify was_correct for predictions verified 2026-09-14→09-19 using the corrected sector_etf map: 10 rows scored against wrong ETFs (Financials→VNQ, Market→XLF, Materials/Consumer→AIQ) still feed sector win rates and LTFT trim; recompute, update was_correct/actual_notes, then re-run trim for affected sectors** (Risk: low | Sectors: pipeline, rules | Owner: coder)
2. `stock-surge-explainer-momentum-rule` — **Add indirect dependency: investing-com "Why is <ticker> stock surging/rallying today?" bullish explainer signals predict 1-5d bullish continuation in the named sector (evidence: INTC+SK hynix semis surge cluster 09-22/23; tech bullish 2/2 verified via SOXX +1.51%/+1.46% in 7d window)** (Risk: low | Sectors: rules | Owner: coder)
3. `low-vol-regime-direction-decay` — **Decay directional predictions toward neutral in flat-market regimes: on 09-18 all 8 sector ETFs moved ≤0.07% and 8/9 directional predictions scored wrong; add a realized-vol / prior-day cross-sector range check to selector (and populate the always-NULL signal_ledger.vix_at_time so a VIX floor gate becomes possible)** (Risk: medium | Sectors: pipeline, risk | Owner: coder)
