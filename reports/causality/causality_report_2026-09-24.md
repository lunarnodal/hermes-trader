# Causality Analysis Report — 2026-09-24

## 48-Hour Overview

- 27 predictions created in the last 2 days; **8 verified** (all from the 2026-09-22 run; 09-23 and 09-24 batches still pending verification). 48h win rate: **2/8 (25%)**.
- Pipeline health: 300 active inference rules, 282 indirect dependencies, 1,450 Qdrant signals in last 24h, **0 parse errors** this month.
- Trade history (7d): **0 closed trades** — the portfolio sat in cash this week, so all causal findings are at prediction level, not executed-trade level.
- Critic split on the 8 verified calls: approve 0/4 correct, challenge 1/1 correct, reject 1/3 correct. The day's only market-level win (bearish, conf 0.5) was in the critic-**reject** bucket.

## Verified Predictions (2026-09-22)

| Sector | Direction | Conf | Critic | Result | Actual |
|---|---|---|---|---|---|
| Market | bearish | 0.50 | reject | **correct** | SPY −0.34% |
| Consumer | mixed | 0.40 | approve | wrong | XLY −1.21% (actual bearish) |
| Industrials | bullish | 0.50 | challenge | **correct** | XLI +0.45% |
| Materials | bullish | 0.45 | reject | wrong | XLB +0.01% (neutral) |
| Healthcare | bullish | 0.75 | approve | wrong | XLV −0.50% (bearish) |
| Financials | bearish | 0.65 | approve | wrong | XLF +0.38% (bullish) |
| Technology | bullish | 0.70 | approve | wrong | SOXX −0.33% (bearish) |
| Energy | bearish | 0.55 | reject | wrong | XLE +0.43% (bullish) |

## Cross-Reference Against Existing Rules

1. **Rule 267 (Fed rate-hike fear cluster → neutral-to-bullish, PROVISIONAL conf 0.5, "promote after one more occurrence")** — the 09-22 market-bearish call was explicitly Fed-hike-cluster-driven (reasoning: "unanimous Fed rate hike, hawkish forward guidance, rising yields"; 27 signals) and verified **correct**. This is a **counter-example**: the rule's 0/2 bearish premise is now 1/3. Over the last 7 sessions, market bearish calls are 1/7 with 09-22 the sole win. The pending card `fed-hike-fear-priced-in-rule` (t_52a66e7b) must NOT be promoted as written. See new card below.
2. **Rule 266 / card fda-catalyst-healthcare-discount (t_25e4e1cf)** — 09-22 healthcare bullish at conf 0.75 with critic approve, wrong (XLV −0.50%). This is the 4th wrong high-conf (≥0.7) healthcare bullish call in 8 sessions (09-16, 09-17, 09-18, 09-22 all wrong; all critic-approved). Healthcare bullish 30d: 22 verified at 35% win rate. Strongly reinforces existing rule — no new card (duplicate).
3. **Rule 265 / card defense-budget-industrials-bullish-rule (t_e34dfc83)** — 09-22 industrials bullish (defense-budget catalyst in reasoning) verified correct (XLI +0.45%). Second positive occurrence; reinforces rule. No new card.
4. **Rule 263 / card bearish-macro-energy-rotation (t_a3acb7c4)** — 09-22: market outlook resolved bearish while energy resolved bullish (XLE +0.43%) against the day's energy-bearish call. Consistent with defensive-rotation-into-energy. Reinforces rule. No new card.
5. **Card critic-verdict-quality-measurement (t_1854b679)** — post-re-verification 30d critic hit rates: approve 32.4% (71), challenge 30.3% (76), reject 46.7% (30). Discrimination still <55%; the 48h window is extreme (approve 0/4) and the critic rejected the day's only correct market call. Reinforces the existing card's premise — no new card (duplicate).
6. **Card low-vol-regime-direction-decay (t_7f098efa)** — 09-22 moves again clustered ≤0.5% (6 of 8 ETFs in a 0.01–0.50% band) and 6/8 directional calls scored wrong. Second consecutive low-vol day matching the 09-18 pattern. Reinforces existing card — no new card (duplicate).
7. **Card technology-bullish-gate-5-violation (t_3f8bb465)** — tech bullish 30d: 23 verified at 42.9%; all five conf ≥0.65 calls in the last 14d were critic-approved, 2 wrong on low-move days. Still sub-55% — Gate 5 question remains open. Covered by existing card.
8. **Financials bearish** — 30d: 8 correct / 8 wrong (50%) after the 09-23 re-verification. No directional pattern; 09-22 bearish call (conf 0.65, approve) was wrong. No new rule — no reliable signal.
9. **Merger arb / rule 244** — 09-22/23/24 all neutral calls, unverified; no new occurrence for the pending `merger-arb-energy-2nd-occurrence` card (t_bff9f66d).

## New Finding

**Fed rate-hike fear rule (267) first counter-example.** The 09-22 market bearish prediction (conf 0.5, 27 signals, reasoning explicitly anchored on a "unanimous Fed rate hike, hawkish forward guidance, rising yields" cluster) was verified correct (SPY 773.35 → 770.74, −0.34%). Rule 267 was written 09-21 on a 0/2 bearish basis with the instruction "promote after one more occurrence" — and the next occurrence resolved bearish. This is the first time in the 7-day window that a Fed-fear-driven bearish market call won (market bearish 1/7 overall). The provisional rule is now 1/3 and the pending promotion card would be acting on stale evidence.

## Kanban Cards to Create
1. `fed-hike-fear-rule-267-counterexample` — **Reconcile provisional rule 267 (Fed rate-hike fear → neutral-to-bullish): 09-22 market bearish call verified correct — rule premise now 1/3, do not promote as written** (Risk: low | Sectors: rules, calibration | Owner: coder)
   - Evidence: prediction 2026-09-22T12:12:42 (bearish, conf 0.5, critic reject, was_correct=1, SPY 773.35→770.74 -0.34%) with hike-cluster reasoning; lessons.db rule 267 still reads "0/2 bearish verified... promote after one more occurrence"; market bearish 1/7 over last 7 sessions with 09-22 the sole win
   - Expected impact: prevents promotion of a contradicted provisional rule; updates occurrences/confidence on rule 267 and gates t_52a66e7b (fed-hike-fear-priced-in-rule) pending two more same-direction occurrences before any promotion

