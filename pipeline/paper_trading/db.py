#!/usr/bin/env python3
"""
Paper trading simulation database
Tracks simulated positions, P&L, and prediction accuracy
Provides feedback loop for tuning model confidence and rule weights
"""

import sys
import sqlite3
import json
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from config import PAPER_DB

sys.path.insert(0, str(Path(__file__).parent.parent))
from sector_etf import get_sector_etf

load_dotenv(Path(__file__).parent.parent / ".env")

DB_PATH = Path(os.environ.get("PAPER_DB_PATH",
               str(PAPER_DB)))

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

        CREATE TABLE IF NOT EXISTS calibration_prereg (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT NOT NULL,
            param_path      TEXT NOT NULL,
            proposed_value  TEXT NOT NULL,
            criteria_json   TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending',
            decided_at      TEXT,
            decision_note   TEXT
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
         model_used, signals_used, key_risk, reasoning_summary, prediction_file,
         full_reasoning, critic_verdict, critic_reasoning, critic_confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        prediction_file or "",
        prediction.get("reasoning", ""),
        p.get("critic_verdict", ""),
        p.get("critic_reasoning", ""),
        p.get("critic_confidence", None),
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

    row = conn.execute(
        "SELECT direction, probability, confidence, reasoning_summary, critic_verdict "
        "FROM predictions WHERE id = ?",
        (prediction_id,)
    ).fetchone()

    if not row:
        log.error(f"Prediction #{prediction_id} not found")
        return

    direction, probability, confidence, reasoning_summary, critic_verdict = row
    was_correct = 1 if direction == actual_direction else 0

    conn.execute("""
        UPDATE predictions
        SET verified_at = ?, actual_direction = ?,
            was_correct = ?, actual_notes = ?
        WHERE id = ?
    """, (now, actual_direction, was_correct, notes, prediction_id))

    # Write rule_performance rows for contributing rules
    try:
        write_rule_performance(conn, prediction_id, was_correct,
                               direction, probability, confidence, reasoning_summary,
                               critic_verdict, now)
    except Exception:
        log.exception(f"write_rule_performance failed for prediction #{prediction_id}")

    conn.commit()

    result = "✓ CORRECT" if was_correct else "✗ WRONG"
    log.info(f"Prediction #{prediction_id} verified: {result} "
             f"(predicted={direction}, actual={actual_direction})")


def write_rule_performance(conn: sqlite3.Connection,
                           prediction_id: int,
                           was_correct: int,
                           direction: str,
                           probability: float,
                           confidence: float,
                           reasoning_summary: str,
                           critic_verdict: str,
                           evaluated_at: str) -> None:
    """Write one rule_performance row per contributing inference rule.

    Schema (rule_performance):
        id, rule_trigger TEXT NOT NULL, evaluated_at TEXT,
        prediction_id INTEGER REFERENCES predictions(id),
        was_correct INTEGER, signal_confidence REAL, notes TEXT

    Extracts rule references from the reasoning_summary and critic_verdict
    text. Idempotent: skips if a (prediction_id, rule_trigger) row exists.
    """
    import re

    text_parts = []
    if reasoning_summary:
        text_parts.append(reasoning_summary)
    if critic_verdict:
        text_parts.append(critic_verdict)
    full_text = " ".join(text_parts)

    if not full_text.strip():
        log.debug(f"No reasoning text for prediction #{prediction_id} — skipping rule perf")
        return

    triggers = set()

    # Pattern 1: inference_rule:<id> (e.g. "inference_rule:42")
    for m in re.finditer(r"inference_rule[:\s]*(\d+)", full_text):
        triggers.add(f"inference_rule:{m.group(1)}")

    # Pattern 2: [Rule <id>] or Rule <id> (bracketed or bare)
    for m in re.finditer(r"\[?\s*Rule\s+(\d+)\s*\]?", full_text):
        triggers.add(f"rule:{m.group(1)}")

    # Pattern 3: inference rule <id> (natural language)
    for m in re.finditer(r"inference\s+rule\s+(\d+)", full_text, re.IGNORECASE):
        triggers.add(f"inference_rule:{m.group(1)}")

    # Pattern 4: Gate identifiers mentioned by name
    gate_keywords = [
        "hard_block_bullish",
        "hard_block_bearish",
        "sector_fuel_trim",
        "ltft",
        "stft",
        "calibration_gate",
        "confidence_gate",
        "win_rate_gate",
    ]
    text_lower = full_text.lower()
    for kw in gate_keywords:
        if kw.replace("_", " ") in text_lower or kw in text_lower:
            triggers.add(kw)

    if not triggers:
        log.debug(f"No contributing rules found for prediction #{prediction_id}")
        return

    # Resolve real sector from the prediction query
    sector = "SPY"
    event_type = "other"
    pred_row = conn.execute(
        "SELECT query FROM predictions WHERE id = ?",
        (prediction_id,)
    ).fetchone()
    if pred_row:
        query_text = pred_row[0]
        sector = get_sector_etf(query_text)
        # Look up event_type from signal_ledger
        try:
            sl_row = conn.execute(
                "SELECT event_type FROM signal_ledger "
                "WHERE query LIKE ? "
                "ORDER BY created_at DESC LIMIT 1",
                (f"%{query_text[:50]}%",)
            ).fetchone()
            if sl_row and sl_row[0]:
                event_type = sl_row[0]
        except Exception:
            pass

    inserted = 0
    for trigger in sorted(triggers):
        # Idempotency check: skip if already recorded
        existing = conn.execute(
            "SELECT id FROM rule_performance "
            "WHERE prediction_id = ? AND rule_trigger = ?",
            (prediction_id, trigger)
        ).fetchone()
        if existing:
            log.debug(f"Skipping duplicate: prediction #{prediction_id} rule {trigger}")
            continue

        # Put context into notes as compact key=value string
        notes_str = f"sector={sector};event_type={event_type};direction={direction}"

        conn.execute(
            "INSERT INTO rule_performance "
            "(rule_trigger, evaluated_at, prediction_id, was_correct, "
            "signal_confidence, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (trigger, evaluated_at, prediction_id, was_correct,
             confidence, notes_str)
        )
        inserted += 1

    # Critic verdict logging — one row per verdict
    if critic_verdict and critic_verdict.strip():
        verdict_trigger = f"critic_verdict:{critic_verdict.strip()}"
        existing = conn.execute(
            "SELECT id FROM rule_performance "
            "WHERE prediction_id = ? AND rule_trigger = ?",
            (prediction_id, verdict_trigger)
        ).fetchone()
        if not existing:
            prob = probability if probability is not None else 0.5
            brier = (prob - (1 if was_correct else 0)) ** 2
            conn.execute(
                "INSERT INTO rule_performance "
                "(rule_trigger, evaluated_at, prediction_id, was_correct, "
                "signal_confidence, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (verdict_trigger, evaluated_at, prediction_id, was_correct,
                 prob, f"brier={brier:.4f}")
            )
            inserted += 1

    if inserted > 0:
        log.info(f"Recorded {inserted} rule performance row(s) for prediction #{prediction_id}")


def backfill_critic_verdict_performance(conn: sqlite3.Connection) -> int:
    """One-time idempotent backfill of critic_verdict rows into rule_performance.

    For all already-verified predictions with a non-empty critic_verdict,
    insert a rule_performance row. Guarded on (rule_trigger, prediction_id)
    so re-running inserts nothing.

    Returns number of rows inserted.
    """
    rows = conn.execute(
        "SELECT id, critic_verdict, probability, was_correct "
        "FROM predictions "
        "WHERE was_correct IS NOT NULL AND critic_verdict != ''"
    ).fetchall()

    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for pred_id, verdict, probability, was_correct in rows:
        verdict = verdict.strip()
        if not verdict:
            continue
        verdict_trigger = f"critic_verdict:{verdict}"

        existing = conn.execute(
            "SELECT id FROM rule_performance "
            "WHERE prediction_id = ? AND rule_trigger = ?",
            (pred_id, verdict_trigger)
        ).fetchone()
        if existing:
            continue

        prob = probability if probability is not None else 0.5
        brier = (prob - (1 if was_correct else 0)) ** 2
        conn.execute(
            "INSERT INTO rule_performance "
            "(rule_trigger, evaluated_at, prediction_id, was_correct, "
            "signal_confidence, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (verdict_trigger, now, pred_id, was_correct,
             prob, f"brier={brier:.4f}")
        )
        inserted += 1

    conn.commit()
    if inserted > 0:
        log.info(f"Backfilled {inserted} critic_verdict performance row(s)")
    else:
        log.info("No critic_verdict backfill needed (already complete)")
    return inserted


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


def critic_verdict_stats(conn: sqlite3.Connection) -> dict:
    """Return per-verdict {n, hit_rate, brier_mean} from rule_performance rows.

    Queries rule_performance rows where rule_trigger starts with
    'critic_verdict:'. Groups by the verdict suffix (approve/challenge/reject).
    Returns dict keyed by verdict with counts, hit rates, and mean Brier scores.
    """
    rows = conn.execute(
        "SELECT rule_trigger, was_correct, signal_confidence, notes "
        "FROM rule_performance "
        "WHERE rule_trigger LIKE 'critic_verdict:%'"
    ).fetchall()

    verdicts = {}
    for trigger, was_correct, confidence, notes in rows:
        verdict = trigger.split(":", 1)[1] if ":" in trigger else trigger
        # Extract Brier score from notes
        brier = 0.0
        if notes:
            for part in notes.split(";"):
                part = part.strip()
                if part.startswith("brier="):
                    try:
                        brier = float(part.split("=", 1)[1])
                    except ValueError:
                        pass
        if verdict not in verdicts:
            verdicts[verdict] = []
        verdicts[verdict].append({
            "was_correct": was_correct,
            "brier": brier,
        })

    result = {}
    for verdict, entries in sorted(verdicts.items()):
        n = len(entries)
        correct = sum(e["was_correct"] for e in entries)
        hit_rate = correct / n if n > 0 else 0
        brier_mean = sum(e["brier"] for e in entries) / n if n > 0 else 0
        result[verdict] = {
            "n": n,
            "hit_rate": round(hit_rate, 4),
            "brier_mean": round(brier_mean, 4),
        }

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    conn = init_db()
    print("Paper trading DB initialized")
    print(f"DB path: {DB_PATH}")

    # Optional: run critic_verdict backfill
    if len(sys.argv) > 1 and sys.argv[1] == "--backfill-critic-verdict":
        n = backfill_critic_verdict_performance(conn)
        print(f"Backfilled {n} critic_verdict performance row(s)")
        conn.close()
        sys.exit(0)

    # Show current state
    summary = get_performance_summary(conn)
    print(f"\nPerformance Summary:")
    print(json.dumps(summary, indent=2))
    conn.close()