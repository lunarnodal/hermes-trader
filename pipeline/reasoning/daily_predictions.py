#!/usr/bin/env python3
"""
Daily scheduled prediction runs
Generates predictions for key sectors each morning
Records all predictions in paper trading DB for verification
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from reasoning.predict import run_prediction, save_and_record

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/mnt/qnap/timeseries/logs/daily_predictions.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

def _build_daily_queries() -> list[dict]:
    """
    Build daily prediction queries with current date context injected.
    Prevents stale query syndrome — same static string returning same
    cached/stale signal matches day after day.
    """
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    dow = datetime.now().strftime("%A")

    return [
        {
            "query":     f"Energy sector outlook as of {today} — oil, gas, utilities, renewables, recent price moves",
            "timeframe": "24h",
            "limit":     15,
            "label":     "energy"
        },
        {
            "query":     f"Technology and AI sector outlook {today} {dow} — semiconductors, ai infrastructure, data centers, recent earnings",
            "timeframe": "24h",
            "limit":     15,
            "label":     "technology_ai"
        },
        {
            "query":     f"Financial sector outlook {today} — banks, interest rates, real estate, Fed policy, recent macro",
            "timeframe": "24h",
            "limit":     15,
            "label":     "financials"
        },
        {
            "query":     f"Healthcare and biotech sector outlook {today} — drug approvals, clinical trials, recent news",
            "timeframe": "24h",
            "limit":     10,
            "label":     "healthcare"
        },
        {
            "query":     f"Materials and mining sector outlook {today} — metals, chemicals, commodities, supply chain",
            "timeframe": "24h",
            "limit":     12,
            "label":     "materials"
        },
        {
            "query":     f"Industrials sector outlook {today} — defense, aerospace, manufacturing, infrastructure spending",
            "timeframe": "24h",
            "limit":     12,
            "label":     "industrials"
        },
        {
            "query":     f"Consumer sector outlook {today} — retail, discretionary, staples, e-commerce, spending trends",
            "timeframe": "24h",
            "limit":     12,
            "label":     "consumer"
        },
        {
            "query":     f"Overall market outlook {today} {dow} — S&P 500, macro environment, geopolitical risks, Fed",
            "timeframe": "24h",
            "limit":     12,
            "label":     "market_overview"
        },
    ]

# DAILY_QUERIES built fresh at runtime in run_daily_predictions()


def run_daily_predictions() -> None:
    # Skip weekends and market holidays — uses shared market_calendar module
    try:
        from portfolio.market_calendar import is_trading_day, get_holiday_name
    except ImportError:
        from market_calendar import is_trading_day, get_holiday_name

    now = datetime.now()
    if not is_trading_day():
        holiday_name = get_holiday_name()
        if holiday_name:
            log.info(f"Skipping daily predictions — market holiday ({holiday_name})")
        else:
            log.info(f"Skipping daily predictions — weekend ({now.strftime('%A')})")
        return

    log.info("═══ Daily predictions starting ═══")
    log.info(f"    {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    # Print calibration report each morning
    try:
        from reasoning.calibration import get_calibration_report
        for line in get_calibration_report().splitlines():
            log.info(line)
    except Exception as e:
        log.warning(f"Calibration report error: {e}")

    results = []
    daily_queries = _build_daily_queries()  # fresh date each run

    # Load repeating failure history once
    import sqlite3 as _sql
    _db = Path(__file__).parent.parent / "data" / "paper_trading.db"

    def _check_repeating_failure(query: str, label: str) -> str | None:
        """Returns a warning string if this query has been wrong 3+ times recently."""
        try:
            _conn = _sql.connect(_db)
            sector_hint = label.replace("_", " ")
            rows = _conn.execute("""
                SELECT direction, was_correct, confidence
                FROM predictions
                WHERE query LIKE ? AND was_correct IS NOT NULL
                  AND confidence >= 0.70
                ORDER BY created_at DESC LIMIT 10
            """, (f"%{sector_hint}%",)).fetchall()
            _conn.close()
            if len(rows) < 3:
                return None
            recent_wrongs = sum(1 for r in rows[:5] if r[1] == 0)
            if recent_wrongs >= 3:
                directions = [r[0] for r in rows[:5] if r[1] == 0]
                most_common = max(set(directions), key=directions.count)
                return (
                    f"REPEATING FAILURE ALERT: This sector query has been wrong "
                    f"{recent_wrongs}/5 recent times at high confidence. "
                    f"Most common wrong direction: {most_common}. "
                    f"Apply extra skepticism — the model may be pattern-matching stale narratives."
                )
        except Exception:
            return None
        return None

    for q in daily_queries:
        log.info(f"── Predicting: {q['label']} ──")
        # Check for repeating failure pattern
        failure_warning = _check_repeating_failure(q["query"], q["label"])
        if failure_warning:
            log.warning(f"REPEATING FAILURE: {q['label']} — {failure_warning[:80]}")
            # Inject warning into query to force extra skepticism
            q = dict(q)
            q["query"] = q["query"] + f" [WARNING: {failure_warning}]"
        for attempt in range(3):
            try:
                result = run_prediction(
                    query=q["query"],
                    timeframe=q["timeframe"],
                    limit=q["limit"]
                )

                if result.get("prediction"):
                    p = result["prediction"]
                    log.info(f"   {q['label']}: {p.get('direction')} "
                             f"({p.get('probability', 0):.0%}) "
                             f"conf={p.get('confidence', 0):.2f}")
                    save_and_record(result)
                    results.append({
                        "label":     q["label"],
                        "direction": p.get("direction"),
                        "probability": p.get("probability"),
                        "confidence": p.get("confidence"),
                    })
                else:
                    log.warning(f"   {q['label']}: No structured prediction returned")
                break  # Success — exit retry loop
            except Exception as e:
                if attempt < 2:
                    log.warning(f"   {q['label']} attempt {attempt+1} failed: {e} — retrying in 30s")
                    import time
                    time.sleep(30)
                else:
                    log.error(f"   {q['label']} failed after 3 attempts: {e}")
    # Daily summary
    log.info("═══ Daily predictions complete ═══")
    log.info(f"    Generated {len(results)}/{len(DAILY_QUERIES)} predictions")
    for r in results:
        log.info(f"    {r['label']:20s}: {r['direction']:7s} "
                 f"{r['probability']:.0%} conf={r['confidence']:.2f}")


if __name__ == "__main__":
    run_daily_predictions()