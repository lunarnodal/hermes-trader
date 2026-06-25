"""
VIX volatility gate

When market fear (VIX) is elevated, reduce position sizes or
pause new entries entirely. High VIX = high uncertainty =
increased gap risk and stop loss probability.

Thresholds:
  VIX < 20:  Normal — full position sizes
  VIX 20-25: Elevated — reduce position sizes by 25%
  VIX 25-30: High — reduce position sizes by 50%
  VIX > 30:  Extreme fear — pause all new entries

Data source: Yahoo Finance (free, VIX = ^VIX)
"""

import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

VIX_TICKER = "^VIX"
YF_BASE    = "https://query1.finance.yahoo.com/v8/finance/chart"

# Cache VIX for 30 minutes to avoid hammering Yahoo Finance
_vix_cache = {"value": None, "fetched_at": None}
VIX_CACHE_MINUTES = 30


def get_vix() -> float | None:
    """Fetch current VIX value from Yahoo Finance"""
    global _vix_cache

    # Check cache
    if _vix_cache["value"] and _vix_cache["fetched_at"]:
        age = (datetime.now(timezone.utc) - _vix_cache["fetched_at"]).total_seconds()
        if age < VIX_CACHE_MINUTES * 60:
            return _vix_cache["value"]

    try:
        resp = requests.get(
            f"{YF_BASE}/{VIX_TICKER}",
            params={"interval": "1m", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        resp.raise_for_status()
        data   = resp.json()
        result = data["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]

        # Get most recent non-null value
        vix = next((v for v in reversed(closes) if v is not None), None)
        if vix:
            _vix_cache["value"]      = round(vix, 2)
            _vix_cache["fetched_at"] = datetime.now(timezone.utc)
            log.info(f"VIX: {vix:.1f}")
            return _vix_cache["value"]
    except Exception as e:
        log.warning(f"VIX fetch failed: {e}")
    return None


def get_vix_gate() -> dict:
    """
    Get VIX-based trading gate status.
    Returns action, size_multiplier, and reason.
    """
    vix = get_vix()

    if vix is None:
        return {
            "vix":              None,
            "action":           "ok",
            "size_multiplier":  1.0,
            "reason":           "VIX unavailable — proceeding normally"
        }

    if vix > 30:
        return {
            "vix":             vix,
            "action":          "pause",
            "size_multiplier": 0.0,
            "reason":          f"VIX {vix:.1f} > 30 — extreme fear, pausing all entries"
        }
    elif vix > 25:
        return {
            "vix":             vix,
            "action":          "reduce",
            "size_multiplier": 0.5,
            "reason":          f"VIX {vix:.1f} > 25 — high volatility, reducing size 50%"
        }
    elif vix > 20:
        return {
            "vix":             vix,
            "action":          "reduce",
            "size_multiplier": 0.75,
            "reason":          f"VIX {vix:.1f} > 20 — elevated volatility, reducing size 25%"
        }
    else:
        return {
            "vix":             vix,
            "action":          "ok",
            "size_multiplier": 1.0,
            "reason":          f"VIX {vix:.1f} — normal volatility"
        }


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    gate = get_vix_gate()
    print(f"VIX: {gate['vix']}")
    print(f"Action: {gate['action']}")
    print(f"Size multiplier: {gate['size_multiplier']:.0%}")
    print(f"Reason: {gate['reason']}")
