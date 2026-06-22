#!/usr/bin/env python3
"""
Portfolio database
Tracks positions, transactions, P&L, and recommendations
"""

import sqlite3
import json
import os
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

DB_PATH = Path(os.environ.get("PORTFOLIO_DB_PATH",
               "/home/trading/trading-ai/data/portfolio.db"))

log = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────

CONFIG = {
    "starting_capital":     50_000.00,
    "max_position_pct":     0.10,      # 10% max per position
    "min_cash_reserve_pct": 0.10,      # 10% minimum cash
    "max_sector_pct":       0.25,      # 25% max per sector
    "stop_loss_pct":        0.02,      # 2% default stop loss (ETFs)
    "stop_loss_by_type": {
        "etf":       0.02,   # 2% — diversified, lower vol
        "large_cap": 0.03,   # 3% — S&P 500 components
        "stock":     0.04,   # 4% — individual stocks default
        "small_cap": 0.05,   # 5% — higher volatility
    },
    "min_hold_before_stop_days": 1,  # Don't stop out same day as entry
    "min_hold_days":        3,         # minimum 3 trading days
    "max_hold_days":        10,        # re-evaluate after 10 trading days
    "max_new_positions_week": 5,       # max 5 new positions per week — quality over quantity
    "max_open_positions":   12,        # max 12 simultaneous open positions
    "drawdown_circuit_breaker_pct": 0.02,   # pause entries if portfolio drops >2% in 5 days
    "macro_gate_enabled": True,             # block entries if market_overview is bearish
    "max_positions_per_sector": {
        "technology":   4,
        "healthcare":   3,
        "energy":       3,
        "financials":   2,
        "materials":    2,
        "industrials":  2,
        "consumer":     2,
        "macro":        1,
        "default":      2,
    },
    "reentry_rules": {
        "stop_loss": {
            "cooldown_days":     2,     # 2 trading days before re-entry allowed
            "min_signals":       3,     # stronger signal requirement
            "min_confidence":    0.80,  # higher confidence required
        },
        "time_exit": {
            "cooldown_days":     1,     # 1 trading day cooldown
            "min_signals":       2,     # normal signal requirement
            "min_confidence":    0.60,  # normal confidence
        },
        "take_profit": {
            "cooldown_days":     0,     # no cooldown — trend continuation fine
            "min_signals":       2,     # normal signal requirement
            "min_confidence":    0.60,  # normal confidence
        },
    },
    "confidence_tiers": {
        "low":    (0.70, 0.75, 0.04),  # raised floor — 0.70 minimum confidence
        "medium": (0.75, 0.85, 0.06),
        "high":   (0.85, 1.00, 0.085),
    },
    "market_open":  "09:30",
    "market_close": "16:00",
    "entry_windows": [
        ("09:30", "10:00"),   # morning window
        ("15:30", "16:00"),   # close window
    ],
    "timezone": "America/New_York",
    # Tiered profit taking — sell fractions as price climbs
    # Each tier: (gain_pct, sell_fraction, move_stop_to)
    # move_stop_to: 'breakeven' | 'previous_tier' | float (pct gain)
    "profit_tiers": [
        (0.05, 0.33, "breakeven"),      # +5%:  sell 33%, stop → breakeven
        (0.08, 0.33, "previous_tier"),  # +8%:  sell 33%, stop → +5%
        (0.12, 1.00, "previous_tier"),  # +12%: sell remaining, stop → +8%
    ],
}


def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS portfolio_config (
            key    TEXT PRIMARY KEY,
            value  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cash_ledger (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT NOT NULL,
            amount       REAL NOT NULL,
            balance      REAL NOT NULL,
            description  TEXT
        );

        CREATE TABLE IF NOT EXISTS positions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker           TEXT NOT NULL,
            sector           TEXT,
            shares           REAL NOT NULL,
            entry_price      REAL NOT NULL,
            entry_date       TEXT NOT NULL,
            entry_signal_id  INTEGER,
            current_price    REAL,
            last_price_update TEXT,
            stop_loss        REAL NOT NULL,
            take_profit      REAL NOT NULL,
            status           TEXT DEFAULT 'open',
            exit_price       REAL,
            exit_date        TEXT,
            exit_reason      TEXT,
            pnl              REAL,
            pnl_pct          REAL,
            hold_days        INTEGER DEFAULT 0,
            tiers_triggered  INTEGER DEFAULT 0,
            notes            TEXT
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp        TEXT NOT NULL,
            ticker           TEXT NOT NULL,
            action           TEXT NOT NULL,
            shares           REAL NOT NULL,
            price            REAL NOT NULL,
            value            REAL NOT NULL,
            position_id      INTEGER REFERENCES positions(id),
            signal_id        INTEGER,
            reason           TEXT,
            cash_before      REAL,
            cash_after       REAL
        );

        CREATE TABLE IF NOT EXISTS recommendations (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            generated_at     TEXT NOT NULL,
            ticker           TEXT NOT NULL,
            action           TEXT NOT NULL,
            sector           TEXT,
            signal_count     INTEGER DEFAULT 0,
            avg_confidence   REAL DEFAULT 0,
            suggested_shares REAL,
            suggested_value  REAL,
            rationale        TEXT,
            status           TEXT DEFAULT 'pending',
            executed_at      TEXT,
            position_id      INTEGER REFERENCES positions(id)
        );

        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_at      TEXT NOT NULL,
            cash             REAL NOT NULL,
            positions_value  REAL NOT NULL,
            total_value      REAL NOT NULL,
            total_return_pct REAL NOT NULL,
            open_positions   INTEGER DEFAULT 0,
            notes            TEXT
        );
    """)

    # Seed starting capital if not already done
    existing = conn.execute(
        "SELECT COUNT(*) FROM cash_ledger"
    ).fetchone()[0]

    if existing == 0:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO cash_ledger (timestamp, amount, balance, description)
            VALUES (?, ?, ?, 'Initial capital')
        """, (now, CONFIG["starting_capital"], CONFIG["starting_capital"]))
        conn.commit()
        log.info(f"Portfolio initialized with ${CONFIG['starting_capital']:,.2f}")

    return conn


# ─── Cash management ──────────────────────────────────────────────────────────

def get_cash_balance(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT balance FROM cash_ledger ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else 0.0


def update_cash(conn: sqlite3.Connection, amount: float,
                description: str) -> float:
    current = get_cash_balance(conn)
    new_balance = current + amount
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO cash_ledger (timestamp, amount, balance, description)
        VALUES (?, ?, ?, ?)
    """, (now, amount, new_balance, description))
    conn.commit()
    return new_balance


# ─── Position management ──────────────────────────────────────────────────────

def get_open_positions(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT id, ticker, sector, shares, entry_price, entry_date,
               current_price, stop_loss, take_profit, hold_days, tiers_triggered, notes
        FROM positions WHERE status = 'open'
        ORDER BY entry_date ASC
    """).fetchall()
    return [
        {
            "id":            row[0],
            "ticker":        row[1],
            "sector":        row[2],
            "shares":        row[3],
            "entry_price":   row[4],
            "entry_date":    row[5],
            "current_price": row[6] or row[4],
            "stop_loss":     row[7],
            "take_profit":   row[8],
            "hold_days":     row[9],
            "tiers_triggered": row[10],
            "notes":         row[11],
            "cost_basis":    round(row[3] * row[4], 2),
            "current_value": round(row[3] * (row[6] or row[4]), 2),
            "unrealized_pnl": round(row[3] * ((row[6] or row[4]) - row[4]), 2),
            "unrealized_pct": round(((row[6] or row[4]) - row[4]) / row[4] * 100, 2),
        }
        for row in rows
    ]


def get_positions_value(conn: sqlite3.Connection) -> float:
    positions = get_open_positions(conn)
    return sum(p["current_value"] for p in positions)


def get_sector_exposure(conn: sqlite3.Connection) -> dict:
    positions = get_open_positions(conn)
    total = get_portfolio_value(conn)
    exposure = {}
    for p in positions:
        sector = p["sector"] or "unknown"
        exposure[sector] = exposure.get(sector, 0) + p["current_value"]
    return {k: round(v / total * 100, 1) for k, v in exposure.items()}


def get_portfolio_value(conn: sqlite3.Connection) -> float:
    return get_cash_balance(conn) + get_positions_value(conn)


def positions_this_week(conn: sqlite3.Connection) -> int:
    """Count BUY trades since Monday 00:00 ET (calendar week reset)"""
    now = datetime.now(timezone.utc)
    # Find most recent Monday midnight UTC
    days_since_monday = now.weekday()  # Mon=0, Sun=6
    monday_midnight = (now - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return conn.execute("""
        SELECT COUNT(*) FROM transactions
        WHERE action = 'BUY' AND timestamp >= ?
    """, (monday_midnight.isoformat(),)).fetchone()[0]


# ─── Position sizing ──────────────────────────────────────────────────────────

def calculate_position_size(confidence: float,
                             current_price: float,
                             portfolio_value: float,
                             cash: float) -> dict:
    """Calculate position size based on confidence tier"""
    # Determine position percentage from confidence
    pos_pct = 0.04  # default
    for tier, (low, high, pct) in CONFIG["confidence_tiers"].items():
        if low <= confidence < high:
            pos_pct = pct
            break

    # Cap at max position size
    pos_pct = min(pos_pct, CONFIG["max_position_pct"])

    # Calculate dollar amount
    target_value = portfolio_value * pos_pct

    # Ensure we don't exceed available cash minus reserve
    available_cash = cash - (portfolio_value * CONFIG["min_cash_reserve_pct"])
    target_value = min(target_value, available_cash)

    if target_value <= 0 or current_price <= 0:
        return {"shares": 0, "value": 0, "position_pct": 0}

    shares = int(target_value / current_price)  # whole shares only
    actual_value = shares * current_price

    return {
        "shares":       shares,
        "value":        round(actual_value, 2),
        "position_pct": round(actual_value / portfolio_value * 100, 1),
        "stop_loss":    round(current_price * (1 - CONFIG["stop_loss_pct"]), 2),
        "take_profit":  round(current_price * (1 + CONFIG["profit_tiers"][0][0]), 2),
    }


# ─── Trade execution ──────────────────────────────────────────────────────────

def open_position(conn: sqlite3.Connection, ticker: str, sector: str,
                  shares: int, entry_price: float,
                  signal_id: int = None, notes: str = "") -> int:
    """Open a new paper position"""
    now   = datetime.now(timezone.utc).isoformat()
    value = shares * entry_price
    cash  = get_cash_balance(conn)

    if value > cash:
        log.warning(f"Insufficient cash: need ${value:.2f}, have ${cash:.2f}")
        return -1

    # Use tiered stop loss based on position type
    # Determine type from ticker — ETFs are known symbols
    ETF_TICKERS = {"SPY","QQQ","XLE","XLK","XLF","XLV","XLU","XLI","XLB",
                   "XLP","XLY","ITA","VNQ","SOXX","AIQ","XOP","MOO","DJP"}
    if ticker.upper() in ETF_TICKERS:
        sl_pct = CONFIG["stop_loss_by_type"]["etf"]
    else:
        sl_pct = CONFIG["stop_loss_by_type"]["stock"]

    stop_loss   = round(entry_price * (1 - sl_pct), 2)
    take_profit = round(entry_price * (1 + CONFIG["profit_tiers"][0][0]), 2)

    cursor = conn.execute("""
        INSERT INTO positions
        (ticker, sector, shares, entry_price, entry_date, entry_signal_id,
         current_price, stop_loss, take_profit, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (ticker, sector, shares, entry_price, now, signal_id,
          entry_price, stop_loss, take_profit, notes))
    position_id = cursor.lastrowid

    # Record transaction
    new_cash = update_cash(conn, -value, f"BUY {shares} {ticker} @ ${entry_price}")
    conn.execute("""
        INSERT INTO transactions
        (timestamp, ticker, action, shares, price, value,
         position_id, signal_id, reason, cash_before, cash_after)
        VALUES (?, ?, 'BUY', ?, ?, ?, ?, ?, 'signal', ?, ?)
    """, (now, ticker, shares, entry_price, value,
          position_id, signal_id, cash, new_cash))

    conn.commit()
    log.info(f"OPENED: {shares} {ticker} @ ${entry_price:.2f} "
             f"(value=${value:.2f}, SL=${stop_loss}, TP=${take_profit})")
    return position_id


def close_position(conn: sqlite3.Connection, position_id: int,
                   exit_price: float, reason: str) -> dict:
    """Close a paper position"""
    pos = conn.execute("""
        SELECT ticker, shares, entry_price, sector
        FROM positions WHERE id = ? AND status = 'open'
    """, (position_id,)).fetchone()

    if not pos:
        log.error(f"Position #{position_id} not found or already closed")
        return {}

    ticker, shares, entry_price, sector = pos
    now    = datetime.now(timezone.utc).isoformat()
    value  = shares * exit_price
    pnl    = (exit_price - entry_price) * shares
    pnl_pct = (exit_price - entry_price) / entry_price * 100
    cash   = get_cash_balance(conn)

    conn.execute("""
        UPDATE positions
        SET status='closed', exit_price=?, exit_date=?,
            exit_reason=?, pnl=?, pnl_pct=?
        WHERE id=?
    """, (exit_price, now, reason, round(pnl, 2), round(pnl_pct, 2), position_id))

    new_cash = update_cash(conn, value, f"SELL {shares} {ticker} @ ${exit_price} ({reason})")
    conn.execute("""
        INSERT INTO transactions
        (timestamp, ticker, action, shares, price, value,
         position_id, reason, cash_before, cash_after)
        VALUES (?, ?, 'SELL', ?, ?, ?, ?, ?, ?, ?)
    """, (now, ticker, shares, exit_price, value,
          position_id, reason, cash, new_cash))

    conn.commit()
    outcome = "WIN" if pnl > 0 else "LOSS"
    log.info(f"CLOSED: {shares} {ticker} @ ${exit_price:.2f} "
             f"P&L=${pnl:+.2f} ({pnl_pct:+.1f}%) [{reason}] {outcome}")

    return {
        "ticker":   ticker,
        "shares":   shares,
        "entry":    entry_price,
        "exit":     exit_price,
        "pnl":      round(pnl, 2),
        "pnl_pct":  round(pnl_pct, 2),
        "reason":   reason,
        "outcome":  outcome
    }


def partial_close_position(conn: sqlite3.Connection,
                           position_id: int,
                           exit_price: float,
                           fraction: float,
                           reason: str,
                           new_stop_loss: float = None) -> dict:
    """
    Partially close a position — sell a fraction of shares
    Optionally move stop loss up to lock in gains
    """
    pos = conn.execute("""
        SELECT ticker, shares, entry_price, sector, stop_loss
        FROM positions WHERE id = ? AND status = 'open'
    """, (position_id,)).fetchone()

    if not pos:
        log.error(f"Position #{position_id} not found or already closed")
        return {}

    ticker, total_shares, entry_price, sector, current_stop = pos
    now         = datetime.now(timezone.utc).isoformat()
    shares_sell = max(1, int(total_shares * fraction))
    shares_keep = total_shares - shares_sell
    value       = shares_sell * exit_price
    pnl         = (exit_price - entry_price) * shares_sell
    pnl_pct     = (exit_price - entry_price) / entry_price * 100
    cash        = get_cash_balance(conn)

    # Update position — reduce shares, optionally move stop loss
    if shares_keep > 0:
        update_sql = "UPDATE positions SET shares = ?"
        params     = [shares_keep]
        if new_stop_loss:
            update_sql += ", stop_loss = ?"
            params.append(new_stop_loss)
            log.info(f"Stop loss moved: ${current_stop:.2f} → ${new_stop_loss:.2f}")
        update_sql += " WHERE id = ?"
        params.append(position_id)
        conn.execute(update_sql, params)
    else:
        # All shares sold — close position fully
        conn.execute("""
            UPDATE positions
            SET status='closed', exit_price=?, exit_date=?,
                exit_reason=?, pnl=?, pnl_pct=?, shares=0
            WHERE id=?
        """, (exit_price, now, reason,
              round((exit_price - entry_price) * total_shares, 2),
              round(pnl_pct, 2), position_id))

    # Record cash and transaction
    new_cash = update_cash(conn, value,
                           f"PARTIAL SELL {shares_sell}/{total_shares} "
                           f"{ticker} @ ${exit_price} ({reason})")
    conn.execute("""
        INSERT INTO transactions
        (timestamp, ticker, action, shares, price, value,
         position_id, reason, cash_before, cash_after)
        VALUES (?, ?, 'PARTIAL_SELL', ?, ?, ?, ?, ?, ?, ?)
    """, (now, ticker, shares_sell, exit_price, value,
          position_id, reason, cash, new_cash))

    conn.commit()

    log.info(f"PARTIAL CLOSE: sold {shares_sell}/{total_shares} {ticker} "
             f"@ ${exit_price:.2f} P&L=${pnl:+.2f} ({pnl_pct:+.1f}%) "
             f"| {shares_keep} shares remain")

    return {
        "ticker":        ticker,
        "shares_sold":   shares_sell,
        "shares_remain": shares_keep,
        "entry":         entry_price,
        "exit":          exit_price,
        "pnl":           round(pnl, 2),
        "pnl_pct":       round(pnl_pct, 2),
        "new_stop":      new_stop_loss,
        "reason":        reason,
    }


# ─── Snapshot ─────────────────────────────────────────────────────────────────

def get_reentry_status(conn: sqlite3.Connection,
                       ticker: str) -> dict:
    """
    Check if a ticker is eligible for re-entry based on exit history.
    Returns dict with eligible bool and any modified thresholds.
    """
    from datetime import datetime, timezone, timedelta

    # Find most recent closed position for this ticker
    row = conn.execute("""
        SELECT exit_reason, exit_date, pnl_pct
        FROM positions
        WHERE ticker = ? AND status = 'closed'
        ORDER BY exit_date DESC LIMIT 1
    """, (ticker,)).fetchone()

    # No history — normal entry criteria
    if not row:
        return {"eligible": True, "min_signals": 2, "min_confidence": 0.60,
                "reason": "no prior position"}

    exit_reason, exit_date, pnl_pct = row

    # Parse exit date
    try:
        exited = datetime.fromisoformat(exit_date.replace("Z", "+00:00"))
    except:
        return {"eligible": True, "min_signals": 2, "min_confidence": 0.60,
                "reason": "could not parse exit date"}

    now = datetime.now(timezone.utc)
    days_since_exit = (now - exited).total_seconds() / 86400

    # Determine which rule applies
    rules = CONFIG["reentry_rules"]
    if "stop_loss" in (exit_reason or ""):
        rule = rules["stop_loss"]
        rule_name = "stop_loss"
    elif "time_exit" in (exit_reason or ""):
        rule = rules["time_exit"]
        rule_name = "time_exit"
    else:
        rule = rules["take_profit"]
        rule_name = "take_profit"

    cooldown = rule["cooldown_days"]

    # Check cooldown
    if days_since_exit < cooldown:
        days_remaining = cooldown - days_since_exit
        return {
            "eligible":       False,
            "min_signals":    rule["min_signals"],
            "min_confidence": rule["min_confidence"],
            "reason":         f"{rule_name} cooldown — {days_remaining:.1f} days remaining",
            "exit_reason":    exit_reason,
            "pnl_pct":        pnl_pct,
        }

    return {
        "eligible":       True,
        "min_signals":    rule["min_signals"],
        "min_confidence": rule["min_confidence"],
        "reason":         f"{rule_name} exit {days_since_exit:.1f} days ago — eligible",
        "exit_reason":    exit_reason,
        "pnl_pct":        pnl_pct,
    }


def take_snapshot(conn: sqlite3.Connection) -> dict:
    cash       = get_cash_balance(conn)
    pos_value  = get_positions_value(conn)
    total      = cash + pos_value
    ret_pct    = (total - CONFIG["starting_capital"]) / CONFIG["starting_capital"] * 100
    open_pos   = len(get_open_positions(conn))
    now        = datetime.now(timezone.utc).isoformat()

    conn.execute("""
        INSERT INTO portfolio_snapshots
        (snapshot_at, cash, positions_value, total_value,
         total_return_pct, open_positions)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (now, cash, pos_value, total, round(ret_pct, 3), open_pos))
    conn.commit()

    return {
        "cash":           round(cash, 2),
        "positions":      round(pos_value, 2),
        "total":          round(total, 2),
        "return_pct":     round(ret_pct, 3),
        "open_positions": open_pos,
    }


if __name__ == "__main__":
    pass  # logging configured by entry point
    conn = init_db()
    snap = take_snapshot(conn)
    print(f"\nPortfolio initialized:")
    print(f"  Cash:       ${snap['cash']:,.2f}")
    print(f"  Positions:  ${snap['positions']:,.2f}")
    print(f"  Total:      ${snap['total']:,.2f}")
    print(f"  Return:     {snap['return_pct']:+.2f}%")
    conn.close()