#!/usr/bin/env python3
"""
Prediction verification script
Fetches actual price data after prediction timeframe expires
Marks predictions as correct/wrong and updates rule performance
Runs every 6 hours via cron
"""

import json
import logging
import os
import requests
import sqlite3
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from paper_trading.db import init_db, verify_prediction, snapshot_portfolio

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/mnt/qnap/timeseries/logs/verify.log"),
        logging.StreamHandler()
    ]
)

# Yahoo Finance unofficial API — free, no key needed
YF_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"

TIMEFRAME_HOURS = {
    "24h": 24,
    "48h": 48,
    "1w":  168
}

# Sector → representative ETF mapping for verification
SECTOR_ETFS = {
    "energy":          "XLE",
    "oil_gas":         "XOP",
    "technology":      "XLK",
    "ai_infrastructure": "AIQ",
    "semiconductors":  "SOXX",
    "financials":      "XLF",
    "healthcare":      "XLV",
    "defense":         "ITA",
    "utilities":       "XLU",
    "real_estate":     "VNQ",
    "consumer_staples": "XLP",
    "manufacturing":   "XLI",
    "agriculture":     "MOO",
    "commodities":     "DJP",
}

# Query keywords → sector ETF mapping
QUERY_SECTOR_MAP = {
    "energy":        "XLE",
    "oil":           "XLE",
    "semiconductor": "SOXX",
    "ai":            "AIQ",
    "tech":          "XLK",
    "financial":     "XLF",
    "healthcare":    "XLV",
    "defense":       "ITA",
    "utility":       "XLU",
    "agriculture":   "MOO",
}


def get_sector_etf(query: str) -> str:
    """Determine best ETF to use for verification based on query"""
    query_lower = query.lower()
    for keyword, etf in QUERY_SECTOR_MAP.items():
        if keyword in query_lower:
            return etf
    return "SPY"  # Default to S&P 500


def fetch_price_change(ticker: str, hours_back: int) -> dict | None:
    """Fetch price change over a time period using Yahoo Finance"""
    try:
        # Use 1-day interval for 24-48h, 1-day for 1w
        interval = "1h" if hours_back <= 48 else "1d"
        period   = "2d" if hours_back <= 48 else "5d"

        url  = f"{YF_BASE}/{ticker}"
        resp = requests.get(url, params={
            "interval": interval,
            "range":    period,
            "includePrePost": False
        }, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        resp.raise_for_status()

        data   = resp.json()
        result = data["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        times  = result["timestamp"]

        # Filter out None values
        valid = [(t, c) for t, c in zip(times, closes) if c is not None]
        if len(valid) < 2:
            return None

        # Get price at start of window and most recent
        now_ts    = datetime.now(timezone.utc).timestamp()
        start_ts  = now_ts - (hours_back * 3600)

        # Find closest prices to start and end of window
        start_prices = [(t, c) for t, c in valid if t >= start_ts]
        if not start_prices:
            start_prices = valid[-2:]

        open_price  = start_prices[0][1]
        close_price = valid[-1][1]
        pct_change  = (close_price - open_price) / open_price * 100

        # Tiered thresholds based on timeframe
        if hours_back <= 24:
            bull_threshold, bear_threshold = 0.2, -0.2   # Tighter for 24h
        elif hours_back <= 48:
            bull_threshold, bear_threshold = 0.3, -0.3   # Medium for 48h
        else:
            bull_threshold, bear_threshold = 0.5, -0.5   # Wider for 1w

        return {
            "ticker":       ticker,
            "open_price":   round(open_price, 2),
            "close_price":  round(close_price, 2),
            "pct_change":   round(pct_change, 2),
            "direction":    "bullish" if pct_change > bull_threshold else
                           "bearish" if pct_change < bear_threshold else "neutral",
            "hours":        hours_back,
            "threshold_used": bull_threshold
        }

    except Exception as e:
        log.warning(f"Could not fetch price for {ticker}: {e}")
        return None


def get_expired_unverified(conn: sqlite3.Connection) -> list[dict]:
    """Get predictions that have passed their timeframe but aren't verified"""
    rows = conn.execute("""
        SELECT id, created_at, query, timeframe, direction, confidence
        FROM predictions
        WHERE verified_at IS NULL
        ORDER BY created_at ASC
    """).fetchall()

    expired = []
    now     = datetime.now(timezone.utc)

    for row in rows:
        pred_id, created_at, query, timeframe, direction, confidence = row
        created = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        hours   = TIMEFRAME_HOURS.get(timeframe, 24)
        expires = created + timedelta(hours=hours)

        if now >= expires:
            # Verify if market has opened at least once since prediction expired
            # Server is now America/New_York so datetime.now() is ET directly
            now_local = datetime.now()
            is_weekday = now_local.weekday() < 5
            market_open_today = now_local.replace(hour=9, minute=30, second=0, microsecond=0)
            market_has_opened = now_local >= market_open_today

            if not is_weekday or not market_has_opened:
                log.info(f"Prediction #{pred_id} expired but market not yet open — "
                         f"deferring to next market session")
                continue

            expired.append({
                "id":         pred_id,
                "created_at": created_at,
                "query":      query,
                "timeframe":  timeframe,
                "direction":  direction,
                "confidence": confidence,
                "expires":    expires.isoformat()
            })

    return expired


def verify_expired_predictions(conn: sqlite3.Connection) -> int:
    """Verify all expired predictions against actual price data"""
    expired = get_expired_unverified(conn)

    if not expired:
        log.info("No expired predictions to verify")
        return 0

    log.info(f"Found {len(expired)} expired predictions to verify")
    verified_count = 0

    for pred in expired:
        pred_id   = pred["id"]
        query     = pred["query"]
        timeframe = pred["timeframe"]
        hours     = TIMEFRAME_HOURS.get(timeframe, 24)

        # Determine which ETF to check
        etf = get_sector_etf(query)
        log.info(f"Verifying prediction #{pred_id} using {etf} "
                 f"({timeframe} window)")

        price_data = fetch_price_change(etf, hours)
        if not price_data:
            log.warning(f"  Could not fetch price data for {etf} — skipping")
            continue

        actual_direction = price_data["direction"]
        notes = (f"Verified via {etf}: "
                 f"{price_data['open_price']} → {price_data['close_price']} "
                 f"({price_data['pct_change']:+.2f}%) "
                 f"threshold=±{price_data.get('threshold_used', 0.2):.1f}%")

        verify_prediction(conn, pred_id, actual_direction, notes)
        verified_count += 1

        log.info(f"  Predicted: {pred['direction']} | "
                 f"Actual: {actual_direction} | "
                 f"{etf} {price_data['pct_change']:+.2f}%")

    return verified_count


def run_verification() -> None:
    log.info("─── Verification run starting ───")
    conn = init_db()

    verified = verify_expired_predictions(conn)
    log.info(f"Verified {verified} predictions")

    if verified > 0:
        snapshot_portfolio(conn)

    # Print current accuracy
    total = conn.execute(
        "SELECT COUNT(*) FROM predictions WHERE was_correct IS NOT NULL"
    ).fetchone()[0]
    correct = conn.execute(
        "SELECT COUNT(*) FROM predictions WHERE was_correct = 1"
    ).fetchone()[0]

    if total > 0:
        log.info(f"Overall accuracy: {correct}/{total} = {correct/total:.1%}")

    conn.close()
    log.info("─── Verification complete ───")


if __name__ == "__main__":
    run_verification()
