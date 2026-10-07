# Causality Analysis Report — 2026-10-05

## Data read (windows, row counts, health)
- get_pipeline_health(): parse_errors total_all_time=0, this_month=0; qdrant signals 603/1d, 6372/7d; inference_rules active=322; indirect_dependencies=303. HEALTH GREEN — no parse-error spike, signal flow sane. Proceeded to causality.
- 48h recent signals (get_recent_signals, 20 rows): dominated by geopolitics (Iran/Hormuz LNG force majeure, Gulf bourses), energy, and generic sector previews. No fresh distinct signal class.
- get_trade_history (7d): 2 trades, 1W/1L, P&L -$164.60 (XLV win +2.2% time_exit; CLS ai_infrastructure loss -6.7% stop_loss). 30d: 5 trades, 2W/3L, P&L -$228.02.
- Verified outcomes pulled: 7d window = 35 rows (id 722-756); 30d window = 168 rows (id 589-756).
- Existing indirect_dependencies (lessons.db): 40 latest read, 303 total. Key active rules: 266 (FDA/clinical-trial catalyst cluster -> healthcare, "treat neutral-biased, tighten critic approval"), 267 (Fed rate-hike fear cluster -> market direction, PROVISIONAL 0.5), 265 (defense catalyst cluster -> industrials), 97/150 (geopolitical/iran -> energy).

## Sector-direction accuracy table (7d + 30d: n, win-rate, avg conf, Brier)
Brier = avg (conf - outcome)^2 where outcome=1 iff actual_direction==direction.

7D (snapshot 2026-10-05):
- Healthcare|bullish  n=4  wr=0.0%   conf=0.682  brier=0.472  <- worst
- M&A|neutral          n=3  wr=0.0%   conf=0.550  brier=0.309
- Financial|bearish    n=4  wr=50.0%  conf=0.713  brier=0.263
- Tech/AI|bullish      n=4  wr=75.0%  conf=0.662  brier=0.218
- Industrials|bullish  n=4  wr=75.0%  conf=0.525  brier=0.204

30D (snapshot 2026-10-05, sector-level Brier ranking, worst->best):
- Healthcare   n=19 wr=42.1% conf=0.690 brier=0.344  <- worst Brier; healthcare|bullish n=18 wr=44.4% brier=0.339
- Financial    n=19 wr=47.4% conf=0.567 brier=0.297
- Tech/AI      n=19 wr=47.4% conf=0.646 brier=0.273
- M&A          n=16 wr=56.2% conf=0.544 brier=0.260
- Consumer     n=19 wr=52.6% conf=0.597 brier=0.242
- Materials    n=19 wr=31.6% conf=0.427 brier=0.236
- Overall      n=19 wr=26.3% conf=0.450 brier=0.229
- Energy       n=19 wr=21.1% conf=0.400 brier=0.218
- Industrials  n=19 wr=36.8% conf=0.434 brier=0.186  <- best

Directional 30d (predictions table): bearish 48/23 (47.9%), bullish 71/31 (43.7%), mixed 26/0 (0%), neutral 23/13 (56.5%).

## Findings (each with evidence rows and table+id citations)

F1 — Healthcare-bullish is the worst-calibrated bucket (7d AND 30d).
- predictions id 725,734,743,752 (all "Healthcare and biotech sector outlook ... drug approvals, clinical trials, recent news"): bullish, conf 0.73/0.70/0.55/0.75, was_correct=0/0/0/0, actual=bearish/neutral/bearish/bearish. 4/4 wrong over 7d at avg conf 0.682.
- 30d healthcare|bullish: n=18, wr=44.4%, brier=0.339 = worst sector-direction Brier.
- CAUSE already captured: lessons.db/indirect_dependencies id=266 "FDA-approval / clinical-trial catalyst cluster (healthcare) -> healthcare sector" (conf 0.7, last_seen 2026-09-21 17:34): "bucket verified 1/4 at avg conf 0.76 with critic approving every wrong call; treat as neutral-biased, tighten critic approval." The 7d failures are exactly the FDA/clinical-trial catalyst-cluster calls this rule names.
- sector_trim: healthcare LTFT=-0.051 (mild, above the -0.10 auto-discount floor).
- Gate disposition: G3 FAIL (relationship + remedy already covered by rule 266; the "tighten critic approval" fix is already the documented remedy). New card would double-charge a documented mechanism. No card.

F2 — Mixed-direction predictions never verify (30d 0/26).
- predictions table: mixed n=26, was_correct all 0.
- Already a pending card t_3c2ddf10 "mixed-direction-hard-block-gate" (2026-09-18). G3 FAIL — duplicate. No card.

F3 — Neutral-direction is the highest win-rate class (30d 13/23, 56.5%) but was historically gate-killed/mis-scored.
- predictions table: neutral n=23, c=13.
- Already a completed card t_5ada02bc "neutral-direction-prediction-verification" (archived 2026-09-23, child coder t_59fdfabc created). G3 FAIL — already worked. No card.

F4 — Macro/overall-market bearish is the worst win-rate sector (30d overall 26.3%).
- predictions: overall bearish n=14, wr=28.6%.
- Covered by lessons.db id=267 (Fed rate-hike fear cluster -> market direction, PROVISIONAL) AND sector_trim macro LTFT=-0.194 (<= -0.10 auto-discount). G4 + G3 FAIL — trim already handles it; card would double-discount. No card.

F5 — STFT is 0.0 across every sector while LTFT carries the discount.
- sector_trim: industrials -0.344, materials -0.208, macro -0.194, financials -0.187, technology -0.102, consumer -0.061, energy -0.055, healthcare -0.051, defense 0.0 — STFT=0.0 for all.
- Interpretation: nightly trim.py STFT (from yesterday's verified outcomes) is netting to zero this period, so no new STFT is feeding LTFT. Not a causal relationship; it's a calibration-loop observation. Insufficient two-source causal evidence to file a card; flagged for the next 48h pull to confirm STFT stays 0 vs. a genuine quiet day.

## Kanban Cards to Create
No new cards. Every candidate pattern (F1-F5) is already covered by an existing indirect_dependencies rule (266, 267), an active sector_trim discount (macro -0.194, industrials -0.344, etc.), or a completed/pending kanban card (t_3c2ddf10 mixed, t_5ada02bc neutral). The strongest signal — healthcare-bullish worst-Brier (7d 0.472 / 30d 0.339) — is the exact mechanism documented by rule 266 whose remedy (tighten critic approval) is already in place; filing a new discount would double-charge it. No candidate survives the G1-G5 gates.
