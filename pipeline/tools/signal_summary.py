#!/usr/bin/env python3
"""
Signal summary tool for Hermes
Generates structured market sentiment overview from scored signals
"""
import json
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

SIGNALS_DIR   = Path("/mnt/qnap/timeseries/signals")
LOOKBACK_HOURS = 48

signals = []
for f in sorted(SIGNALS_DIR.glob("scored_*.jsonl"), reverse=True)[:20]:
    with open(f) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    signals.append(json.loads(line))
                except:
                    continue

total = len(signals)
if total == 0:
    print("No signals found")
    exit()

sentiment_counts = Counter(s["sentiment"] for s in signals)
sector_sentiment = defaultdict(list)
ticker_sentiment = defaultdict(list)
source_counts    = Counter(s["source"] for s in signals)

for s in signals:
    for sector in s.get("sectors", []):
        sector_sentiment[sector].append(s["sentiment"])
    for ticker in s.get("tickers", []):
        ticker_sentiment[ticker].append(s["sentiment"])

avg_conf = sum(s["confidence"] for s in signals) / total

print(f"{'='*60}")
print(f"  MARKET SIGNAL SUMMARY — last {LOOKBACK_HOURS}h ({total} signals)")
print(f"{'='*60}\n")

print("OVERALL SENTIMENT:")
for sent, count in sentiment_counts.most_common():
    bar = "█" * int(count / total * 30)
    print(f"  {sent:8s} {count:3d} ({count/total*100:.1f}%) {bar}")
print(f"\n  Avg confidence: {avg_conf:.2f}\n")

print("SOURCES:")
for source, count in source_counts.most_common():
    print(f"  {source:30s}: {count} signals")

print("\nTOP SECTORS:")
for sector, sentiments in sorted(sector_sentiment.items(),
                                  key=lambda x: len(x[1]), reverse=True)[:10]:
    bull = sentiments.count("bullish")
    bear = sentiments.count("bearish")
    neut = sentiments.count("neutral")
    bias = "↑" if bull > bear else "↓" if bear > bull else "→"
    print(f"  {bias} {sector:28s}: {len(sentiments):2d} signals  "
          f"bull={bull} bear={bear} neut={neut}")

print("\nMOST MENTIONED TICKERS:")
for ticker, sentiments in sorted(ticker_sentiment.items(),
                                  key=lambda x: len(x[1]), reverse=True)[:15]:
    bull = sentiments.count("bullish")
    bear = sentiments.count("bearish")
    bias = "↑" if bull > bear else "↓" if bear > bull else "→"
    print(f"  {bias} {ticker:6s}: {len(sentiments)} signals  bull={bull} bear={bear}")

print(f"\n{'='*60}")
