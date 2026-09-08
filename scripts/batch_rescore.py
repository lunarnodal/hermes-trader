#!/usr/bin/env python3
"""
Batch re-scoring write-back for misclassified leadership_transition signals.

1. Adds 'event_type' column to signal_ledger (idempotent ALTER TABLE)
2. Loads scored signals from scored_rescore_leadership.jsonl
3. Inserts them into signal_ledger with correct event_type
4. Logs count and event_type distribution
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

DB_PATH = Path("/home/trading/trading-ai/data/paper_trading.db")
SCORED_PATH = Path("/mnt/qnap/timeseries/signals/scored_rescore_leadership.jsonl")


def ensure_event_type_column() -> None:
    """Add event_type column to signal_ledger if it doesn't exist."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.execute("PRAGMA table_info(signal_ledger)")
        columns = {row[1] for row in cur.fetchall()}
        if "event_type" in columns:
            log.info("event_type column already exists on signal_ledger")
            return
        conn.execute("ALTER TABLE signal_ledger ADD COLUMN event_type TEXT DEFAULT 'other'")
        conn.commit()
        log.info("Added event_type column to signal_ledger")
    except Exception as e:
        log.error(f"Failed to add event_type column: {e}")
        raise
    finally:
        conn.close()


def load_scored_signals() -> list[dict]:
    """Load re-scored signals from the JSONL output file."""
    signals = []
    with SCORED_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                signals.append(json.loads(line))
            except json.JSONDecodeError as e:
                log.warning(f"Skipping malformed line: {e}")
    return signals


def write_to_ledger(signals: list[dict]) -> int:
    """Insert scored signals into signal_ledger with event_type."""
    conn = sqlite3.connect(str(DB_PATH))
    inserted = 0
    now = datetime.now(timezone.utc).isoformat()

    for sig in signals:
        try:
            conn.execute(
                """INSERT INTO signal_ledger
                   (created_at, query, sector, direction,
                    raw_confidence, adj_confidence,
                    gate_failed, gate_reason,
                    sector_win_rate, vix_at_time, event_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    now,
                    sig.get("title", ""),
                    sig.get("sectors", ["unknown"])[0],
                    sig.get("sentiment", "neutral"),
                    float(sig.get("confidence", 0.65)),
                    float(sig.get("confidence", 0.65)),
                    "rescored",
                    f"Re-scored from 'other' to '{sig.get('event_type','other')}'",
                    None,
                    None,
                    sig.get("event_type", "other"),
                ),
            )
            inserted += 1
        except Exception as e:
            log.warning(f"Failed to insert signal {sig.get('guid','?')}: {e}")

    conn.commit()
    conn.close()
    return inserted


def main():
    log.info("=" * 60)
    log.info("Batch re-scoring write-back started")
    log.info("=" * 60)

    ensure_event_type_column()

    signals = load_scored_signals()
    if not signals:
        log.error(f"No signals found in {SCORED_PATH}")
        return

    log.info(f"Loaded {len(signals)} scored signals from {SCORED_PATH.name}")

    from collections import Counter
    dist = Counter(s.get("event_type", "other") for s in signals)
    log.info("Event type distribution:")
    for et, cnt in sorted(dist.items()):
        log.info(f"  {et}: {cnt}")

    inserted = write_to_ledger(signals)
    log.info(f"Inserted {inserted}/{len(signals)} signals into signal_ledger")

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.execute(
        "SELECT event_type, COUNT(*) FROM signal_ledger WHERE event_type IS NOT NULL GROUP BY event_type"
    )
    log.info("Post-insert signal_ledger event_type distribution:")
    for row in cur.fetchall():
        log.info(f"  {row[0]}: {row[1]}")
    conn.close()

    log.info("=" * 60)
    log.info(f"Batch re-scoring complete: {inserted} signals written")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
