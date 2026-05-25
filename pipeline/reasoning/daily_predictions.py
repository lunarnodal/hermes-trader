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

DAILY_QUERIES = [
    {
        "query":     "Energy sector outlook — oil, gas, utilities, renewables",
        "timeframe": "24h",
        "limit":     15,
        "label":     "energy"
    },
    {
        "query":     "Technology and AI sector outlook — semiconductors, ai infrastructure, data centers",
        "timeframe": "24h",
        "limit":     15,
        "label":     "technology_ai"
    },
    {
        "query":     "Financial sector outlook — banks, rates, real estate, macro",
        "timeframe": "24h",
        "limit":     15,
        "label":     "financials"
    },
    {
        "query":     "Healthcare and biotech sector outlook",
        "timeframe": "24h",
        "limit":     10,
        "label":     "healthcare"
    },
    {
        "query":     "Overall market outlook — S&P 500, macro environment, geopolitical risks",
        "timeframe": "24h",
        "limit":     12,
        "label":     "market_overview"
    },
]


def run_daily_predictions() -> None:
    # Skip weekends and market holidays
    # Server is America/New_York so datetime.now() is ET directly
    from datetime import date
    MARKET_HOLIDAYS_2026 = {
        date(2026, 1,  1), date(2026, 1, 19), date(2026, 2, 16),
        date(2026, 4,  3), date(2026, 5, 25), date(2026, 7,  3),
        date(2026, 9,  7), date(2026, 11, 26), date(2026, 11, 27),
        date(2026, 12, 25),
    }
    now = datetime.now()
    if now.weekday() >= 5:
        log.info(f"Skipping daily predictions — weekend ({now.strftime('%A')})")
        return
    if date.today() in MARKET_HOLIDAYS_2026:
        log.info(f"Skipping daily predictions — market holiday ({date.today()})")
        return

    log.info("═══ Daily predictions starting ═══")
    log.info(f"    {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    results = []
    for q in DAILY_QUERIES:
        log.info(f"── Predicting: {q['label']} ──")
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

        except Exception as e:
            log.error(f"   {q['label']} failed: {e}")
            continue

    # Daily summary
    log.info("═══ Daily predictions complete ═══")
    log.info(f"    Generated {len(results)}/{len(DAILY_QUERIES)} predictions")
    for r in results:
        log.info(f"    {r['label']:20s}: {r['direction']:7s} "
                 f"{r['probability']:.0%} conf={r['confidence']:.2f}")


if __name__ == "__main__":
    run_daily_predictions()