#!/usr/bin/env python3
"""
test_verify_rejected.py — Unit tests for verify_rejected_signals().

Tests the outcome writer for rejected signal_ledger rows with fully
mocked price data and in-memory SQLite. No network or production DB access.

Run:
    python pipeline/paper_trading/test_verify_rejected.py
"""

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from paper_trading.verify import (
    verify_rejected_signals,
    _next_trading_day,
    fetch_close_on_date,
)
from paper_trading.db import init_db

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)


class _FakeDatetime(datetime):
    """Subclass of datetime that overrides .now() but keeps all class methods."""
    _fake_now = None

    @classmethod
    def now(cls, tz=None):
        if cls._fake_now is None:
            return super().now(tz)
        return cls._fake_now


def _set_fake_now(dt):
    _FakeDatetime._fake_now = dt


def _clear_fake_now():
    _FakeDatetime._fake_now = None


# -----------------------------------------------------------------------
# Helper: build in-memory DB with signal_ledger + test rows
# -----------------------------------------------------------------------
def _make_db(rows: list[dict]) -> sqlite3.Connection:
    """Create an in-memory DB with signal_ledger table and seed rows."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE signal_ledger (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT NOT NULL,
            query           TEXT NOT NULL,
            sector          TEXT NOT NULL,
            direction       TEXT NOT NULL,
            raw_confidence  REAL NOT NULL,
            adj_confidence  REAL NOT NULL,
            gate_failed     TEXT NOT NULL,
            gate_reason     TEXT,
            sector_win_rate REAL,
            vix_at_time     REAL,
            next_day_drift  REAL,
            was_correct     INTEGER,
            verified_at     TEXT,
            event_type      TEXT DEFAULT 'other'
        )
    """)
    conn.execute("""
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY,
            was_correct INTEGER
        )
    """)
    for r in rows:
        conn.execute(
            """INSERT INTO signal_ledger
               (created_at, query, sector, direction, raw_confidence,
                adj_confidence, gate_failed, gate_reason, next_day_drift,
                was_correct, verified_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)""",
            (
                r["created_at"],
                r["query"],
                r["sector"],
                r["direction"],
                r.get("raw_conf", 0.7),
                r.get("adj_conf", 0.7),
                r.get("gate_failed", "direction_confidence"),
                r.get("gate_reason", ""),
            ),
        )
    conn.commit()
    return conn


# -----------------------------------------------------------------------
# Test 1: _next_trading_day skips weekends
# -----------------------------------------------------------------------
def test_next_trading_day():
    print("\nTest 1: _next_trading_day skips weekends")

    # Thursday -> Friday
    thu = datetime(2026, 9, 24)  # Thursday
    Fri = _next_trading_day(thu)
    check("Thu -> Fri", Fri.weekday() == 4)  # 4 = Friday
    check("Fri is Sep 25", Fri.day == 25)

    # Friday -> Monday
    fri = datetime(2026, 9, 25)  # Friday
    mon = _next_trading_day(fri)
    check("Fri -> Mon", mon.weekday() == 0)
    check("Mon is Sep 28", mon.day == 28)

    # Wednesday -> Thursday (no skip)
    wed = datetime(2026, 9, 23)
    thu2 = _next_trading_day(wed)
    check("Wed -> Thu", thu2.weekday() == 3)
    check("Thu is Sep 24", thu2.day == 24)

    # Saturday -> Monday
    sat = datetime(2026, 9, 26)
    mon2 = _next_trading_day(sat)
    check("Sat -> Mon", mon2.weekday() == 0)
    check("Mon is Sep 28", mon2.day == 28)


# -----------------------------------------------------------------------
# Test 2: fetch_close_on_date mocks correctly
# -----------------------------------------------------------------------
def test_fetch_close_on_date():
    print("\nTest 2: fetch close on date (mocked)")

    mock_data = {
        "chart": {
            "result": [{
                "indicators": {"quote": [{}]},
                "timestamp": [1695657600],
            }],
        }
    }

    # Test bullish sector (XLK)
    mock_data["chart"]["result"][0]["indicators"]["quote"][0]["close"] = [180.50]
    with mock.patch("requests.get") as mock_get:
        mock_resp = mock.Mock()
        mock_resp.json.return_value = mock_data
        mock_resp.raise_for_status = mock.Mock()
        mock_get.return_value = mock_resp

        close = fetch_close_on_date("XLK", "2026-09-25")
        check("XLK close fetched", close == 180.50)
        check("URL contains XLK", "XLK" in mock_get.call_args[0][0])

    # Test failure returns None
    with mock.patch("requests.get") as mock_get:
        mock_get.side_effect = Exception("timeout")
        close = fetch_close_on_date("XLK", "2026-09-25")
        check("Failure returns None", close is None)


# -----------------------------------------------------------------------
# Test 3: Bullish rejected signal — sector drops -> validated (was_correct=1)
# -----------------------------------------------------------------------
def test_bullish_rejected_validated():
    print("\nTest 3: Bullish rejected signal, sector drops -> validated")

    rows = [{
        "created_at": "2026-09-25T14:00:00+00:00",  # Friday
        "query": "Technology and AI sector outlook — semiconductors",
        "sector": "technology",
        "direction": "bullish",
    }]
    conn = _make_db(rows)

    # Mock fetch_close_on_date: signal day close=180.00, next day=178.20 (-1%)
    def fake_close(ticker, date):
        closes = {
            ("XLK", "2026-09-25"): 180.00,
            ("XLK", "2026-09-28"): 178.20,  # Monday after Friday
        }
        return closes.get((ticker, date))

    # Mock market as open (weekday, after 9:30 AM)
    fake_now = datetime(2026, 9, 28, 11, 0, 0)  # Monday 11 AM
    _set_fake_now(fake_now)

    with mock.patch(
        "paper_trading.verify.fetch_close_on_date", side_effect=fake_close
    ), mock.patch("paper_trading.verify.get_sector_etf", return_value="XLK"), \
         mock.patch("paper_trading.verify.datetime", _FakeDatetime):

        count = verify_rejected_signals(conn)

    _clear_fake_now()

    check("Scored 1 signal", count == 1, f"count={count}")

    row = conn.execute(
        "SELECT next_day_drift, was_correct, verified_at FROM signal_ledger"
    ).fetchone()
    drift, was_correct, verified_at = row

    # Drift should be ~-0.01 (-1%)
    check("Drift is negative", drift is not None and drift < 0,
          f"drift={drift}")
    check("Drift ~ -0.01", abs(drift - (-0.01)) < 0.001, f"drift={drift}")
    # was_correct = 1 (rejection validated: bullish rejected, sector went down)
    check("was_correct = 1 (validated)", was_correct == 1,
          f"was_correct={was_correct}")
    check("verified_at set", verified_at is not None)


# -----------------------------------------------------------------------
# Test 4: Bullish rejected signal — sector rises -> false negative (was_correct=0)
# -----------------------------------------------------------------------
def test_bullish_rejected_false_negative():
    print("\nTest 4: Bullish rejected signal, sector rises -> false negative")

    rows = [{
        "created_at": "2026-09-24T14:00:00+00:00",  # Thursday
        "query": "Technology sector outlook — tech stocks",
        "sector": "technology",
        "direction": "bullish",
    }]
    conn = _make_db(rows)

    def fake_close(ticker, date):
        closes = {
            ("XLK", "2026-09-24"): 178.00,
            ("XLK", "2026-09-25"): 180.00,  # +1.1% — sector rose
        }
        return closes.get((ticker, date))

    fake_now = datetime(2026, 9, 25, 11, 0, 0)
    _set_fake_now(fake_now)

    with mock.patch(
        "paper_trading.verify.fetch_close_on_date", side_effect=fake_close
    ), mock.patch("paper_trading.verify.get_sector_etf", return_value="XLK"), \
         mock.patch("paper_trading.verify.datetime", _FakeDatetime):

        count = verify_rejected_signals(conn)

    _clear_fake_now()

    check("Scored 1 signal", count == 1)

    row = conn.execute(
        "SELECT next_day_drift, was_correct FROM signal_ledger"
    ).fetchone()
    check("Drift is positive", row[0] is not None and row[0] > 0,
          f"drift={row[0]}")
    # was_correct = 0 (false negative: bullish rejected, but sector rose)
    check("was_correct = 0 (false negative)", row[1] == 0,
          f"was_correct={row[1]}")


# -----------------------------------------------------------------------
# Test 5: Bearish rejected signal — sector rises -> validated (was_correct=1)
# -----------------------------------------------------------------------
def test_bearish_rejected_validated():
    print("\nTest 5: Bearish rejected signal, sector rises -> validated")

    rows = [{
        "created_at": "2026-09-23T14:00:00+00:00",  # Wednesday
        "query": "Energy sector outlook — oil, gas",
        "sector": "energy",
        "direction": "bearish",
    }]
    conn = _make_db(rows)

    def fake_close(ticker, date):
        closes = {
            ("XLE", "2026-09-23"): 85.00,
            ("XLE", "2026-09-24"): 86.70,  # +2% — sector rose
        }
        return closes.get((ticker, date))

    fake_now = datetime(2026, 9, 24, 11, 0, 0)
    _set_fake_now(fake_now)

    with mock.patch(
        "paper_trading.verify.fetch_close_on_date", side_effect=fake_close
    ), mock.patch("paper_trading.verify.get_sector_etf", return_value="XLE"), \
         mock.patch("paper_trading.verify.datetime", _FakeDatetime):

        count = verify_rejected_signals(conn)

    _clear_fake_now()

    check("Scored 1 signal", count == 1)

    row = conn.execute(
        "SELECT next_day_drift, was_correct FROM signal_ledger"
    ).fetchone()
    check("Drift is positive", row[0] is not None and row[0] > 0,
          f"drift={row[0]}")
    # was_correct = 1 (bearish rejected, sector rose = rejection validated)
    check("was_correct = 1 (validated)", row[1] == 1,
          f"was_correct={row[1]}")


# -----------------------------------------------------------------------
# Test 6: Bearish rejected signal — sector drops -> false negative (was_correct=0)
# -----------------------------------------------------------------------
def test_bearish_rejected_false_negative():
    print("\nTest 6: Bearish rejected signal, sector drops -> false negative")

    rows = [{
        "created_at": "2026-09-22T14:00:00+00:00",  # Tuesday
        "query": "Financial sector outlook — banks",
        "sector": "financials",
        "direction": "bearish",
    }]
    conn = _make_db(rows)

    def fake_close(ticker, date):
        closes = {
            ("XLF", "2026-09-22"): 45.00,
            ("XLF", "2026-09-23"): 44.10,  # -2% — sector fell
        }
        return closes.get((ticker, date))

    fake_now = datetime(2026, 9, 23, 11, 0, 0)
    _set_fake_now(fake_now)

    with mock.patch(
        "paper_trading.verify.fetch_close_on_date", side_effect=fake_close
    ), mock.patch("paper_trading.verify.get_sector_etf", return_value="XLF"), \
         mock.patch("paper_trading.verify.datetime", _FakeDatetime):

        count = verify_rejected_signals(conn)

    _clear_fake_now()

    check("Scored 1 signal", count == 1)

    row = conn.execute(
        "SELECT next_day_drift, was_correct FROM signal_ledger"
    ).fetchone()
    check("Drift is negative", row[0] is not None and row[0] < 0,
          f"drift={row[0]}")
    # was_correct = 0 (bearish rejected, sector fell = false negative)
    check("was_correct = 0 (false negative)", row[1] == 0,
          f"was_correct={row[1]}")


# -----------------------------------------------------------------------
# Test 7: Mixed/neutral signal — was_correct stays NULL
# -----------------------------------------------------------------------
def test_mixed_signal_null_correct():
    print("\nTest 7: Mixed signal — was_correct stays NULL")

    rows = [{
        "created_at": "2026-09-21T14:00:00+00:00",  # Monday
        "query": "Overall market outlook — S&P 500, macro",
        "sector": "macro",
        "direction": "mixed",
    }]
    conn = _make_db(rows)

    def fake_close(ticker, date):
        closes = {
            ("SPY", "2026-09-21"): 550.00,
            ("SPY", "2026-09-22"): 552.00,  # +0.36%
        }
        return closes.get((ticker, date))

    fake_now = datetime(2026, 9, 22, 11, 0, 0)
    _set_fake_now(fake_now)

    with mock.patch(
        "paper_trading.verify.fetch_close_on_date", side_effect=fake_close
    ), mock.patch("paper_trading.verify.get_sector_etf", return_value="SPY"), \
         mock.patch("paper_trading.verify.datetime", _FakeDatetime):

        count = verify_rejected_signals(conn)

    _clear_fake_now()

    check("Scored 1 signal", count == 1)

    row = conn.execute(
        "SELECT next_day_drift, was_correct FROM signal_ledger"
    ).fetchone()
    check("Drift is set", row[0] is not None, f"drift={row[0]}")
    check("was_correct is NULL (mixed)", row[1] is None,
          f"was_correct={row[1]}")


# -----------------------------------------------------------------------
# Test 8: Idempotent — already verified rows are skipped
# -----------------------------------------------------------------------
def test_idempotent():
    print("\nTest 8: Idempotent — already verified rows skipped")

    rows = [
        {
            "created_at": "2026-09-25T14:00:00+00:00",
            "query": "Technology sector outlook",
            "sector": "technology",
            "direction": "bullish",
        },
    ]
    conn = _make_db(rows)

    # Pre-verify the row
    conn.execute("""
        UPDATE signal_ledger
        SET next_day_drift = -0.01, was_correct = 1, verified_at = '2026-09-28T00:00:00'
    """)
    conn.commit()

    fake_now = datetime(2026, 9, 28, 11, 0, 0)
    _set_fake_now(fake_now)

    with mock.patch("paper_trading.verify.datetime", _FakeDatetime):

        count = verify_rejected_signals(conn)

    _clear_fake_now()

    check("Scored 0 (already verified)", count == 0, f"count={count}")


# -----------------------------------------------------------------------
# Test 9: Weekend gate — defers when market closed
# -----------------------------------------------------------------------
def test_weekend_gate():
    print("\nTest 9: Weekend gate — defers when market closed")

    rows = [{
        "created_at": "2026-09-23T14:00:00+00:00",
        "query": "Technology sector outlook",
        "sector": "technology",
        "direction": "bullish",
    }]
    conn = _make_db(rows)

    # Saturday — market closed
    fake_now = datetime(2026, 9, 26, 11, 0, 0)  # Saturday
    _set_fake_now(fake_now)

    with mock.patch("paper_trading.verify.datetime", _FakeDatetime):

        count = verify_rejected_signals(conn)

    _clear_fake_now()

    check("Scored 0 (market closed)", count == 0, f"count={count}")


# -----------------------------------------------------------------------
# Test 10: Multiple signals in one batch
# -----------------------------------------------------------------------
def test_batch_multiple():
    print("\nTest 10: Batch multiple signals")

    rows = [
        {
            "created_at": "2026-09-23T14:00:00+00:00",
            "query": "Technology sector outlook — semiconductors",
            "sector": "technology",
            "direction": "bullish",
        },
        {
            "created_at": "2026-09-23T14:05:00+00:00",
            "query": "Energy sector outlook — oil, gas",
            "sector": "energy",
            "direction": "bearish",
        },
        {
            "created_at": "2026-09-23T14:10:00+00:00",
            "query": "Overall market outlook — S&P 500",
            "sector": "macro",
            "direction": "mixed",
        },
    ]
    conn = _make_db(rows)

    def fake_close(ticker, date):
        closes = {
            ("XLK", "2026-09-23"): 180.00,
            ("XLK", "2026-09-24"): 179.00,  # -0.56% bullish rejected -> validated
            ("XLE", "2026-09-23"): 85.00,
            ("XLE", "2026-09-24"): 84.00,  # -1.18% bearish rejected -> false neg
            ("SPY", "2026-09-23"): 550.00,
            ("SPY", "2026-09-24"): 551.00,  # +0.18% mixed -> drift set, was_correct NULL
        }
        return closes.get((ticker, date))

    fake_now = datetime(2026, 9, 24, 11, 0, 0)
    _set_fake_now(fake_now)

    # Map query -> ETF for batch test
    def fake_etf(query):
        if "Technology" in query:
            return "XLK"
        elif "Energy" in query:
            return "XLE"
        else:
            return "SPY"

    with mock.patch(
        "paper_trading.verify.fetch_close_on_date", side_effect=fake_close
    ), mock.patch("paper_trading.verify.get_sector_etf", side_effect=fake_etf), \
         mock.patch("paper_trading.verify.datetime", _FakeDatetime):

        count = verify_rejected_signals(conn)

    _clear_fake_now()

    check("Scored 3 signals", count == 3, f"count={count}")

    all_rows = conn.execute(
        "SELECT direction, next_day_drift, was_correct FROM signal_ledger "
        "ORDER BY id"
    ).fetchall()

    check("3 rows updated", len(all_rows) == 3)
    if len(all_rows) == 3:
        # Bullish + sector down -> was_correct=1
        check("Bullish validated", all_rows[0][2] == 1,
              f"was_correct={all_rows[0][2]}")
        # Bearish + sector down -> was_correct=0 (false negative)
        check("Bearish false neg", all_rows[1][2] == 0,
              f"was_correct={all_rows[1][2]}")
        # Mixed -> was_correct=NULL
        check("Mixed is NULL", all_rows[2][2] is None,
              f"was_correct={all_rows[2][2]}")


# -----------------------------------------------------------------------
# Test 11: Flat sector — bullish rejected with zero drift -> was_correct=0
# -----------------------------------------------------------------------
def test_flat_sector():
    print("\nTest 11: Flat sector — bullish rejected, drift=0 -> was_correct=0")

    rows = [{
        "created_at": "2026-09-24T14:00:00+00:00",
        "query": "Technology sector outlook",
        "sector": "technology",
        "direction": "bullish",
    }]
    conn = _make_db(rows)

    def fake_close(ticker, date):
        return 180.00  # Same close both days -> drift = 0

    fake_now = datetime(2026, 9, 25, 11, 0, 0)
    _set_fake_now(fake_now)

    with mock.patch(
        "paper_trading.verify.fetch_close_on_date", side_effect=fake_close
    ), mock.patch("paper_trading.verify.get_sector_etf", return_value="XLK"), \
         mock.patch("paper_trading.verify.datetime", _FakeDatetime):

        count = verify_rejected_signals(conn)

    _clear_fake_now()

    check("Scored 1 signal", count == 1)

    row = conn.execute(
        "SELECT next_day_drift, was_correct FROM signal_ledger"
    ).fetchone()
    check("Drift is ~0", abs(row[0]) < 0.0001, f"drift={row[0]}")
    # Flat: bullish rejected with no drop -> was_correct=0 (false negative,
    # sector didn't move against the bullish call)
    check("was_correct = 0 (flat = false negative)", row[1] == 0,
          f"was_correct={row[1]}")


# -----------------------------------------------------------------------
# Test 12: Friday signal -> Monday next day (skip weekend)
# -----------------------------------------------------------------------
def test_friday_to_monday():
    print("\nTest 12: Friday signal -> Monday next day")

    rows = [{
        "created_at": "2026-09-25T14:00:00+00:00",  # Friday
        "query": "Technology sector outlook",
        "sector": "technology",
        "direction": "bullish",
    }]
    conn = _make_db(rows)

    def fake_close(ticker, date):
        # Should look up Monday, NOT Sat/Sun
        closes = {
            ("XLK", "2026-09-25"): 180.00,  # Friday
            ("XLK", "2026-09-28"): 179.00,  # Monday (skip Sat/Sun)
        }
        if date not in ("2026-09-25", "2026-09-28"):
            raise ValueError(f"Unexpected date: {date}")
        return closes.get((ticker, date))

    fake_now = datetime(2026, 9, 28, 11, 0, 0)
    _set_fake_now(fake_now)

    with mock.patch(
        "paper_trading.verify.fetch_close_on_date", side_effect=fake_close
    ), mock.patch("paper_trading.verify.get_sector_etf", return_value="XLK"), \
         mock.patch("paper_trading.verify.datetime", _FakeDatetime):

        count = verify_rejected_signals(conn)

    _clear_fake_now()

    check("Scored 1 signal", count == 1)
    check("Used Monday data (not Sat/Sun)", True)  # Would have raised if wrong


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------

def main():
    global PASS, FAIL
    print("=" * 60)
    print("verify_rejected_signals — Unit Tests")
    print("=" * 60)

    test_next_trading_day()
    test_fetch_close_on_date()
    test_bullish_rejected_validated()
    test_bullish_rejected_false_negative()
    test_bearish_rejected_validated()
    test_bearish_rejected_false_negative()
    test_mixed_signal_null_correct()
    test_idempotent()
    test_weekend_gate()
    test_batch_multiple()
    test_flat_sector()
    test_friday_to_monday()

    print("\n" + "=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")
    print("=" * 60)
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
