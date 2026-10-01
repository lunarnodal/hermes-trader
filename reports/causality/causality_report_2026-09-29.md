# Causality Analysis Report — 2026-09-29

## Data Sources (48h window: 2026-09-27 → 09-29)

- `predictions` (paper_trading.db): 20 rows, 09-28/09-29 batch — all unverified (verify next day)
- Verified outcomes (7d): 27 predictions, 22% overall win rate
- `get_recent_signals` (48h pool): 15 signals
- `get_trade_history` (7d): 1 closed trade (CLS, −$253.40 / −6.7% stop-loss)
- `signal_ledger` (7d): 50 rejections — direction_confidence 35, critic_reject 8, meta_correction 5, hard_block 2
- `indirect_dependencies` (lessons.db): 297 rules — cross-referenced, no duplicates found for findings below
- Code: `pipeline/portfolio/selector.py`, `pipeline/paper_trading/verify.py`

## 48h Signal Landscape

Active clusters in the pool: China precursor-chemical export curbs (bearish 0.75 → chemicals/materials/semis, fresh, unverified), Citi short-covering warning + DAX bear flag (broad equity risk-off), merger-arb Form 8.3 filings (neutral), KKR UK logistics buyout, Smiths Group H2 beat (healthcare/industrials bullish 0.75). Several 09-29 predictions: consumer bearish 0.70, financials bearish 0.75, tech/AI bullish 0.70, healthcare bullish 0.70, energy bullish 0.40. All pending verification.

No new *verified* causal relationships this window: the 09-28/29 batch has no outcomes yet, and the one verified 48h-relevant outcome (CLS stop-loss) traces to a gate defect, not a market relationship (Finding 1). Fresh clusters (China export curbs, Citi positioning) are watch items — promote only after 2 verified occurrences, per protocol.

## 7d Verified Outcome Patterns

- Bullish calls (the only direction traded): ~2/11 correct (tech 1/3, healthcare 1/3, materials 0/3, consumer 0/1, industrials 0/1)
- Merger-arb neutral: 2/3 (67%) — best bucket; existing card t_bff9f66d already tracks the 2nd-occurrence validation
- Financials bearish 0.75: 1/3 (09-23 right, 09-24/25 wrong) — untradeable direction, no rule warranted
- Materials bullish 0/3 at avg conf 0.48 — trim penalty (LTFT −0.208) working as intended; no new rule
- Defense bullish 0.45 rejected at theta floor 09-25, drift −1.0%, rejection validated — covered by existing t_e34dfc83

## Finding 1 — META-CORRECTION GATE IS NON-BLOCKING (GATE LEAK) — VERIFIED

**Evidence (3 independent sources):**

1. **Code** — `selector.py` ~line 770-793: when `adjusted_confidence < 0.70` after sector trim, the meta_correction branch calls `_log_rejected_signal(gate="meta_correction", ...)` **with no `continue`**. Contrast the `direction_hard_block` branch directly below, which does `continue`. Execution proceeds: `confidence = adjusted_confidence` → direction calibration → `select_stocks_for_sector(sector, confidence, signals, ...)`.
2. **DB chain** — `recommendations` table: `2026-09-24T19:35:06.717708 | CLS | BUY | ai_infrastructure | 1 signal | avg_conf 0.75 | score 0.938`. Same second, `transactions` row 162: `CLS BUY 10 @ $378.03`. Same minute, `signal_ledger`: `ai_infrastructure bullish raw=75% adj=68% gate=meta_correction "raw=75% adj=68% < 0.70 after sector penalty"`.
3. **Outcome** — `portfolio.log` 09-28: `STOP LOSS: CLS @ $352.69 (entry=$378.03, -6.7%)` → CLOSED −$253.40 LOSS. This was the week's only trade.

**Root cause chain:** (a) meta_correction logs the "rejection" but does not stop the flow; (b) the adjusted prediction confidence is not threaded into individual-stock selection — `select_stocks_for_sector` gates individual stocks on the *signal's own* avg_conf ≥ hardcoded 0.75 (`prediction_confidence` only gates the ETF fallback at line 466), so CLS's single 0.75 signal qualified regardless of the prediction being adjusted down to 0.58; (c) the ledger records the signal as "rejected", so the signal-ledger outcome analysis (was_correct / validated-rejection metrics) is contaminated — the 09-24 row shows `was_correct=0` (false negative) while the signal actually *was* traded and lost.

**Impact:** every future run where a sector trim drops a bullish signal below 0.70 (5 such events this week) silently still trades the underlying ticker if its raw signal conf ≥ 0.75. With a 22% 7d win rate, this is the week's only P&L event.

**Recommended fix:** add `continue` (or an equivalent skip-flag) after the meta_correction rejection log, AND pass the adjusted prediction confidence into `select_stocks_for_sector`'s individual-stock threshold check so the adjusted value actually gates stock selection (at minimum: `min_conf = max(_min_conf, prediction_confidence)` when `prediction_confidence < 0.75`).

## Finding 2 — `ai_infrastructure` MISSING FROM `sector_trim`

The trim table holds 9 sectors (industrials −0.344, materials −0.208, macro −0.189, financials −0.187, technology −0.102, consumer −0.061, energy −0.055, healthcare −0.051, defense 0.0) — **no `ai_infrastructure` row**. selector.py's trim read then falls back to `_default_penalties.get(sector, 0.10)` — confirmed by the ledger: adj_conf is exactly raw × 0.90 (75→68, 70→63) in every ai_infrastructure row. Consequence: STFT/LTFT never accumulate for the sector the pipeline trades most (CLS was its only 7d trade), so its penalty is a constant blind default rather than calibrated trim. `trim.py` must be extended to include the ai_infrastructure keyword bucket.

## Finding 3 — MISLEADING REASON STRING ON NON-BULLISH SKIP (cosmetic)

`selector.py` ~line 597 logs non-bullish skips with the stale template `f"direction={direction} conf={confidence:.2f} < 0.70"` — producing `direction=bearish conf=0.75 < 0.70`, which is arithmetically impossible and misreads as a threshold violation. The real reason: only bullish predictions are traded. 6+ of this week's 50 ledger rows carry this wrong string, corrupting gate-reason analysis (the financials-bearish rows look like confidence failures). Fix: per-direction reason text ("non-bullish direction — not traded, conf=0.75 recorded for outcome tracking").

## Observations (no card)

- **`signal_ledger.was_correct` is inverted vs `predictions.was_correct`** — in the ledger, 1 = "rejection validated" (market moved AGAINST the predicted direction), 0 = "false negative". Confirmed against verify.py:334-340 comments. Future analysis runs must not join these columns naively; consider renaming to `rejection_validated`.
- **Zero-confidence opinion pieces in the "high-confidence" feed**: 4 of 15 pool signals (Copper Giant, D-Wave, Axon/ServiceNow, Cheniere — seeking-alpha opinion titles) have confidence 0.0 and empty sector lists. They cannot meet any gate, but they pollute the pool and the MCP feed. Related to t_41c665ca (sector auto-tagging); fold a min-confidence filter there if it gets reopened.
- No false positives proposed: China-export-curbs → materials/semis and Citi-positioning → broad equity are each single-occurrence unverified clusters — held back pending 2-source verification per protocol.

## Kanban Cards to Create
1. `meta-correction-gate-leak` — **Fix non-blocking meta_correction gate in selector.py: add continue after rejection log and thread adjusted prediction confidence into individual-stock selection** (Risk: medium | Sectors: pipeline, risk | Owner: coder)
   - Evidence: 09-24 19:35 UTC CLS BUY ($378.03 × 10) executed in the same second as its meta_correction rejection (ledger adj=68% < 0.70); stopped out 09-28 at −6.7% (−$253.40), the week's only trade. selector.py meta_correction branch lacks the `continue` that direction_hard_block has; select_stocks_for_sector ignores prediction_confidence for individual stocks (hardcoded signal conf ≥ 0.75).
   - Expected impact: stops trading signals the gate has already rejected; cleans signal_ledger "rejection" contamination; recovers future stop-loss losses of this type.
2. `ai-infrastructure-trim-missing` — **Add ai_infrastructure to sector_trim table and trim.py sector coverage so STFT/LTFT accumulate instead of the blind 0.10 default penalty** (Risk: low | Sectors: calibration, pipeline | Owner: coder)
   - Evidence: trim table has 9 sectors, no ai_infrastructure; selector.py falls back to default 0.10 penalty (ledger shows adj_conf = raw × 0.90 exactly on every ai_infrastructure row, 5 events in 7d).
   - Expected impact: calibrated penalty for the most-traded sector; meta-correction and hard-block gates use measured win-rate data instead of a constant.
3. `fix-ledger-bearish-reason-string` — **Fix misleading signal_ledger reason string on non-bullish skip ("direction=bearish conf=0.75 < 0.70" is a stale template; actual reason: non-bullish directions are not traded)** (Risk: low | Sectors: pipeline, signal-quality | Owner: coder)
   - Evidence: selector.py ~line 597 hardcodes the `< 0.70` template in the `direction != "bullish"` skip path; 6+ of this week's 50 ledger rows (financials bearish 0.75 at 09-24/25/28, ×2 runs) misread as threshold violations.
   - Expected impact: gate-reason analysis and rejection statistics become trustworthy; no behavior change, logging only.
