#!/usr/bin/env python3
"""
Pre-registration discipline for calibration changes.

Adapted from modales validate.py pattern: before any calibration parameter
(learning_rate, confidence_threshold, etc.) is changed in production, the
change must be registered with pre-declared acceptance criteria. Criteria
cannot be modified after creation (append-only semantics). Status and
decision fields are the only mutable columns after registration.

Usage:
    from portfolio.prereg import register_change, get_pending, decide

    # Register a proposed change
    reg_id = register_change(
        param_path="trim.learning_rate",
        proposed_value="0.15",
        criteria={"metric": "accuracy", "threshold": 0.65, "min_samples": 20}
    )

    # Check what's pending
    pending = get_pending()

    # Decide on a registration
    decide(reg_id, accepted=True, note="Passed OOS window")
"""

import json
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# Same DB as sector_trim (paper_trading.db)
PAPER_DB = Path(__file__).parent.parent.parent / "data" / "paper_trading.db"

# Required keys in the acceptance criteria dict.
# These enforce the modales gate pattern: metric to evaluate, threshold to beat,
# and minimum sample size for statistical significance.
REQUIRED_CRITERIA_KEYS = {"metric", "threshold", "min_samples"}

VALID_STATUSES = {"pending", "accepted", "rejected", "superseded"}


class PreregError(Exception):
    """Raised when a pre-registration operation is invalid."""
    pass


def _get_conn() -> sqlite3.Connection:
    """Open a connection to the paper trading DB with the prereg table ensured."""
    conn = sqlite3.connect(PAPER_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calibration_prereg (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT NOT NULL,
            param_path      TEXT NOT NULL,
            proposed_value  TEXT NOT NULL,
            criteria_json   TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending',
            decided_at      TEXT,
            decision_note   TEXT
        )
    """)
    conn.commit()
    return conn


def register_change(param_path: str, proposed_value, criteria: dict) -> int:
    """
    Register a proposed calibration change with pre-declared acceptance criteria.

    Args:
        param_path: Dotted path identifying the parameter
                    (e.g. 'trim.learning_rate', 'gates.confidence_threshold')
        proposed_value: The value to change to (stored as string for type neutrality)
        criteria: Dict with acceptance criteria. Must contain keys:
                  - metric: str, what to evaluate (e.g. 'accuracy', 'sharpe')
                  - threshold: float, minimum value to pass
                  - min_samples: int, minimum data points for significance

    Returns:
        The new registration row id.

    Raises:
        PreregError: If criteria is missing required keys or param_path is empty.
    """
    if not param_path or not isinstance(param_path, str):
        raise PreregError(f"param_path must be a non-empty string, got: {param_path!r}")

    if not isinstance(criteria, dict):
        raise PreregError("criteria must be a dict")

    missing = REQUIRED_CRITERIA_KEYS - set(criteria.keys())
    if missing:
        raise PreregError(
            f"criteria missing required keys: {missing} "
            f"(need {REQUIRED_CRITERIA_KEYS})"
        )

    # Validate types of required fields
    if not isinstance(criteria["metric"], str) or not criteria["metric"].strip():
        raise PreregError("criteria['metric'] must be a non-empty string")
    try:
        float(criteria["threshold"])
    except (TypeError, ValueError):
        raise PreregError("criteria['threshold'] must be a numeric value")
    if not isinstance(criteria["min_samples"], int) or criteria["min_samples"] < 1:
        raise PreregError("criteria['min_samples'] must be a positive integer")

    proposed_str = str(proposed_value)
    criteria_json = json.dumps(criteria, sort_keys=True)

    now = datetime.now(timezone.utc).isoformat()

    conn = _get_conn()
    cursor = conn.execute("""
        INSERT INTO calibration_prereg
            (created_at, param_path, proposed_value, criteria_json, status)
        VALUES (?, ?, ?, ?, 'pending')
    """, (now, param_path, proposed_str, criteria_json))
    reg_id = cursor.lastrowid
    conn.commit()
    conn.close()

    log.info(
        f"[PREREG] Registered #{reg_id}: {param_path} = {proposed_str} "
        f"(criteria: {criteria_json})"
    )
    return reg_id


def get_pending() -> list[dict]:
    """
    Return all pending pre-registrations (not yet accepted/rejected/superseded).

    Returns:
        List of dicts with keys: id, created_at, param_path, proposed_value,
        criteria_json, criteria (parsed dict).
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT id, created_at, param_path, proposed_value, criteria_json
        FROM calibration_prereg
        WHERE status = 'pending'
        ORDER BY created_at ASC
    """).fetchall()
    conn.close()

    results = []
    for row in rows:
        rid, created_at, param_path, proposed_value, criteria_json = row
        results.append({
            "id": rid,
            "created_at": created_at,
            "param_path": param_path,
            "proposed_value": proposed_value,
            "criteria_json": criteria_json,
            "criteria": json.loads(criteria_json),
        })
    return results


def get_all() -> list[dict]:
    """
    Return all pre-registrations regardless of status.

    Returns:
        List of dicts with full row data.
    """
    conn = _get_conn()
    rows = conn.execute("""
        SELECT id, created_at, param_path, proposed_value,
               criteria_json, status, decided_at, decision_note
        FROM calibration_prereg
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()

    return [
        {
            "id": row[0],
            "created_at": row[1],
            "param_path": row[2],
            "proposed_value": row[3],
            "criteria_json": row[4],
            "criteria": json.loads(row[4]),
            "status": row[5],
            "decided_at": row[6],
            "decision_note": row[7],
        }
        for row in rows
    ]


def decide(reg_id: int, accepted: bool, note: str) -> dict:
    """
    Decide on a pre-registration: accept or reject it.

    This only updates the status, decided_at, and decision_note fields.
    The criteria_json is never modified — append-only semantics.

    Args:
        reg_id: The registration id to decide on.
        accepted: True to accept, False to reject.
        note: Reason for the decision.

    Returns:
        The updated row as a dict.

    Raises:
        PreregError: If the registration doesn't exist, is already decided,
                     or the note is empty.
    """
    if not note or not isinstance(note, str) or not note.strip():
        raise PreregError("decision note cannot be empty")

    new_status = "accepted" if accepted else "rejected"
    if new_status not in VALID_STATUSES:
        raise PreregError(f"Invalid status: {new_status}")

    now = datetime.now(timezone.utc).isoformat()

    conn = _get_conn()

    # Read current state
    row = conn.execute("""
        SELECT id, created_at, param_path, proposed_value,
               criteria_json, status, decided_at, decision_note
        FROM calibration_prereg
        WHERE id = ?
    """, (reg_id,)).fetchone()

    if not row:
        conn.close()
        raise PreregError(f"Registration #{reg_id} not found")

    if row[5] != "pending":
        conn.close()
        raise PreregError(
            f"Registration #{reg_id} is already '{row[5]}' — cannot re-decide"
        )

    # Update ONLY status, decided_at, decision_note — criteria_json untouched
    conn.execute("""
        UPDATE calibration_prereg
        SET status = ?, decided_at = ?, decision_note = ?
        WHERE id = ? AND status = 'pending'
    """, (new_status, now, note, reg_id))
    conn.commit()

    # Read back the full row
    updated = conn.execute("""
        SELECT id, created_at, param_path, proposed_value,
               criteria_json, status, decided_at, decision_note
        FROM calibration_prereg
        WHERE id = ?
    """, (reg_id,)).fetchone()
    conn.close()

    result = {
        "id": updated[0],
        "created_at": updated[1],
        "param_path": updated[2],
        "proposed_value": updated[3],
        "criteria_json": updated[4],
        "criteria": json.loads(updated[4]),
        "status": updated[5],
        "decided_at": updated[6],
        "decision_note": updated[7],
    }

    log.info(
        f"[PREREG] Decided #{reg_id}: {result['param_path']} -> "
        f"{new_status} ({note})"
    )
    return result


def supersede(reg_id: int, superseded_by_id: int) -> dict:
    """
    Mark a registration as superseded by a newer one.

    Args:
        reg_id: The registration to supersede.
        superseded_by_id: The newer registration that replaces it.

    Returns:
        The updated row.

    Raises:
        PreregError: If either registration doesn't exist or isn't pending.
    """
    conn = _get_conn()

    # Verify both exist and the target is pending
    row = conn.execute("""
        SELECT id, status FROM calibration_prereg WHERE id = ?
    """, (reg_id,)).fetchone()
    if not row:
        conn.close()
        raise PreregError(f"Registration #{reg_id} not found")
    if row[1] != "pending":
        conn.close()
        raise PreregError(f"Registration #{reg_id} is '{row[1]}', not pending")

    newer = conn.execute("""
        SELECT id, status FROM calibration_prereg WHERE id = ?
    """, (superseded_by_id,)).fetchone()
    if not newer:
        conn.close()
        raise PreregError(f"Registration #{superseded_by_id} not found")
    if newer[1] not in ("pending",):
        # Allow superseding even if newer is already decided — the act of
        # superseding just marks the old one as irrelevant.
        pass

    now = datetime.now(timezone.utc).isoformat()
    note = f"Superseded by #{superseded_by_id}"
    conn.execute("""
        UPDATE calibration_prereg
        SET status = 'superseded', decided_at = ?, decision_note = ?
        WHERE id = ?
    """, (now, note, reg_id))
    conn.commit()

    updated = conn.execute("""
        SELECT id, created_at, param_path, proposed_value,
               criteria_json, status, decided_at, decision_note
        FROM calibration_prereg
        WHERE id = ?
    """, (reg_id,)).fetchone()
    conn.close()

    return {
        "id": updated[0],
        "created_at": updated[1],
        "param_path": updated[2],
        "proposed_value": updated[3],
        "criteria_json": updated[4],
        "criteria": json.loads(updated[4]),
        "status": updated[5],
        "decided_at": updated[6],
        "decision_note": updated[7],
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: prereg.py [register|pending|decide|list]")
        print("  register <param_path> <value> <criteria_json>")
        print("  pending")
        print("  decide <id> <accepted:0|1> <note>")
        print("  list")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "register":
        if len(sys.argv) < 5:
            print("Usage: prereg.py register <param_path> <value> <criteria_json>")
            sys.exit(1)
        rid = register_change(sys.argv[2], sys.argv[3], json.loads(sys.argv[4]))
        print(f"Registered: #{rid}")

    elif cmd == "pending":
        for r in get_pending():
            print(f"  #{r['id']} {r['param_path']} = {r['proposed_value']} "
                  f"(criteria: {r['criteria_json']})")

    elif cmd == "decide":
        if len(sys.argv) < 5:
            print("Usage: prereg.py decide <id> <accepted:0|1> <note>")
            sys.exit(1)
        result = decide(int(sys.argv[2]), sys.argv[3] == "1", sys.argv[4])
        print(f"Decided: #{result['id']} -> {result['status']}")

    elif cmd == "list":
        for r in get_all():
            print(f"  #{r['id']} {r['param_path']} = {r['proposed_value']} "
                  f"[{r['status']}] {r.get('decision_note', '')}")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
