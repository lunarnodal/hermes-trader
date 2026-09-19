# Causality Analysis Report — 2026-09-16

## 1. Data Scope
- Predictions: 2026-09-14 (verified) through 2026-09-16 (unverified), paper_trading.db
- Signals: 15 most recent high-confidence signals (news pipeline), 72 signal_ledger entries in 48h
- Closed trades: 3 in 7d (SPCX +5.1% WIN, AMZN -1.1% LOSS, CRM -3.4% LOSS)
- Existing causal map: 257 indirect_dependency rules; 285 active inference rules; 0 parse errors
- Qdrant: 1,613 signals (1d), 7,497 (7d) — feed volume healthy

## 2. 48h Signal → Move Cross-Reference

Verified outcomes (2026-09-14 batch, 8 predictions): 3/8 correct.
- Correct: Industrials neutral (ITA +0.18%), Consumer bearish (AIQ -0.36%), Market neutral (SPY -0.03%)
- Wrong: Energy mixed (XLE +1.24%), Tech bullish (SOXX -0.61%), Financials bearish (XLF +0.34%), Materials mixed (AIQ -0.36%)

Dominant new signal in the feed: **Brent crude at $100 as Iran war intensifies** (marketwatch-top, 0.85, bearish) — corroborated same window by **finnhub-amzn "Oil Surge and Carry Trade Weigh On Stocks"** (0.75, bearish, AAPL/FLD/USO). Two independent feeds, same day, same direction: oil spike → broad-market pressure.

## 3. Finding A — Verification ETF Mapping is Systemically Wrong (BLOCKING data-integrity bug)

Predictions are verified against the WRONG ETFs. Every "win rate" the pipeline uses for calibration, the hard-block gate, and the critic is contaminated.

Evidence source 1 (prediction-level, paper_trading.db):
| ID | Sector query | Verified via | Problem |
|----|-------------|--------------|---------|
| 635 | Healthcare & biotech | XLK | XLK = Technology ETF |
| 636 | Materials & mining | AIQ | AIQ = quality factor index, not a sector ETF |
| 638 | Consumer | AIQ | AIQ is not a consumer ETF (should be XLP/XLY) |

Evidence source 2 (full-history sector→ETF mapping, all 86+ verifications per sector):
| Sector | Wrong ETF used | Correct ETF | Correct ETF used (all-time) |
|--------|---------------|-------------|------------------------------|
| Healthcare | XLK (86x) | XLV | 0 |
| Consumer | AIQ (74x) | XLP/XLY | 0 |
| Materials | SPY (49x) + AIQ (10x) | XLB | 0 |
| Financials | XLF (86x) | XLF | ✓ correct |
| Tech | SOXX (86x) | SOXX | ✓ correct |
| Energy | XLE (85x) | XLE | ✓ correct |

Present in every month 2026-05 → 2026-09. This means:
- The "Healthcare bullish 75% (3/4)" 7-day win rate is actually **tech (XLK) performance**. The high-confidence healthcare bullish calls approved by the critic today (0.80, 0.85) are riding a phantom win rate.
- Materials "0% win rate" was measured against SPY (market) + AIQ (quality index) — not XLB.
- Consumer "25% bearish" measured against AIQ.
- sector_trim LTFT/STFT values for healthcare, consumer, and materials absorb these wrong outcomes every night (trim.py, 5 AM ET), so the calibration feedback loop has been feeding garbage into itself for 4 months.

Two independent data sources confirm (row-level notes + monthly aggregation). Not a false positive.

## 4. Finding B — New Candidate Causal Rule: Crude Surge → Broad Market Bearish

Candidate: `crude_oil_surge (brent >= $100, supply disruption)` → `broad market / macro outlook` — bearish pressure on equities within 1-3 trading days via rate/liquidity and risk-off channel.

Evidence:
1. marketwatch-top: "Brent crude reaches $100 as war in Iran intensifies" (2026-09-16, 0.85)
2. finnhub-amzn: "Oil Surge and Carry Trade Weigh On Stocks" (2026-09-16, 0.75, bearish, tickers FLD/USO/AAPL)
3. Mechanism precedent in existing rules: #97 (geopolitical events iran → energy sector), #196 (iran → global shipping/energy), #180 (iran tensions → energy+defense volatility) — the oil→energy half of the chain is already mapped; the oil→equity-market half is NOT.

Non-duplicate check: no existing indirect_dependency encodes crude price level → broad-market direction. Closest existing rules ("geopolitical events → market volatility/sentiment") are direction-agnostic and not price-level-triggered.

Status: PROVISIONAL — outcome unverified (2026-09-15/16 market predictions pending). Propose at low confidence (0.5) and let occurrence_count accumulate; do not wire into a hard gate. Note the 2026-09-14 energy "mixed" call was wrong (XLE +1.24%) — oil strength was already in motion a day before the $100 print, consistent with crude leading equity reaction.

## 5. Finding C — Signal Ledger Sector Attribution Broken

64 of 72 signal_ledger entries in the last 3 days have `sector = "unknown"`. The attribution query (queue step 2) cannot join ledger → sector while ingestion drops the sector field. This is additional evidence that the attribution pipeline (queue position 2) is blocked on a data-quality fix upstream — no new card proposed; folded into existing queue rationale.

## 6. Sectors Reviewed — No New Rules
- **Tech/AI**: bullish 25% (1/4) 7d — structural bullish bias already documented; no new causal relationship beyond existing rules.
- **Financials**: bearish 50% (2/4) — small sample, direction scattered; no new rule.
- **Healthcare**: do NOT propose momentum rules from this sector until Finding A is fixed — the win-rate signal is invalid.

## Kanban Cards to Create
1. `fix-verification-etf-mapping` — **Fix verification ETF mapping: healthcare→XLV, consumer→XLP (XLY for discretionary), materials→XLB; re-verify 86+ historical predictions after fix** (Risk: low | Sectors: pipeline, risk | Owner: coder)
   - Evidence: prediction-level row inspection (IDs 635/636/638) + full-history sector→ETF table (0 correct-ETF verifications for healthcare/consumer/materials since 2026-05)
   - Expected impact: restores validity of sector win rates, sector_trim feedback, and the <35% hard-block gate for 3 of 8 sectors
   - Risk note: after the fix, healthcare/consumer/materials win rates will change; trim values will re-converge over the next nights
2. `crude-surge-market-rule` — **Add candidate causal rule: crude oil surge (Brent ≥ $100, supply-disruption driven) → broad market bearish pressure 1-3 days (confidence 0.5, provisional)** (Risk: low | Sectors: rules | Owner: coder)
   - Evidence: two independent feeds (marketwatch-top 0.85, finnhub-amzn 0.75) same-day corroboration; mechanism half already mapped in rules #97/#180/#196
   - Do NOT gate trades on this rule until attribution confirms outcomes
