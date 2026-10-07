# Causality Analysis Report — 2026-10-06

## Data read (windows, row counts, health)
- **Pipeline health (green):** 325 active inference_rules, 306 indirect_dependencies,
  qdrant signals last-1d 1,297 / 3d 1,890 / 7d 6,260 / 30d 26,668.
  parse_errors total_all_time = 0, this_month = 0. No health red flag → proceed.
- **Predictions (verified, 30d):** 130 rows across 8 sector buckets, 2 windows.
  7d window = 27 verified predictions (overall win rate 37% per get_prediction_accuracy).
- **Trade history (30d):** 5 closed trades, 2W/3L, 40% win rate, PnL -$228.02.
  (Healthcare XLV time-exit +2.2% WIN; the 3 losses are technology/ai_infra.)
- **Signal ledger (7d):** 50 rejected. By gate: direction_confidence 25,
  critic_verdict_reject 17, hard_block_bullish 6, meta_correction 2.
- **Sector trim (all):** industrials -0.344, materials -0.208, macro -0.190,
  financials -0.187, technology -0.102, consumer -0.061, energy -0.055,
  healthcare -0.051, defense 0.0. All STFT = 0.0.

## Sector-direction accuracy table (7d + 30d: n, win-rate, avg conf, Brier)
Brier = mean squared error only for directional (bullish/bearish) rows; neutral/mixed
rows are excluded from Brier (no direction to be wrong on). Snapshot 2026-10-06.

| Sector / direction | 7d n | 7d WR | 7d conf | 7d Brier | 30d n | 30d WR | 30d conf | 30d Brier |
|---|---|---|---|---|---|---|---|---|
| healthcare / bullish | 4 | 0.00 | 0.675 | 0.461 | 19 | 0.421 | 0.693 | 0.347 |
| technology / bullish | 4 | 0.50 | 0.637 | 0.308 | 16 | 0.438 | 0.649 | 0.273 |
| consumer / bearish | 2 | 0.50 | 0.625 | 0.346 | 10 | 0.700 | 0.640 | 0.249 |
| consumer / bullish | 2 | 0.50 | 0.725 | 0.326 | 2 | 0.500 | 0.575 | 0.231 |
| financials / bearish | 4 | 0.250 | 0.675 | 0.316 | 15 | 0.533 | 0.555 | 0.306 |
| industrials / bullish | 4 | 0.50 | 0.525 | 0.204 | 11 | 0.455 | 0.459 | 0.223 |
| materials / bullish | 4 | 0.50 | 0.500 | 0.255 | 10 | 0.100 | 0.481 | 0.222 |
| energy / bullish | 1 | 1.00 | 0.400 | 0.360 | 3 | 0.333 | 0.467 | 0.365 |
| overall / bearish | 2 | 0.50 | 0.460 | 0.154 | 12 | 0.250 | 0.478 | 0.241 |
| merger_arb / neutral | 4 | 0.50 | 0.500 | — | 14 | 0.643 | 0.550 | — |

Notes:
- healthcare bullish is the **highest-Brier major bullish bucket (0.347, n=19)**; the
  only higher value is energy bullish (0.365, n=3) on a sample too small to be reliable.
- consumer bearish is the standout WINNER bucket (30d 70% WR, Brier 0.249) — no action.
- materials bullish 30d WR is only 10% but materials LTFT = -0.208 (already heavily
  trimmed) → see G4 below.

## Findings (each with evidence rows and table+id citations)

### F1 — Critic reject gate is killing MORE winners than losers (DUPLICATE — not re-filed)
- paper_trading.db signal_ledger, gate_failed='critic_verdict_reject', 7d, was_correct IS NOT NULL:
  n=20, was_correct=1 (killed a winner) = 13, was_correct=0 (killed a loser) = 7.
  The active critic-reject portfolio gate blocked ~65% winners vs ~35% losers.
- Corroborated by paper_trading.db predictions 7d: e.g. id 755 (Consumer bullish 0.70,
  was_correct=1, critic=reject), id 735 (Materials bullish 0.50, was_correct=1, critic=reject),
  id 723 (Technology bullish 0.75, was_correct=1, critic=approve→ but 732 tech bullish 0.70 was_correct=0).
- **G3 kill:** already pending as kanban card `critic-verdict-quality-measurement`
  (task t_1854b679, filed 09-22): "reject is the WORST bucket... down-weight or
  recalibrate the critic gate if discrimination stays < 55%." This run just re-confirms
  the trend (13/20 winners killed); not a new relationship → dropped to avoid a dup.

### F2 — Healthcare-bullish sector-outlook calls are systematically overconfident (NEW → card)
- paper_trading.db predictions, query LIKE '%Healthcare%', direction='bullish', 30d,
  was_correct IS NOT NULL: **19 rows, 8 correct = 42.1% WR, avg conf 0.693, Brier 0.347.**
  Most-recent-7d subset (ids 734, 743, 752, 761): 0/4, avg conf 0.675, Brier 0.461.
  Representative rows: id 671 (0.85 conf, was_correct=0, critic=approve), id 689
  (0.75, 0, approve), id 761 (0.70, 0, reject), id 752 (0.75, 0, reject).
- paper_trading.db sector_trim, sector='healthcare': **LTFT = -0.0512** (mild), STFT 0.0.
  The nightly trim has applied only a ~5% discount even though the direction is
  verified at 42% with 69% average stated confidence — the penalty is undersized for
  the miscalibration.
- Brier cross-bucket check (30d, bullish only): healthcare 0.347 > technology 0.280 >
  consumer 0.279 > materials 0.231 > industrials 0.218 → healthcare bullish is the
  least-calibrated major bullish bucket (rank computed over 30d, n=19; snapshot 10-06).
- Distinct from the narrower pending card `fda-catalyst-healthcare-discount`
  (t_25e4e1cf, 09-21), which discounts **FDA/catalyst-cluster**-driven healthcare
  bullish calls. F2 is about the **generic daily "Healthcare and biotech sector
  outlook"** bullish direction (a different query class / trigger), so it is not a dup.

### F3 — Sector-trim gate already covers the other overconfident sectors (no card)
- materials bullish (30d 10% WR), technology bullish (30d 44%), overall/macro bearish
  (30d 25%), financials bearish (30d 53%) are all candidate discounts, but their
  sector_trim LTFT is already ≤ -0.10 (materials -0.208, technology -0.102, macro -0.190,
  financials -0.187, industrials -0.344). Per gate t_b14dc8da the nightly trim.py
  already handles these; filing a card would double-discount. Dropped.

### F4 — ai_infrastructure hard-block killing winners (DUPLICATE — not re-filed)
- paper_trading.db signal_ledger, gate_failed='hard_block_bullish', sector='ai_infrastructure',
  7d: 3 rows, was_correct=1 on 2 of them (e.g. 10-02 13:35 eff_wr 26%, 10-02 19:35 eff_wr 31%).
  The hard-block uses a low effective WR because ai_infrastructure has **no sector_trim
  row**, so it falls back to the blind 0.10 default penalty.
- **G3 kill:** already pending as `ai-infrastructure-trim-missing` (task t_32a63ab1,
  filed 09-29): "Add ai_infrastructure to sector_trim table and trim.py sector coverage
  so STFT/LTFT accumulate instead of the blind 0.10 default penalty." Dropped.

## Kanban Cards to Create

1. `healthcare-bullish-overconfidence-discount` — **Discount overconfident healthcare-bullish sector-outlook calls** (Risk: medium | Sectors: calibration, rules | Owner: coder)
   - Evidence: paper_trading.db predictions, direction='bullish' AND query LIKE '%Healthcare%', 30d verified = 19 rows, 8 correct (42.1% WR), avg conf 0.693, Brier 0.347 (highest-Brier major bullish bucket; 7d ids 734/743/752/761 = 0/4 @ conf 0.675); sector_trim healthcare LTFT = -0.0512 (row read this session, not <= -0.10 so gate permits).
   - Expected impact: healthcare bullish is the least-calibrated major bullish bucket yet carries only a ~5% trim; a direction-aware discount/confidence ceiling would cut false-long healthcare entries (8 wrong calls @ 69% conf over 30d, 4 critic-APPROVED ids 653/662/671/689).
   - Data vs code: CODE: 4-stage. Fix is in trim.py / selector.py — add a direction-aware trim or confidence ceiling for healthcare bullish, NOT a hand-written sector_trim value that trim.py overwrites nightly.
