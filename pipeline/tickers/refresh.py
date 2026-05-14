#!/usr/bin/env python3
"""Refresh NASDAQ ticker list weekly"""
import requests, sqlite3
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path('/home/trading/trading-ai/data/tickers.db')

resp = requests.get(
    "https://api.nasdaq.com/api/screener/stocks",
    params={"tableonly": "true", "limit": 25, "offset": 0, "download": "true"},
    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    timeout=20
)
rows = resp.json().get("data", {}).get("rows", [])
conn = sqlite3.connect(DB_PATH)
now  = datetime.now(timezone.utc).isoformat()
added = 0
for row in rows:
    ticker  = row.get("symbol", "").strip()
    company = row.get("name", "").strip()
    sector  = row.get("sector", "").strip().lower().replace(" ", "_")
    if not ticker or not company or len(ticker) > 6:
        continue
    conn.execute("""
        INSERT OR IGNORE INTO tickers
        (ticker, company_name, exchange, sector, source, created_at)
        VALUES (?, ?, 'NASDAQ', ?, 'nasdaq_api', ?)
    """, (ticker, company, sector, now))
    added += 1
conn.commit()
conn.close()
print(f"Ticker DB refreshed: {added} symbols processed")
