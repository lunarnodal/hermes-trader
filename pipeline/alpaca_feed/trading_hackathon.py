"""
Alpaca hackathon paper account — separate from organic trading account.
Used for demo purposes during the Alpaca AI Trading Agents Hackathon.
All trades visible to judges in a clean account starting Aug 28, 2026.
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
log = logging.getLogger(__name__)

ALPACA_KEY    = os.getenv("ALPACA_HACKATHON_KEY", "")
ALPACA_SECRET = os.getenv("ALPACA_HACKATHON_SECRET", "")
PAPER         = True  # always paper for hackathon


def get_trading_client():
    from alpaca.trading.client import TradingClient
    return TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=PAPER)


def place_market_order(ticker: str, qty: float, side: str, reason: str = "") -> dict:
    """Place a market order on the hackathon paper account."""
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        client = get_trading_client()
        order_side = OrderSide.BUY if side.lower() == 'buy' else OrderSide.SELL
        req = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
        )
        order = client.submit_order(req)
        result = {
            'success':  True,
            'order_id': str(order.id),
            'ticker':   ticker,
            'side':     side,
            'qty':      qty,
            'status':   str(order.status),
            'reason':   reason,
            'account':  'hackathon',
        }
        log.info(f"Hackathon order: {side.upper()} {qty} {ticker} [{order.id}]")
        return result
    except Exception as e:
        log.error(f"Hackathon order failed: {ticker} {side} {qty}: {e}")
        return {'success': False, 'ticker': ticker, 'side': side, 'qty': qty, 'error': str(e)}


def get_position(ticker: str) -> dict | None:
    """Get current position for a ticker in hackathon account."""
    try:
        client = get_trading_client()
        pos = client.get_open_position(ticker)
        return {
            'ticker':          ticker,
            'qty':             float(pos.qty),
            'avg_cost':        float(pos.avg_entry_price),
            'market_value':    float(pos.market_value),
            'unrealized_pl':   float(pos.unrealized_pl),
            'unrealized_plpc': float(pos.unrealized_plpc),
            'current_price':   float(pos.current_price),
        }
    except Exception:
        return None


def get_all_positions() -> list[dict]:
    """Get all open positions in hackathon account."""
    try:
        client = get_trading_client()
        positions = client.get_all_positions()
        return [{
            'ticker':          p.symbol,
            'qty':             float(p.qty),
            'avg_cost':        float(p.avg_entry_price),
            'market_value':    float(p.market_value),
            'unrealized_pl':   float(p.unrealized_pl),
            'unrealized_plpc': float(p.unrealized_plpc),
            'current_price':   float(p.current_price),
        } for p in positions]
    except Exception as e:
        log.warning(f"Could not fetch hackathon positions: {e}")
        return []


def get_account_summary() -> dict:
    """Get hackathon account summary."""
    try:
        client = get_trading_client()
        account = client.get_account()
        return {
            'cash':            float(account.cash),
            'portfolio_value': float(account.portfolio_value),
            'buying_power':    float(account.buying_power),
            'status':          str(account.status),
            'account':         'hackathon',
            'account_number':  account.account_number,
        }
    except Exception as e:
        log.warning(f"Could not fetch hackathon account: {e}")
        return {}
