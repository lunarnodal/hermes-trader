"""
Replay tests for the theta lifecycle (Round 2 items R2-2 and R2-3).

They run the changed functions against a COPY of a real portfolio.db (prod
schema + data) and real alpaca-py model objects built from recorded paper
broker responses, so schema drift and enum/string mistakes fail here.

    FIXTURE_PORTFOLIO_DB=/mnt/qnap/timeseries/db-backups/<YYYYMMDD>/portfolio.db \
        python -m pytest pipeline/tests/test_theta_lifecycle_replay.py -q

Skipped (not passed) when FIXTURE_PORTFOLIO_DB is unset.
"""
import json
import os
import shutil
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

from alpaca.trading.models import Order, Position  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
SRC_DB = os.environ.get("FIXTURE_PORTFOLIO_DB")
pytestmark = pytest.mark.skipif(
    not SRC_DB or not Path(SRC_DB).exists(),
    reason="set FIXTURE_PORTFOLIO_DB to a copy of prod portfolio.db")

BTC_FILLED_ID = "a578182e-8edf-4614-a7f7-9add261a0b15"   # XLK261030P00182000 BTC @ 1.28
STO_FILLED_ID = "f7434800-c7be-4198-804f-a5ea616007fe"   # XLK261030P00182000 STO @ 2.33
OPT = "XLK261030P00182000"


def _orders():
    return {o["id"]: o for o in json.loads(
        (FIXTURES / "broker_orders_xlk_theta.json").read_text())}


def _order(order_id, **overrides):
    raw = dict(_orders()[order_id])
    raw.update(overrides)
    return Order.model_validate(raw)


def _position(symbol, qty, avg):
    return Position.model_validate({
        "asset_id": "00000000-0000-0000-0000-000000000001", "symbol": symbol,
        "exchange": "ARCA", "asset_class": "us_equity",
        "avg_entry_price": str(avg), "qty": str(qty), "side": "long",
        "market_value": str(qty * avg), "cost_basis": str(qty * avg),
        "unrealized_pl": "0", "unrealized_plpc": "0",
        "unrealized_intraday_pl": "0", "unrealized_intraday_plpc": "0",
        "current_price": str(avg), "lastday_price": str(avg),
        "change_today": "0", "qty_available": str(qty)})


class FakeClient:
    def __init__(self, orders=None, positions=None):
        self.orders = orders or {}
        self.positions = positions or []
        self.submitted = []

    def get_order_by_id(self, order_id):
        return self.orders[order_id]

    def get_all_positions(self):
        return list(self.positions)

    def submit_order(self, req):          # must never be called by these paths
        self.submitted.append(req)
        raise AssertionError("no order may be submitted during fill/assignment checks")


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "portfolio.db"
    shutil.copy(SRC_DB, db)
    c = sqlite3.connect(db)
    yield c
    c.close()


def _ledger_count(conn):
    return conn.execute("SELECT COUNT(*) FROM cash_ledger").fetchone()[0]


def _insert_theta(conn, status, notes, expiry="2026-10-30"):
    # 'closing' rows carry assignment_event, exactly as transition_theta_closing() writes them
    event = "profit_close" if status == "closing" else None
    cur = conn.execute("""
        INSERT INTO theta_positions
        (ticker, sector, instrument_type, strike, expiry, premium_collected,
         entry_date, status, assignment_event, notes, option_symbol)
        VALUES ('XLK', 'technology', 'cash_secured_put', 182.0, ?, 2.33,
                '2026-09-21T13:35:06+00:00', ?, ?, ?, ?)
    """, (expiry, status, event, notes, OPT))
    conn.commit()
    return cur.lastrowid


# ── R2-2: BTC fill confirmation ─────────────────────────────────────────────

def test_btc_fill_closes_at_broker_fill_and_releases_once(conn):
    from alpaca_feed.theta_execution import check_theta_closing_fills
    pid = _insert_theta(conn, "closing",
                        f"order_id={STO_FILLED_ID} | confirmed_fill | "
                        f"closing: order={BTC_FILLED_ID} expected=1.25 profit_50pct")
    client = FakeClient(orders={BTC_FILLED_ID: _order(BTC_FILLED_ID)})
    before = _ledger_count(conn)

    res = check_theta_closing_fills(conn, client=client)
    row = conn.execute("SELECT status, exit_price, pnl FROM theta_positions WHERE id=?",
                       (pid,)).fetchone()
    assert row == ("closed", 1.28, 105.0)
    assert res[0]["action"] == "BTC_FILLED"
    assert _ledger_count(conn) == before + 1
    rel = conn.execute("SELECT amount, description FROM cash_ledger ORDER BY id DESC LIMIT 1").fetchone()
    assert rel[0] == 18200.0 and "THETA RELEASE" in rel[1] and OPT in rel[1]

    # second cycle: nothing left in 'closing', no second release, no orders
    assert check_theta_closing_fills(conn, client=client) == []
    assert _ledger_count(conn) == before + 1
    assert client.submitted == []


def test_btc_expired_reverts_to_open_exactly_once(conn):
    from alpaca_feed.theta_execution import check_theta_closing_fills
    pid = _insert_theta(conn, "closing",
                        f"order_id={STO_FILLED_ID} | confirmed_fill | "
                        f"closing: order={BTC_FILLED_ID} expected=1.25 profit_50pct")
    expired = _order(BTC_FILLED_ID, status="expired", filled_avg_price=None,
                     filled_qty="0", filled_at=None, expired_at="2026-10-05T20:00:00Z")
    client = FakeClient(orders={BTC_FILLED_ID: expired})
    before = _ledger_count(conn)

    res = check_theta_closing_fills(conn, client=client)
    assert conn.execute("SELECT status FROM theta_positions WHERE id=?", (pid,)).fetchone()[0] == "open"
    assert res[0]["action"] == "BTC_REVERTED"
    assert check_theta_closing_fills(conn, client=client) == []
    assert _ledger_count(conn) == before
    assert client.submitted == []


def test_pending_btc_stays_closing(conn):
    from alpaca_feed.theta_execution import check_theta_closing_fills
    pid = _insert_theta(conn, "closing",
                        f"confirmed_fill | closing: order={BTC_FILLED_ID} expected=1.25 x")
    pending = _order(BTC_FILLED_ID, status="new", filled_avg_price=None,
                     filled_qty="0", filled_at=None)
    check_theta_closing_fills(conn, client=FakeClient(orders={BTC_FILLED_ID: pending}))
    assert conn.execute("SELECT status FROM theta_positions WHERE id=?", (pid,)).fetchone()[0] == "closing"


# ── R2-3: assignment / expiry detection ─────────────────────────────────────

def test_assignment_with_sync_created_equity_row(conn):
    from portfolio.db import detect_theta_assignments
    pid = _insert_theta(conn, "open", f"order_id={STO_FILLED_ID} | confirmed_fill")
    conn.execute("""INSERT INTO positions (ticker, sector, shares, entry_price, entry_date,
                    current_price, stop_loss, take_profit, status, notes)
                    VALUES ('XLK','unknown',100,182.0,'2026-10-20T13:35:00+00:00',179.0,
                            172.9,189.28,'open','Created by Alpaca cache sync')""")
    conn.commit()
    before = _ledger_count(conn)
    n_pos = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]

    stats = detect_theta_assignments(conn, client=FakeClient(
        positions=[_position("XLK", 100, 182.0)]), today=date(2026, 10, 20))
    assert stats["assigned"] == 1 and stats["errors"] == 0
    assert conn.execute("SELECT status, assignment_event, pnl FROM theta_positions WHERE id=?",
                        (pid,)).fetchone() == ("assigned", "exercised", 233.0)
    ev = conn.execute("""SELECT sector, ticker, event_type FROM theta_assignment_events
                         WHERE theta_position_id=?""", (pid,)).fetchall()
    assert ev == [("technology", "XLK", "assignment")]
    assert _ledger_count(conn) == before + 1
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == n_pos  # no duplicate
    eq = conn.execute("SELECT sector, notes FROM positions WHERE ticker='XLK' AND status='open'").fetchone()
    assert eq[0] == "technology" and "Theta assignment" in eq[1]
    # idempotent
    detect_theta_assignments(conn, client=FakeClient(positions=[_position("XLK", 100, 182.0)]),
                             today=date(2026, 10, 20))
    assert _ledger_count(conn) == before + 1


def test_assignment_creates_equity_row_with_all_required_columns(conn):
    from portfolio.db import detect_theta_assignments
    _insert_theta(conn, "open", f"order_id={STO_FILLED_ID} | confirmed_fill")
    stats = detect_theta_assignments(conn, client=FakeClient(
        positions=[_position("XLK", 100, 182.0)]), today=date(2026, 10, 31))
    assert stats["assigned"] == 1 and stats["errors"] == 0
    row = conn.execute("""SELECT shares, entry_price, stop_loss, take_profit, sector
                          FROM positions WHERE ticker='XLK' AND status='open'""").fetchone()
    assert row == (100.0, 182.0, 172.9, 189.28, "technology")


def test_expired_worthless_keeps_premium(conn):
    from portfolio.db import detect_theta_assignments
    pid = _insert_theta(conn, "open", f"order_id={STO_FILLED_ID} | confirmed_fill")
    stats = detect_theta_assignments(conn, client=FakeClient(positions=[]),
                                     today=date(2026, 10, 31))
    assert stats["expired"] == 1
    assert conn.execute("SELECT status, assignment_event, pnl FROM theta_positions WHERE id=?",
                        (pid,)).fetchone() == ("closed", "expired", 233.0)


def test_option_still_held_is_untouched(conn):
    from portfolio.db import detect_theta_assignments
    pid = _insert_theta(conn, "open", f"order_id={STO_FILLED_ID} | confirmed_fill")
    stats = detect_theta_assignments(conn, client=FakeClient(
        positions=[_position(OPT, -1, 2.33)]), today=date(2026, 10, 31))
    assert stats == {"assigned": 0, "expired": 0, "anomalies": 0, "errors": 0}
    assert conn.execute("SELECT status FROM theta_positions WHERE id=?", (pid,)).fetchone()[0] == "open"


def test_assignment_failure_rolls_back_everything(conn):
    from portfolio.db import detect_theta_assignments
    pid = _insert_theta(conn, "open", f"order_id={STO_FILLED_ID} | confirmed_fill")
    # Make the equity INSERT fail: a trigger aborts any new XLK position row
    conn.execute("""CREATE TRIGGER fail_xlk BEFORE INSERT ON positions
                    WHEN NEW.ticker = 'XLK' BEGIN SELECT RAISE(ABORT, 'forced'); END""")
    conn.commit()
    before_ledger = _ledger_count(conn)
    before_events = conn.execute("SELECT COUNT(*) FROM theta_assignment_events").fetchone()[0]

    stats = detect_theta_assignments(conn, client=FakeClient(
        positions=[_position("XLK", 100, 182.0)]), today=date(2026, 10, 31))
    assert stats["errors"] == 1 and stats["assigned"] == 0
    assert conn.execute("SELECT status FROM theta_positions WHERE id=?", (pid,)).fetchone()[0] == "open"
    assert _ledger_count(conn) == before_ledger
    assert conn.execute("SELECT COUNT(*) FROM theta_assignment_events").fetchone()[0] == before_events


# ── STO fill confirmation (old string test never matched 'canceled') ────────

def test_sto_fill_confirmed_and_reserved(conn):
    from portfolio.db import cancel_expired_theta_positions
    pid = _insert_theta(conn, "open", f"delta=-0.25 dte=39 order_id={STO_FILLED_ID}")
    before = _ledger_count(conn)
    cancel_expired_theta_positions(conn, client=FakeClient(
        orders={STO_FILLED_ID: _order(STO_FILLED_ID)}))
    notes, prem = conn.execute("SELECT notes, premium_collected FROM theta_positions WHERE id=?",
                               (pid,)).fetchone()
    assert "confirmed_fill" in notes and prem == 2.33
    assert _ledger_count(conn) == before + 1


def test_sto_canceled_marks_cancelled(conn):
    from portfolio.db import cancel_expired_theta_positions
    pid = _insert_theta(conn, "open", f"order_id={STO_FILLED_ID}")
    canceled = _order(STO_FILLED_ID, status="canceled", filled_avg_price=None,
                      filled_qty="0", filled_at=None, canceled_at="2026-09-21T20:00:00Z")
    n = cancel_expired_theta_positions(conn, client=FakeClient(orders={STO_FILLED_ID: canceled}))
    assert n == 1
    assert conn.execute("SELECT status FROM theta_positions WHERE id=?", (pid,)).fetchone()[0] == "cancelled"


# ── cash sync keeps a closing CSP reserved ──────────────────────────────────

def test_sync_cash_counts_closing_csp_as_reserved(conn):
    from alpaca_feed.alpaca_cache import sync_cash
    from portfolio.db import get_cash_balance
    _insert_theta(conn, "closing", f"confirmed_fill | closing: order={BTC_FILLED_ID} expected=1.25 x")
    alpaca_cash = 100000.0
    reconciled = sync_cash(conn, alpaca_cash)
    assert reconciled == pytest.approx(alpaca_cash - 18200.0)
    assert get_cash_balance(conn) == pytest.approx(alpaca_cash - 18200.0)
