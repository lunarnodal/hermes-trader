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

# Shared sector→ETF module — replaces local QUERY_SECTOR_MAP + get_sector_etf
from sector_etf import get_sector_etf


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


# ─── Rejected-signal outcome writer ─────────────────────────────────────────

def _next_trading_day(dt: datetime) -> datetime:
    """Return the next US trading day after *dt* (skip Sat/Sun)."""
    d = dt + timedelta(days=1)
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d += timedelta(days=1)
    return d


def fetch_close_on_date(ticker: str, date_str: str) -> float | None:
    """Fetch the closing price for *ticker* on a specific trading date.

    Args:
        ticker: ETF or stock ticker (e.g. 'XLK').
        date_str: Date string 'YYYY-MM-DD' in America/New_York timezone.

    Returns:
        Closing price as float, or None on failure.
    """
    try:
        # Convert NY date to UTC timestamps for Yahoo API
        ny_tz = ZoneInfo("America/New_York")
        local_date = datetime.strptime(date_str, "%Y-%m-%d").replace(
            tzinfo=ny_tz
        )
        # period1 = start of that day (UTC), period2 = end of that day (UTC)
        period1 = int((local_date.replace(hour=0, minute=0, second=0)
                       .astimezone(timezone.utc)).timestamp())
        period2 = int((local_date.replace(hour=23, minute=59, second=59)
                       .astimezone(timezone.utc)).timestamp())

        url = f"{YF_BASE}/{ticker}"
        resp = requests.get(url, params={
            "interval": "1d",
            "period1": period1,
            "period2": period2,
            "includePrePost": False,
        }, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        resp.raise_for_status()

        data = resp.json()
        result = data["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        times = result["timestamp"]

        valid = [(t, c) for t, c in zip(times, closes) if c is not None]
        if not valid:
            return None

        # Return the last close within our date window
        return round(valid[-1][1], 2)

    except Exception as e:
        log.warning(f"Could not fetch close for {ticker} on {date_str}: {e}")
        return None


def verify_rejected_signals(conn: sqlite3.Connection) -> int:
    """Compute next_day_drift + was_correct for rejected signal_ledger rows.

    For each rejected signal with NULL outcomes:
      - Fetch sector-ETF close on signal_date and next trading day.
      - Store close-to-close pct_change as next_day_drift (decimal fraction).
      - Set was_correct = 1 when the rejection was validated (sector moved
        against predicted direction), 0 when it was a false negative, NULL
        for neutral / mixed / directionless signals.
      - Mark verified_at so the update is idempotent.

    Safe for backfill since any date — uses date-anchored Yahoo Finance closes.
    """
    rows = conn.execute("""
        SELECT id, sector, query, direction, created_at
        FROM signal_ledger
        WHERE next_day_drift IS NULL
          AND was_correct IS NULL
          AND verified_at IS NULL
        ORDER BY created_at ASC
    """).fetchall()

    if not rows:
        log.info("No unverified rejected signals to score")
        return 0

    log.info(f"Found {len(rows)} rejected signals to score outcomes for")

    # Gate: only score when market is open (so we have fresh closes)
    now_local = datetime.now()
    is_weekday = now_local.weekday() < 5
    market_open_today = now_local.replace(
        hour=9, minute=30, second=0, microsecond=0
    )
    if not is_weekday or now_local < market_open_today:
        log.info("Market not yet open — deferring rejected-signal scoring")
        return 0

    verified_count = 0
    for row in rows:
        sig_id, sector, query, direction, created_at = row

        # Resolve ETF for this sector
        etf = get_sector_etf(query)

        # Parse signal date (America/New_York local date of creation)
        try:
            created_dt = datetime.fromisoformat(
                created_at.replace("Z", "+00:00")
            )
            signal_date_ny = created_dt.astimezone(ZoneInfo("America/New_York"))
            signal_date_str = signal_date_ny.strftime("%Y-%m-%d")
            next_day_dt = _next_trading_day(signal_date_ny)
            next_date_str = next_day_dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError) as e:
            log.warning(
                f"  Bad date for signal #{sig_id}: {created_at!r} — {e}"
            )
            continue

        # Fetch closes
        signal_close = fetch_close_on_date(etf, signal_date_str)
        next_close = fetch_close_on_date(etf, next_date_str)

        if signal_close is None or next_close is None:
            log.warning(
                f"  Missing close data for {etf} "
                f"({signal_date_str} / {next_date_str}) — skipping #{sig_id}"
            )
            continue

        # Close-to-close pct change (decimal fraction)
        drift = round((next_close - signal_close) / signal_close, 4)

        # Determine was_correct:
        #   1 = rejection validated (sector moved AGAINST predicted direction)
        #   0 = false negative (sector moved WITH predicted direction)
        #   NULL = neutral/mixed signal — no direction to validate
        was_correct = None
        if direction == "bullish":
            # Bullish signal was rejected; if sector went down, rejection was right
            was_correct = 1 if drift < 0 else 0
        elif direction == "bearish":
            # Bearish signal was rejected; if sector went up, rejection was right
            was_correct = 1 if drift > 0 else 0
        # else: neutral/mixed — leave was_correct NULL (still record drift)

        verified_at = datetime.now(timezone.utc).isoformat()

        conn.execute("""
            UPDATE signal_ledger
            SET next_day_drift = ?, was_correct = ?, verified_at = ?
            WHERE id = ?
        """, (drift, was_correct, verified_at, sig_id))

        direction_label = direction if direction else "neutral"
        drift_str = f"{drift:+.1%}"
        verdict = (
            "validated" if was_correct == 1
            else "false-negative" if was_correct == 0
            else "neutral"
        )
        log.info(
            f"  Rejected #{sig_id}: {sector} {direction_label} "
            f"drift={drift_str} "
            f"({signal_close:.2f} -> {next_close:.2f}) "
            f"→ {verdict}"
        )
        verified_count += 1

    conn.commit()
    log.info(f"Scored {verified_count} rejected signal outcomes")
    return verified_count


def run_verification() -> None:
    log.info("─── Verification run starting ───")
    conn = init_db()

    verified = verify_expired_predictions(conn)
    log.info(f"Verified {verified} predictions")

    if verified > 0:
        snapshot_portfolio(conn)

    # Score outcomes for previously-rejected signals
    rejected_scored = verify_rejected_signals(conn)
    log.info(f"Scored {rejected_scored} rejected signal outcomes")

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
