# Causality Analysis Report — 2026-10-08

## Data read (windows, row counts, health)

- **PHASE 0 health gate: GREEN.**
  - parse_errors / traceback counts in active logs (predict, score, trim, rule_discovery, portfolio): all 0.
  - Counts: predictions total=802 (verified 30d=195), signal_ledger total=704, lessons.db indirect_dependencies=306, rules.db inference_rules=325, rule_proposals: pending=418, approved=13, promoted=1.
- **Windows read:**
  - 48h signals: 0 new cards discovered (pre-run script) — 48h signal stream quiet.
  - 48h trade history: no new closed trades (no trade activity this window).
  - Verified predictions 7d: 40 rows (ids 749–783).
  - Verified predictions 30d: 195 rows; overall 30d win-rate 0.395.
  - lessons.db indirect_dependencies: last-40 rows read (ids 268–306 + id 251 last_seen 2026-10-08).
  - sector_trim: 9 sectors read (all STFT=0.0; LTFT: industrials -0.344, materials -0.208, macro -0.193, financials -0.187, technology -0.102, consumer -0.061, energy -0.055, healthcare -0.051, defense 0.0).
  - Kanban manifest (Hermes VM /home/sam/.hermes/cron/causality/kanban_manifest.json): 39 card ids read for G3 dedup.
- **Sector derivation note:** sector derived from `predictions.query` text for this report; production attribution uses `sector_etf.py` ordered word-boundary keyword table (macro = "market"/"s&p"; financials = "banks"/"financial"/"rates"/"fed"/…). "Merger arbitrage …" rows do NOT match any sector keyword in sector_etf.py and fall through to SPY; they are grouped under macro here ONLY because the selector hard-blocks the whole "macro" bucket. All cited rows are re-verifiable by the exact queries below.

## Sector-direction accuracy table

30d (snapshot 2026-10-08; window = created_at >= now - 30 days):

| Sector | Direction | n | Win-rate | Avg conf | Brier |
|---|---|---|---|---|---|
| consumer | bullish | 6 | 0.500 | 0.683 | 0.2900 |
| consumer | bearish | 12 | 0.667 | 0.638 | 0.2648 |
| consumer | mixed | 4 | 0.000 | 0.463 | 0.2156 |
| energy | bullish | 4 | 0.500 | 0.450 | 0.3638 |
| energy | bearish | 6 | 0.500 | 0.400 | 0.2683 |
| energy | mixed | 11 | 0.000 | 0.368 | 0.1443 |
| energy | neutral | 1 | 0.000 | 0.300 | 0.0900 |
| financials | bearish | 21 | 0.476 | 0.591 | 0.3066 |
| financials | mixed | 1 | 0.000 | 0.300 | 0.0900 |
| healthcare | bullish | 21 | 0.476 | 0.693 | 0.3228 |
| healthcare | mixed | 1 | 0.000 | 0.650 | 0.4225 |
| industrials | bullish | 16 | 0.500 | 0.491 | 0.2098 |
| industrials | mixed | 5 | 0.000 | 0.320 | 0.1040 |
| industrials | neutral | 1 | 0.000 | 0.300 | 0.0900 |
| macro | neutral | 22 | 0.545 | 0.505 | 0.2532 |
| macro | bearish | 14 | 0.286 | 0.475 | 0.2282 |
| macro | mixed | 5 | 0.000 | 0.370 | 0.1425 |
| materials | bullish | 16 | 0.250 | 0.501 | 0.2350 |
| materials | mixed | 3 | 0.000 | 0.300 | 0.0900 |
| materials | neutral | 3 | 1.000 | 0.333 | 0.4450 |
| technology | bullish | 22 | 0.455 | 0.647 | 0.2751 |

7d (snapshot 2026-10-08; window = created_at >= now - 7 days; 40 rows):

| Sector | Direction | n | Win-rate | Avg conf | Brier |
|---|---|---|---|---|---|
| consumer | bullish | 4 | 0.500 | 0.738 | 0.3194 |
| energy | bearish | 2 | 0.500 | 0.325 | 0.2563 |
| energy | mixed | 1 | 0.000 | 0.300 | 0.0900 |
| energy | neutral | 1 | 0.000 | 0.300 | 0.0900 |
| financials | bearish | 4 | 0.250 | 0.637 | 0.3106 |
| healthcare | bullish | 4 | 0.500 | 0.712 | 0.3081 |
| industrials | bullish | 3 | 0.667 | 0.600 | 0.1667 |
| industrials | mixed | 1 | 0.000 | 0.300 | 0.0900 |
| macro | mixed | 3 | 0.000 | 0.350 | 0.1242 |
| macro | neutral | 4 | 0.500 | 0.425 | 0.1888 |
| materials | bullish | 4 | 0.500 | 0.575 | 0.2825 |
| technology | bullish | 4 | 0.500 | 0.637 | 0.2581 |

Direction-level 30d (all sectors): bullish n=85 wr=0.435; bearish n=53 wr=0.472; neutral n=27 wr=0.556; mixed n=30 wr=0.000.

## Findings (each with evidence rows and table+id citations)

All findings below are OBSERVATIONS of already-handled behavior — none produces a new card (see Kanban Cards section and kill list).

### F1 — "mixed" direction: 0/30 verified over 30d, never traded
- Query: `SELECT id, created_at, confidence, was_correct, actual_direction, critic_verdict FROM predictions WHERE was_correct IS NOT NULL AND created_at >= datetime('now','-30 days') AND direction='mixed' ORDER BY created_at DESC;` → 30 rows, every row was_correct=0 (ids e.g. 783, 781, 765, 758, 756, 740, 729, 728, 722, 720, 719, 713, 710, 709, 704, 700, 695, 692, 680, 663, 650, 641, 636, 632, 628, 619, 609, 599, 597, 589 — paper_trading.db predictions table).
- Second source: signal_ledger (paper_trading.db) gate reasons include `direction=mixed conf=0.30 < 0.70` (32 rows) and `direction=mixed conf=0.45 < 0.70` (20 rows) over 30d — mixed predictions are being rejected at the direction gate, consistent with 0 realized trades.
- Code confirmation: pipeline/portfolio/selector.py ~L611 `if direction != "bullish": ... continue` — non-bullish (incl. mixed) never reaches recommendations.
- **Disposition: already covered** by manifest card `mixed-direction-hard-block-gate` (t_3c2ddf10, 2026-09-18) and the live non-bullish skip gate. No new card.

### F2 — Critic verdict win rates are non-monotonic: reject (0.429) > approve (0.400) > challenge (0.358), 30d
- Query: `SELECT critic_verdict, COUNT(*), ROUND(AVG(was_correct),3) FROM predictions WHERE was_correct IS NOT NULL AND created_at >= datetime('now','-30 days') GROUP BY critic_verdict;` → approve 65/0.400, challenge 67/0.358, reject 63/0.429.
- Second source: 7d sample (ids 749–783) shows the same ordering (e.g. reject rows 782/780/779/773/772/771/770/767/755/753/752/750 include correct calls 779, 773, 772, 771, 770, 767, 755, 753, 750; approve rows include misses 777, 769, 751).
- Gate context: selector.py ~L637–644 blocks verdict="reject" from recommendations; 30d ledger shows `critic verdict=reject` rejections = 65 rows.
- **Disposition: already covered** by manifest card `critic-verdict-quality-measurement` (filing of this exact anomaly, pending). No new card.

### F3 — Low-confidence predictions (conf < 0.4) verified at 21.6% (n=37, 30d) vs high-confidence (conf >= 0.65) at 46.3% (n=54)
- Query: `SELECT COUNT(*), ROUND(AVG(was_correct),3) FROM predictions WHERE was_correct IS NOT NULL AND created_at >= datetime('now','-30 days') AND confidence<0.4;` → 37 / 0.216. High-conf: 54 / 0.463.
- Second source: 7d rows 776 (bearish conf=0.3, wrong), 774 (neutral 0.3, wrong), 781/783 (mixed 0.3, wrong) — low-conf calls wrong at the margin.
- **Disposition: confidence thresholds already gate trading** (bullish < 0.50 theta floor; < 0.70 equity threshold — ledger rejections `conf=0.45 < 0.50 (theta floor)` 33 rows, `conf=0.40 < 0.50` 24 rows). The low win-rate is a calibration observation, not a leakage. A discount card would need to lower a gate threshold — prohibited by remediation rule R1 ("No gate changes … if a fix appears to require touching a gate, stop and escalate to Sam"). No card.

### F4 — Macro hard-block is live and consistent with data
- macro/bearish 30d: 4/14 = 0.286 win-rate (ids e.g. 756, 749, 740, 722, 700, 695 …); macro/neutral 22 rows 0.545.
- Code confirmation: selector.py ~L660 hard-blocks sector=="macro" citing "0% win rate bearish, 27% bullish all-time".
- **Disposition: covered** — the hard-block gate is live; 30d data confirms the block remains justified. No card.

### F5 — Financials-bearish overconfidence persists but is already discounted
- financials/bearish 30d: 21 rows, wr=0.476, avg conf=0.591, Brier=0.3066; 7d: 4 rows, wr=0.250, avg conf=0.637.
- Calibration gate check (G4): `SELECT sector, ROUND(ltft,3), ROUND(stft,3) FROM sector_trim WHERE sector='financials';` → financials LTFT = -0.187 <= -0.10.
- **Disposition: KILLED by G4** — nightly trim already double-covers; a new card would double-discount. Also already filed as manifest card `financials-bearish-yield-spike-overconfidence`. No card.

### F6 — Neutral predictions verify at 55.6% (n=27, 30d) — a "correct" direction that is never tradable
- neutral 30d: 27 rows, wr=0.556 (ids e.g. 775, 774, 766, 757, 749, …).
- **Disposition: already covered** by manifest card `neutral-direction-prediction-verification` (verification-semantics of the neutral class). No new card.

## Kanban Cards to Create

No new cards. All six candidate findings from this window (F1–F6) fail the gates for documented reasons:

- **F1 mixed 0/30** — G3 duplicate: manifest card `mixed-direction-hard-block-gate` (t_3c2ddf10); additionally already enforced by selector.py non-bullish skip.
- **F2 critic non-monotonicity** — G3 duplicate: manifest card `critic-verdict-quality-measurement`; the reject-veto gate is live (selector.py ~L637).
- **F3 low-confidence decay** — R1 gate-change prohibition: any discount would change gate behavior; observed pattern is a calibration observation only.
- **F4 macro** — already hard-blocked in selector.py; 30d data confirms the block is justified, no new relationship to capture.
- **F5 financials-bearish overconfidence** — G4 calibration gate: sector_trim LTFT = -0.187 (<= -0.10); nightly trim handles it; also filed in manifest (`financials-bearish-yield-spike-overconfidence`).
- **F6 neutral verification semantics** — G3 duplicate: manifest card `neutral-direction-prediction-verification`.

## Data vs code

No changes proposed this run — no data writes, no code changes. All findings are read-only observations corroborating existing gates/cards.
