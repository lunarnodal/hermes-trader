"""
Theta-gang strike and expiry selection.
Finds optimal CSP or CC contract for a given ticker.
Target: 15-30 delta, 28-50 DTE, bid/ask spread < 10%

Alpaca OptionsSnapshot structure:
  opt.symbol              — OCC option symbol
  opt.implied_volatility  — IV
  opt.greeks.delta        — delta (negative for puts)
  opt.latest_quote.bid_price / ask_price
  Strike/expiry parsed from OCC symbol:
    UNDERLYING YY MM DD C/P STRIKE*1000
"""

import os
import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

TARGET_DELTA_MIN  = 0.15
TARGET_DELTA_MAX  = 0.30
TARGET_DTE_MIN    = 28
TARGET_DTE_MAX    = 50
MAX_SPREAD_PCT    = 0.35
MIN_PREMIUM       = 0.20   # per share ($20 per contract)


def _parse_occ_symbol(symbol: str) -> tuple[float | None, str | None]:
    """
    Parse strike and expiry from OCC option symbol.
    Format: UNDERLYING(var) YY MM DD C/P STRIKE*1000
    e.g. XLK261009P00187000 → (187.0, '2026-10-09')
    """
    try:
        ul_end = len(symbol) - 15
        date_str = symbol[ul_end:ul_end + 6]
        strike = float(symbol[ul_end + 7:]) / 1000.0
        year  = 2000 + int(date_str[:2])
        month = int(date_str[2:4])
        day   = int(date_str[4:6])
        return strike, f"{year}-{month:02d}-{day:02d}"
    except Exception:
        return None, None


def _get_options_client():
    from alpaca.data.historical import OptionHistoricalDataClient
    return OptionHistoricalDataClient(
        os.getenv("ALPACA_API_KEY", ""),
        os.getenv("ALPACA_SECRET_KEY", "")
    )


def select_csp_contract(ticker: str,
                         underlying_price: float,
                         max_strike_pct: float = 0.95) -> dict | None:
    """
    Select optimal cash-secured put contract.
    Targets 15-30 delta puts, 28-50 DTE, <10% bid/ask spread.

    Returns contract dict or None if no suitable contract found.
    """
    try:
        client = _get_options_client()
        from alpaca.data.requests import OptionChainRequest

        strike_min = round(underlying_price * 0.80, 0)
        strike_max = round(underlying_price * max_strike_pct, 0)
        expiry_start = (datetime.now(timezone.utc) + timedelta(days=TARGET_DTE_MIN)).strftime("%Y-%m-%d")
        expiry_end   = (datetime.now(timezone.utc) + timedelta(days=TARGET_DTE_MAX)).strftime("%Y-%m-%d")

        req = OptionChainRequest(
            underlying_symbol=ticker,
            expiration_date_gte=expiry_start,
            expiration_date_lte=expiry_end,
            strike_price_gte=str(strike_min),
            strike_price_lte=str(strike_max),
            type="put",
            limit=100,
        )

        chain = client.get_option_chain(req)
        if not chain:
            log.warning(f"[THETA] No put options for {ticker}")
            return None

        candidates = []
        for symbol, opt in chain.items():
            try:
                # Quote from latest_quote sub-object
                q   = opt.latest_quote
                bid = float(q.bid_price or 0) if q else 0
                ask = float(q.ask_price or 0) if q else 0
                if bid <= 0 or ask <= 0:
                    continue

                mid        = (bid + ask) / 2
                spread_pct = (ask - bid) / mid if mid > 0 else 1.0

                if spread_pct > MAX_SPREAD_PCT or mid < MIN_PREMIUM:
                    continue

                # Delta from greeks sub-object (put delta is negative)
                greeks = opt.greeks
                if not greeks or greeks.delta is None:
                    continue
                delta = abs(float(greeks.delta))

                if not (TARGET_DELTA_MIN <= delta <= TARGET_DELTA_MAX):
                    continue

                # Parse strike and expiry from OCC symbol
                strike, expiry = _parse_occ_symbol(symbol)
                if not strike or not expiry:
                    continue

                dte = (datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                       - datetime.now(timezone.utc)).days

                candidates.append({
                    "option_symbol":        symbol,
                    "strike":               strike,
                    "expiry":               expiry,
                    "dte":                  dte,
                    "delta":                round(delta, 3),
                    "mid_price":            round(mid, 2),
                    "bid":                  round(bid, 2),
                    "ask":                  round(ask, 2),
                    "spread_pct":           round(spread_pct * 100, 1),
                    "premium_per_contract": round(mid * 100, 2),
                    "cash_required":        round(strike * 100, 2),
                    "annualized_yield":     round(mid / strike * 365 / max(dte, 1) * 100, 2),
                    "iv":                   round(float(opt.implied_volatility or 0), 4),
                })

            except Exception as e:
                log.debug(f"[THETA] Skip {symbol}: {e}")
                continue

        if not candidates:
            log.info(f"[THETA] No qualifying CSP for {ticker} "
                     f"(${underlying_price:.2f}, strikes ${strike_min:.0f}-${strike_max:.0f})")
            return None

        def score(c):
            delta_score = 1.0 - abs(c["delta"] - 0.25) / 0.10
            dte_score   = 1.0 - abs(c["dte"] - 35) / 15
            yield_score = min(c["annualized_yield"] / 20.0, 1.0)
            return yield_score * 0.5 + delta_score * 0.3 + dte_score * 0.2

        best = max(candidates, key=score)
        log.info(f"[THETA] CSP: {ticker} strike=${best['strike']:.2f} "
                 f"({best['strike']/underlying_price*100:.0f}% of ${underlying_price:.2f}) "
                 f"expiry={best['expiry']} DTE={best['dte']} "
                 f"delta={best['delta']:.2f} premium=${best['mid_price']:.2f} "
                 f"yield={best['annualized_yield']:.1f}%/yr")
        return best

    except Exception as e:
        log.error(f"[THETA] CSP selection failed for {ticker}: {e}")
        return None


def select_covered_call_contract(ticker: str,
                                  underlying_price: float,
                                  min_strike_pct: float = 1.02) -> dict | None:
    """
    Select optimal covered call contract.
    Targets 15-30 delta calls, 28-50 DTE, <10% spread.
    Requires 100 shares of underlying to be held.
    """
    try:
        client = _get_options_client()
        from alpaca.data.requests import OptionChainRequest

        strike_min = round(underlying_price * min_strike_pct, 0)
        strike_max = round(underlying_price * 1.15, 0)
        expiry_start = (datetime.now(timezone.utc) + timedelta(days=TARGET_DTE_MIN)).strftime("%Y-%m-%d")
        expiry_end   = (datetime.now(timezone.utc) + timedelta(days=TARGET_DTE_MAX)).strftime("%Y-%m-%d")

        req = OptionChainRequest(
            underlying_symbol=ticker,
            expiration_date_gte=expiry_start,
            expiration_date_lte=expiry_end,
            strike_price_gte=str(strike_min),
            strike_price_lte=str(strike_max),
            type="call",
            limit=100,
        )

        chain = client.get_option_chain(req)
        if not chain:
            log.warning(f"[THETA] No call options for {ticker}")
            return None

        candidates = []
        for symbol, opt in chain.items():
            try:
                q   = opt.latest_quote
                bid = float(q.bid_price or 0) if q else 0
                ask = float(q.ask_price or 0) if q else 0
                if bid <= 0 or ask <= 0:
                    continue

                mid        = (bid + ask) / 2
                spread_pct = (ask - bid) / mid if mid > 0 else 1.0
                if spread_pct > MAX_SPREAD_PCT or mid < MIN_PREMIUM:
                    continue

                greeks = opt.greeks
                if not greeks or greeks.delta is None:
                    continue
                delta = float(greeks.delta)  # call delta is positive

                if not (TARGET_DELTA_MIN <= delta <= TARGET_DELTA_MAX):
                    continue

                strike, expiry = _parse_occ_symbol(symbol)
                if not strike or not expiry:
                    continue

                dte = (datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                       - datetime.now(timezone.utc)).days

                candidates.append({
                    "option_symbol":        symbol,
                    "strike":               strike,
                    "expiry":               expiry,
                    "dte":                  dte,
                    "delta":                round(delta, 3),
                    "mid_price":            round(mid, 2),
                    "bid":                  round(bid, 2),
                    "ask":                  round(ask, 2),
                    "spread_pct":           round(spread_pct * 100, 1),
                    "premium_per_contract": round(mid * 100, 2),
                    "annualized_yield":     round(mid / underlying_price * 365 / max(dte, 1) * 100, 2),
                    "iv":                   round(float(opt.implied_volatility or 0), 4),
                })

            except Exception as e:
                log.debug(f"[THETA] Skip {symbol}: {e}")
                continue

        if not candidates:
            log.info(f"[THETA] No qualifying CC for {ticker}")
            return None

        def score(c):
            delta_score = 1.0 - abs(c["delta"] - 0.25) / 0.10
            dte_score   = 1.0 - abs(c["dte"] - 35) / 15
            yield_score = min(c["annualized_yield"] / 20.0, 1.0)
            return yield_score * 0.5 + delta_score * 0.3 + dte_score * 0.2

        best = max(candidates, key=score)
        log.info(f"[THETA] CC: {ticker} strike=${best['strike']:.2f} "
                 f"expiry={best['expiry']} DTE={best['dte']} "
                 f"delta={best['delta']:.2f} premium=${best['mid_price']:.2f}")
        return best

    except Exception as e:
        log.error(f"[THETA] CC selection failed for {ticker}: {e}")
        return None


def check_earnings_veto(ticker: str, dte: int) -> bool:
    """Returns True if earnings fall within option DTE window — veto entry."""
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockEarningsRequest
        client = StockHistoricalDataClient(
            os.getenv("ALPACA_API_KEY", ""),
            os.getenv("ALPACA_SECRET_KEY", "")
        )
        start = datetime.now(timezone.utc)
        end   = start + timedelta(days=dte + 5)
        req   = StockEarningsRequest(symbol_or_symbols=ticker, start=start, end=end)
        earnings = client.get_stock_earnings(req)
        if earnings and ticker in earnings and earnings[ticker]:
            log.info(f"[THETA] VETO {ticker} — earnings within {dte}-day window")
            return True
        return False
    except Exception as e:
        log.debug(f"[THETA] Earnings check failed for {ticker}: {e}")
        return False  # Don't veto on API failure


if __name__ == "__main__":
    import sys
    from pathlib import Path
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")

    ticker = sys.argv[1] if len(sys.argv) > 1 else "XLK"
    price  = float(sys.argv[2]) if len(sys.argv) > 2 else 187.85

    print(f"\nSelecting CSP for {ticker} @ ${price:.2f}")
    contract = select_csp_contract(ticker, price)
    if contract:
        import json
        print(json.dumps(contract, indent=2))
    else:
        print("No contract found")
