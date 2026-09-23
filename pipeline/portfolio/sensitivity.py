#!/usr/bin/env python3
"""
Sensitivity analysis helper for calibration parameters.

Runs the existing trim STFT computation across perturbations of named
parameters and returns a table of outcome deltas. This is a REPORT tool —
it does not apply anything to production.

Adapted from modales validate.py sensitivity plateau pattern: grid of
parameter perturbations to verify that a proposed calibration change is
robust (not overfit to recent noise).

Usage:
    from portfolio.sensitivity import perturb_and_report

    report = perturb_and_report(
        param_overrides={"trim.learning_rate": 0.10},
        n=3  # number of perturbation steps per parameter
    )
    print(report)
"""

import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from copy import copy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

PAPER_DB = Path(__file__).parent.parent.parent / "data" / "paper_trading.db"

# Default perturbation ranges by parameter type.
# Learning-rate-like params: large perturbation (+/- 50%)
# Threshold-like params: smaller perturbation (+/- 10%)
PERTURBATION_RANGES = {
    "learning_rate":  0.50,   # +/- 50% for rate params
    "lr":             0.50,
    "trim_weight":    0.50,
    "stft_scale":     0.50,
    "confidence_threshold": 0.10,  # +/- 10% for threshold params
    "threshold":      0.10,
    "gate":           0.10,
}

# Defaults for params not in the map above
DEFAULT_PERTURBATION = 0.50


def _get_perturbation_range(param_name: str) -> float:
    """Get the default perturbation fraction for a parameter name."""
    lower = param_name.lower()
    for key, frac in PERTURBATION_RANGES.items():
        if key in lower:
            return frac
    return DEFAULT_PERTURBATION


def _compute_stft_outcomes(
    conn: sqlite3.Connection,
    learning_rate: float = None
) -> dict:
    """
    Compute STFT outcomes using the same logic as trim.py, but as a pure
    function that accepts parameter overrides.

    This is the refactored compute path from trim.py's compute_stft() and
    apply_trim(). It does NOT write to the DB — it returns computed values.

    Args:
        conn: Open DB connection (read-only usage).
        learning_rate: Override learning rate. If None, uses per-sector LR
                       from sector_trim table.

    Returns:
        Dict of {sector: {"ltft_before": float, "ltft_after": float,
                          "stft": float, "lr": float, "delta": float}}
        where delta = ltft_after - ltft_before.
    """
    from portfolio.trim import (
        compute_stft, STFT_MAX, STFT_MIN, LTFT_MAX, LTFT_MIN
    )

    # Compute STFT outcomes
    try:
        # compute_stft uses PAPER_DB module-level var; we need to use the
        # current conn instead. Re-implement the core logic here:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        rows = conn.execute("""
            SELECT query, direction, was_correct, confidence
            FROM predictions
            WHERE verified_at >= ? AND was_correct IS NOT NULL
            ORDER BY verified_at DESC
        """, (cutoff,)).fetchall()
    except Exception:
        # No predictions or table doesn't exist in throwaway DB
        return {}

    if not rows:
        return {}

    # Accumulate per sector
    sector_results = {}
    for query, direction, correct, confidence in rows:
        # Map query to sector using keyword fallback (same as trim.py)
        from portfolio.trim import query_to_sector
        sector = query_to_sector(query)
        if sector not in sector_results:
            sector_results[sector] = {"correct": 0, "wrong": 0, "total": 0}
        sector_results[sector]["total"] += 1
        if correct:
            sector_results[sector]["correct"] += 1
        else:
            sector_results[sector]["wrong"] += 1

    # Get current trim state
    trim_rows = conn.execute("""
        SELECT sector, ltft, stft, learning_rate
        FROM sector_trim
    """).fetchall()
    if not trim_rows:
        return {}
    trim_state = {r[0]: {"ltft": r[1], "stft": r[2], "lr": r[3]} for r in trim_rows}

    # Compute corrections and simulate absorption
    outcomes = {}
    for sector, data in sector_results.items():
        if data["total"] < 2:
            continue
        if sector not in trim_state:
            continue

        win_rate = data["correct"] / data["total"]
        current_ltft = trim_state[sector]["ltft"]
        lr = learning_rate if learning_rate is not None else trim_state[sector]["lr"]

        expected_wr = 0.50 + current_ltft
        deviation = win_rate - expected_wr
        correction = deviation * 0.20
        correction = max(STFT_MIN, min(STFT_MAX, correction))

        new_ltft = current_ltft + (correction * lr)
        new_ltft = max(LTFT_MIN, min(LTFT_MAX, new_ltft))
        delta = new_ltft - current_ltft

        outcomes[sector] = {
            "ltft_before": current_ltft,
            "stft": correction,
            "lr": lr,
            "ltft_after": new_ltft,
            "delta": delta,
            "samples": data["total"],
        }

    return outcomes


def perturb_and_report(
    param_overrides: dict,
    n: int = 3,
    db_path: Path = None
) -> list[dict]:
    """
    Run sensitivity analysis by perturbing named calibration parameters.

    For each parameter in param_overrides, generates n perturbation steps
    on each side (+/-) and computes the trim outcome delta at each step.

    Args:
        param_overrides: Dict of {param_name: base_value}.
                         Keys are parameter names, values are the baseline
                         values to perturb from.
        n: Number of perturbation steps per direction (default 3).
           With n=3: base, base*(1-f), base*(1-f/2), base,
                     base*(1+f/2), base*(1+f), base*(1+f*1.5)
           Actually: n steps means n+1 levels per side.
        db_path: Optional path to a throwaway DB copy. If None, reads
                 from the production PAPER_DB (read-only queries only).

    Returns:
        List of dicts, one per perturbation run:
        {
            "param": str,
            "base_value": float,
            "perturbed_value": float,
            "pct_change": float,  # percentage from base
            "outcomes": dict,  # sector -> outcome dict from _compute_stft_outcomes
        }
    """
    if db_path is None:
        db_path = PAPER_DB

    conn = sqlite3.connect(db_path)
    results = []

    for param_name, base_value in param_overrides.items():
        frac = _get_perturbation_range(param_name)

        # Generate perturbation levels: n steps on each side
        # Levels: -n*frac, -(n-1)*frac, ..., -frac, 0, +frac, ..., +n*frac
        levels = []
        for i in range(n, 0, -1):
            levels.append(base_value * (1 - i * frac))
        levels.append(base_value)  # baseline
        for i in range(1, n + 1):
            levels.append(base_value * (1 + i * frac))

        for perturbed_value in levels:
            pct_change = ((perturbed_value - base_value) / base_value * 100) if base_value != 0 else 0.0

            # Compute outcomes with perturbed value
            outcomes = _compute_stft_outcomes(conn, learning_rate=perturbed_value)

            results.append({
                "param": param_name,
                "base_value": base_value,
                "perturbed_value": round(perturbed_value, 6),
                "pct_change": round(pct_change, 1),
                "outcomes": outcomes,
            })

    conn.close()
    return results


def format_report(results: list[dict]) -> str:
    """
    Format sensitivity results as a readable text table.

    Args:
        results: Output from perturb_and_report().

    Returns:
        Multi-line string with a formatted table.
    """
    if not results:
        return "No sensitivity results to report."

    lines = []
    lines.append("=" * 80)
    lines.append("SENSITIVITY ANALYSIS REPORT")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("=" * 80)

    # Group by parameter
    params = {}
    for r in results:
        params.setdefault(r["param"], []).append(r)

    for param, runs in params.items():
        lines.append(f"\n--- {param} (base={runs[0]['base_value']}) ---\n")
        lines.append(f"{'Pct':>6s}  {'Value':>10s}  {'Sector':>12s}  "
                      f"{'LTFT_before':>10s}  {'STFT':>8s}  {'LTFT_after':>10s}  {'Delta':>8s}")
        lines.append("-" * 72)

        for run in runs:
            pct_str = f"{run['pct_change']:+.1f}%"
            val_str = f"{run['perturbed_value']:.4f}"

            if not run["outcomes"]:
                lines.append(f"{pct_str}  {val_str}  {'(no outcomes)':>45s}")
                continue

            for sector, outcome in sorted(run["outcomes"].items()):
                lines.append(
                    f"{pct_str}  {val_str}  {sector:>12s}  "
                    f"{outcome['ltft_before']:+.4f}  "
                    f"{outcome['stft']:+.4f}  "
                    f"{outcome['ltft_after']:+.4f}  "
                    f"{outcome['delta']:+.4f}"
                )

    lines.append("\n" + "=" * 80)
    lines.append("END REPORT")
    lines.append("=" * 80)

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    import json

    # Default: perturb learning_rate using values from sector_trim
    # Example usage:
    #   python sensitivity.py --param trim.learning_rate --base 0.10 --n 3
    #   python sensitivity.py --json '{"trim.learning_rate": 0.10}'

    if len(sys.argv) < 2:
        print("Sensitivity analysis helper for calibration parameters.")
        print("Usage:")
        print("  python sensitivity.py --json '{\"trim.learning_rate\": 0.10}'")
        print("  python sensitivity.py --param trim.learning_rate --base 0.10 --n 3")
        sys.exit(0)

    # Parse args
    if "--json" in sys.argv:
        idx = sys.argv.index("--json")
        param_overrides = json.loads(sys.argv[idx + 1])
        n = int(sys.argv[idx + 2]) if idx + 2 < len(sys.argv) and not sys.argv[idx + 2].startswith("--") else 3
    elif "--param" in sys.argv:
        idx = sys.argv.index("--param")
        param_name = sys.argv[idx + 1]
        base_idx = sys.argv.index("--base")
        base_value = float(sys.argv[base_idx + 1])
        n_idx = sys.argv.index("--n") if "--n" in sys.argv else -1
        n = int(sys.argv[n_idx + 1]) if n_idx >= 0 else 3
        param_overrides = {param_name: base_value}
    else:
        print("Use --json or --param/--base")
        sys.exit(1)

    results = perturb_and_report(param_overrides, n=n)
    print(format_report(results))
