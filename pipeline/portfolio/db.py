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
    "starting_capital":     100_000.00,
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
    "max_hold_days":        15,        # extended from 10 — give positions more time to developding days
    "max_new_positions_week": 5,       # max 5 new positions per week — quality over quantity
    "max_open_positions":   12,        # max 12 simultaneous open positions
    "drawdown_circuit_breaker_pct": 0.05,   # portfolio freeze only on catastrophic loss (5%+)
    "sector_breaker_stops": 2,              # pause sector after N stop losses in 7 days
    "concentration_max_pct":     80.0,     # 80% — reject if >80% closed P&L from <=min_trades tickers
    "concentration_min_trades":  3,        # max tickers allowed to dominate before gate triggers
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
        (0.04, 0.33, "breakeven"),   # +4%: sell 33%, stop → breakeven (was 5%)
        (0.08, 0.33, "previous_tier"),  # +8%:  sell 33%, stop → +5%
        (0.12, 1.00, "previous_tier"),  # +12%: sell remaining, stop → +8%
    ],
}

# ─── Theta-gang risk defaults ────────────────────────────────────────────────

THETA_CONFIG = {
    "theta_eligibility_threshold": 0.65,        # score floor to enter options mode
    "assignment_risk_limit_pct":   0.15,        # max 15% of portfolio in theta exposure
    "early_close_gap_threshold":   0.05,        # close if underlying gaps >5% overnight
    "iv_crush_dte_threshold":      7,           # flag IV crush risk when DTE < 7
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

        CREATE TABLE IF NOT EXISTS theta_positions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker              TEXT NOT NULL,
            sector              TEXT,
            instrument_type     TEXT NOT NULL,  -- 'covered_call' | 'cash_secured_put'
            strike              REAL NOT NULL,
            expiry              TEXT NOT NULL,   -- ISO date string
            premium_collected   REAL NOT NULL,
            entry_date          TEXT NOT NULL,
            status              TEXT DEFAULT 'open',  -- 'open' | 'closed' | 'assigned'
            assignment_event    TEXT,             -- 'early_close' | 'exercised' | 'expired'
            exit_date           TEXT,
            exit_price          REAL,
            pnl                 REAL,
            pnl_pct             REAL,
            notes               TEXT
        );

        CREATE TABLE IF NOT EXISTS theta_assignment_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            sector        TEXT NOT NULL,
            ticker        TEXT NOT NULL,
            event_date    TEXT NOT NULL,
            event_type    TEXT NOT NULL,  -- 'assignment' | 'early_close' | 'expiry'
            theta_position_id INTEGER REFERENCES theta_positions(id)
        );

        CREATE TABLE IF NOT EXISTS theta_risk_params (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            sector                    TEXT NOT NULL UNIQUE,
            theta_eligibility_score   REAL DEFAULT 0.0,
            assignment_count          INTEGER DEFAULT 0,
            assignment_rate           REAL DEFAULT 0.0,
            assignment_risk_limit_pct REAL DEFAULT 0.15,
            early_close_gap_threshold REAL DEFAULT 0.05,
            iv_crush_dte_threshold    INTEGER DEFAULT 7,
            updated_at                TEXT NOT NULL
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

    # Dynamic stop loss based on sector volatility
    # Hermes rec 2026-08-31: static stops get head-faked in volatile sectors
    ETF_TICKERS = {"SPY","QQQ","XLE","XLK","XLF","XLV","XLU","XLI","XLB",
                   "XLP","XLY","ITA","VNQ","SOXX","AIQ","XOP","MOO","DJP"}
    if ticker.upper() in ETF_TICKERS:
        base_sl_pct = CONFIG["stop_loss_by_type"]["etf"]
    else:
        base_sl_pct = CONFIG["stop_loss_by_type"]["stock"]

    # Sector volatility multipliers — high vol sectors get more room
    sector_vol_multipliers = {
        "technology":  1.5,
        "energy":      1.4,
        "materials":   1.3,
        "financials":  1.2,
        "healthcare":  1.1,
        "consumer":    1.0,
        "industrials": 1.0,
        "defense":     0.9,
        "macro":       0.8,
    }
    vol_mult = sector_vol_multipliers.get(sector, 1.0)
    sl_pct = min(base_sl_pct * vol_mult, 0.10)  # cap at 10%

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


# ─── Theta-gang position tracking ────────────────────────────────────────────

def open_theta_position(conn: sqlite3.Connection,
                        ticker: str,
                        sector: str,
                        instrument_type: str,
                        strike: float,
                        expiry: str,
                        premium_collected: float,
                        notes: str = "",
                        option_symbol: str = "") -> int:
    """Open a theta-gang position (covered call or cash-secured put)"""
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute("""
        INSERT INTO theta_positions
        (ticker, sector, instrument_type, strike, expiry,
         premium_collected, entry_date, notes, option_symbol)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (ticker, sector, instrument_type, strike, expiry,
          premium_collected, now, notes, option_symbol))
    position_id = cursor.lastrowid
    log.info(f"THETA OPENED: {instrument_type} {ticker} strike=${strike:.2f} "
             f"expiry={expiry} premium=${premium_collected:.2f}")
    return position_id


def close_theta_position(conn: sqlite3.Connection,
                         position_id: int,
                         assignment_event: str,
                         exit_price: float = None,
                         notes: str = "") -> dict:
    """Close a theta position with assignment event tracking"""
    pos = conn.execute("""
        SELECT id, ticker, sector, instrument_type, strike,
               premium_collected, entry_date
        FROM theta_positions WHERE id = ? AND status = 'open'
    """, (position_id,)).fetchone()

    if not pos:
        log.error(f"Theta position #{position_id} not found or already closed")
        return {}

    pos_id, ticker, sector, instrument_type, strike, premium, entry_date = pos
    now = datetime.now(timezone.utc).isoformat()

    # Calculate P&L
    pnl = premium
    if exit_price is not None:
        # For early close: exit_price is the price paid to buy back the option
        pnl = premium - abs(exit_price)
    pnl_pct = (pnl / premium * 100) if premium else 0.0

    new_status = "assigned" if assignment_event == "exercised" else "closed"

    conn.execute("""
        UPDATE theta_positions
        SET status=?, assignment_event=?, exit_date=?,
            exit_price=?, pnl=?, pnl_pct=?, notes=?
        WHERE id=?
    """, (new_status, assignment_event, now,
          exit_price, round(pnl, 2), round(pnl_pct, 2), notes, position_id))

    # Record assignment event in tracking table
    conn.execute("""
        INSERT INTO theta_assignment_events
        (sector, ticker, event_date, event_type, theta_position_id)
        VALUES (?, ?, ?, ?, ?)
    """, (sector, ticker, now, assignment_event, position_id))

    # Increment assignment count for this sector
    _increment_sector_assignment_count(conn, sector)

    conn.commit()
    log.info(f"THETA CLOSED: {ticker} {assignment_event} "
             f"P&L=${pnl:+.2f} ({pnl_pct:+.1f}%)")

    return {
        "ticker":          ticker,
        "sector":          sector,
        "instrument_type": instrument_type,
        "strike":          strike,
        "premium":         premium,
        "assignment_event": assignment_event,
        "pnl":             round(pnl, 2),
        "pnl_pct":         round(pnl_pct, 2),
    }


def reserve_cash_for_put(conn, ticker: str, strike: float,
                          option_symbol: str = "", notes: str = "") -> None:
    """
    Reserve cash for a cash-secured put position.
    Deducts strike * 100 from available cash balance.
    Called after confirmed fill of STO order.
    """
    reserved = round(strike * 100, 2)
    now = datetime.now(timezone.utc).isoformat()
    last = conn.execute(
        "SELECT balance FROM cash_ledger ORDER BY id DESC LIMIT 1"
    ).fetchone()
    current_balance = float(last[0]) if last else 0.0
    new_balance = round(current_balance - reserved, 2)
    conn.execute("""
        INSERT INTO cash_ledger (timestamp, amount, balance, description)
        VALUES (?, ?, ?, ?)
    """, (now, -reserved, new_balance,
          f"THETA RESERVE: CSP {ticker} strike=${strike:.2f} {option_symbol}"))
    conn.commit()
    log.info(f"[THETA] Cash reserved: ${reserved:.2f} for {ticker} CSP "
             f"(balance ${current_balance:.2f} → ${new_balance:.2f})")


def release_cash_for_put(conn, ticker: str, strike: float,
                          premium_collected: float = 0,
                          notes: str = "") -> None:
    """
    Release reserved cash when CSP closes (profit close, expired, or assigned).
    Returns strike * 100 to available cash, minus any assignment cost.
    """
    reserved = round(strike * 100, 2)
    now = datetime.now(timezone.utc).isoformat()
    last = conn.execute(
        "SELECT balance FROM cash_ledger ORDER BY id DESC LIMIT 1"
    ).fetchone()
    current_balance = float(last[0]) if last else 0.0
    new_balance = round(current_balance + reserved, 2)
    conn.execute("""
        INSERT INTO cash_ledger (timestamp, amount, balance, description)
        VALUES (?, ?, ?, ?)
    """, (now, reserved, new_balance,
          f"THETA RELEASE: CSP {ticker} strike=${strike:.2f} {notes}"))
    conn.commit()
    log.info(f"[THETA] Cash released: ${reserved:.2f} for {ticker} CSP "
             f"(balance ${current_balance:.2f} → ${new_balance:.2f})")


def cancel_expired_theta_positions(conn) -> int:
    """
    Mark theta positions as cancelled if their DAY order expired unfilled.
    Called during portfolio cycle. Returns count of cancelled positions.
    """
    import os
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")

        client = TradingClient(
            os.getenv("ALPACA_API_KEY", ""),
            os.getenv("ALPACA_SECRET_KEY", ""),
            paper=True
        )

        # Get open theta positions that haven't been confirmed filled
        rows = conn.execute("""
            SELECT id, ticker, strike, option_symbol, premium_collected
            FROM theta_positions
            WHERE status = 'open'
            AND notes NOT LIKE '%confirmed_fill%'
        """).fetchall()

        if not rows:
            return 0

        # Get closed orders to check for expired/cancelled
        closed_orders = client.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=50)
        )
        closed_by_symbol = {str(o.symbol): o for o in closed_orders}

        # Get open orders to check pending
        open_orders = client.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.OPEN)
        )
        open_symbols = {str(o.symbol) for o in open_orders}

        cancelled = 0
        now = datetime.now(timezone.utc).isoformat()

        for pos_id, ticker, strike, option_symbol, premium in rows:
            if not option_symbol:
                continue

            if option_symbol in open_symbols:
                # Still pending — leave it
                continue

            if option_symbol in closed_by_symbol:
                order = closed_by_symbol[option_symbol]
                status = str(order.status).lower()

                if "filled" in status and float(order.filled_qty or 0) > 0:
                    # Confirmed fill — update actual fill price, reserve cash
                    actual_premium = float(order.filled_avg_price or premium)
                    reserve_cash_for_put(conn, ticker, strike, option_symbol,
                                         f"confirmed fill @ {actual_premium:.2f}")
                    conn.execute("""
                        UPDATE theta_positions
                        SET notes = notes || ' | confirmed_fill',
                            premium_collected = ?
                        WHERE id = ?
                    """, (actual_premium, pos_id))
                    conn.commit()
                    log.info(f"[THETA] Confirmed fill: {option_symbol} "
                             f"premium=${actual_premium:.2f} — cash reserved")

                elif "expired" in status or "cancelled" in status:
                    # Order expired unfilled — cancel the DB position
                    conn.execute("""
                        UPDATE theta_positions
                        SET status = 'cancelled',
                            exit_date = ?,
                            notes = notes || ' | order_expired_unfilled'
                        WHERE id = ?
                    """, (now, pos_id))
                    conn.commit()
                    cancelled += 1
                    log.info(f"[THETA] Position cancelled (order expired): {option_symbol}")

            # Check for assignment — option closed but equity position appeared
            # Assignment: short put assigned = forced to buy 100 shares
            try:
                alpaca_positions = {p.symbol: p for p in client.get_all_positions()}
                # Check if underlying equity appeared unexpectedly
                if ticker in alpaca_positions and instrument_type == "cash_secured_put":
                    eq_pos = alpaca_positions[ticker]
                    eq_qty = float(eq_pos.qty)
                    # Check if we already have this as an open equity position
                    existing = conn.execute("""
                        SELECT id FROM positions
                        WHERE ticker = ? AND status = 'open'
                    """, (ticker,)).fetchone()
                    if not existing and eq_qty >= 100:
                        # Assignment detected — create equity position
                        avg_cost = float(eq_pos.avg_entry_price)
                        log.info(f"[THETA] ASSIGNMENT DETECTED: {ticker} "
                                 f"{eq_qty:.0f} shares @ ${avg_cost:.2f}")
                        # Close theta position as assigned
                        conn.execute("""
                            UPDATE theta_positions
                            SET status = 'assigned',
                                assignment_event = 'exercised',
                                exit_date = ?,
                                exit_price = 0.0,
                                pnl = ?,
                                notes = notes || ' | ASSIGNED'
                            WHERE id = ?
                        """, (now, premium * 100, pos_id))
                        # Record assignment event
                        conn.execute("""
                            INSERT INTO theta_assignment_events
                            (position_id, ticker, sector, strike, expiry,
                             event_type, event_date, notes)
                            VALUES (?, ?, ?, ?, ?, 'assignment', ?, ?)
                        """, (pos_id, ticker, sector, strike, expiry,
                              now, f"assigned {eq_qty:.0f} shares @ ${avg_cost:.2f}"))
                        # Release reserved cash (it was spent on shares)
                        release_cash_for_put(conn, ticker, strike,
                                             notes="assignment — cash spent on shares")
                        # Create equity position in portfolio DB
                        cost_basis = round(avg_cost * eq_qty, 2)
                        conn.execute("""
                            INSERT INTO positions
                            (ticker, sector, shares, entry_price, entry_date,
                             current_price, stop_loss, cost_basis, status, notes)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
                        """, (ticker, sector, eq_qty, avg_cost, now,
                              avg_cost,
                              round(avg_cost * 0.95, 2),  # 5% stop loss
                              cost_basis,
                              f"Theta assignment: CSP strike=${strike:.2f} premium=${premium:.2f}"))
                        conn.commit()
                        log.info(f"[THETA] Assignment processed: {ticker} position "
                                 f"created at ${avg_cost:.2f}, stop at "
                                 f"${avg_cost*0.95:.2f}")
            except Exception as _ae:
                log.warning(f"[THETA] Assignment check failed for {ticker}: {_ae}")

        return cancelled

    except Exception as e:
        log.warning(f"[THETA] Cancel expired positions failed: {e}")
        return 0


def get_theta_history(conn: sqlite3.Connection, sector: str) -> list[dict]:
    """Get all theta positions for a sector, ordered by entry date desc"""
    rows = conn.execute("""
        SELECT id, ticker, sector, instrument_type, strike, expiry,
               premium_collected, entry_date, status, assignment_event,
               exit_date, exit_price, pnl, pnl_pct, notes
        FROM theta_positions
        WHERE sector = ?
        ORDER BY entry_date DESC
    """, (sector,)).fetchall()

    return [
        {
            "id":               row[0],
            "ticker":           row[1],
            "sector":           row[2],
            "instrument_type":  row[3],
            "strike":           row[4],
            "expiry":           row[5],
            "premium_collected": row[6],
            "entry_date":       row[7],
            "status":           row[8],
            "assignment_event": row[9],
            "exit_date":        row[10],
            "exit_price":       row[11],
            "pnl":              row[12],
            "pnl_pct":          row[13],
            "notes":            row[14],
        }
        for row in rows
    ]


def get_theta_stats(conn: sqlite3.Connection, sector: str) -> dict:
    """Get aggregated theta stats for a sector"""
    # Risk params (per-sector, fall back to defaults)
    param_row = conn.execute("""
        SELECT theta_eligibility_score, assignment_count, assignment_rate,
               assignment_risk_limit_pct, early_close_gap_threshold,
               iv_crush_dte_threshold
        FROM theta_risk_params WHERE sector = ?
    """, (sector,)).fetchone()

    # Trade counts
    total = conn.execute("""
        SELECT COUNT(*) FROM theta_positions WHERE sector = ?
    """, (sector,)).fetchone()[0]

    open_count = conn.execute("""
        SELECT COUNT(*) FROM theta_positions
        WHERE sector = ? AND status = 'open'
    """, (sector,)).fetchone()[0]

    # Assignment events
    assignment_count = conn.execute("""
        SELECT COUNT(*) FROM theta_assignment_events WHERE sector = ?
    """, (sector,)).fetchone()[0]

    # P&L aggregation
    pnl_row = conn.execute("""
        SELECT COALESCE(SUM(pnl), 0), COALESCE(AVG(pnl), 0),
               COALESCE(SUM(pnl_pct), 0) / COUNT(*)
        FROM theta_positions
        WHERE sector = ? AND pnl IS NOT NULL
    """, (sector,)).fetchone()
    total_pnl = pnl_row[0]
    avg_pnl = pnl_row[1]
    avg_pnl_pct = pnl_row[2] if pnl_row[2] else 0.0

    # Assignment rate
    assignment_rate = (assignment_count / total * 100) if total > 0 else 0.0

    # Per-sector risk params or defaults
    if param_row:
        eligibility_score = param_row[0]
        stored_count = param_row[1] or 0
        stored_rate = param_row[2] or 0.0
        risk_limit = param_row[3] or THETA_CONFIG["assignment_risk_limit_pct"]
        gap_thresh = param_row[4] or THETA_CONFIG["early_close_gap_threshold"]
        iv_dte = param_row[5] or THETA_CONFIG["iv_crush_dte_threshold"]
    else:
        eligibility_score = 0.0
        stored_count = 0
        stored_rate = 0.0
        risk_limit = THETA_CONFIG["assignment_risk_limit_pct"]
        gap_thresh = THETA_CONFIG["early_close_gap_threshold"]
        iv_dte = THETA_CONFIG["iv_crush_dte_threshold"]

    return {
        "sector":                   sector,
        "theta_eligibility_score":  eligibility_score,
        "total_theta_trades":       total,
        "open_theta_trades":        open_count,
        "assignment_count":         assignment_count,
        "assignment_rate_pct":      round(assignment_rate, 2),
        "total_pnl":                round(total_pnl, 2),
        "avg_pnl":                  round(avg_pnl, 2),
        "avg_pnl_pct":              round(avg_pnl_pct, 2),
        "assignment_risk_limit_pct": risk_limit,
        "early_close_gap_threshold": gap_thresh,
        "iv_crush_dte_threshold":    iv_dte,
    }


def update_assignment_event(conn: sqlite3.Connection,
                            sector: str,
                            ticker: str,
                            event_type: str,
                            theta_position_id: int = None) -> None:
    """
    Record an assignment event and update sector-level tracking.
    event_type: 'assignment' | 'early_close' | 'expiry'
    """
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO theta_assignment_events
        (sector, ticker, event_date, event_type, theta_position_id)
        VALUES (?, ?, ?, ?, ?)
    """, (sector, ticker, now, event_type, theta_position_id))

    _increment_sector_assignment_count(conn, sector)
    _update_sector_assignment_rate(conn, sector)
    conn.commit()


def update_theta_risk_params(conn: sqlite3.Connection,
                             sector: str,
                             theta_eligibility_score: float = None,
                             assignment_risk_limit_pct: float = None,
                             early_close_gap_threshold: float = None,
                             iv_crush_dte_threshold: int = None) -> None:
    """Set or update per-sector theta risk parameters"""
    now = datetime.now(timezone.utc).isoformat()

    # Get current row or use defaults
    existing = conn.execute("""
        SELECT theta_eligibility_score, assignment_risk_limit_pct,
               early_close_gap_threshold, iv_crush_dte_threshold
        FROM theta_risk_params WHERE sector = ?
    """, (sector,)).fetchone()

    if existing:
        score = theta_eligibility_score if theta_eligibility_score is not None else existing[0]
        risk = assignment_risk_limit_pct if assignment_risk_limit_pct is not None else (existing[1] or THETA_CONFIG["assignment_risk_limit_pct"])
        gap = early_close_gap_threshold if early_close_gap_threshold is not None else (existing[2] or THETA_CONFIG["early_close_gap_threshold"])
        iv_dte = iv_crush_dte_threshold if iv_crush_dte_threshold is not None else (existing[3] or THETA_CONFIG["iv_crush_dte_threshold"])

        conn.execute("""
            UPDATE theta_risk_params
            SET theta_eligibility_score=?,
                assignment_risk_limit_pct=?,
                early_close_gap_threshold=?,
                iv_crush_dte_threshold=?,
                updated_at=?
            WHERE sector=?
        """, (score, risk, gap, iv_dte, now, sector))
    else:
        score = theta_eligibility_score if theta_eligibility_score is not None else 0.0
        risk = assignment_risk_limit_pct or THETA_CONFIG["assignment_risk_limit_pct"]
        gap = early_close_gap_threshold or THETA_CONFIG["early_close_gap_threshold"]
        iv_dte = iv_crush_dte_threshold or THETA_CONFIG["iv_crush_dte_threshold"]

        conn.execute("""
            INSERT INTO theta_risk_params
            (sector, theta_eligibility_score, assignment_risk_limit_pct,
             early_close_gap_threshold, iv_crush_dte_threshold, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (sector, score, risk, gap, iv_dte, now))

    conn.commit()


def get_open_theta_positions(conn: sqlite3.Connection) -> list[dict]:
    """Get all open theta positions across all sectors"""
    rows = conn.execute("""
        SELECT id, ticker, sector, instrument_type, strike, expiry,
               premium_collected, entry_date, notes
        FROM theta_positions
        WHERE status = 'open'
        ORDER BY entry_date ASC
    """).fetchall()

    return [
        {
            "id":               row[0],
            "ticker":           row[1],
            "sector":           row[2],
            "instrument_type":  row[3],
            "strike":           row[4],
            "expiry":           row[5],
            "premium_collected": row[6],
            "entry_date":       row[7],
            "notes":            row[8],
        }
        for row in rows
    ]


def get_theta_exposure(conn: sqlite3.Connection) -> float:
    """Total dollar exposure of all open theta positions (premium collected)"""
    row = conn.execute("""
        SELECT COALESCE(SUM(premium_collected), 0)
        FROM theta_positions WHERE status = 'open'
    """).fetchone()
    return row[0]


# ─── Internal helpers (not exported) ─────────────────────────────────────────

def _increment_sector_assignment_count(conn: sqlite3.Connection, sector: str) -> None:
    """Increment the assignment count for a sector in theta_risk_params"""
    now = datetime.now(timezone.utc).isoformat()
    existing = conn.execute("""
        SELECT assignment_count FROM theta_risk_params WHERE sector = ?
    """, (sector,)).fetchone()

    if existing and existing[0] is not None:
        conn.execute("""
            UPDATE theta_risk_params
            SET assignment_count = assignment_count + 1, updated_at = ?
            WHERE sector = ?
        """, (now, sector))
    else:
        conn.execute("""
            INSERT OR REPLACE INTO theta_risk_params
            (sector, assignment_count, updated_at)
            VALUES (?, 1, ?)
        """, (sector, now))


def _update_sector_assignment_rate(conn: sqlite3.Connection, sector: str) -> None:
    """Recalculate assignment_rate = assignment_count / total_theta_trades for sector"""
    now = datetime.now(timezone.utc).isoformat()
    total = conn.execute("""
        SELECT COUNT(*) FROM theta_positions WHERE sector = ?
    """, (sector,)).fetchone()[0]

    events = conn.execute("""
        SELECT COUNT(*) FROM theta_assignment_events WHERE sector = ?
    """, (sector,)).fetchone()[0]

    rate = events / total if total > 0 else 0.0

    conn.execute("""
        UPDATE theta_risk_params
        SET assignment_rate = ?, updated_at = ?
        WHERE sector = ?
    """, (rate, now, sector))


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
