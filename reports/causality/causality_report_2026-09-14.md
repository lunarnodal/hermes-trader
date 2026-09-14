# Causality Analysis Report — 2026-09-14

## Data Sources Reviewed
- **Pipeline health**: 282 active inference rules, 254 indirect dependencies, 0 parse errors
- **Recent signals** (48h): 5 signals from finnhub (whale alerts, fund flows, fintech, aviation, media)
- **Recent predictions** (48h): 9 sector outlook queries across all sectors
- **Trade history** (30d): 6 trades — 4 wins (+349), 2 losses (-193), win rate 67%
- **Indirect dependencies**: 30 rules reviewed (IDs 225–244 recent; 1–157 legacy)

---

## 1. Recent Signal Classification

| Signal | Source | Sentiment | Confidence | Tickers | Sectors |
|--------|--------|-----------|------------|---------|---------|
| IGV/SNOW fund flow article | finnhub-crm | bearish | 0.75 | IGV, SNOW | ai_infrastructure, software, financials |
| MELI fintech LatAm article | finnhub-amzn | bullish | 0.75 | MELI | fintech, latam, financials |
| Amazon cargo jet crash | finnhub-amzn | neutral | 0.65 | AMZN, BA | aviation, transportation, retail |
| Consumer whale alerts | finnhub-amzn | neutral | 0.65 | UNIT | consumer, consumer_discretionary |
| Job search article | finnhub-general | neutral | 0.65 | — | entertainment, media |

**No high-confidence signals** (>=0.80) in the 48h window.

---

## 2. Prediction Accuracy — 7-Day Window

| Sector | Direction | Win Rate | Avg Confidence | Issue |
|--------|-----------|----------|---------------|-------|
| Energy | mixed | 0% | 38% | Hard-block candidate |
| Materials | bullish | 0% | 49% | Small sample (n=2) |
| Materials | mixed | 0% | 30% | Small sample (n=1) |
| Consumer | bearish | 33% | 55% | Low confidence, no edge |
| Healthcare | bullish | 33% | 64% | Small sample (n=3) |
| Industrials | bullish | 33% | 45% | Low confidence |
| Overall market | bearish | 33% | 45% | Low confidence |
| Technology | bullish | 33% | 60% | Low sample |
| Financial | bearish | 50% | 40% | Low confidence |
| Energy | bullish | 100% | 30% | Single occurrence |
| Financial | mixed | 0% | 30% | Single occurrence |

**Overall: 29% win rate across 24 predictions.** Below 35% hard-block threshold for bullish predictions.

---

## 3. Trade Outcomes — Cross-Reference with Signals

| Ticker | Sector | Entry | Exit | P&L% | Signal Present? |
|--------|--------|-------|------|------|-----------------|
| SPCX | technology | $140.97 | $148.17 | +5.1% | No direct signal in 48h |
| AMZN | technology | $257.81 | $255.02 | -1.1% | Amazon cargo crash signal (neutral) |
| CRM | technology | $252.86 | $244.27 | -3.4% | IGV fund flow signal (bearish on software) |
| XLV | healthcare | $167.23 | $171.40 | +2.5% | No direct signal in 48h |
| XLB | materials | $51.12 | $52.78 | +3.2% | No direct signal in 48h |
| XOM | energy | $153.70 | $164.69 | +7.2% | No direct signal in 48h |

CRM (-3.4%, stop loss) aligns with bearish IGV/SNOW software flow signal. AMZN (-1.1%) loss despite neutral aviation signal. XOM (+7.2%) and XLB (+3.2%) wins with no corresponding signals in the 48h window. Gap: energy, materials, healthcare wins were signal-capture misses.

---

## 4. Indirect Dependency Coverage Review

### High-Confidence Rules (>=0.85)
- ai compute demand surge -> NVDA AMD INTC (0.90, n=1)
- ai compute demand surge -> TSMC Samsung ASML (0.88, n=1)
- data center construction -> VRT ETN HUBB (0.88, n=1)
- hyperscaler capex increase -> ai infrastructure supply chain (0.88, n=1)
- data center power demand -> NEE CEG VST NRG (0.87, n=1)
- ai chip development -> CDNS SNPS (0.86, n=1)
- data center construction -> EQIX DLR AMT (0.85, n=1)

### Merger Arb Rule (Single Occurrence — Needs Validation)
- Rule 244 (2026-09-08): merger_arb_signal -> energy_sector (0.75, n=1). No merger arb signal present in the recent 5-signal window. Requires second occurrence to become actionable.

---

## 5. Causal Relationship Findings

### What is Working
1. AI infrastructure supply chain rules are well-covered with 7 high-confidence rules (GPU demand -> semis, data center -> power/cooling/networking).
2. Geopolitical -> energy sector (Rule 97, 0.70, n=3) has 3 occurrences and is the most-validated legacy rule.

### Gaps Identified
1. **Merger arb signals -> energy sector**: Rule 244 is single-occurrence. Needs second data point.
2. **Fund flow signals -> sector rotation**: IGV fund outflow signal (bearish, 0.75) correctly preceded CRM loss but is not a named rule.
3. **LatAm fintech signals -> sector exposure**: MELI bullish signal (0.75) has no indirect dependency rule.
4. **Mixed-direction predictions escape hard-block**: Energy mixed at 0% win rate and 38% confidence was still generated. Mixed-direction predictions below 55% confidence should face the same hard-block scrutiny as bullish predictions.

---

## Kanban Cards to Create
1. `mixed-direction-hard-block-gate` — **Add hard-block gate for mixed-direction predictions below 55% confidence** (Risk: medium | Sectors: pipeline, risk | Owner: coder)
2. `merger-arb-energy-2nd-occurrence` — **Validate merger_arb_signal -> energy_sector rule on second occurrence** (Risk: low | Sectors: rules | Owner: coder)
3. `latam-fintech-signal-rule` — **Create indirect dependency: LatAm fintech news -> MELI/LATAM fintech sector** (Risk: low | Sectors: rules | Owner: coder)
4. `fund-flow-sector-rotation-rule` — **Create indirect dependency: IGV/fund outflow signals -> software sector short-term bearish** (Risk: low | Sectors: rules | Owner: coder)
