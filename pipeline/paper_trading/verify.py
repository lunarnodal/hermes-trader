#!/usr/bin/env python3
"""
Prediction verification script
Fetches actual price data after prediction timeframe expires
Marks predictions as correct/wrong and updates rule performance
Runs every 6 hours via cron
"""

import logging
import sqlite3
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from paper_trading.db import init_db, verify_prediction, snapshot_portfolio

from alpaca_feed.data import get_daily_close

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        __import__("config").log_handler("verify.log"),
        logging.StreamHandler()
    ]
)


TIMEFRAME_HOURS = {
    "24h": 24,
    "48h": 48,
    "1w":  168
}

# Shared sector→ETF module — replaces local QUERY_SECTOR_MAP + get_sector_etf
from sector_etf import get_sector_etf


def _rounding_threshold(hours_back: int) -> float:
    """Return the rounding threshold for a given timeframe."""
    if hours_back <= 24:
        return 0.2
    elif hours_back <= 48:
        return 0.3
    return 0.5


NY = ZoneInfo("America/New_York")
DATA_DELAY = timedelta(minutes=16)   # free SIP feed excludes the last 15 minutes


def _last_close_at_or_before(ts: datetime) -> date:
    """Trading date of the last regular-session close at or before *ts*."""
    from portfolio.market_calendar import (is_trading_day, previous_trading_day,
                                           session_close)
    d = ts.astimezone(NY).date()
    if is_trading_day(d) and ts >= session_close(d, NY):
        return d
    return previous_trading_day(d)


def prediction_window(created: datetime, hours: int) -> tuple[date, date]:
    """Close-to-close window a prediction is scored on.

    start = last close at or before the prediction was made (the price it was made against)
    end   = last close at or before the prediction expired; at least one session after start
    """
    from portfolio.market_calendar import next_trading_day
    start = _last_close_at_or_before(created)
    end = _last_close_at_or_before(created + timedelta(hours=hours))
    if end <= start:
        end = next_trading_day(start)
    return start, end


def _close_available(d: date, now: datetime) -> bool:
    from portfolio.market_calendar import session_close
    return now >= session_close(d, NY) + DATA_DELAY


def fetch_price_change(ticker: str, start_date: date, end_date: date,
                       hours: int) -> dict | None:
    """Close-to-close change for *ticker* between two trading dates."""
    start_close = get_daily_close(ticker, start_date.isoformat())
    end_close = get_daily_close(ticker, end_date.isoformat())
    if not start_close or end_close is None:
        log.warning(f"  Missing close data for {ticker} "
                    f"({start_date} / {end_date})")
        return None
    pct_change = (end_close - start_close) / start_close * 100
    threshold = _rounding_threshold(hours)
    return {
        "ticker": ticker,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "start_close": start_close,
        "end_close": end_close,
        "pct_change": round(pct_change, 2),
        "direction": "bullish" if pct_change > threshold else
                     "bearish" if pct_change < -threshold else "neutral",
        "hours": hours,
        "threshold_used": threshold}


def get_expired_unverified(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict]:
    """Unverified predictions whose scoring window has closed and whose
    end-of-window close is available from the data feed."""
    now = now or datetime.now(timezone.utc)
    rows = conn.execute("""
        SELECT id, created_at, query, timeframe, direction, confidence
        FROM predictions
        WHERE verified_at IS NULL
        ORDER BY created_at ASC
    """).fetchall()

    ready = []
    for pred_id, created_at, query, timeframe, direction, confidence in rows:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        hours = TIMEFRAME_HOURS.get(timeframe, 24)
        expires = created + timedelta(hours=hours)
        if now < expires:
            continue
        start, end = prediction_window(created, hours)
        if not _close_available(end, now):
            log.info(f"Prediction #{pred_id} expired; close for {end} not available yet")
            continue
        ready.append({
            "id": pred_id, "created_at": created_at, "query": query,
            "timeframe": timeframe, "direction": direction,
            "confidence": confidence, "expires": expires.isoformat(),
            "start_date": start, "end_date": end,
        })
    return ready


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
        timeframe = pred["timeframe"]
        hours     = TIMEFRAME_HOURS.get(timeframe, 24)

        etf = get_sector_etf(pred["query"])
        log.info(f"Verifying prediction #{pred_id} using {etf} "
                 f"({timeframe}: close {pred['start_date']} -> close {pred['end_date']})")

        price_data = fetch_price_change(etf, pred["start_date"], pred["end_date"], hours)
        if price_data is None:
            log.warning(f"  Could not fetch price data for {etf} — skipping")
            continue

        actual_direction = price_data["direction"]
        notes = (f"Verified via {etf}: "
                 f"{price_data['start_date']} {price_data['start_close']} → "
                 f"{price_data['end_date']} {price_data['end_close']} "
                 f"({price_data['pct_change']:+.2f}%) "
                 f"threshold=±{price_data['threshold_used']:.1f}% [window-v2]")

        verify_prediction(conn, pred_id, actual_direction, notes)
        verified_count += 1

        log.info(f"  Predicted: {pred['direction']} | "
                 f"Actual: {actual_direction} | "
                 f"{etf} {price_data['pct_change']:+.2f}%")

    return verified_count


# ─── Rejected-signal outcome writer ─────────────────────────────────────────

def _next_trading_day(dt: datetime) -> datetime:
    """Next US trading day after *dt* (weekends and NYSE holidays skipped)."""
    from portfolio.market_calendar import next_trading_day
    nd = next_trading_day(dt.date())
    return dt.replace(year=nd.year, month=nd.month, day=nd.day)


def fetch_close_on_date(ticker: str, date_str: str) -> float | None:
    """Fetch the closing price for *ticker* on a specific trading date.
    Delegates to get_daily_close so IBKR migration replaces one function."""
    return get_daily_close(ticker, date_str)

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

    now_utc = datetime.now(timezone.utc)

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

        # Score only once the next-day close is published (no future/recent requests)
        if not _close_available(next_day_dt.date(), now_utc):
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
