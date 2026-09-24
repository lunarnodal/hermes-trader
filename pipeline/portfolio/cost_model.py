"""
cost_model.py — Per-leg half-spread + slippage cost estimator.

Ported from jennycruzy-convex costs.py (adapted for equities).
Computes one-way entry cost from live Alpaca bid/ask quotes:
    cost = half_spread * qty + slip_bps/10000 * notional

All parameters are configurable via config_reader.  Defaults are set to
conservative values for equities.
"""

from __future__ import annotations

import datetime
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

log = logging.getLogger(__name__)


# ─── Configurable defaults ───────────────────────────────────────────────────
# Overridable via portfolio_config table (config_reader.get_config).
# All values are conservative defaults for liquid equities.

DEFAULT_HALF_SPREAD_FACTOR  = 1.0    # pay at least the half-spread
DEFAULT_SLIPPAGE_BPS        = 5      # 5 bps per-leg slippage estimate
DEFAULT_COMMISSION_PER_SHARE = 0.0   # Alpaca paper has zero commissions


# ─── Quote fetcher ───────────────────────────────────────────────────────────

def get_quote(ticker: str) -> dict[str, Any] | None:
    """
    Fetch the latest bid/ask quote for a ticker from Alpaca.

    Returns dict with keys: bid, ask, mid, timestamp (epoch), age_seconds.
    Returns None if the fetch fails or the quote is missing.
    """
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest
        import os
        from dotenv import load_dotenv
        from pathlib import Path

        load_dotenv(Path(__file__).parent.parent.parent / ".env")

        api_key = os.getenv("ALPACA_API_KEY", "")
        api_secret = os.getenv("ALPACA_SECRET_KEY", "")

        if not api_key:
            log.warning("[COST_MODEL] No ALPACA_API_KEY — quote fetch skipped")
            return None

        client = StockHistoricalDataClient(api_key, api_secret)
        req = StockLatestQuoteRequest(symbol_or_symbols=[ticker])
        quotes = client.get_stock_latest_quote(req)

        if ticker not in quotes:
            log.warning(f"[COST_MODEL] No quote for {ticker}")
            return None

        q = quotes[ticker]
        if not q.bid_price or not q.ask_price:
            log.warning(f"[COST_MODEL] Incomplete quote for {ticker}")
            return None

        now = time.time()
        ts = q.timestamp if hasattr(q, "timestamp") and q.timestamp else now
        if isinstance(ts, datetime.datetime):
            # alpaca-py >= 0.44 returns a tz-aware datetime (UTC), not epoch
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=datetime.timezone.utc)
            ts = ts.timestamp()
        elif isinstance(ts, int) and ts > 1e12:
            ts = ts / 1000.0  # ms → seconds

        bid = float(q.bid_price)
        ask = float(q.ask_price)

        return {
            "bid": bid,
            "ask": ask,
            "mid": round((bid + ask) / 2.0, 4),
            "timestamp": ts,
            "age_seconds": round(now - ts, 1),
        }

    except Exception as e:
        log.warning(f"[COST_MODEL] Quote fetch failed for {ticker}: {e}")
        return None


# ─── Cost estimator ──────────────────────────────────────────────────────────

class CostModel:
    """
    Per-leg transaction cost estimator for equities.

    Computes one-way entry cost from:
      - Half-spread crossing cost (proportional to spread width)
      - Slippage estimate (bps on notional)
      - Per-share commission (zero for Alpaca paper)

    Parameters:
        half_spread_factor: multiplier on quoted half-spread (default 1.0)
        slippage_bps: flat slippage estimate in basis points (default 5)
        commission_per_share: broker fee per share (default 0.0)

    Config keys (portfolio_config table):
        cost_half_spread_factor, cost_slippage_bps, cost_commission_per_share
    """

    def __init__(
        self,
        half_spread_factor: float = DEFAULT_HALF_SPREAD_FACTOR,
        slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
        commission_per_share: float = DEFAULT_COMMISSION_PER_SHARE,
    ):
        self.half_spread_factor = half_spread_factor
        self.slippage_bps = slippage_bps
        self.commission_per_share = commission_per_share

    @classmethod
    def from_config(
        cls,
        conn=None,
        half_spread_factor: float = DEFAULT_HALF_SPREAD_FACTOR,
        slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
        commission_per_share: float = DEFAULT_COMMISSION_PER_SHARE,
    ) -> "CostModel":
        """
        Build CostModel from config_reader (DB > defaults).
        Falls back to defaults on any error.
        """
        try:
            from config_reader import get_config
            if conn is not None:
                half_spread_factor = get_config(
                    conn, "cost_half_spread_factor", half_spread_factor, float)
                slippage_bps = get_config(
                    conn, "cost_slippage_bps", slippage_bps, float)
                commission_per_share = get_config(
                    conn, "cost_commission_per_share", commission_per_share, float)
        except Exception as e:
            log.debug(f"[COST_MODEL] Config read failed, using defaults: {e}")

        return cls(
            half_spread_factor=half_spread_factor,
            slippage_bps=slippage_bps,
            commission_per_share=commission_per_share,
        )

    def estimate_entry_cost(
        self,
        ticker: str,
        qty: float,
        quote: dict[str, Any],
    ) -> dict[str, float]:
        """
        Estimate one-way entry cost for a single leg.

        Args:
            ticker: symbol
            qty: number of shares
            quote: dict from get_quote() with bid, ask, mid

        Returns dict:
            cost_usd:   total one-way cost in dollars
            cost_pct:   cost as fraction of notional (0.00 = 0%)
            spread_pct: relative bid-ask spread (0.00 = 0%)

        Formula:
            half_spread  = (ask - bid) / 2 * half_spread_factor
            slippage     = slippage_bps / 10000 * notional
            commission   = commission_per_share * qty
            cost_usd     = half_spread + slippage + commission
            cost_pct     = cost_usd / notional
            spread_pct   = (ask - bid) / mid
        """
        bid = quote["bid"]
        ask = quote["ask"]
        mid = quote["mid"]

        if mid <= 0:
            return {
                "cost_usd": 0.0,
                "cost_pct": 0.0,
                "spread_pct": 0.0,
            }

        spread = ask - bid
        half_spread = spread / 2.0 * self.half_spread_factor
        notional = mid * qty
        slippage = (self.slippage_bps / 10000.0) * notional
        commission = self.commission_per_share * qty

        cost_usd = half_spread + slippage + commission
        cost_pct = cost_usd / notional if notional > 0 else 0.0
        spread_pct = spread / mid if mid > 0 else 0.0

        return {
            "cost_usd": round(cost_usd, 4),
            "cost_pct": round(cost_pct, 6),
            "spread_pct": round(spread_pct, 6),
        }
