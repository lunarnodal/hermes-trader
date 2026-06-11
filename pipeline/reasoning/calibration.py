"""
Prediction confidence calibration based on historical sector win rates.
Applies penalty/bonus to DeepSeek confidence scores before saving predictions.

Rolling window: last 20 verified predictions per sector
Target: push win rate toward 50% by reducing confidence on weak sectors
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timezone

log = logging.getLogger(__name__)

PAPER_DB = Path("/home/trading/trading-ai/data/paper_trading.db")

# Minimum predictions needed before applying calibration
MIN_SAMPLE_SIZE = 5

# Maximum adjustment in either direction
MAX_PENALTY  = -0.20
MAX_BONUS    =  0.10

# Target win rate — above this = bonus, below = penalty
TARGET_WIN_RATE = 0.50

# Sector keyword mapping (matches daily_predictions query labels)
SECTOR_KEYWORDS = {
    'technology':     ['technology', 'semiconductor', 'ai infrastructure', 'data center'],
    'healthcare':     ['healthcare', 'biotech'],
    'energy':         ['energy', 'oil', 'gas', 'utilities', 'renewables'],
    'financials':     ['financial', 'bank', 'rates', 'real estate'],
    'market_overview':['market outlook', 'macro', 's&p 500'],
    'materials':      ['materials', 'mining', 'metals', 'chemicals'],
    'industrials':    ['industrials', 'defense', 'aerospace', 'manufacturing'],
    'consumer':       ['consumer', 'retail', 'discretionary', 'staples'],
}


def get_sector_from_query(query: str) -> str:
    """Map a prediction query to a sector label"""
    q = query.lower()
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return sector
    return 'other'


def get_sector_win_rates(conn: sqlite3.Connection,
                         window: int = 20) -> dict:
    """
    Calculate rolling win rate for each sector over last N verified predictions.
    Returns dict of {sector: {'win_rate': float, 'total': int, 'correct': int}}
    """
    rows = conn.execute("""
        SELECT query, was_correct
        FROM predictions
        WHERE was_correct IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 200
    """).fetchall()

    from collections import defaultdict
    sector_data = defaultdict(list)

    for query, correct in rows:
        sector = get_sector_from_query(query)
        sector_data[sector].append(correct)

    stats = {}
    for sector, results in sector_data.items():
        recent = results[:window]  # already sorted DESC
        total   = len(recent)
        correct = sum(recent)
        stats[sector] = {
            'win_rate': correct / total if total > 0 else 0.50,
            'total':    total,
            'correct':  correct,
        }

    return stats


def calculate_adjustment(win_rate: float, total: int) -> float:
    """
    Calculate confidence adjustment based on historical win rate.

    Win rate vs target (50%):
      < 30%: -0.20 (severe penalty)
      30-40%: -0.10 (moderate penalty)
      40-50%: -0.05 (slight penalty)
      50-60%:  0.00 (no adjustment)
      60-70%: +0.05 (slight bonus)
      > 70%:  +0.10 (bonus)
    """
    if total < MIN_SAMPLE_SIZE:
        return 0.0  # not enough data

    if win_rate < 0.30:
        adj = -0.20
    elif win_rate < 0.40:
        adj = -0.10
    elif win_rate < 0.50:
        adj = -0.05
    elif win_rate < 0.60:
        adj = 0.00
    elif win_rate < 0.70:
        adj = +0.05
    else:
        adj = +0.10

    return max(MAX_PENALTY, min(MAX_BONUS, adj))


def calibrate_confidence(query: str, direction: str,
                         raw_confidence: float,
                         conn: sqlite3.Connection = None) -> tuple[float, str]:
    """
    Apply sector calibration to a raw confidence score.
    Returns (calibrated_confidence, explanation)
    """
    if conn is None:
        try:
            conn = sqlite3.connect(PAPER_DB)
            close_conn = True
        except Exception as e:
            log.warning(f"Calibration DB error: {e}")
            return raw_confidence, "no calibration (db error)"
    else:
        close_conn = False

    try:
        sector = get_sector_from_query(query)
        stats  = get_sector_win_rates(conn)

        if sector not in stats or stats[sector]['total'] < MIN_SAMPLE_SIZE:
            return raw_confidence, f"no calibration ({sector} — insufficient data)"

        s   = stats[sector]
        adj = calculate_adjustment(s['win_rate'], s['total'])

        calibrated = round(max(0.30, min(0.95, raw_confidence + adj)), 2)

        explanation = (
            f"{sector} win rate: {s['correct']}/{s['total']} = "
            f"{s['win_rate']:.0%} → adjustment: {adj:+.2f} "
            f"({raw_confidence:.2f} → {calibrated:.2f})"
        )

        if adj != 0:
            log.info(f"Calibration: {explanation}")

        return calibrated, explanation

    except Exception as e:
        log.warning(f"Calibration error: {e}")
        return raw_confidence, f"no calibration (error: {e})"
    finally:
        if close_conn:
            conn.close()


def get_calibration_report() -> str:
    """Generate a human-readable calibration report"""
    try:
        conn = sqlite3.connect(PAPER_DB)
        stats = get_sector_win_rates(conn)
        conn.close()

        lines = ["Sector calibration report:", ""]
        for sector, s in sorted(stats.items()):
            adj = calculate_adjustment(s['win_rate'], s['total'])
            bar = '█' * int(s['win_rate'] * 20)
            lines.append(
                f"  {sector:20s} {s['correct']:2d}/{s['total']:2d} "
                f"= {s['win_rate']:.0%}  {bar:20s}  adj: {adj:+.2f}"
            )
        return '\n'.join(lines)
    except Exception as e:
        return f"Calibration report error: {e}"


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    print(get_calibration_report())
