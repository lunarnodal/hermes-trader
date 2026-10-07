# Causality Analysis Report — 2026-10-02

## Data read (windows, row counts, health)

**Phase 0 — Health gate: GREEN.**
- `get_pipeline_health()`: parse_errors 0 (all-time and this month), inference_rules active=319, indirect_dependencies=300, Qdrant signals 1,342 (24h) / 3,806 (3d) / 6,605 (7d) — sane ingestion rate.
- `get_indirect_dependencies()`: 300 rules; ids 225–244 (AI infra cluster), 97/101/150 (geopolitical), 148/157 (bond yields → tech/growth), 218/220 (exec transitions), 244 (merger arb → energy), plus legacy 1–13.
- Note: `lessons.db indirect_dependencies id=251` is a placeholder row (`...`/`...`, conf 0.7, 14 occurrences, last_seen 2026-10-01T08:32) — confirmed via SELECT in this session. Not a real rule; ignore when deduping.

**Phase 1 — Read:**
- 48h verified predictions: 35 rows (paper_trading.db predictions ids 713–747, 2026-09-25 → 09-30).
- 30d verified predictions: 183 rows (ids ~565–747, 2026-09-02 → 09-30).
- 48h signals (get_recent_signals, 20 returned): bond-rout cluster ("U.S 10-year yield climbs for 6 straight week as bond rout deepens", 0.85 bearish; "Bond Yields Slip After Surging to Highest in Decades", 0.75), Akamai surge cluster (2× 0.75 bullish cloud/tech), ArcelorMittal Ukraine plant halt + $1B impairment (0.91 bearish, materials/defense/industrial), Safestay -35% (0.85 bearish consumer/travel), OXF insider buy (0.75).
- 48h trade history: 1 closed trade — XLV (healthcare) WIN +2.2% / +$88.80, time_exit, held 09-15 → 09-30.
- sector_trim (paper_trading.db, all STFT=0.0, updated 2026-09-29T09:00): industrials -0.344, materials -0.208, macro -0.191, financials -0.187, technology -0.102, consumer -0.061, energy -0.055, healthcare -0.051, defense 0.0.

## Sector-direction accuracy table (7d + 30d: n, win-rate, avg conf, Brier)

Sector derived from prediction query string; Brier = avg (conf − outcome)². Snapshot date 2026-10-02.

| Sector (direction) | 7d n | 7d WR | 7d avg conf | 7d Brier | 30d n | 30d WR | 30d avg conf | 30d Brier |
|---|---|---|---|---|---|---|---|---|
| consumer (bearish) | 2 | 50% | 0.625 | 0.346 | 14 | **64.3%** | 0.639 | **0.266** |
| merger-arb (neutral) | 3 | 33% | 0.600 | 0.238 | 15 | 60.0% | 0.543 | 0.258 |
| technology (bullish) | 4 | 50% | 0.688 | 0.301 | 21 | 47.6% | 0.651 | 0.274 |
| financials (bearish) | 4 | 50% | 0.750 | 0.314 | 20 | 50.0% | 0.561 | 0.295 |
| healthcare (bullish) | 4 | **0%** | 0.645 | 0.421 | 20 | 40.0% | **0.693** | 0.355 |
| industrials (bullish) | 4 | 50% | 0.488 | 0.214 | 16 | 37.5% | 0.466 | 0.220 |
| macro (bearish) | 2 | 50% | 0.460 | 0.154 | 17 | 23.5% | 0.465 | 0.219 |
| materials (bullish) | 4 | 25% | 0.463 | 0.216 | 15 | **13.3%** | 0.481 | 0.226 |
| energy (bullish) | — | — | — | — | 7 | 28.6% | 0.571 | **0.440** |
| energy (mixed) | 3 | 0% | 0.300 | 0.090 | 10 | 0% | 0.375 | 0.150 |

## Findings (each with evidence rows and table+id citations)

**F1 — Healthcare bullish overconfidence, 30d evidence now 5× stronger than the pending card's.**
- 30d: n=20, WR 40%, avg conf 0.693 (highest of any 30d bucket), Brier 0.355. Last 11 calls (ids 653, 662, 671, 689, 698, 707, 716, 725, 734, 743 — 09-16 → 09-30): 10 wrong, 1 right.
- High-confidence slice (conf ≥ 0.70): 5 rows, 1/5 correct — 576 (0.75, ✗), 584 (0.75, ✗), 644 (0.85, ✓), 653 (0.80, ✗), 671 (0.85, ✗). **All 5 had critic_verdict=approve** (paper_trading.db predictions, ids 576/584/644/653/671).
- Second source: paper_trading.db sector_trim row `healthcare` LTFT=-0.051, STFT=0.0 — trim is accumulating but has not reached the -0.10 floor.
- 7d: 0/4 (ids 716, 725, 734, 743; avg conf 0.645, Brier 0.421 — worst 7d bucket).
- Gate outcome: **G3 kill** — pending card `fda-catalyst-healthcare-discount` (t_25e4e1cf, report 2026-09-21, based on 1/4 at avg conf 0.76) already files the same fix (discount healthcare bullish + tighten critic approval). Filing a second discount card for the same bucket would double-discount once both land; healthcare LTFT is also still accruing via nightly trim. → No new card; this run's evidence (n=20 vs n=4; 10-of-last-11 wrong; all-approve high-conf slice) should be attached to t_25e4e1cf at approval review.

**F2 — Energy bullish: worst 30d Brier, but sample too thin to file.**
- 30d: n=7, WR 28.6%, Brier 0.440 (worst 30d bucket). High-conf slice (≥0.65): 0/3 — ids 565 (0.65, ✗, approve), 573 (0.75, ✗, approve), 581 (0.80, ✗, approve), all in one 3-day span (09-02 → 09-04). Low-conf slice (<0.60): 2/4 — 605 (0.30, ✓, challenge), 614 (0.55, ✗), 623 (0.55, ✗), 731 (0.40, ✓, reject).
- Second source: sector_trim `energy` LTFT=-0.055 (above -0.10, so G4 technically passes).
- Gate outcome: **G1 strength kill** — 0/3 vs 2/4 across the confidence split is not separable at n=7 (within chance range), and the 3 wrong high-conf instances all cluster in one 3-day regime. Recheck in next run; if two more high-conf energy-bullish misses appear with critic-approve, file then. Monitor, no card.

**F3 — Consumer bearish is the most reliable directional bucket (positive finding, no action).**
- 30d: n=14, WR 64.3%, Brier 0.266 — best of all 30d sector-direction buckets (>=30d window, snapshot 2026-10-02). 7d: 1/2.
- Sector is NOT over-penalized: sector_trim `consumer` LTFT=-0.061, STFT=0.0.
- No fix required — nightly trim will let positive outcomes accredit the sector automatically; a manual card would fight the auto-calibrator. No card.

**F4 — Merger-arb neutral predictions: 60% (n=15) but neutral verification is mid-repair.**
- 30d: n=15, WR 60%, Brier 0.258 (2nd best). However pending card `neutral-direction-prediction-verification` (t_5ada02bc) is repairing how neutral outcomes are scored; any "neutral is reliable" rule filed now would rest on semantics that are actively changing. No card; re-evaluate after t_5ada02bc lands.

**F5 — G4-blocked buckets (do not file — nightly trim already deep-discounting, a card would double-discount):**
- materials bullish: 30d n=15, WR 13.3% — LTFT -0.208 ≤ -0.10. G4 hard block.
- macro bearish: 30d n=17, WR 23.5% — LTFT -0.191 ≤ -0.10. G4 hard block.
- technology bullish: 30d n=21, WR 47.6% — LTFT -0.102 ≤ -0.10. G4 hard block (plus pending t_3f8bb465 covers the gate-5 question).
- industrials bullish: 30d n=16, WR 37.5% — LTFT -0.344 ≤ -0.10, and pending `industrials-trim-overpenalty-reconcile` (t_196898f1) argues the opposite direction (trim too deep). A discount card would contradict a pending card. G3+G4 kill.

**F6 — Yield-spike → financials edge: already filed yesterday, no duplicate.**
- Today's top signals include the rout continuation ("U.S 10-year yield climbs for 6 straight week", 0.85 bearish, tagged energy/financials/real_estate/utilities). Pending card `financials-bearish-yield-spike-overconfidence` (t_b2d4f09d, 2026-10-01) already discounts the yield-spike → financials-sell-off edge. 30d bucket: financials bearish n=20, WR 50%, Brier 0.295 — consistent with that card's premise. No new card.
- The bond-yields → tech/growth general edge is already an active indirect_dependencies rule (lessons.db ids 148, 157 — confirmed by SELECT via get_indirect_dependencies).

**F7 — 48h signal → outcome spot check (09-30):** bond-rout headlines present; financials bearish ✓ (id 742, 0.8, approve), macro bearish ✓ (id 747, 0.57, challenge), tech bullish ✓ (id 741, 0.6, approve); all three same-day bullish sector calls (healthcare/industrials/materials) ✗. Pattern is a regime-day effect consistent with F1/F5, not a new causal edge. No card.

## Kanban Cards to Create

No new cards (all candidate patterns killed at the gates: F1/G3 duplicate of pending t_25e4e1cf with recommendation to attach this run's stronger evidence at approval; F2/G1 sample too thin at n=7, monitor next run; F3 no action needed (auto-trim); F4 blocked on pending t_5ada02bc verification repair; F5 G4 sector_trim ≤ -0.10 hard blocks; F6 already filed t_b2d4f09d on 2026-10-01).
