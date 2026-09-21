# Causality Analysis Report — 2026-09-21

## Window & Data Sources

- Verified predictions: 35 (2026-09-14 → 2026-09-17 batches), overall win rate 46%
- Today's unverified predictions: 8 (2026-09-21 batch, outcomes pending)
- Signals (last 48h, high-confidence): 25 via get_recent_signals; signal_ledger 3d: 18 rows, all event_type="other", was_correct all NULL (attribution gap — known, carded t_7d2aba5a)
- Closed trades (7d): 1 — SPCX +5.1% WIN (time_exit)
- Existing rules cross-referenced: indirect_dependencies (latest 20 + targeted queries on fed/defense/healthcare/industrials/financials), predict.log critic verdicts

## Sector × Direction Outcomes (8-day verified)

| Sector | Dir | W/L | Avg conf | Critic avg |
|---|---|---|---|---|
| Industrials | bullish | 3/3 (100%) | 48% | 38% |
| Technology | bullish | 3/4 (75%) | 60% | 50% |
| Financials | bearish | 2/4 (50%) | 48% | 48% |
| Consumer | bearish | 1/4 (25%) | 68% | 48% |
| Healthcare | bullish | 1/4 (25%) | 76% | 76% |
| Energy | mixed | 0/3 (0%) | 50% | 50% |
| Materials | mixed | 0/2 (0%) | 30% | 30% |
| Market Overview | bearish | 0/2 (0%) | 45% | 35% |
| Market Overview | neutral | 2/2 (100%) | 35% | 35% |
| Merger arb | neutral | 2/3 (67%) | 53% | 53% |

## Candidate Causal Relationships (evidence-verified, two sources each)

### 1. Defense-budget catalyst cluster → industrials bullish (STRONG, new)

**Signal types:** XLI multi-day leadership, $1.5T defense budget headline, India defense tailwind, defense conference-driven capex narratives.

**Evidence:**
- Source 1 — predictions table: Industrials bullish 3/3 correct (09-14 neutral-correct baseline, 09-15 conf 0.45 ✓, 09-16 conf 0.60 ✓, 09-17 conf 0.40 ✓), each reasoning_summary citing the defense cluster.
- Source 2 — predict.log critic verdicts: all three received challenge or reject (critic avg confidence 38%, lowest gap of any sector — critic is systematically under-rating the strongest sector this week).
- Cross-reference: lessons.db has defense→financials (id 32), geopolitics→defense (92, 138), Fed/bonds/tech→industrials (91, 108–110, 136–137, 172, 179, 187) but **no rule from defense-spending catalysts → industrials sector**. Gap confirmed.
- Note: reverses the Sep 2 snapshot (Industrials bullish 0/3 then) — regime flip worth capturing as a rule with 7-day recency weighting.

Today's industrials bullish 0.45 again drew critic challenge→40% (09-21 08:42). If it verifies correct this is a 4th occurrence.

### 2. FDA-approval / clinical-trial catalyst cluster → healthcare bullish is inverted (STRONG, new)

**Signal types:** "FDA approval", "Phase III trial success", "pipeline progress", "analyst upgrades" clusters in healthcare reasoning.

**Evidence:**
- Source 1 — predictions table: Healthcare bullish 1/4 correct at the **highest avg confidence of any bucket (0.76)**; the single hit was the 0.85-confidence one (09-15 ✓); 0.80 (09-16) and 0.70 (09-17) both verified **neutral**, 0.70 (09-14) verified **bearish**.
- Source 2 — critic + signal_ledger: critic APPROVED all three 09-15→09-17 runs (0 issues) — critic provides no protective value in healthcare — while signal_ledger shows healthcare bullish runs repeatedly rejected on meta_correction (raw 70% → adj 66-67% < 0.70 threshold, 09-14 and 09-17). High-confidence healthcare bullish is being produced fastest in exactly the bucket where it loses.
- Cross-reference: existing healthcare-relevant rules only cover macro/liquidity→healthcare, crypto divergence, tech-rotation suppression. **No rule encodes that FDA/clinical catalyst clusters resolve neutral, not bullish, within 24h.** Gap confirmed.
- Matches the known structural pattern (bullish calibration: bullish -15% penalty exists, but this bucket's raw conf is the highest in the system — the penalty is not enough).

### 3. Fed rate-hike FEAR cluster → broad market does NOT go bearish (PROVISIONAL, 0.5 confidence)

**Signal types:** "imminent Fed rate hike", "strategist warning of margin compression", "25bp hike + deteriorating breadth" clusters in Overall-market and Financial bearish reasoning.

**Evidence:**
- Source 1 — predictions table: Market-overview bearish 0/2 (09-15 actual bullish, 09-17 actual neutral); market-overview neutral 2/2. Both failed bearish calls were Fed-fear-driven. Financial bearish Fed-driven calls only 2/4.
- Source 2 — get_recent_signals today: Bloomberg "Wall Street Strategists See Stock Rally Surviving Fed Rate Hike" bullish conf 0.85 (highest-confidence signal in the feed) — market consensus is pricing the hike as absorbable, contradicting the model's fear-driven bearish generation.
- Cross-reference: existing rules (102, 111, 113, 123, 166, 167, 208) all encode Fed hawkish → **tech/sectors negative**; none encode the broad-market "fear already priced-in → neutral" behavior, which is what actually verified. Provisional — needs one more occurrence window to promote, same pattern as crude-surge card t_3fbca761.

## Candidates REJECTED (no card)

- **Bearish macro → consumer defensive** — already carded (consumer-staples-defensive-rotation, t_18d521e3); this week's consumer bearish 1/4 is consistent with the pending rule, do not duplicate.
- **Crude ≥$100 → market/energy stress** — already carded (crude-surge-market-rule, t_3fbca761); 09-17 energy bearish ✓ is a data point for that pending card, not a new card.
- **Tech bullish gate-5 non-trigger** — already carded (t_3f8bb465). Today's tech bullish 0.75 approved by critic feeds that card's evidence, not a new one.
- **AI-lab-pause narrative → semis bearish** — signal arrived today (Marketwatch, conf 0.75), zero verified outcomes yet. Watch next run.
- **Merger-arb neutral 2/3** — too few samples, existing Form-8.3 logic already handles it.
- signal_ledger event_type still 100% "other" — attribution blocker, already carded (t_7d2aba5a, t_bb850e04).

## Pipeline Health Notes

- signal_ledger.was_correct: still entirely NULL — attribution chain (t_bb850e04 / t_7d2aba5a) remains the top unblock.
- Critic calibration inverted this week: industrials critic conf 38% vs 100% realized (over-challenging the winner), healthcare critic conf 76% vs 25% realized (rubber-stamping the loser). Worth flagging to whichever card owns critic-weight calibration.

## Kanban Cards to Create

1. `defense-budget-industrials-bullish-rule` — **Add indirect dependency rule: defense-spending catalyst cluster (defense budget headlines, XLI multi-day leadership, defense-conference capex narratives) → industrials bullish within 24-48h** (Risk: low | Sectors: rules, pipeline | Owner: coder)
2. `fda-catalyst-healthcare-discount` — **Discount FDA-approval / clinical-trial catalyst clusters as healthcare bullish drivers: bucket verified 1/4 at avg conf 0.76 with critic approving every wrong call; treat catalyst-cluster healthcare bullish as neutral-biased and tighten critic approval for it** (Risk: medium | Sectors: rules, calibration | Owner: coder)
3. `fed-hike-fear-priced-in-rule` — **Provisional rule (conf 0.5): Fed rate-hike fear cluster in market-overview signals resolves neutral-to-bullish, not bearish (0/2 bearish verified; both failed calls Fed-fear-driven; today's top signal conf 0.85 says rally survives hike) — promote after one more occurrence** (Risk: low | Sectors: rules | Owner: coder)
