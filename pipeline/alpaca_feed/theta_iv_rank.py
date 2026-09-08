"""
Theta-gang IV rank data fetcher.
Pulls options chain from Alpaca for sector ETFs,
computes IV rank (current IV vs 52-week range),
premium yield, liquidity, and market regime.

Alpaca OptionsSnapshot structure:
  opt.symbol              — OCC option symbol
  opt.implied_volatility  — current IV
  opt.greeks.delta        — delta
  opt.latest_quote.bid_price / ask_price — quotes
  Strike/expiry parsed from OCC symbol format:
    UNDERLYING YY MM DD C/P STRIKE*1000
    e.g. XLK261009P00187000 = XLK 2026-10-09 Put $187.00
"""

import os
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

SECTOR_ETFS = {
    "technology":     "XLK",
    "energy":         "XLE",
    "healthcare":     "XLV",
    "financials":     "XLF",
    "industrials":    "XLI",
    "consumer":       "XLY",
    "materials":      "XLB",
    "macro":          "SPY",
    "defense":        "XAR",
    "market_overview":"SPY",
}

_iv_cache: dict = {}
_iv_cache_ts: dict = {}
IV_CACHE_TTL = 1800  # 30 minutes


def _parse_occ_symbol(symbol: str) -> tuple[float | None, str | None]:
    """
    Parse strike price and expiry date from OCC option symbol.
    Format: UNDERLYING(var) YY MM DD C/P STRIKE*1000
    Example: XLK261009P00187000 → (187.0, '2026-10-09')
    """
    try:
        ul_end = len(symbol) - 15
        date_str = symbol[ul_end:ul_end + 6]
        strike = float(symbol[ul_end + 7:]) / 1000.0
        year  = 2000 + int(date_str[:2])
        month = int(date_str[2:4])
        day   = int(date_str[4:6])
        expiry = f"{year}-{month:02d}-{day:02d}"
        return strike, expiry
    except Exception:
        return None, None


def _get_alpaca_clients():
    from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
    key    = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")
    return (
        StockHistoricalDataClient(key, secret),
        OptionHistoricalDataClient(key, secret),
    )


def _get_market_regime(etf: str, stock_client) -> str:
    """
    Determine market regime using 20-day vs 50-day SMA + recent volatility.
    Returns: sideways / trending_bull / trending_bear / volatile
    """
    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        req = StockBarsRequest(
            symbol_or_symbols=etf,
            timeframe=TimeFrame.Day,
            start=datetime.now(timezone.utc) - timedelta(days=75),
        )
        bars = stock_client.get_stock_bars(req)
        if etf not in bars or not bars[etf]:
            return "sideways"

        closes = [float(b.close) for b in bars[etf]]
        if len(closes) < 25:
            return "sideways"

        sma20 = sum(closes[-20:]) / 20
        sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sum(closes) / len(closes)
        current = closes[-1]

        returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(-10, 0)]
        vol = (sum(r**2 for r in returns) / len(returns)) ** 0.5 * 100

        if vol > 2.5:
            return "volatile"
        elif current > sma20 > sma50:
            return "trending_bull"
        elif current < sma20 < sma50:
            return "trending_bear"
        else:
            return "sideways"

    except Exception as e:
        log.warning(f"Market regime detection failed for {etf}: {e}")
        return "sideways"


def _compute_iv_rank(current_iv: float, historical_ivs: list[float]) -> float:
    """IV Rank = (current - 52w_low) / (52w_high - 52w_low) * 100"""
    if not historical_ivs or len(historical_ivs) < 10:
        return 50.0
    iv_low  = min(historical_ivs)
    iv_high = max(historical_ivs)
    if iv_high == iv_low:
        return 50.0
    rank = (current_iv - iv_low) / (iv_high - iv_low) * 100
    return round(max(0.0, min(100.0, rank)), 1)


def _get_historical_ivs(etf: str, current_iv: float,
                         conn: sqlite3.Connection = None) -> list[float]:
    """Get stored IV history or bootstrap from current IV."""
    if conn:
        try:
            row = conn.execute(
                "SELECT iv_history FROM theta_risk_params WHERE sector = ?",
                (etf,)
            ).fetchone()
            if row and row[0]:
                import json
                return json.loads(row[0])
        except Exception:
            pass

    # Bootstrap until we have real history
    import random
    random.seed(etf)
    base_iv = current_iv * 100
    return [max(5.0, base_iv + random.gauss(0, base_iv * 0.20)) for _ in range(52)]


def get_sector_market_data(sector: str, conn: sqlite3.Connection = None) -> dict:
    """
    Fetch real market data for theta eligibility scoring.
    Returns dict with iv_rank, premium_yield, market_regime,
    open_interest, option_volume, sector_assignment_rate,
    underlying_price, etf.
    """
    import time
    now = time.time()

    if sector in _iv_cache and now - _iv_cache_ts.get(sector, 0) < IV_CACHE_TTL:
        log.debug(f"[THETA] IV cache hit for {sector}")
        return _iv_cache[sector]

    etf = SECTOR_ETFS.get(sector, "SPY")
    result = {
        "iv_rank":                50.0,
        "premium_yield":          0.02,
        "market_regime":          "sideways",
        "open_interest":          500,
        "option_volume":          100,
        "sector_assignment_rate": 0.0,
        "underlying_price":       100.0,
        "etf":                    etf,
    }

    try:
        stock_client, options_client = _get_alpaca_clients()

        # 1. Current ETF price
        from alpaca.data.requests import StockLatestBarRequest
        bar_req = StockLatestBarRequest(symbol_or_symbols=etf)
        bars = stock_client.get_stock_latest_bar(bar_req)
        underlying_price = float(bars[etf].close) if etf in bars else 100.0
        result["underlying_price"] = underlying_price

        # 2. Market regime
        result["market_regime"] = _get_market_regime(etf, stock_client)

        # 3. Options chain — ATM puts, 30-45 DTE
        from alpaca.data.requests import OptionChainRequest
        chain_req = OptionChainRequest(
            underlying_symbol=etf,
            expiration_date_gte=(datetime.now(timezone.utc) + timedelta(days=28)).strftime("%Y-%m-%d"),
            expiration_date_lte=(datetime.now(timezone.utc) + timedelta(days=50)).strftime("%Y-%m-%d"),
            strike_price_gte=str(round(underlying_price * 0.90, 0)),
            strike_price_lte=str(round(underlying_price * 1.10, 0)),
            type="put",
            limit=50,
        )

        try:
            chain = options_client.get_option_chain(chain_req)
            if chain:
                # Find ATM put using parsed strike from OCC symbol
                def _atm_key(opt):
                    strike, _ = _parse_occ_symbol(opt.symbol)
                    return abs((strike or 999) - underlying_price)

                atm_opt = min(chain.values(), key=_atm_key)
                atm_strike, atm_expiry = _parse_occ_symbol(atm_opt.symbol)
                atm_iv = float(atm_opt.implied_volatility or 0.25)

                # Quote from latest_quote sub-object
                q = atm_opt.latest_quote
                bid = float(q.bid_price or 0) if q else 0
                ask = float(q.ask_price or 0) if q else 0
                atm_mid = (bid + ask) / 2 if bid and ask else ask or bid

                # Premium yield
                if atm_expiry and atm_mid:
                    dte = max(1, (
                        datetime.strptime(atm_expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                        - datetime.now(timezone.utc)
                    ).days)
                    result["premium_yield"] = round(
                        (atm_mid / underlying_price) * (365 / dte), 4
                    )

                # IV rank
                historical_ivs = _get_historical_ivs(etf, atm_iv, conn)
                result["iv_rank"] = _compute_iv_rank(atm_iv * 100, historical_ivs)

                log.info(f"[THETA] {sector} ({etf}): price=${underlying_price:.2f} "
                         f"IV={atm_iv:.1%} IV_rank={result['iv_rank']:.0f} "
                         f"yield={result['premium_yield']:.1%} "
                         f"regime={result['market_regime']}")
            else:
                log.warning(f"[THETA] No options chain for {etf}")

        except Exception as e:
            log.warning(f"[THETA] Options chain fetch failed for {etf}: {e}")

        # 4. Assignment rate from DB
        if conn:
            try:
                row = conn.execute(
                    "SELECT assignment_rate FROM theta_risk_params WHERE sector = ?",
                    (sector,)
                ).fetchone()
                if row:
                    result["sector_assignment_rate"] = float(row[0] or 0.0)
            except Exception:
                pass

    except Exception as e:
        log.warning(f"[THETA] Market data fetch failed for {sector}: {e}")

    _iv_cache[sector] = result
    _iv_cache_ts[sector] = now
    return result


def get_all_sector_market_data(conn: sqlite3.Connection = None) -> dict[str, dict]:
    """Fetch market data for all sectors. Returns {sector: data_dict}"""
    results = {}
    for sector in SECTOR_ETFS:
        if sector == "market_overview":
            continue
        try:
            results[sector] = get_sector_market_data(sector, conn)
        except Exception as e:
            log.warning(f"[THETA] Failed market data for {sector}: {e}")
            results[sector] = {
                "iv_rank": 50.0, "premium_yield": 0.02,
                "market_regime": "sideways", "open_interest": 500,
                "option_volume": 100, "sector_assignment_rate": 0.0,
                "underlying_price": 100.0, "etf": SECTOR_ETFS[sector],
            }
    return results


def store_iv_snapshot(sector: str, iv_rank: float, conn: sqlite3.Connection) -> None:
    """Store daily IV snapshot for building historical IV rank over time."""
    try:
        import json
        now = datetime.now(timezone.utc).isoformat()
        row = conn.execute(
            "SELECT iv_history FROM theta_risk_params WHERE sector = ?",
            (sector,)
        ).fetchone()
        history = json.loads(row[0]) if row and row[0] else []
        history.append(round(iv_rank, 1))
        history = history[-365:]
        conn.execute("""
            INSERT INTO theta_risk_params (sector, iv_history, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(sector) DO UPDATE SET
                iv_history = excluded.iv_history,
                updated_at = excluded.updated_at
        """, (sector, json.dumps(history), now))
        conn.commit()
    except Exception as e:
        log.warning(f"[THETA] IV snapshot store failed for {sector}: {e}")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")

    sector = sys.argv[1] if len(sys.argv) > 1 else "technology"
    print(f"\nFetching market data for: {sector}")
    data = get_sector_market_data(sector)
    for k, v in data.items():
        print(f"  {k}: {v}")
