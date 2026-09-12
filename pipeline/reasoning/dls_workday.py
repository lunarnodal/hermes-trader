#!/usr/bin/env python3
"""
DLs_workday v10 — Dynamic Distribution Groups for workday-aware signal allocation.

Groups sectors into dynamic distributions that shift based on the current
workday context (day-of-week patterns, proximity to holidays, early close days).

Used by daily_predictions._build_daily_queries() to modulate per-sector
query limits and weight allocations based on temporal factors.
"""

import logging
import sqlite3
from datetime import date, timedelta

from portfolio.market_calendar import is_trading_day, is_market_holiday, is_early_close, get_holiday_name

log = logging.getLogger(__name__)

VERSION = 10

DEFAULT_SECTORS = [
    "energy",
    "technology_ai",
    "financials",
    "healthcare",
    "materials",
    "industrials",
    "consumer",
    "market_overview",
    "merger_arbitrage",
]

# Sectors considered "defensive" — boosted on early-close / risk-off days
_DEFENSIVE_SECTORS = {"healthcare", "consumer"}

# Sectors considered "speculative" — reduced on early-close / risk-off days
_SPECULATIVE_SECTORS = {"merger_arbitrage"}

# Base query limits per sector (from daily_predictions.py _build_daily_queries)
_BASE_LIMITS = {
    "energy": 15,
    "technology_ai": 15,
    "financials": 15,
    "healthcare": 10,
    "materials": 12,
    "industrials": 12,
    "consumer": 12,
    "market_overview": 12,
    "merger_arbitrage": 15,
}

# Default day-of-week bias factors when rules.db has no sector_day_of_week data.
# Applied as multiplier: weight *= (1 + bias)
# Positive = sector tends to outperform on that day
_DOW_BIAS = {
    ("energy", "Monday"): 0.05,
    ("financials", "Monday"): 0.05,
    ("market_overview", "Monday"): 0.02,
    ("technology_ai", "Tuesday"): 0.05,
    ("technology_ai", "Wednesday"): 0.05,
    ("materials", "Thursday"): 0.03,
    ("industrials", "Thursday"): 0.03,
    ("merger_arbitrage", "Friday"): 0.08,
    ("healthcare", "Friday"): 0.03,
}


class DLsWorkday:
    """Dynamic Distribution Groups for workday-aware signal allocation."""

    def __init__(self, sector_labels: list[str]):
        """
        Initialize with sector labels.

        Args:
            sector_labels: List of sector labels (same as daily_predictions sectors).
        """
        self.sector_labels = sector_labels
        self._dow_bias: dict[tuple[str, str], float] = self._load_dow_bias()

    def _load_dow_bias(self) -> dict[tuple[str, str], float]:
        """
        Load day-of-week historical bias from rules.db.

        Falls back to _DOW_BIAS defaults if table does not exist or is empty.
        """
        from pathlib import Path
        db_path = Path(__file__).parent.parent.parent / "data" / "rules.db"

        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            # Check if table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sector_day_of_week'"
            )
            if not cursor.fetchone():
                conn.close()
                log.info("DLs_workday: no sector_day_of_week table in rules.db, using defaults")
                return dict(_DOW_BIAS)

            # Load data
            cursor.execute(
                "SELECT sector, day_name, avg_direction, avg_confidence FROM sector_day_of_week"
            )
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                log.info("DLs_workday: sector_day_of_week table empty, using defaults")
                return dict(_DOW_BIAS)

            bias: dict[tuple[str, str], float] = {}
            for sector, day_name, avg_direction, avg_confidence in rows:
                # Convert avg_direction to bias factor, capped at ±20%
                factor = max(-0.2, min(0.2, avg_direction * 0.2))
                bias[(sector, day_name)] = factor
                log.debug("DLs_workday: loaded bias for %s/%s = %s", sector, day_name, factor)

            if not bias:
                return dict(_DOW_BIAS)

            return bias

        except Exception as e:
            log.warning("DLs_workday: failed to load dow bias from rules.db: %s, using defaults", e)
            return dict(_DOW_BIAS)

    def _get_today_info(self) -> dict:
        """Return today's trading context."""
        today = date.today()
        holiday_name = get_holiday_name(today)

        # Check holiday proximity (within 2 days)
        holiday_proximity = 0
        for delta in range(1, 3):
            if is_market_holiday(today + timedelta(days=delta)):
                holiday_proximity = delta
                break
            if is_market_holiday(today - timedelta(days=delta)):
                holiday_proximity = delta
                break

        return {
            "date": today,
            "day_name": today.strftime("%A"),
            "is_holiday": is_market_holiday(today),
            "holiday_name": holiday_name,
            "is_early_close": is_early_close(today),
            "holiday_proximity": holiday_proximity,
        }

    def get_distribution(self) -> dict[str, float]:
        """
        Return sector weights for today, summing to 1.0.

        Weights start uniform (1/N), then modulated by:
        - Day-of-week historical bias (±20%)
        - Holiday proximity effects (within 2 days)
        - Early close effects (reduce speculative, boost defensive)
        """
        today = self._get_today_info()
        sectors = self.sector_labels
        n = len(sectors)

        # Start with uniform weights
        weights: dict[str, float] = {s: 1.0 / n for s in sectors}

        # Apply day-of-week bias
        day_name = today["day_name"]
        for s in sectors:
            bias = self._dow_bias.get((s, day_name), 0.0)
            weights[s] *= (1.0 + bias)

        # Apply holiday proximity effects (reduce speculative, boost defensive)
        if today["holiday_proximity"] > 0:
            proximity_factor = 0.15 / today["holiday_proximity"]  # Stronger when closer
            for s in sectors:
                if s in _SPECULATIVE_SECTORS:
                    weights[s] *= (1.0 - proximity_factor)
                if s in _DEFENSIVE_SECTORS:
                    weights[s] *= (1.0 + proximity_factor * 0.5)

        # Apply early close effects
        if today["is_early_close"]:
            for s in sectors:
                if s in _SPECULATIVE_SECTORS:
                    weights[s] *= 0.7  # -30%
            # Boost defensive sectors proportionally
            total_reduction = 0.0
            for s in sectors:
                if s in _SPECULATIVE_SECTORS:
                    total_reduction += (1.0 / n) * 0.3
            boost_per_defensive = total_reduction / len(_DEFENSIVE_SECTORS)
            for s in sectors:
                if s in _DEFENSIVE_SECTORS:
                    weights[s] += boost_per_defensive

        # Normalize to sum to 1.0
        total = sum(weights.values())
        if total > 0:
            weights = {s: w / total for s, w in weights.items()}

        # Round to 6 decimal places to avoid floating point drift
        weights = {s: round(w, 6) for s, w in weights.items()}

        # Final normalization after rounding
        total = sum(weights.values())
        if abs(total - 1.0) > 1e-6:
            diff = 1.0 - total
            max_sector = max(weights, key=weights.get)
            weights[max_sector] = round(weights[max_sector] + diff, 6)

        return weights

    def get_query_limits(self) -> dict[str, int]:
        """
        Return per-sector query limit overrides based on distribution weights.

        Higher-weight sectors get +2 limit, lower-weight get -1.
        Clamp between min 5 and max 20.
        """
        weights = self.get_distribution()
        sectors = self.sector_labels
        sorted_vals = sorted(weights.values())
        median_weight = sorted_vals[len(sorted_vals) // 2]

        limits = {}
        for s in sectors:
            base = _BASE_LIMITS.get(s, 12)
            if weights[s] >= median_weight:
                limits[s] = base + 2
            else:
                limits[s] = base - 1
            # Clamp between min 5 and max 20
            limits[s] = max(5, min(20, limits[s]))

        return limits

    def summary(self) -> str:
        """
        Return human-readable one-liner.

        Format: "DLs_workday v10 — {day} distribution: {top2_sectors} leading ({weights})"
        """
        weights = self.get_distribution()
        today = self._get_today_info()

        # Sort by weight descending, get top 2
        sorted_sectors = sorted(weights.items(), key=lambda x: x[1], reverse=True)
        top2 = sorted_sectors[:2]
        top2_labels = f"{top2[0][0]}={top2[0][1]:.3f}, {top2[1][0]}={top2[1][1]:.3f}"

        all_weights = ", ".join(f"{s}={w:.3f}" for s, w in sorted_sectors)

        return (
            f"DLs_workday v10 — {today['day_name']} distribution: "
            f"{top2_labels} leading ({all_weights})"
        )


if __name__ == "__main__":
    dls = DLsWorkday(DEFAULT_SECTORS)
    weights = dls.get_distribution()
    limits = dls.get_query_limits()

    print(dls.summary())
    print(f"\nWeights sum: {sum(weights.values())}")
    print(f"\nDistribution:")
    for s, w in sorted(weights.items(), key=lambda x: x[1], reverse=True):
        print(f"  {s}: {w:.6f}")
    print(f"\nQuery limits:")
    for s, l in limits.items():
        print(f"  {s}: {l}")
