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

# Sector keyword mapping — fallback when semantic lookup unavailable
SECTOR_KEYWORDS = {
    "technology":  ["technology", "ai", "semiconductor", "data center", "ai_infrastructure"],
    "energy":      ["energy", "oil", "gas", "utilities", "renewables"],
    "financials":  ["financial", "bank", "rates", "real estate"],
    "healthcare":  ["healthcare", "biotech", "pharma"],
    "defense":     ["defense", "aerospace"],
    "materials":   ["materials", "mining", "metals", "chemicals", "commodities"],
    "industrials": ["industrials", "manufacturing", "infrastructure"],
    "consumer":    ["consumer", "retail", "discretionary", "staples"],
    "macro":       ["market outlook", "macro", "s&p 500"],
}

# Sector prototype queries for semantic embedding comparison
SECTOR_PROTOTYPES = {
    "technology":  "technology AI semiconductor data center cloud computing software",
    "energy":      "energy oil gas utilities renewables crude petroleum power",
    "financials":  "financial banking interest rates real estate Fed monetary policy",
    "healthcare":  "healthcare biotech pharma drug approval clinical trial medical",
    "defense":     "defense aerospace military contractor weapons spending NATO",
    "materials":   "materials mining metals chemicals commodities copper lithium",
    "industrials": "industrials manufacturing infrastructure construction machinery",
    "consumer":    "consumer retail discretionary staples spending e-commerce",
    "macro":       "market outlook macro S&P 500 GDP recession Fed policy",
}

# Cache sector prototype embeddings
_sector_embeddings: dict[str, list[float]] = {}


def _get_sector_embeddings() -> dict[str, list[float]]:
    """Lazily compute and cache sector prototype embeddings."""
    global _sector_embeddings
    if _sector_embeddings:
        return _sector_embeddings
    try:
        import requests
        import os
        embed_url = os.getenv("LLAMA_EMBED_URL", "http://localhost:8081/v1/embeddings")
        embed_model = os.getenv("LLAMA_EMBED_MODEL", "bge-m3")
        for sector, proto in SECTOR_PROTOTYPES.items():
            resp = requests.post(
                embed_url,
                json={"model": embed_model, "input": proto},
                timeout=30
            )
            if resp.ok:
                data = resp.json().get("data", [])
                if data and "embedding" in data[0]:
                    _sector_embeddings[sector] = data[0]["embedding"]
        log.info(f"[TRIM] Sector embeddings loaded: {list(_sector_embeddings.keys())}")
    except Exception as e:
        log.warning(f"[TRIM] Could not load sector embeddings: {e}")
    return _sector_embeddings


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    import math
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def query_to_sector(query: str) -> str:
    """
    Map a prediction query to a canonical sector.
    Tries semantic similarity first, falls back to keyword matching.
    """
    # Try semantic sector mapping
    try:
        import requests
        import os
        embed_url = os.getenv("LLAMA_EMBED_URL", "http://localhost:8081/v1/embeddings")
        embed_model = os.getenv("LLAMA_EMBED_MODEL", "bge-m3")
        resp = requests.post(
            embed_url,
            json={"model": embed_model, "input": query},
            timeout=15
        )
        if resp.ok:
            data = resp.json().get("data", [])
            if data and "embedding" in data[0]:
                query_vec = data[0]["embedding"]
                sector_vecs = _get_sector_embeddings()
                if sector_vecs:
                    best_sector = max(
                        sector_vecs.keys(),
                        key=lambda s: _cosine_similarity(query_vec, sector_vecs[s])
                    )
                    best_score = _cosine_similarity(query_vec, sector_vecs[best_sector])
                    if best_score > 0.5:  # minimum similarity threshold
                        log.debug(f"[TRIM] Semantic sector: '{query[:40]}' → {best_sector} ({best_score:.2f})")
                        return best_sector
    except Exception as e:
        log.debug(f"[TRIM] Semantic sector lookup failed: {e}")

    # Keyword fallback
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


def _has_pending_prereg(conn: sqlite3.Connection, param_path: str) -> bool:
    """
    Check if there is a pending pre-registration for the given param_path.
    Returns True if any pending row matches.
    """
    try:
        row = conn.execute("""
            SELECT COUNT(*) FROM calibration_prereg
            WHERE param_path = ? AND status = 'pending'
        """, (param_path,)).fetchone()
        return row and row[0] > 0
    except Exception:
        # Table doesn't exist yet — safe fallback to False (no prereg enforcement)
        return False


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

        # Pre-registration guard: defer absorption if pending prereg exists
        if _has_pending_prereg(conn, "trim.learning_rate"):
            log.info(
                f"TRIM {sector}: prereg pending — absorption deferred "
                f"(would have: LTFT {old_ltft:+.3f} + STFT={stft_val:+.3f} × lr={lr})"
            )
            continue

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


def reconcile_industrials_trim() -> float:
    """
    Reconcile the ``industrials`` sector LTFT trim with recent verified outcomes.

    Reads current LTFT from sector_trim, fetches trailing-7d win rate and
    average direction confidence from predictions.  If the sector is
    outperforming (win_rate > 60%) but carries a stale deep penalty
    (abs(LTFT) > 25%), computes a fresh trim from the recent window,
    writes it to the DB, and returns the reconciled value.
    Otherwise returns the legacy LTFT unchanged (no write).

    Idempotent — safe to call multiple times per cycle.

    Returns:
        Final trim value for the ``industrials`` sector, clamped to [-0.5, 0.5].
    """
    conn = sqlite3.connect(PAPER_DB)

    # 1. Read current LTFT from config
    row = conn.execute(
        "SELECT ltft FROM sector_trim WHERE sector=?", ("industrials",)
    ).fetchone()
    current_ltft = row[0] if row else -0.10

    # 2. Fetch trailing-7d verified win rate and avg confidence
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    row2 = conn.execute(
        """SELECT COUNT(*), AVG(confidence),
                  SUM(CASE WHEN was_correct=1 THEN 1 ELSE 0 END)*1.0/COUNT(*)
           FROM predictions
           WHERE verified_at >= ? AND was_correct IS NOT NULL
             AND (query LIKE '%industrials%'
                  OR query LIKE '%manufacturing%'
                  OR query LIKE '%infrastructure%'
                  OR query LIKE '%machinery%'
                  OR query LIKE '%Caterpillar%'
                  OR query LIKE '%CAT%')""",
        (cutoff,)
    ).fetchone()

    count = row2[0] if row2 else 0
    avg_conf = row2[1] if row2 and row2[1] is not None else 0.0
    win_rate = row2[2] if row2 and row2[2] is not None else 0.0

    # Guard: need at least 2 outcomes to trust the signal
    if count < 2:
        log.info(
            f"[RECONCILE] industrials: only {count} outcomes in trailing 7d — "
            f"keeping legacy LTFT {current_ltft:+.3f}"
        )
        conn.close()
        return current_ltft

    # 3. Freshness / contradiction flag
    penalty_depth = abs(current_ltft)
    flagged = win_rate > 0.60 and penalty_depth > 0.25

    if not flagged:
        log.info(
            f"[RECONCILE] industrials: win_rate={win_rate:.0%} "
            f"penalty_depth={penalty_depth:.0%} — no contradiction, "
            f"keeping legacy LTFT {current_ltft:+.3f}"
        )
        conn.close()
        return current_ltft

    # 4. Re-derive trim from recent window
    miss_rate = 1.0 - win_rate
    new_ltft = -(miss_rate * avg_conf)
    new_ltft = max(-0.5, min(0.5, new_ltft))

    # 5. Write reconciled value to DB with provenance note
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        UPDATE sector_trim
        SET ltft=?, updated_at=?
        WHERE sector=?
    """, (new_ltft, now, "industrials"))
    conn.commit()

    log.info(
        f"[RECONCILE] industrials: FLAGGED "
        f"(win_rate={win_rate:.0%} penalty_depth={penalty_depth:.0%}) "
        f"-> LTFT {current_ltft:+.3f} -> {new_ltft:+.3f} "
        f"from {count} outcomes (avg_conf={avg_conf:.2f} miss_rate={miss_rate:.2f})"
    )

    conn.close()
    return new_ltft


def run_trim_cycle() -> None:
    log.info("═══ Sector trim cycle starting ═══")

    # Log current state
    state = get_trim_state()
    for sector, vals in state.items():
        log.info(f"  {sector}: LTFT={vals['ltft']:+.3f} STFT={vals['stft']:+.3f}")

    # Compute STFT corrections
    stft = compute_stft()

    # Reconcile industrials LTFT with verified outcomes BEFORE absorption
    # so the reconciled baseline is what apply_trim reads and persists
    reconcile_industrials_trim()

    apply_trim(stft)

    # Log new state
    log.info("── After trim ──")
    state = get_trim_state()
    for sector, vals in state.items():
        log.info(f"  {sector}: LTFT={vals['ltft']:+.3f} STFT={vals['stft']:+.3f}")

    log.info("═══ Trim cycle complete ═══")


if __name__ == "__main__":
    run_trim_cycle()
