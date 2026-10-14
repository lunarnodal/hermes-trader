"""Rem item 4 (r2): seed_static_rules must compare confidence, not the row id,
and must only UPDATE + log when stored sectors actually differ (no no-op writes).
Unreadable or NULL stored sectors count as "differ" (the update repairs the
row) and must never raise.

Regression history: `SELECT id ...` fetchone() put the integer primary key at
existing[0]; `existing[0] == 0.9` was always False, so the UPDATE branch never
ran and static sector updates were silently skipped every cycle.
FAIL on base commit, PASS on the change. 0 skipped.
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rules.rule_engine import seed_static_rules  # noqa: E402


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE inference_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger TEXT NOT NULL,
            sectors TEXT,
            confidence REAL DEFAULT 0.5,
            source TEXT DEFAULT 'static',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            occurrence_count INTEGER DEFAULT 1,
            active INTEGER DEFAULT 1,
            notes TEXT
        )
    """)
    return conn


def test_default_confidence_stale_sectors_gets_update():
    """conf=0.9 + sectors differing from static list -> UPDATE runs.
    (FAILs on the base commit: row id 328 != 0.9 -> skip.)"""
    conn = _db()
    conn.execute(
        "INSERT INTO inference_rules "
        "(id, trigger, sectors, confidence, source, created_at, updated_at) "
        "VALUES (328, 'tariff trade war', ?, 0.9, 'static', 't0', 't0')",
        (json.dumps(["stale"]),),
    )
    seed_static_rules(conn)
    row = conn.execute(
        "SELECT sectors, updated_at FROM inference_rules WHERE trigger = 'tariff trade war'"
    ).fetchone()
    assert json.loads(row[0]) == ["manufacturing", "automotive", "steel", "retail", "technology"]
    assert row[1] != "t0"  # updated_at advanced -> the UPDATE actually ran


def test_default_confidence_in_sync_sectors_no_write():
    """conf=0.9 + sectors already equal to the static list -> no UPDATE,
    no rewrite: updated_at must stay 't0' (no-op is a no-op)."""
    conn = _db()
    conn.execute(
        "INSERT INTO inference_rules "
        "(id, trigger, sectors, confidence, source, created_at, updated_at) "
        "VALUES (328, 'tariff trade war', ?, 0.9, 'static', 't0', 't0')",
        (json.dumps(["manufacturing", "automotive", "steel", "retail", "technology"]),),
    )
    seed_static_rules(conn)
    row = conn.execute(
        "SELECT sectors, updated_at FROM inference_rules WHERE trigger = 'tariff trade war'"
    ).fetchone()
    assert json.loads(row[0]) == ["manufacturing", "automotive", "steel", "retail", "technology"]
    assert row[1] == "t0"  # untouched: conditional UPDATE skipped the no-op


def test_null_sectors_at_default_confidence_are_repaired():
    """conf=0.9 + sectors = NULL -> the update repairs the row; no exception.
    (None would crash json.loads unguarded; NULL counts as 'differ'.)"""
    conn = _db()
    conn.execute(
        "INSERT INTO inference_rules "
        "(id, trigger, sectors, confidence, source, created_at, updated_at) "
        "VALUES (328, 'tariff trade war', NULL, 0.9, 'static', 't0', 't0')"
    )
    seed_static_rules(conn)  # must not raise
    row = conn.execute(
        "SELECT sectors, updated_at FROM inference_rules WHERE trigger = 'tariff trade war'"
    ).fetchone()
    assert json.loads(row[0]) == ["manufacturing", "automotive", "steel", "retail", "technology"]
    assert row[1] != "t0"  # repaired


def test_operator_overridden_confidence_is_preserved():
    """A row whose confidence moved away from 0.9 keeps its sectors
    (operator override preserved, before and after the fix)."""
    conn = _db()
    conn.execute(
        "INSERT INTO inference_rules "
        "(id, trigger, sectors, confidence, source, created_at, updated_at) "
        "VALUES (328, 'tariff trade war', ?, 0.7, 'static', 't0', 't0')",
        (json.dumps(["custom"]),),
    )
    seed_static_rules(conn)
    row = conn.execute(
        "SELECT sectors, updated_at FROM inference_rules WHERE trigger = 'tariff trade war'"
    ).fetchone()
    assert json.loads(row[0]) == ["custom"]
    assert row[1] == "t0"


def test_missing_triggers_are_inserted_with_default_confidence():
    conn = _db()
    seed_static_rules(conn)
    n = conn.execute("SELECT COUNT(*) FROM inference_rules").fetchone()[0]
    assert n >= 18
    row = conn.execute(
        "SELECT confidence FROM inference_rules WHERE trigger = 'tariff trade war'"
    ).fetchone()
    assert row[0] == 0.9
