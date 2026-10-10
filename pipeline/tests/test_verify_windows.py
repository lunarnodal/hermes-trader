"""
Tests for Round 2 item R2-1 (prediction verification).

Window logic is tested directly. The end-to-end test runs against a COPY of a
real paper_trading.db with get_daily_close stubbed (no network):

    FIXTURE_PAPER_DB=/mnt/qnap/timeseries/db-backups/<YYYYMMDD>/paper_trading.db \
        python -m pytest pipeline/tests/test_verify_windows.py -q
"""
import os
import shutil
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

verify = pytest.importorskip("paper_trading.verify")
U = timezone.utc


@pytest.mark.parametrize("created,hours,expected", [
    # Thu 10/08 08:14 ET premarket, 24h -> scored on Wed close -> Thu close
    (datetime(2026, 10, 8, 12, 14, tzinfo=U), 24, (date(2026, 10, 7), date(2026, 10, 8))),
    # Fri 10/09 08:14 ET, 24h expires Sat -> Thu close -> Fri close
    (datetime(2026, 10, 9, 12, 14, tzinfo=U), 24, (date(2026, 10, 8), date(2026, 10, 9))),
    # Fri 10/09 17:00 ET (after close), 24h expires Sat -> Fri close -> Mon close
    (datetime(2026, 10, 9, 21, 0, tzinfo=U), 24, (date(2026, 10, 9), date(2026, 10, 12))),
    # Wed 11/25 08:14 ET, 24h expires Thanksgiving -> Tue close -> Wed close
    (datetime(2026, 11, 25, 13, 14, tzinfo=U), 24, (date(2026, 11, 24), date(2026, 11, 25))),
    # Wed 11/25 after close, 48h -> Wed close -> Fri 11/27 (early close day)
    (datetime(2026, 11, 25, 22, 0, tzinfo=U), 48, (date(2026, 11, 25), date(2026, 11, 27))),
    # Mon 10/05 08:14 ET, 1w -> Fri 10/02 close -> Fri 10/09 close
    (datetime(2026, 10, 5, 12, 14, tzinfo=U), 168, (date(2026, 10, 2), date(2026, 10, 9))),
])
def test_prediction_window(created, hours, expected):
    assert verify.prediction_window(created, hours) == expected


def test_close_availability_respects_data_delay():
    # Fri 10/09 close is 20:00 UTC; available only from 20:16 UTC
    assert not verify._close_available(date(2026, 10, 9), datetime(2026, 10, 9, 20, 15, tzinfo=U))
    assert verify._close_available(date(2026, 10, 9), datetime(2026, 10, 9, 20, 16, tzinfo=U))
    # early close 11/27: 13:00 ET = 18:00 UTC
    assert verify._close_available(date(2026, 11, 27), datetime(2026, 11, 27, 18, 16, tzinfo=U))


def test_get_daily_close_never_requests_unfinished_sessions():
    from alpaca_feed.data import get_daily_close
    # future session and non-trading day return None without touching the network
    assert get_daily_close("SPY", "2099-06-01") is None
    assert get_daily_close("SPY", "2026-10-10") is None   # Saturday
    assert get_daily_close("SPY", "2026-11-26") is None   # Thanksgiving


SRC_DB = os.environ.get("FIXTURE_PAPER_DB")


@pytest.mark.skipif(not SRC_DB or not Path(SRC_DB).exists(),
                    reason="set FIXTURE_PAPER_DB to a copy of prod paper_trading.db")
def test_backlog_from_broken_runs_is_ready_and_scored(tmp_path, monkeypatch):
    db = tmp_path / "paper_trading.db"
    shutil.copy(SRC_DB, db)
    conn = sqlite3.connect(db)

    # The run that logged "Verified 0 predictions": Fri 2026-10-09 22:00 EDT
    run_at = datetime(2026, 10, 10, 2, 0, tzinfo=U)
    ready = verify.get_expired_unverified(conn, now=run_at)
    ids = {r["id"] for r in ready}
    # Expired by then: 10/08 24h batch (expired 10/09 ~12:00 UTC) and 10/07 48h.
    stuck = {r[0] for r in conn.execute("""
        SELECT id FROM predictions WHERE verified_at IS NULL AND (
            (timeframe = '24h' AND created_at >= '2026-10-08' AND created_at < '2026-10-09')
         OR (timeframe = '48h' AND created_at >= '2026-10-07' AND created_at < '2026-10-08'))""")}
    assert stuck and stuck <= ids, "expired backlog must be ready for scoring"
    # Not yet expired at 10/10 02:00 UTC: 10/08 48h and the 10/09 batch.
    not_yet = {r[0] for r in conn.execute("""
        SELECT id FROM predictions WHERE verified_at IS NULL AND (
            (timeframe = '48h' AND created_at >= '2026-10-08' AND created_at < '2026-10-09')
         OR created_at >= '2026-10-09')""")}
    assert not (not_yet & ids)

    closes = {}

    def fake_close(sym, d):
        return closes.setdefault((sym, d), 100.0 if d < "2026-10-09" else 101.0)

    real_ready = verify.get_expired_unverified
    monkeypatch.setattr(verify, "get_daily_close", fake_close)
    monkeypatch.setattr(verify, "get_expired_unverified", lambda c: real_ready(c, now=run_at))

    scored = verify.verify_expired_predictions(conn)
    assert scored >= len(stuck)
    marks = ",".join(map(str, stuck))
    left = conn.execute(f"SELECT COUNT(*) FROM predictions "
                        f"WHERE verified_at IS NULL AND id IN ({marks})").fetchone()[0]
    assert left == 0
    notes = dict(conn.execute(f"SELECT timeframe || ':' || substr(created_at,1,10), actual_notes "
                              f"FROM predictions WHERE id IN ({marks})").fetchall())
    # 24h made Thu 10/08 premarket: Wed close -> Thu close
    assert "2026-10-07 100.0 → 2026-10-08" in notes["24h:2026-10-08"]
    # 48h made Wed 10/07 premarket: Tue close -> Thu close
    assert "2026-10-06 100.0 → 2026-10-08" in notes["48h:2026-10-07"]
    assert all("[window-v2]" in n for n in notes.values())
