#!/usr/bin/env python3
"""
Sector Fuel Trim — Dynamic calibration adjustment
Mimics automotive short-term/long-term fuel trim logic.

STFT: computed from recent verified prediction outcomes (resets nightly)
LTFT: accumulated baseline, absorbs STFT gradually via learning_rate

Nightly cycle:
  1. Compute STFT from yesterday's verified outcomes per sector
  2. Absorb STFT into LTFT: LTFT += STFT * learning_rate
  3. Clamp LTFT to [-0.50, 0.0] — never positive, never catastrophic
  4. Reset STFT to 0
  5. Log changes
"""
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/mnt/qnap/timeseries/logs/trim.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

PAPER_DB  = Path(__file__).parent.parent.parent / "data" / "paper_trading.db"
LTFT_MIN  = -0.50   # never penalize more than 50%
LTFT_MAX  =  0.0    # never reward (trust is earned by removing penalty, not adding bonus)
STFT_MAX  =  0.20   # cap single-cycle STFT correction
STFT_MIN  = -0.20

# Sector keyword mapping — matches query text to sector
SECTOR_KEYWORDS = {
    "technology":  ["technology", "ai", "semiconductor", "data center"],
    "energy":      ["energy", "oil", "gas", "utilities", "renewables"],
    "financials":  ["financial", "bank", "rates", "real estate"],
    "healthcare":  ["healthcare", "biotech", "pharma"],
    "defense":     ["defense", "aerospace"],
    "materials":   ["materials", "mining", "metals", "chemicals", "commodities"],
    "industrials": ["industrials", "manufacturing", "infrastructure"],
    "consumer":    ["consumer", "retail", "discretionary", "staples"],
    "macro":       ["market outlook", "macro", "s&p 500"],
}

def query_to_sector(query: str) -> str:
    q = query.lower()
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return sector
    return "macro"

def compute_stft() -> dict:
    """
    Compute short-term fuel trim from yesterday's verified outcomes.
    Returns dict of {sector: stft_correction}
    """
    conn = sqlite3.connect(PAPER_DB)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    rows = conn.execute("""
        SELECT query, direction, was_correct, confidence
        FROM predictions
        WHERE verified_at >= ? AND was_correct IS NOT NULL
        ORDER BY verified_at DESC
    """, (cutoff,)).fetchall()
    conn.close()

    if not rows:
        log.info("No verified predictions in last 24h — STFT unchanged")
        return {}

    # Accumulate corrections per sector
    sector_results = {}
    for query, direction, correct, confidence in rows:
        sector = query_to_sector(query)
        if sector not in sector_results:
            sector_results[sector] = {"correct": 0, "wrong": 0, "total": 0}
        sector_results[sector]["total"] += 1
        if correct:
            sector_results[sector]["correct"] += 1
        else:
            sector_results[sector]["wrong"] += 1

    # Compute STFT correction per sector
    # Outperforming → positive STFT (reduce penalty)
    # Underperforming → negative STFT (increase penalty)
    stft = {}
    for sector, data in sector_results.items():
        if data["total"] < 2:
            continue  # need at least 2 outcomes to adjust
        win_rate = data["correct"] / data["total"]

        # Expected win rate from LTFT — read current LTFT
        trim_conn = sqlite3.connect(PAPER_DB)
        row = trim_conn.execute(
            "SELECT ltft FROM sector_trim WHERE sector=?", (sector,)
        ).fetchone()
        trim_conn.close()

        current_ltft = row[0] if row else -0.10
        # Implied expected win rate from penalty
        # If LTFT is -0.20, we expect ~30% win rate
        # If actual is 67%, system outperforming → loosen trim
        expected_wr = 0.50 + current_ltft  # rough baseline

        deviation = win_rate - expected_wr
        correction = deviation * 0.20  # scale correction
        correction = max(STFT_MIN, min(STFT_MAX, correction))

        stft[sector] = correction
        log.info(
            f"STFT {sector}: win_rate={win_rate:.0%} expected={expected_wr:.0%} "
            f"deviation={deviation:+.0%} correction={correction:+.3f}"
        )

    return stft


def apply_trim(stft: dict) -> None:
    """
    Apply STFT corrections:
    - Update STFT in DB
    - Absorb into LTFT via learning_rate
    - Reset STFT
    - Clamp LTFT
    """
    if not stft:
        log.info("No STFT corrections to apply")
        return

    conn = sqlite3.connect(PAPER_DB)
    now = datetime.now(timezone.utc).isoformat()

    for sector, stft_val in stft.items():
        row = conn.execute(
            "SELECT ltft, stft, learning_rate FROM sector_trim WHERE sector=?",
            (sector,)
        ).fetchone()

        if not row:
            log.warning(f"Sector {sector} not in sector_trim table — skipping")
            continue

        old_ltft, old_stft, lr = row

        # Absorb STFT into LTFT
        new_ltft = old_ltft + (stft_val * lr)
        new_ltft = max(LTFT_MIN, min(LTFT_MAX, new_ltft))

        conn.execute("""
            UPDATE sector_trim
            SET ltft=?, stft=?, updated_at=?, last_stft_reset=?
            WHERE sector=?
        """, (new_ltft, 0.0, now, now, sector))

        log.info(
            f"TRIM {sector}: LTFT {old_ltft:+.3f} → {new_ltft:+.3f} "
            f"(absorbed STFT={stft_val:+.3f} × lr={lr})"
        )

    conn.commit()
    conn.close()


def get_trim_state() -> dict:
    """Return current trim state for all sectors."""
    conn = sqlite3.connect(PAPER_DB)
    rows = conn.execute(
        "SELECT sector, ltft, stft, updated_at FROM sector_trim ORDER BY ltft"
    ).fetchall()
    conn.close()
    return {r[0]: {"ltft": r[1], "stft": r[2], "updated_at": r[3]} for r in rows}


def run_trim_cycle() -> None:
    log.info("═══ Sector trim cycle starting ═══")

    # Log current state
    state = get_trim_state()
    for sector, vals in state.items():
        log.info(f"  {sector}: LTFT={vals['ltft']:+.3f} STFT={vals['stft']:+.3f}")

    # Compute and apply
    stft = compute_stft()
    apply_trim(stft)

    # Log new state
    log.info("── After trim ──")
    state = get_trim_state()
    for sector, vals in state.items():
        log.info(f"  {sector}: LTFT={vals['ltft']:+.3f} STFT={vals['stft']:+.3f}")

    log.info("═══ Trim cycle complete ═══")


if __name__ == "__main__":
    run_trim_cycle()
