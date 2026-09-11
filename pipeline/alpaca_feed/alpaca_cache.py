#!/usr/bin/env python3
"""
Alpaca Cache — sync Alpaca account state into local DB at start of each cycle.

Alpaca is the source of truth for:
  - Cash balance
  - Open equity positions (shares, avg cost, current price)
  - Orders placed this week (for weekly trade limit gate)

Local DB retains ownership of:
  - Sector tags (Alpaca has no sector concept)
  - Stop loss / take profit levels
  - Profit tier state (tiers_triggered)
  - Entry date / hold days
  - Theta positions metadata
  - Predictions, signals, rules (entirely local)

Sync strategy:
  1. Pull Alpaca account → reconcile cash_ledger
  2. Pull Alpaca positions → upsert positions table, preserve local metadata
  3. Pull Alpaca orders this week → reconcile transaction count
  4. Log divergences for audit trail

Called once at start of run_portfolio_cycle() in manager.py.
Fast enough to run every cycle (~200ms).
"""

import os
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

# Alpaca credentials
ALPACA_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY", "")
PAPER         = os.getenv("ALPACA_PAPER", "true").lower() == "true"


def _get_trading_client():
    from alpaca.trading.client import TradingClient
    return TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=PAPER)


def sync_cash(conn: sqlite3.Connection, alpaca_cash: float) -> float:
    """
    Reconcile cash_ledger with Alpaca account cash.

    If there's a divergence, adds a correction entry to the ledger
    so future reads return the correct balance.

    Returns the reconciled cash balance.
    """
    from portfolio.db import get_cash_balance

    db_cash = get_cash_balance(conn)

    # Account for theta reservations — these reduce DB cash but not Alpaca cash
    # (Alpaca doesn't know about our CSP reservations)
    try:
        theta_reserved = conn.execute("""
            SELECT COALESCE(SUM(strike * 100), 0.0)
            FROM theta_positions
            WHERE status = 'open'
            AND notes LIKE '%confirmed_fill%'
        """).fetchone()[0] or 0.0
    except Exception:
        theta_reserved = 0.0

    # DB cash should equal Alpaca cash minus theta reservations
    expected_db_cash = round(alpaca_cash - theta_reserved, 2)
    divergence = round(db_cash - expected_db_cash, 2)

    if abs(divergence) > 1.0:  # more than $1 divergence
        log.warning(f"[CACHE] Cash divergence: DB=${db_cash:.2f} "
                    f"Alpaca=${alpaca_cash:.2f} "
                    f"theta_reserved=${theta_reserved:.2f} "
                    f"expected_db=${expected_db_cash:.2f} "
                    f"divergence=${divergence:.2f}")

        # Apply correction
        now = datetime.now(timezone.utc).isoformat()
        new_balance = round(db_cash - divergence, 2)
        conn.execute("""
            INSERT INTO cash_ledger (timestamp, amount, balance, description)
            VALUES (?, ?, ?, ?)
        """, (now, -divergence, new_balance,
              f"ALPACA SYNC: cash reconciliation (Alpaca=${alpaca_cash:.2f})"))
        conn.commit()
        log.info(f"[CACHE] Cash corrected: ${db_cash:.2f} → ${new_balance:.2f}")
        return new_balance
    else:
        log.debug(f"[CACHE] Cash in sync: DB=${db_cash:.2f} "
                  f"Alpaca=${alpaca_cash:.2f} (delta=${divergence:.2f})")
        return db_cash


def sync_positions(conn: sqlite3.Connection,
                   alpaca_positions: list[dict]) -> dict:
    """
    Reconcile positions table with Alpaca equity positions.

    For each Alpaca position:
      - If in DB: update shares, current_price. Preserve stop/take/sector/tiers.
      - If not in DB: create new position record (sector=unknown until enriched)

    For each DB position not in Alpaca:
      - If it has been in DB > 1 hour: mark as closed (Alpaca is truth)
      - If < 1 hour: may be a pending fill, leave it

    Returns dict with reconciliation stats.
    """
    stats = {"updated": 0, "created": 0, "closed": 0, "divergences": []}

    # Build Alpaca position map
    alpaca_map = {p["ticker"]: p for p in alpaca_positions
                  if not _is_option_symbol(p["ticker"])}  # equity only

    # Get DB positions
    db_rows = conn.execute("""
        SELECT id, ticker, sector, shares, entry_price, entry_date,
               stop_loss, take_profit, tiers_triggered, notes
        FROM positions WHERE status = 'open'
    """).fetchall()
    db_map = {row[1]: row for row in db_rows}

    now = datetime.now(timezone.utc).isoformat()

    # Update/create from Alpaca
    for ticker, ap in alpaca_map.items():
        alpaca_shares = float(ap["qty"])
        alpaca_price  = float(ap["current_price"])
        alpaca_cost   = float(ap["avg_cost"])

        if ticker in db_map:
            row = db_map[ticker]
            db_id, _, sector, db_shares, db_entry, db_entry_date, \
                db_stop, db_take, db_tiers, db_notes = row

            # Check for share count divergence
            if abs(db_shares - alpaca_shares) > 0.5:
                log.warning(f"[CACHE] Share divergence {ticker}: "
                            f"DB={db_shares:.0f} Alpaca={alpaca_shares:.0f} "
                            f"→ correcting to Alpaca")
                stats["divergences"].append({
                    "ticker": ticker,
                    "field": "shares",
                    "db": db_shares,
                    "alpaca": alpaca_shares,
                })

            # Update shares and current price from Alpaca
            conn.execute("""
                UPDATE positions
                SET shares = ?,
                    current_price = ?,
                    last_price_update = ?
                WHERE id = ? AND status = 'open'
            """, (alpaca_shares, alpaca_price, now, db_id))
            stats["updated"] += 1

        else:
            # Position in Alpaca but not in DB — create it
            log.warning(f"[CACHE] Position {ticker} in Alpaca but not DB — creating")
            stop_loss  = round(alpaca_cost * 0.95, 2)  # 5% default stop
            take_profit = round(alpaca_cost * 1.04, 2)  # 4% default take
            conn.execute("""
                INSERT INTO positions
                (ticker, sector, shares, entry_price, entry_date,
                 current_price, last_price_update, stop_loss, take_profit,
                 status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """, (ticker, "unknown", alpaca_shares, alpaca_cost,
                  now, alpaca_price, now, stop_loss, take_profit,
                  "Created by Alpaca cache sync"))
            stats["created"] += 1
            stats["divergences"].append({
                "ticker": ticker,
                "field": "missing_from_db",
                "db": None,
                "alpaca": alpaca_shares,
            })

    # Close DB positions not in Alpaca
    for ticker, row in db_map.items():
        if ticker not in alpaca_map:
            db_id, _, sector, db_shares, db_entry, db_entry_date, \
                db_stop, db_take, db_tiers, db_notes = row

            # Check how long it's been open — give 2 hours grace for pending fills
            try:
                entry_dt = datetime.fromisoformat(
                    db_entry_date.replace("Z", "+00:00")
                )
                age_hours = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
            except Exception:
                age_hours = 99

            if age_hours > 2:
                log.warning(f"[CACHE] Position {ticker} in DB but not Alpaca "
                            f"(age={age_hours:.1f}h) — marking closed")
                conn.execute("""
                    UPDATE positions
                    SET status = 'closed',
                        exit_date = ?,
                        exit_reason = 'alpaca_sync_not_found',
                        notes = COALESCE(notes, '') || ' | closed_by_alpaca_sync'
                    WHERE id = ? AND status = 'open'
                """, (now, db_id))
                stats["closed"] += 1
                stats["divergences"].append({
                    "ticker": ticker,
                    "field": "missing_from_alpaca",
                    "db": db_shares,
                    "alpaca": None,
                })
            else:
                log.debug(f"[CACHE] {ticker} not in Alpaca but DB entry < 2h old — skipping")

    conn.commit()
    return stats


def sync_week_orders(conn: sqlite3.Connection,
                     alpaca_orders: list) -> int:
    """
    Sync this week's order count to transactions table.
    Returns the count of BUY orders placed this week via Alpaca.
    """
    monday = (datetime.now(timezone.utc) - timedelta(
        days=datetime.now(timezone.utc).weekday()
    )).replace(hour=0, minute=0, second=0, microsecond=0)

    week_buys = sum(
        1 for o in alpaca_orders
        if (str(o.side).lower().endswith("buy") and
            o.filled_at and
            o.filled_at.replace(tzinfo=timezone.utc) >= monday)
    )
    return week_buys


def _is_option_symbol(symbol: str) -> bool:
    """Check if a symbol is an options contract (OCC format)."""
    return len(symbol) > 10 and any(c.isdigit() for c in symbol[3:8])


def sync_from_alpaca(conn: sqlite3.Connection) -> dict:
    """
    Main entry point — sync all Alpaca state into local DB.
    Called at start of each portfolio cycle.

    Returns summary dict with sync results.
    """
    result = {
        "synced": False,
        "cash_reconciled": False,
        "positions_synced": 0,
        "divergences": [],
        "error": None,
    }

    try:
        client = _get_trading_client()

        # 1. Account cash
        account = client.get_account()
        alpaca_cash = float(account.cash)
        alpaca_portfolio = float(account.portfolio_value)

        reconciled_cash = sync_cash(conn, alpaca_cash)
        result["cash_reconciled"] = True
        result["alpaca_cash"] = alpaca_cash
        result["alpaca_portfolio"] = alpaca_portfolio

        # 2. Equity positions
        alpaca_positions = []
        for p in client.get_all_positions():
            if not _is_option_symbol(p.symbol):
                alpaca_positions.append({
                    "ticker":        p.symbol,
                    "qty":           float(p.qty),
                    "avg_cost":      float(p.avg_entry_price),
                    "market_value":  float(p.market_value),
                    "current_price": float(p.current_price),
                    "unrealized_pl": float(p.unrealized_pl),
                })

        pos_stats = sync_positions(conn, alpaca_positions)
        result["positions_synced"] = pos_stats["updated"] + pos_stats["created"]
        result["positions_closed"] = pos_stats["closed"]
        result["divergences"]      = pos_stats["divergences"]

        # 3. Week order count (for positions_this_week gate)
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        monday = (datetime.now(timezone.utc) - timedelta(
            days=datetime.now(timezone.utc).weekday()
        )).replace(hour=0, minute=0, second=0, microsecond=0)

        week_orders = client.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            after=monday,
            limit=50,
        ))
        result["week_buys"] = sync_week_orders(conn, week_orders)

        result["synced"] = True

        if pos_stats["divergences"]:
            log.warning(f"[CACHE] Sync complete with {len(pos_stats['divergences'])} "
                        f"divergence(s): "
                        f"{[d['ticker'] for d in pos_stats['divergences']]}")
        else:
            log.info(f"[CACHE] Alpaca sync OK — "
                     f"cash=${alpaca_cash:,.2f} "
                     f"positions={len(alpaca_positions)} "
                     f"updated={pos_stats['updated']} "
                     f"created={pos_stats['created']} "
                     f"closed={pos_stats['closed']}")

    except Exception as e:
        log.error(f"[CACHE] Alpaca sync failed: {e}")
        result["error"] = str(e)

    return result


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")

    from portfolio.db import init_db
    conn = init_db()
    result = sync_from_alpaca(conn)
    conn.close()

    import json
    print(json.dumps(result, indent=2, default=str))
