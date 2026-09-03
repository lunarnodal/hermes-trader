#!/usr/bin/env python3
"""
Dynamic inference rule engine
Rules are discovered from signal co-occurrence patterns
and stored in SQLite alongside static baseline rules
"""

import sqlite3
import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("RULES_DB_PATH", "/home/trading/trading-ai/data/rules.db"))
log = logging.getLogger(__name__)


def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS inference_rules (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger      TEXT NOT NULL,
            sectors      TEXT NOT NULL,
            confidence   REAL DEFAULT 0.5,
            source       TEXT DEFAULT 'static',
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            occurrence_count INTEGER DEFAULT 1,
            active       INTEGER DEFAULT 1,
            notes        TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rule_proposals (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger      TEXT NOT NULL,
            sectors      TEXT NOT NULL,
            evidence     TEXT,
            occurrence_count INTEGER DEFAULT 1,
            first_seen   TEXT NOT NULL,
            last_seen    TEXT NOT NULL,
            status       TEXT DEFAULT 'pending',
            approved_at  TEXT
        )
    """)
    conn.commit()
    return conn


def seed_static_rules(conn: sqlite3.Connection) -> None:
    """Seed the baseline static rules — only if not already seeded"""
    existing = conn.execute("SELECT COUNT(*) FROM inference_rules WHERE source = 'static'").fetchone()[0]
    if existing > 0:
        return  # Already seeded
    now = datetime.now(timezone.utc).isoformat()
    static_rules = [
        # Geopolitical
        ("war conflict military strikes sanctions",
         ["energy","defense","commodities","oil_gas"]),
        ("russia ukraine",
         ["energy","oil_gas","commodities","chemicals","semiconductors","neon_gas"]),
        ("iran saudi arabia opec",
         ["energy","oil_gas"]),
        ("china export controls gallium germanium",
         ["semiconductors","materials","ai_infrastructure"]),
        ("taiwan tsmc strait",
         ["semiconductors","ai_infrastructure","technology"]),
        # Macro
        ("federal reserve interest rates inflation",
         ["financials","real_estate","utilities"]),
        ("dollar strength weakness",
         ["commodities","emerging_markets","exporters"]),
        ("tariff trade war",
         ["manufacturing","automotive","steel","retail","technology"]),
        # AI/Tech supply chain
        ("ai infrastructure data center gpu",
         ["semiconductors","ai_infrastructure","data_center","utilities"]),
        ("memory hbm dram supply shortage",
         ["memory","semiconductors","ai_infrastructure"]),
        ("data center construction hyperscaler capex",
         ["utilities","real_estate","construction","ai_infrastructure"]),
        ("specialty gases neon argon krypton",
         ["semiconductors","chemicals","manufacturing"]),
        ("power grid electricity energy infrastructure",
         ["utilities","energy","ai_infrastructure","data_center"]),
        # Supply chain
        ("supply chain disruption",
         ["manufacturing","technology","retail"]),
        ("cybersecurity attack breach",
         ["technology","cybersecurity","financials"]),
        # Agriculture/Weather
        ("drought flood weather",
         ["agriculture","insurance","utilities"]),
        ("food prices crop harvest",
         ["agriculture","consumer_staples"]),
    ]

    existing = {row[0] for row in
                conn.execute("SELECT trigger FROM inference_rules").fetchall()}

    for trigger, sectors in static_rules:
        if trigger not in existing:
            conn.execute("""
                INSERT INTO inference_rules
                (trigger, sectors, confidence, source, created_at, updated_at)
                VALUES (?, ?, ?, 'static', ?, ?)
            """, (trigger, json.dumps(sectors), 0.9, now, now))

    conn.commit()
    log.info(f"Static rules seeded — {len(static_rules)} rules")


def get_active_rules(conn: sqlite3.Connection) -> list[dict]:
    """Return all active rules sorted by confidence descending"""
    rows = conn.execute("""
        SELECT trigger, sectors, confidence, source, occurrence_count
        FROM inference_rules
        WHERE active = 1
        ORDER BY confidence DESC, occurrence_count DESC
    """).fetchall()

    return [
        {
            "trigger": row[0],
            "sectors": json.loads(row[1]),
            "confidence": row[2],
            "source": row[3],
            "occurrences": row[4]
        }
        for row in rows
    ]


def propose_rule(conn: sqlite3.Connection, trigger: str,
                 sectors: list[str], evidence: str) -> None:
    """Record a discovered rule proposal from signal analysis"""
    now = datetime.now(timezone.utc).isoformat()

    existing = conn.execute(
        "SELECT id, occurrence_count FROM rule_proposals WHERE trigger = ? AND status = 'pending'",
        (trigger,)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE rule_proposals
            SET occurrence_count = occurrence_count + 1,
                last_seen = ?,
                evidence = ?
            WHERE id = ?
        """, (now, evidence, existing[0]))
        count = existing[1] + 1
        log.info(f"Rule proposal updated: '{trigger}' ({count} occurrences)")
    else:
        conn.execute("""
            INSERT INTO rule_proposals
            (trigger, sectors, evidence, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
        """, (trigger, json.dumps(sectors), evidence, now, now))
        count = 1
        log.info(f"New rule proposal: '{trigger}'")

    conn.commit()
    # Auto-promote to active rule after threshold
    if count >= 2:
        promote_proposal(conn, trigger)


def promote_proposal(conn: sqlite3.Connection, trigger: str) -> None:
    """Promote a proposal to an active rule after sufficient evidence"""
    now = datetime.now(timezone.utc).isoformat()

    proposal = conn.execute(
        "SELECT sectors, occurrence_count, evidence FROM rule_proposals WHERE trigger = ?",
        (trigger,)
    ).fetchone()

    if not proposal:
        return

    sectors, count, evidence = proposal
    confidence = min(0.5 + (count * 0.05), 0.85)  # Cap at 0.85 for discovered rules

    existing = conn.execute(
        "SELECT id FROM inference_rules WHERE trigger = ?", (trigger,)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE inference_rules
            SET occurrence_count = ?, confidence = ?, updated_at = ?,
                source = 'discovered'
            WHERE trigger = ?
        """, (count, confidence, now, trigger))
    else:
        conn.execute("""
            INSERT INTO inference_rules
            (trigger, sectors, confidence, source, created_at, updated_at,
             occurrence_count, notes)
            VALUES (?, ?, ?, 'discovered', ?, ?, ?, ?)
        """, (trigger, sectors, confidence, now, now, count, evidence))

    conn.execute(
        "UPDATE rule_proposals SET status = 'promoted', approved_at = ? WHERE trigger = ?",
        (now, trigger)
    )
    conn.commit()
    log.info(f"Rule promoted to active: '{trigger}' (confidence={confidence:.2f})")


def build_prompt_rules(conn: sqlite3.Connection) -> str:
    """Build the inference rules section for the scorer prompt dynamically"""
    rules = get_active_rules(conn)
    lines = ["SECTOR INFERENCE RULES — always apply these cross-sector inferences:"]
    for rule in rules:
        sectors_str = ", ".join(f'"{s}"' for s in rule["sectors"])
        source_tag = "" if rule["source"] == "static" else f" [discovered:{rule['occurrences']}x]"
        lines.append(f'- {rule["trigger"]} → always add {sectors_str}{source_tag}')
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    conn = init_db()
    seed_static_rules(conn)
    rules = get_active_rules(conn)
    print(f"\nActive rules: {len(rules)}")
    for r in rules:
        print(f"  [{r['source']:10s}] {r['trigger'][:50]:50s} → {r['sectors'][:3]}")
    conn.close()