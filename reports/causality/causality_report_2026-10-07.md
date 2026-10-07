# Causality Analysis Report — 2026-10-07

## Data read (windows, row counts, health)
- Pipeline health: GREEN. inference_rules active=325, indirect_dependencies=306, qdrant_signals 1d=1345 / 7d=6126 / 30d=27730, parse_errors total=0, this_month=0.
- 7d verified predictions (paper_trading.db): 35 rows, ids 740–774 (2026-09-30 → 2026-10-05).
- 30d verified predictions: 186 rows (grouped table below).
- get_recent_signals (48h): 5 signals (ALDX FDA review; Sweden CB inflation warning; German unemployment +12k; German SEFE gas storage; US 10y yield 2-yr monthly jump). No novel cross-sector pattern vs existing rules.
- get_trade_history (30d): 6 trades, 3W/3L, P&L -$46.94. Latest: TSM ai_infrastructure WIN +10.3% (exited 10-06), XLV healthcare WIN +2.2% (exited 09-30).
- Existing rules read: lessons.db indirect_dependencies ids 1–251 (30 returned, top ids 220–251), incl. 266/267 verified by direct SELECT this session.

## Sector-direction accuracy table (7d + 30d: n, win-rate, avg conf, Brier)
Snapshot: 2026-10-07 09:05 UTC (airig, paper_trading.db predictions, was_correct IS NOT NULL).

| sector | direction | 7d (n/WR/conf/Brier) | 30d (n/WR/conf/Brier) |
|---|---|---|---|
| consumer | bearish | 1/100%/0.550/0.202 | 12/66.7%/0.638/0.265 |
| consumer | bullish | 3/66.7%/0.733/0.238 | 5/60%/0.670/0.236 |
| consumer | mixed | – | 4/0%/0.463/0.216 |
| energy | bearish | 1/100%/0.350/0.423 | 5/60%/0.420/0.304 |
| energy | bullish | – | 4/50%/0.450/0.364 |
| energy | mixed | 2/0%/0.300/0.090 | 11/0%/0.368/0.144 |
| energy | neutral | 1/0%/0.300/0.090 | 1/0%/0.300/0.090 |
| financials | bearish | 4/25%/0.662/0.298 | 20/45%/0.586/0.317 |
| financials | mixed | – | 1/0%/0.300/0.090 |
| healthcare | bullish | 4/25%/0.675/0.361 | 20/45%/0.693/0.334 |
| healthcare | mixed | – | 1/0%/0.650/0.423 |
| industrials | bullish | 4/50%/0.563/0.176 | 16/50%/0.491/0.210 |
| industrials | mixed | – | 4/0%/0.325/0.108 |
| industrials | neutral | – | 1/0%/0.300/0.090 |
| materials | bullish | 4/50%/0.525/0.233 | 15/26.7%/0.494/0.227 |
| materials | mixed | – | 3/0%/0.300/0.090 |
| materials | neutral | – | 3/100%/0.333/0.445 |
| merger_arb | neutral | 3/66.7%/0.517/0.269 | 18/61.1%/0.539/0.259 |
| overall_market | bearish | 1/100%/0.570/0.185 | 14/28.6%/0.475/0.228 |
| overall_market | mixed | 2/0%/0.375/0.141 | 4/0%/0.388/0.156 |
| overall_market | neutral | 1/0%/0.300/0.090 | 3/33.3%/0.333/0.247 |
| technology | bullish | 4/75%/0.637/0.208 | 21/47.6%/0.649/0.271 |
| mixed (all sectors) | – | 6/0%/– | 28/0%/0.379/– |

Critic verdicts (30d): approve 62/25% / challenge 65/37% / reject 59/44%. 7d: approve 12/50% / challenge 9/33% / reject 14/50%.

## Findings
- F1 (healthcare-bullish worst major bullish bucket: 30d 20n 45% WR, avg conf 0.693, Brier 0.334; 7d 4n 25% WR @ conf 0.675, ids 734/743/752/761) — already filed as pending card `healthcare-bullish-overconfidence-discount` (manifest, created 2026-10-06, gated at approval). G3 duplication kill.
- F2 (mixed-direction 0/28 over 30d, max conf 0.65, avg 0.379) — covered by existing pending card `mixed-direction-hard-block-gate` (t_3c2ddf10) and the system's documented Gate 4 (mixed hard-blocked). G3 kill.
- F3 (critic verdict not discriminating: reject 44% > approve 40% > challenge 37% over 30d) — already filed as pending card `critic-verdict-quality-measurement`. G3 kill.
- F4 (consumer-bearish best bucket 30d 66.7% WR; merger_arb-neutral 61.1%) — consistent with existing indirect_dependencies rules (218/220 leadership events; 244 merger_arb_signal→energy_sector) and manifest cards `consumer-staples-defensive-rotation`, `bearish-macro-energy-rotation`. No new relationship; no action.
- F5 (materials-bullish 30d 26.7% WR) — G4 kill: sector_trim materials LTFT=-0.208 (SELECTed this session) ≤ -0.10; nightly trim.py already discounts materials. A card would double-discount.
- F6 (financials-bearish 7d 25% WR @ conf 0.662; 30d 45% @ 0.586) — G3 kill: pending card `financials-bearish-yield-spike-overconfidence` covers exactly this (yield-spike cluster driving overconfident financials-bearish calls). 7d n=4 is below the ≥2-independent-sources threshold for a new claim anyway.
- F7 (overall_market-bearish 30d 28.6% WR; materials-neutral 100% n=3) — 30d macro bearish underperformance is the documented subject of rules 266/267 territory and `low-vol-regime-direction-decay` / `fed-hike-fear-priced-in-rule` manifest cards; macro LTFT=-0.190 already active. No new relationship.

## Kanban Cards to Create
No new cards (reason: all candidate patterns this session map to one of (a) pending manifest cards in 14-day refire window — healthcare-bullish discount, mixed hard-block, critic-quality, financials-bearish yield-spike — (b) active sector_trim discounts with LTFT ≤ -0.10 (materials -0.208, macro -0.190, financials -0.187, technology -0.102) per gate t_b14dc8da, or (c) existing indirect_dependencies rules. 0 candidates survive G1–G5.)
