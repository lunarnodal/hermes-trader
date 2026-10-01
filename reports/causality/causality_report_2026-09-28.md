# Causality Analysis Report — 2026-09-28

Run: 9:00 AM ET Monday (12:00 UTC). 48h signal window: created since 2026-09-26 ~13:00 UTC. Note: no signals/predictions were created over the weekend (09-26/09-27); the live 48h set is the 09-28 12:02–12:14 UTC batch (9 predictions, all unverified — 24h/48h timeframes still open).

## Pipeline Health (pre-check)
- inference_rules active: 313; indirect_dependencies: 294; parse errors: 0 (all-time)
- Qdrant signals: 721 (1d) / 1,835 (3d) / 6,413 (7d)
- Closed trades last 7d: 0 (gates blocking all execution)
- 7-day verified win rate: 22% (27 predictions) — bearish 5/10, bullish 6/15, mixed 0/7, neutral 0/3
- STFT: all sectors 0.0; last_stft_reset spans 09-03 → 09-26

## Verified Outcomes vs Critic Verdict (7d, 35 verified predictions)
| Verdict | n | correct | win rate |
|---|---|---|---|
| approve | 16 | 5 | 31% |
| challenge | 9 | 2 | 22% |
| reject | 10 | 4 | 40% |

Critic ranking still inverted (reject > approve > challenge) — matches 09-25 report (n=17). Additional evidence for existing card `critic-verdict-quality-measurement`; no new card.

Mixed/neutral directions: 0/10 correct in 7d — covered by `mixed-direction-hard-block-gate` + `low-vol-regime-direction-decay`. No new card.

## New Causal Findings (2-source verified)

### Finding 1 — Verification market-open deferral + trim ordering creates a structural Monday STFT blind spot
Causal chain: verify.py defers any expired prediction until the next run where (a) local weekday AND (b) local clock >= 09:30; trim.py computes STFT only from predictions with verified_at in the trailing 24h; trim runs 05:00 ET / 09:00 UTC.
Consequences:
- Friday predictions expire Saturday → first eligible run is Monday 14:00 UTC, i.e. ~50h after the 24h timeframe (2-day lag).
- The Monday 09:00 UTC trim cycle (09-28) runs BEFORE Monday's first eligible verification run → it can never see a verified batch → STFT is structurally zero on every Monday cycle.
- Evidence (3 sources):
  1. DB: 09-25 batch (9 predictions, created Fri 12:0x UTC, 24h timeframe, expired Sat 12:0x UTC) all verified_at IS NULL as of Mon 12:00 UTC; last verification 2026-09-25 14:00 UTC.
  2. trim.log 09-27 and 09-28 05:00 ET: "No verified predictions in last 24h — STFT unchanged" and "No STFT corrections to apply".
  3. sector_trim table: STFT = 0.0 for all 9 sectors; last_stft_reset dates 09-03 → 09-26 (never refreshed this week).
Impact: the short-term learning loop (STFT to LTFT absorption, lr=0.05) receives at most one 9-prediction batch per week, lagged 1-2 days; LTFT penalties (e.g. industrials -0.343) persist uncorrected through a 22%-win-rate week.
Not a duplicate: `fix-verification-cron-lock-starvation` covers lock contention, not deferral/trim ordering; `industrials-trim-overpenalty-reconcile` is sector-specific reconcile logic.

### Finding 2 — signal_ledger.next_day_drift has no writer; gate-rejection outcomes never recorded
- DB: 549 signal_ledger rows, COUNT(next_day_drift) = 0 (field never populated since 09-04).
- Code: grep across pipeline (excl. .venv) finds exactly one reference — a read at pipeline_mcp.py:916. No writer exists anywhere.
Consequence: the dominant gate rejectors in the 3-day ledger are direction_confidence (7 of 15 entries) plus hard_block_bullish and critic_verdict_reject — but none of these rejections ever record what the sector actually did the next day. Gate thresholds (e.g. direction-confidence cut, hard-block 35% floor) therefore have zero outcome feedback and cannot be tuned from data.
Not a duplicate: `wire-rule-performance-attribution` covers inference-rule attribution in predictions, not the signal_ledger rejection channel.

## Observations (insufficient for a card — monitor next run)
- 09-28 12:0x UTC batch: critic rejected 3 of 9 at conf 0.45-0.73 (materials, healthcare, industrials bullish) while approving tech bullish 0.75 and financials bearish 0.70. Tech bullish has now been issued 4 consecutive days (09-24 to 09-28) at conf 0.63-0.75; 09-23 and 09-24 verified wrong. Existing `technology-bullish-gate-5-violation` card already targets this; 2 more data points.
- 48h signals: Amkor AI/HPC packaging demand (bullish 0.85, AMKR/BILL/PRG) — n=1, no packaging-supply rule yet; candidate for rule discovery if a second occurrence appears.
- Novo Nordisk -6% on 2030 growth targets (bearish 0.75) vs Cue/Syncona/J&J positive catalysts (bullish 0.75): healthcare mixed within same window — consistent with `fda-catalyst-healthcare-discount` scope; no new rule.
- Ticker-extraction sanity: 09-28 window clean (no spurious tickers like the 09-25 LPP to TGT/TRON case) — prior observation not yet confirmed on a second example; `stock-surge-explainer-momentum-rule` unaffected.

## Kanban Cards to Create
1. `verify-stft-monday-blindspot` — **Fix verification/trim ordering so STFT sees Monday's verified batch** (Risk: medium | Sectors: pipeline, verification | Owner: coder)
   - Evidence: DB shows 09-25 batch (9 preds, 24h TF, expired Sat 12:0x UTC) unverified as of Mon 12:00 UTC, last verification 09-25 14:00 UTC; trim.log 09-27/09-28 log "No verified predictions in last 24h"; sector_trim STFT all 0.0 (last_stft_reset 09-03 to 09-26). verify.py market-open deferral (weekday + 09:30) + trim.py 24h verified_at window + 09:00 UTC trim schedule = Friday batch verifies ~50h late and every Monday trim cycle sees zero inputs.
   - Expected impact: STFT learning loop gets Monday-cycle inputs with 1-day lag; LTFT absorption reacts to real outcomes instead of stale/frozen penalties.
2. `signal-ledger-drift-writer-missing` — **Add outcome writer for signal_ledger.next_day_drift (and was_correct) on rejected signals** (Risk: low | Sectors: pipeline, verification | Owner: coder)
   - Evidence: 549 signal_ledger rows with COUNT(next_day_drift)=0 (never populated since 09-04); grep of pipeline/ (excl. .venv) finds only a reader (pipeline_mcp.py:916), no writer. 3-day ledger sample: direction_confidence rejects 7/15, plus hard_block_bullish and critic_verdict_reject — none of these rejections record next-day sector move, so gate thresholds have no outcome feedback.
   - Expected impact: gate tuning (direction-confidence cut, hard-block floor, critic reject rate) becomes data-driven; Sunday classification and weekly reviews gain outcome labels on the rejection channel.
