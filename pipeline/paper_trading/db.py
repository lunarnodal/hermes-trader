#!/usr/bin/env python3
"""
Paper trading simulation database
Tracks simulated positions, P&L, and prediction accuracy
Provides feedback loop for tuning model confidence and rule weights
"""

import sqlite3
import json
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

DB_PATH = Path(os.environ.get("PAPER_DB_PATH",
               "/home/trading/trading-ai/data/paper_trading.db"))

log = logging.getLogger(__name__)


def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS predictions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at       TEXT NOT NULL,
            query            TEXT NOT NULL,
            timeframe        TEXT NOT NULL,
            direction        TEXT NOT NULL,
            probability      REAL NOT NULL,
            confidence       REAL NOT NULL,
            model_used       TEXT NOT NULL,
            rules_version    INTEGER DEFAULT 0,
            signals_used     INTEGER DEFAULT 0,
            key_risk         TEXT,
            reasoning_summary TEXT,
            prediction_file  TEXT,
            verified_at      TEXT,
            actual_direction TEXT,
            was_correct      INTEGER,
            actual_notes     TEXT
        );

        CREATE TABLE IF NOT EXISTS paper_trades (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at       TEXT NOT NULL,
            prediction_id    INTEGER REFERENCES predictions(id),
            ticker           TEXT NOT NULL,
            direction        TEXT NOT NULL,
            entry_price      REAL,
            entry_time       TEXT,
            exit_price       REAL,
            exit_time        TEXT,
            quantity         REAL DEFAULT 100,
            pnl              REAL,
            pnl_pct          REAL,
            status           TEXT DEFAULT 'open',
            notes            TEXT
        );

        CREATE TABLE IF NOT EXISTS rule_performance (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_trigger     TEXT NOT NULL,
            evaluated_at     TEXT NOT NULL,
            prediction_id    INTEGER REFERENCES predictions(id),
            was_correct      INTEGER,
            signal_confidence REAL,
            notes            TEXT
        );

        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_at      TEXT NOT NULL,
            total_predictions INTEGER DEFAULT 0,
            correct_predictions INTEGER DEFAULT 0,
            win_rate         REAL DEFAULT 0,
            avg_confidence   REAL DEFAULT 0,
            total_pnl        REAL DEFAULT 0,
            open_positions   INTEGER DEFAULT 0,
            notes            TEXT
        );
    """)
    conn.commit()
    return conn


# ─── Predictions ──────────────────────────────────────────────────────────────

def record_prediction(conn: sqlite3.Connection,
                      prediction: dict,
                      prediction_file: str = None) -> int:
    """Record a new prediction from the reasoning engine"""
    p = prediction.get("prediction", {})
    now = datetime.now(timezone.utc).isoformat()

    cursor = conn.execute("""
        INSERT INTO predictions
        (created_at, query, timeframe, direction, probability, confidence,
         model_used, signals_used, key_risk, reasoning_summary, prediction_file)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now,
        prediction.get("query", ""),
        prediction.get("timeframe", "24h"),
        p.get("direction", "neutral"),
        p.get("probability", 0.5),
        p.get("confidence", 0.5),
        prediction.get("model", "deepseek-r1:70b"),
        prediction.get("signals_used", 0),
        p.get("key_risk", ""),
        p.get("reasoning_summary", ""),
        prediction_file or ""
    ))
    conn.commit()
    pred_id = cursor.lastrowid
    log.info(f"Recorded prediction #{pred_id}: {p.get('direction')} "
             f"({p.get('probability', 0):.0%}) — {prediction.get('query','')[:50]}")
    return pred_id


def verify_prediction(conn: sqlite3.Connection,
                      prediction_id: int,
                      actual_direction: str,
                      notes: str = "") -> None:
    """Mark a prediction as verified with actual outcome"""
    now = datetime.now(timezone.utc).isoformat()

    predicted = conn.execute(
        "SELECT direction FROM predictions WHERE id = ?",
        (prediction_id,)
    ).fetchone()

    if not predicted:
        log.error(f"Prediction #{prediction_id} not found")
        return

    was_correct = 1 if predicted[0] == actual_direction else 0

    conn.execute("""
        UPDATE predictions
        SET verified_at = ?, actual_direction = ?,
            was_correct = ?, actual_notes = ?
        WHERE id = ?
    """, (now, actual_direction, was_correct, notes, prediction_id))
    conn.commit()

    result = "✓ CORRECT" if was_correct else "✗ WRONG"
    log.info(f"Prediction #{prediction_id} verified: {result} "
             f"(predicted={predicted[0]}, actual={actual_direction})")


# ─── Paper Trades ─────────────────────────────────────────────────────────────

def open_trade(conn: sqlite3.Connection,
               prediction_id: int,
               ticker: str,
               direction: str,
               entry_price: float,
               quantity: float = 100) -> int:
    """Open a simulated paper trade"""
    now = datetime.now(timezone.utc).isoformat()

    cursor = conn.execute("""
        INSERT INTO paper_trades
        (created_at, prediction_id, ticker, direction,
         entry_price, entry_time, quantity, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
    """, (now, prediction_id, ticker, direction,
          entry_price, now, quantity))
    conn.commit()
    trade_id = cursor.lastrowid
    log.info(f"Opened paper trade #{trade_id}: {direction} {quantity} {ticker} @ {entry_price}")
    return trade_id


def close_trade(conn: sqlite3.Connection,
                trade_id: int,
                exit_price: float,
                notes: str = "") -> dict:
    """Close a paper trade and calculate P&L"""
    now = datetime.now(timezone.utc).isoformat()

    trade = conn.execute(
        "SELECT ticker, direction, entry_price, quantity FROM paper_trades WHERE id = ?",
        (trade_id,)
    ).fetchone()

    if not trade:
        log.error(f"Trade #{trade_id} not found")
        return {}

    ticker, direction, entry_price, quantity = trade

    if direction == "long":
        pnl     = (exit_price - entry_price) * quantity
        pnl_pct = (exit_price - entry_price) / entry_price * 100
    else:  # short
        pnl     = (entry_price - exit_price) * quantity
        pnl_pct = (entry_price - exit_price) / entry_price * 100

    conn.execute("""
        UPDATE paper_trades
        SET exit_price = ?, exit_time = ?, pnl = ?,
            pnl_pct = ?, status = 'closed', notes = ?
        WHERE id = ?
    """, (exit_price, now, pnl, pnl_pct, notes, trade_id))
    conn.commit()

    result = {
        "trade_id":   trade_id,
        "ticker":     ticker,
        "direction":  direction,
        "entry":      entry_price,
        "exit":       exit_price,
        "pnl":        round(pnl, 2),
        "pnl_pct":    round(pnl_pct, 2),
        "outcome":    "win" if pnl > 0 else "loss"
    }
    log.info(f"Closed trade #{trade_id}: {ticker} P&L=${pnl:+.2f} ({pnl_pct:+.1f}%)")
    return result


# ─── Analytics ────────────────────────────────────────────────────────────────

def get_performance_summary(conn: sqlite3.Connection) -> dict:
    """Generate performance summary for tuning decisions"""

    # Prediction accuracy
    total = conn.execute(
        "SELECT COUNT(*) FROM predictions WHERE was_correct IS NOT NULL"
    ).fetchone()[0]
    correct = conn.execute(
        "SELECT COUNT(*) FROM predictions WHERE was_correct = 1"
    ).fetchone()[0]
    win_rate = correct / total if total > 0 else 0

    # P&L summary
    pnl_row = conn.execute("""
        SELECT COUNT(*), SUM(pnl), AVG(pnl_pct)
        FROM paper_trades WHERE status = 'closed'
    """).fetchone()
    trade_count = pnl_row[0] or 0
    total_pnl   = pnl_row[1] or 0
    avg_pnl_pct = pnl_row[2] or 0

    # Best/worst performing directions
    direction_stats = conn.execute("""
        SELECT direction, COUNT(*) as total,
               SUM(was_correct) as correct
        FROM predictions
        WHERE was_correct IS NOT NULL
        GROUP BY direction
    """).fetchall()

    # Confidence calibration — are high-confidence predictions more accurate?
    calibration = conn.execute("""
        SELECT
            CASE
                WHEN confidence >= 0.8 THEN 'high (>=0.8)'
                WHEN confidence >= 0.6 THEN 'medium (0.6-0.8)'
                ELSE 'low (<0.6)'
            END as conf_band,
            COUNT(*) as total,
            SUM(was_correct) as correct
        FROM predictions
        WHERE was_correct IS NOT NULL
        GROUP BY conf_band
    """).fetchall()

    return {
        "prediction_accuracy": {
            "total":      total,
            "correct":    correct,
            "win_rate":   round(win_rate, 3),
        },
        "paper_trading": {
            "total_trades": trade_count,
            "total_pnl":    round(total_pnl, 2),
            "avg_pnl_pct":  round(avg_pnl_pct, 2),
        },
        "by_direction": {
            row[0]: {"total": row[1], "correct": row[2] or 0}
            for row in direction_stats
        },
        "confidence_calibration": {
            row[0]: {
                "total":    row[1],
                "correct":  row[2] or 0,
                "accuracy": round((row[2] or 0) / row[1], 3) if row[1] > 0 else 0
            }
            for row in calibration
        }
    }


def snapshot_portfolio(conn: sqlite3.Connection) -> None:
    """Save a portfolio performance snapshot"""
    summary = get_performance_summary(conn)
    now     = datetime.now(timezone.utc).isoformat()

    open_positions = conn.execute(
        "SELECT COUNT(*) FROM paper_trades WHERE status = 'open'"
    ).fetchone()[0]

    conn.execute("""
        INSERT INTO portfolio_snapshots
        (snapshot_at, total_predictions, correct_predictions,
         win_rate, total_pnl, open_positions)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        now,
        summary["prediction_accuracy"]["total"],
        summary["prediction_accuracy"]["correct"],
        summary["prediction_accuracy"]["win_rate"],
        summary["paper_trading"]["total_pnl"],
        open_positions
    ))
    conn.commit()
    log.info(f"Portfolio snapshot: win_rate={summary['prediction_accuracy']['win_rate']:.1%} "
             f"pnl=${summary['paper_trading']['total_pnl']:+.2f}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    conn = init_db()
    print("Paper trading DB initialized")
    print(f"DB path: {DB_PATH}")

    # Show current state
    summary = get_performance_summary(conn)
    print(f"\nPerformance Summary:")
    print(json.dumps(summary, indent=2))
    conn.close()
