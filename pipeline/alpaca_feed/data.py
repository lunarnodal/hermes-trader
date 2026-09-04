"""
Alpaca data feed — live prices for open positions

Phase 1: Data only, no trading
Provides real-time quotes to replace stale DB prices on dashboard
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
log = logging.getLogger(__name__)

ALPACA_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY", "")

# Cache quotes for 30 seconds to avoid hammering API on every dashboard refresh
_quote_cache = {}
_cache_ts    = {}
CACHE_TTL    = 30


def get_live_prices(symbols: list[str]) -> dict[str, float]:
    """
    Fetch latest mid prices for a list of symbols.
    Returns {symbol: price} using bid/ask midpoint.
    Falls back to None for symbols that fail.
    """
    if not symbols or not ALPACA_KEY:
        return {}

    import time
    now = time.time()

    # Check cache
    fresh   = {s: _quote_cache[s] for s in symbols
                if s in _quote_cache and now - _cache_ts.get(s, 0) < CACHE_TTL}
    stale   = [s for s in symbols if s not in fresh]

    if not stale:
        return fresh

    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests  import StockLatestQuoteRequest

        client = StockHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)
        req    = StockLatestQuoteRequest(symbol_or_symbols=stale)
        quotes = client.get_stock_latest_quote(req)

        for sym, q in quotes.items():
            if q.bid_price and q.ask_price:
                mid = round((q.bid_price + q.ask_price) / 2, 4)
            elif q.ask_price:
                mid = q.ask_price
            elif q.bid_price:
                mid = q.bid_price
            else:
                continue
            _quote_cache[sym] = mid
            _cache_ts[sym]    = now
            fresh[sym]        = mid

        log.debug(f"Fetched live prices for {list(quotes.keys())}")

    except Exception as e:
        log.warning(f"Alpaca price fetch failed: {e}")

    return fresh


def is_market_open() -> bool:
    """Check if regular trading session is currently open."""
    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=True)
        clock = client.get_clock()
        return clock.is_open
    except Exception:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        if now.weekday() >= 5:
            return False
        mins = now.hour * 60 + now.minute
        return 13 * 60 + 30 <= mins <= 20 * 60


def get_extended_prices(symbols: list[str]) -> dict:
    """
    Fetch both regular session close and current after-hours price.
    Returns {symbol: {regular_close, after_hours, market_open, price_source}}
    """
    if not symbols or not ALPACA_KEY:
        return {}
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from datetime import datetime, timezone, timedelta
        import time

        client = StockHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)
        market_open = is_market_open()

        # Latest quote (regular or after-hours)
        quote_req = StockLatestQuoteRequest(symbol_or_symbols=symbols)
        quotes = client.get_stock_latest_quote(quote_req)

        # Last regular session close via latest bar
        try:
            from alpaca.data.requests import StockLatestBarRequest
            bar_req = StockLatestBarRequest(symbol_or_symbols=symbols)
            bars = client.get_stock_latest_bar(bar_req)
        except Exception:
            bars = {}

        result = {}
        for sym in symbols:
            entry = {
                'regular_close': None,
                'after_hours':   None,
                'market_open':   market_open,
                'price_source':  'live' if market_open else 'after_hours',
            }

            # Get last bar close
            if sym in bars and bars[sym]:
                    entry['regular_close'] = round(float(bars[sym].close), 4)

            # Get current quote midpoint
            if sym in quotes:
                q = quotes[sym]
                if q.bid_price and q.ask_price:
                    mid = round((q.bid_price + q.ask_price) / 2, 4)
                elif q.ask_price:
                    mid = q.ask_price
                elif q.bid_price:
                    mid = q.bid_price
                else:
                    mid = None
                entry['after_hours'] = mid
                if market_open:
                    entry['regular_close'] = mid  # during session, quote IS the price

            result[sym] = entry
        return result
    except Exception as e:
        log.warning(f"Extended price fetch failed: {e}")
        return {}


def get_account_info() -> dict:
    """Get Alpaca paper account summary"""
    try:
        from alpaca.trading.client import TradingClient
        client  = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=True)
        account = client.get_account()
        return {
            'cash':            float(account.cash),
            'portfolio_value': float(account.portfolio_value),
            'buying_power':    float(account.buying_power),
            'status':          str(account.status),
            'paper':           True,
        }
    except Exception as e:
        log.warning(f"Alpaca account fetch failed: {e}")
        return {}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    prices = get_live_prices(['XLB', 'XLV', 'SPY', 'NVDA', 'AAPL'])
    print("Live prices:")
    for sym, price in prices.items():
        print(f"  {sym:6s}: ${price:.2f}")
    print()
    account = get_account_info()
    print(f"Paper account: ${account.get('portfolio_value', 0):,.2f}")

