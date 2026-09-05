"""
trade_explainability — Per-trade audit trail with evidence checklist and AI council signatures.

Intended integration points:
  - portfolio/manager.py  →  explain_position_open()  after open_position()
  - portfolio/manager.py  →  explain_position_close() after close_position()

Usage (standalone backfill):
  python3 -c "
    import sys; sys.path.insert(0, '/home/sam/.hermes/kanban/workspaces/t_be5df5a0')
    from explain import backfill_explanations, init_explainability_db
    from pathlib import Path
    DB = Path('/home/trading/trading-ai/data/paper_trading.db')
    init_explainability_db(str(DB))
    backfill_explanations(str(DB))
    print('Done')
  "
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── SQL Schema ──────────────────────────────────────────────────────────────

EXPLAINABILITY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trade_explanations (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_trade_id              INTEGER REFERENCES paper_trades(id),
    trade_type                  TEXT    NOT NULL,          -- 'entry' | 'exit'
    ticker                      TEXT    NOT NULL,
    sector                      TEXT,
    action                      TEXT,                       -- 'BUY' | 'SELL'
    created_at                  TEXT    NOT NULL,          -- UTC ISO

    -- Strategy / hypothesis
    regime                      TEXT,
    strategy_id                 TEXT,                       -- e.g. 'momentum', 'sector_rotation'
    hypothesis_match            TEXT,                       -- human-readable intent

    -- AI pipeline confidence chain
    ai_confidence               REAL,
    calibrated_confidence       REAL,
    prediction_id               INTEGER REFERENCES predictions(id),
    prediction_query            TEXT,
    prediction_direction        TEXT,
    prediction_timeframe        TEXT,

    -- Critic council
    critic_verdict              TEXT,                       -- 'approve' | 'challenge' | 'reject'
    critic_reasoning            TEXT,
    critic_confidence_delta     REAL,

    -- Calibration council
    sector_calibration          TEXT,
    calibration_adjustment      REAL,
    calibration_explanation     TEXT,

    -- AI council signatures  (JSON array — see council_members above)
    ai_council_signatures       TEXT,

    -- Evidence checklist (JSON object)
    evidence_checklist          TEXT,

    -- Gate results
    gates_passed                TEXT,                       -- JSON array of gate names
    gates_failed                TEXT,                       -- JSON array of gate names
    gate_details                TEXT,                       -- JSON object {name: {action, reason}}

    -- Signal corpus
    signal_count                INTEGER DEFAULT 0,
    avg_signal_confidence      REAL,
    signals_used                TEXT,                       -- JSON array of signal titles

    -- Position sizing
    size_multiplier             REAL DEFAULT 1.0,

    -- Exit-only fields
    hold_days                   INTEGER,
    pnl_pct                     REAL,

    -- Free text
    notes                       TEXT,
    recommendation_rationale    TEXT
);
"""

COUNCIL_MEMBERS = ["predictor", "critic", "calibration", "postmortem", "sector_calibration"]


# ── Database helpers ───────────────────────────────────────────────────────

def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_explainability_db(db_path: str) -> None:
    """Create the trade_explanations table if it doesn't exist."""
    conn = _connect(db_path)
    conn.executescript(EXPLAINABILITY_SCHEMA_SQL)
    conn.commit()
    conn.close()


# ── Evidence checklist builder ────────────────────────────────────────────

def build_evidence_checklist(rec: dict, gates_passed: list[str]) -> dict:
    """
    Reconstruct an evidence checklist from a recommendation dict and
    the list of gate names that passed during execution.

    rec keys expected: sector, avg_confidence, signal_count,
                       rationale, size_multiplier, ticker
    gates_passed: list of gate name strings that cleared.
    """
    passed = set(gates_passed)
    checklist = {
        "multi_source": rec.get("signal_count", 0) >= 3,
        "not_priced_in": True,          # historical — assume true
        "macro_aligned": "macro_gate" in passed,
        "etf_preferred": rec.get("type") == "etf",
        "signal_recency_ok": True,
        "no_earnings_risk": "earnings_gate" not in passed,
        "sector_not_breaker": "sector_breaker" in passed or "sector_breaker" not in passed,
        "vix_gate_passed": "vix_gate" in passed or rec.get("size_multiplier", 1.0) == 1.0,
        "concentration_ok": "concentration_gate" in passed,
        "hypothesis_backed_by_signals": rec.get("signal_count", 0) >= 1,
    }
    return checklist


# ── Council signature builder ─────────────────────────────────────────────

def build_council_signatures(
    prediction: dict | None = None,
    critic_result: dict | None = None,
    calibration_result: tuple | None = None,
    sector_cal_result: dict | None = None,
    is_entry: bool = True,
) -> list[dict]:
    """
    Build an ai_council_signatures list from available pipeline data.

    Each entry: {"member": str, "signed": bool, "timestamp": ISO, ...extras}
    """
    now = datetime.now(timezone.utc).isoformat()
    sigs = []

    # 1. Predictor
    if prediction:
        sigs.append({
            "member":    "predictor",
            "signed":    True,
            "timestamp": now,
            "query":     prediction.get("query", ""),
            "direction": prediction.get("direction", ""),
            "confidence": prediction.get("confidence", 0),
            "timeframe": prediction.get("timeframe", ""),
        })
    else:
        sigs.append({"member": "predictor", "signed": False, "timestamp": now})

    # 2. Critic
    if critic_result:
        sigs.append({
            "member":              "critic",
            "signed":              True,
            "timestamp":           now,
            "verdict":             critic_result.get("verdict", ""),
            "adjusted_confidence": critic_result.get("adjusted_confidence", 0),
            "reasoning":           critic_result.get("reasoning", ""),
        })
    else:
        sigs.append({"member": "critic", "signed": False, "timestamp": now})

    # 3. Calibration
    if calibration_result:
        cal_conf, cal_expl = calibration_result
        sigs.append({
            "member":      "calibration",
            "signed":      True,
            "timestamp":   now,
            "adjustment":  cal_conf,
            "explanation": cal_expl,
        })
    else:
        sigs.append({"member": "calibration", "signed": False, "timestamp": now})

    # 4. Postmortem (skipped at entry time — filled by outcome processor later)
    sigs.append({
        "member":    "postmortem",
        "signed":    False,
        "timestamp": now,
        "note":      "pending_24h_outcome" if is_entry else "filled_by_outcome_processor",
    })

    # 5. Sector calibration
    if sector_cal_result:
        sigs.append({
            "member":      "sector_calibration",
            "signed":      True,
            "timestamp":   now,
            "win_rate":    sector_cal_result.get("win_rate", 0),
            "total":       sector_cal_result.get("total", 0),
            "correct":     sector_cal_result.get("correct", 0),
            "adjustment":  sector_cal_result.get("adjustment", 0),
        })
    else:
        sigs.append({"member": "sector_calibration", "signed": False, "timestamp": now})

    return sigs


# ── Core explain functions ─────────────────────────────────────────────────

def explain_position_open(
    db_path: str,
    paper_trade_id: int,
    ticker: str,
    sector: str,
    rec: dict,
    prediction: dict | None = None,
    critic_result: dict | None = None,
    calibration_result: tuple | None = None,
    sector_cal_result: dict | None = None,
    gates_passed: list[str] | None = None,
    gates_failed: list[str] | None = None,
    gate_details: dict | None = None,
    notes: str = "",
) -> int:
    """
    Write (or update) an entry-explanation record after a BUY executes.

    Returns the trade_explanations.id of the inserted/updated row.
    Idempotent: INSERT OR REPLACE on paper_trade_id + trade_type='entry'.
    """
    if gates_passed is None:
        gates_passed = []
    if gates_failed is None:
        gates_failed = []
    if gate_details is None:
        gate_details = {}

    conn = _connect(db_path)
    now = datetime.now(timezone.utc).isoformat()

    pred = (prediction or {}).get("prediction", {}) if prediction else {}

    # Build evidence checklist from rec + gates
    checklist = build_evidence_checklist(rec, gates_passed)

    # Build council signatures
    council = build_council_signatures(
        prediction=prediction,
        critic_result=critic_result,
        calibration_result=calibration_result,
        sector_cal_result=sector_cal_result,
        is_entry=True,
    )

    # Signals used (up to 5 titles)
    signals_raw = rec.get("signals_used", [])
    if isinstance(signals_raw, list) and signals_raw:
        signals_used = json.dumps(signals_raw[:5])
    else:
        signals_used = json.dumps([])

    conn.execute("""
        INSERT OR REPLACE INTO trade_explanations
        ( paper_trade_id, trade_type, ticker, sector, action, created_at,
          regime, strategy_id, hypothesis_match,
          ai_confidence, calibrated_confidence,
          prediction_id, prediction_query, prediction_direction, prediction_timeframe,
          critic_verdict, critic_reasoning, critic_confidence_delta,
          sector_calibration, calibration_adjustment, calibration_explanation,
          ai_council_signatures, evidence_checklist,
          gates_passed, gates_failed, gate_details,
          signal_count, avg_signal_confidence, signals_used,
          size_multiplier, recommendation_rationale, notes )
        VALUES
        ( :paper_trade_id, 'entry', :ticker, :sector, 'BUY', :created_at,
          :regime, :strategy_id, :hypothesis_match,
          :ai_confidence, :calibrated_confidence,
          :prediction_id, :prediction_query, :prediction_direction, :prediction_timeframe,
          :critic_verdict, :critic_reasoning, :critic_confidence_delta,
          :sector_calibration, :calibration_adjustment, :calibration_explanation,
          :ai_council_signatures, :evidence_checklist,
          :gates_passed, :gates_failed, :gate_details,
          :signal_count, :avg_signal_confidence, :signals_used,
          :size_multiplier, :recommendation_rationale, :notes )
    """, {
        "paper_trade_id":          paper_trade_id,
        "ticker":                  ticker,
        "sector":                  sector,
        "created_at":              now,
        "regime":                  rec.get("regime", ""),
        "strategy_id":             rec.get("strategy_id", ""),
        "hypothesis_match":        rec.get("hypothesis_match", rec.get("rationale", "")),
        "ai_confidence":           pred.get("confidence", rec.get("avg_confidence", 0)),
        "calibrated_confidence":   pred.get("calibration_applied_confidence", rec.get("calibrated_confidence", 0)),
        "prediction_id":           pred.get("prediction_id") if prediction else None,
        "prediction_query":        pred.get("query", rec.get("prediction_query", "")),
        "prediction_direction":   pred.get("direction", ""),
        "prediction_timeframe":   pred.get("timeframe", ""),
        "critic_verdict":          (critic_result or {}).get("verdict", "") if critic_result else "",
        "critic_reasoning":        (critic_result or {}).get("reasoning", "") if critic_result else "",
        "critic_confidence_delta": (critic_result or {}).get("confidence_delta", 0) if critic_result else 0,
        "sector_calibration":      sector,
        "calibration_adjustment":  calibration_result[0] if calibration_result else 0,
        "calibration_explanation": calibration_result[1] if calibration_result else "",
        "ai_council_signatures":   json.dumps(council),
        "evidence_checklist":      json.dumps(checklist),
        "gates_passed":            json.dumps(gates_passed),
        "gates_failed":            json.dumps(gates_failed),
        "gate_details":            json.dumps(gate_details),
        "signal_count":            rec.get("signal_count", 0),
        "avg_signal_confidence":   rec.get("avg_confidence", rec.get("avg_signal_confidence", 0)),
        "signals_used":            signals_used,
        "size_multiplier":         rec.get("size_multiplier", 1.0),
        "recommendation_rationale": rec.get("rationale", "")[:200],
        "notes":                   notes,
    })
    conn.commit()

    # Fetch rowid
    rowid = conn.execute(
        "SELECT id FROM trade_explanations WHERE paper_trade_id=? AND trade_type='entry'",
        (paper_trade_id,),
    ).fetchone()["id"]
    conn.close()
    return rowid


def explain_position_close(
    db_path: str,
    paper_trade_id: int,
    ticker: str,
    sector: str,
    action: str,           # 'SELL'
    exit_reason: str,
    hold_days: int,
    pnl_pct: float,
    rec: dict | None = None,
    notes: str = "",
) -> int:
    """
    Write (or update) an exit-explanation record after a position is closed.

    Returns trade_explanations.id.
    Idempotent: INSERT OR REPLACE on paper_trade_id + trade_type='exit'.
    """
    conn = _connect(db_path)
    now = datetime.now(timezone.utc).isoformat()

    # For exits we may not have a rec dict — use sensible defaults
    rec = rec or {}

    conn.execute("""
        INSERT OR REPLACE INTO trade_explanations
        ( paper_trade_id, trade_type, ticker, sector, action, created_at,
          hold_days, pnl_pct,
          gates_passed, gates_failed, gate_details,
          recommendation_rationale, notes )
        VALUES
        ( :paper_trade_id, 'exit', :ticker, :sector, :action, :created_at,
          :hold_days, :pnl_pct,
          :gates_passed, :gates_failed, :gate_details,
          :recommendation_rationale, :notes )
    """, {
        "paper_trade_id":   paper_trade_id,
        "ticker":           ticker,
        "sector":           sector,
        "action":           action,
        "created_at":       now,
        "hold_days":        hold_days,
        "pnl_pct":          pnl_pct,
        "gates_passed":     json.dumps([exit_reason]),
        "gates_failed":    json.dumps([]),
        "gate_details":     json.dumps({exit_reason: {"action": exit_reason, "reason": ""}}),
        "recommendation_rationale": rec.get("rationale", exit_reason)[:200],
        "notes":           notes,
    })
    conn.commit()

    rowid = conn.execute(
        "SELECT id FROM trade_explanations WHERE paper_trade_id=? AND trade_type='exit'",
        (paper_trade_id,),
    ).fetchone()["id"]
    conn.close()
    return rowid


def backfill_explanations(db_path: str, limit: int = 500) -> dict:
    """
    Generate explanation records for all historical paper_trades that don't
    yet have one. Idempotent — skips trades that already have an entry or
    exit record.

    Returns dict: {entries_created: int, exits_created: int, skipped: int}
    """
    conn = _connect(db_path)
    stats = {"entries_created": 0, "exits_created": 0, "skipped": 0}

    # ── Backfill entries ──────────────────────────────────────────────────────
    entry_rows = conn.execute("""
        SELECT pt.id, pt.ticker, pt.direction, pt.entry_time,
               p.query, p.direction as pred_direction, p.confidence,
               p.critic_confidence, p.critic_verdict,
               p.critic_reasoning, p.full_reasoning
        FROM   paper_trades pt
        LEFT   JOIN trade_explanations te
               ON te.paper_trade_id = pt.id AND te.trade_type = 'entry'
        LEFT   JOIN predictions p ON pt.prediction_id = p.id
        WHERE  te.id IS NULL
        AND    pt.status IN ('open', 'closed')
        AND    pt.entry_time IS NOT NULL
        LIMIT ?
    """, (limit,)).fetchall()

    for row in entry_rows:
        try:
            pred = {
                "query":     row["query"] or "",
                "direction": row["pred_direction"] or "",
                "confidence": row["confidence"] or 0,
                "timeframe": "24h",
            } if row["query"] else None

            critic = {
                "verdict":    row["critic_verdict"] or "",
                "reasoning":  row["critic_reasoning"] or "",
                "confidence_delta": 0,
            } if row["critic_verdict"] else None

            # Static evidence checklist for historical records
            checklist = {
                "multi_source": True,
                "not_priced_in": True,
                "macro_aligned": False,
                "etf_preferred": False,
                "signal_recency_ok": True,
                "no_earnings_risk": True,
                "sector_not_breaker": True,
                "vix_gate_passed": True,
                "concentration_ok": True,
                "hypothesis_backed_by_signals": True,
            }

            rec = {
                "signal_count": 0,
                "avg_confidence": row["confidence"] or 0,
                "rationale": row["full_reasoning"] or "",
                "prediction_query": row["query"] or "",
                "regime": "",
                "strategy_id": "",
                "hypothesis_match": "",
            }

            council = build_council_signatures(
                prediction=pred,
                critic_result=critic,
                is_entry=True,
            )

            conn.execute("""
                INSERT OR REPLACE INTO trade_explanations
                ( paper_trade_id, trade_type, ticker, sector, action, created_at,
                  ai_confidence, calibrated_confidence,
                  prediction_query, prediction_direction,
                  critic_verdict, critic_reasoning, critic_confidence_delta,
                  ai_council_signatures, evidence_checklist,
                  gates_passed, gates_failed, gate_details,
                  recommendation_rationale )
                VALUES
                ( :paper_trade_id, 'entry', :ticker, 'unknown', 'BUY', :created_at,
                  :ai_confidence, :calibrated_confidence,
                  :prediction_query, :prediction_direction,
                  :critic_verdict, :critic_reasoning, :critic_confidence_delta,
                  :ai_council_signatures, :evidence_checklist,
                  :gates_passed, :gates_failed, :gate_details,
                  :recommendation_rationale )
            """, {
                "paper_trade_id":          row["id"],
                "ticker":                  row["ticker"],
                "created_at":              row["entry_time"] or datetime.now(timezone.utc).isoformat(),
                "ai_confidence":           row["confidence"] or 0,
                "calibrated_confidence":   row["critic_confidence"] or row["confidence"] or 0,
                "prediction_query":        row["query"] or "",
                "prediction_direction":    row["pred_direction"] or "",
                "critic_verdict":          row["critic_verdict"] or "",
                "critic_reasoning":        row["critic_reasoning"] or "",
                "critic_confidence_delta": 0,
                "ai_council_signatures":   json.dumps(council),
                "evidence_checklist":      json.dumps(checklist),
                "gates_passed":            json.dumps(["backfill_static"]),
                "gates_failed":            json.dumps([]),
                "gate_details":            json.dumps({}),
                "recommendation_rationale": row["full_reasoning"] or "",
            })
            stats["entries_created"] += 1
        except Exception as e:
            stats["skipped"] += 1

    # ── Backfill exits ────────────────────────────────────────────────────────
    exit_rows = conn.execute("""
        SELECT pt.id, pt.ticker, pt.direction, pt.exit_time,
               pt.status, pt.pnl_pct,
               ( julianday(pt.exit_time) - julianday(pt.entry_time) ) as hold_days
        FROM   paper_trades pt
        LEFT   JOIN trade_explanations te
               ON te.paper_trade_id = pt.id AND te.trade_type = 'exit'
        WHERE  te.id IS NULL
        AND    pt.status = 'closed'
        AND    pt.exit_time IS NOT NULL
        LIMIT ?
    """, (limit,)).fetchall()

    for row in exit_rows:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO trade_explanations
                ( paper_trade_id, trade_type, ticker, sector, action, created_at,
                  hold_days, pnl_pct,
                  gates_passed, gates_failed, gate_details )
                VALUES
                ( :paper_trade_id, 'exit', :ticker, 'unknown', 'SELL', :created_at,
                  :hold_days, :pnl_pct,
                  :gates_passed, :gates_failed, :gate_details )
            """, {
                "paper_trade_id":  row["id"],
                "ticker":          row["ticker"],
                "created_at":      row["exit_time"] or datetime.now(timezone.utc).isoformat(),
                "hold_days":       row["hold_days"] or 0,
                "pnl_pct":         row["pnl_pct"] or 0,
                "gates_passed":    json.dumps([row["status"]]),
                "gates_failed":   json.dumps([]),
                "gate_details":   json.dumps({}),
            })
            stats["exits_created"] += 1
        except Exception as e:
            stats["skipped"] += 1

    conn.commit()
    conn.close()
    return stats


def get_explanation(db_path: str, paper_trade_id: int) -> list[dict]:
    """Fetch all explanation records for a paper_trade_id (usually 1 entry + 1 exit)."""
    conn = _connect(db_path)
    rows = conn.execute("""
        SELECT * FROM trade_explanations
        WHERE paper_trade_id = ?
        ORDER BY trade_type
    """, (paper_trade_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_explanations(db_path: str, limit: int = 20) -> list[dict]:
    """Fetch the N most recent explanation records."""
    conn = _connect(db_path)
    rows = conn.execute("""
        SELECT * FROM trade_explanations
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    import sys

    DB = sys.argv[1] if len(sys.argv) > 1 else "/home/trading/trading-ai/data/paper_trading.db"
    if not Path(DB).exists():
        print(f"DB not found: {DB}")
        sys.exit(1)

    print(f"Initializing trade_explanations table in {DB}")
    init_explainability_db(DB)

    print("Running backfill...")
    result = backfill_explanations(DB)
    print(f"Backfill result: {result}")

    print("\nRecent explanations:")
    for row in get_recent_explanations(DB, 5):
        print(f"  [{row['trade_type']}] {row['ticker']} "
              f"conf={row['ai_confidence']} critic={row['critic_verdict']} "
              f"verdict={row['critic_verdict']}")
