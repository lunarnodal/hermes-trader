"""
Alpaca paper trading — order execution layer

Phase 2: Mirror our paper trades to Alpaca paper account
  - Place market orders when our system decides BUY/SELL
  - Track fills and confirm execution
  - Compare Alpaca P&L vs our internal tracking

Phase 3 (future): Switch paper=True to paper=False for live trading
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
log = logging.getLogger(__name__)

ALPACA_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY", "")
PAPER         = os.getenv("ALPACA_PAPER", "true").lower() == "true"


def get_trading_client():
    from alpaca.trading.client import TradingClient
    return TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=PAPER)


def place_market_order(ticker: str,
                        qty: float,
                        side: str,
                        reason: str = "") -> dict:
    """
    Place a market order on Alpaca.
    
    Args:
        ticker: stock symbol
        qty:    number of shares (positive)
        side:   'buy' or 'sell'
        reason: human-readable reason for logging
    
    Returns:
        dict with order details or error
    """
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        client = get_trading_client()

        order_side = OrderSide.BUY if side.lower() == 'buy' else OrderSide.SELL

        req = MarketOrderRequest(
            symbol      = ticker,
            qty         = qty,
            side        = order_side,
            time_in_force = TimeInForce.DAY,
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
        }
        log.info(
            f"Alpaca order placed: {side.upper()} {qty} {ticker} "
            f"[{order.id}] status={order.status}"
        )
        return result

    except Exception as e:
        log.error(f"Alpaca order failed: {ticker} {side} {qty}: {e}")
        return {
            'success': False,
            'ticker':  ticker,
            'side':    side,
            'qty':     qty,
            'error':   str(e),
        }


def get_position(ticker: str) -> dict | None:
    """Get current Alpaca position for a ticker"""
    try:
        client = get_trading_client()
        pos = client.get_open_position(ticker)
        return {
            'ticker':      ticker,
            'qty':         float(pos.qty),
            'avg_cost':    float(pos.avg_entry_price),
            'market_value': float(pos.market_value),
            'unrealized_pl': float(pos.unrealized_pl),
            'unrealized_plpc': float(pos.unrealized_plpc),
            'current_price': float(pos.current_price),
        }
    except Exception:
        return None


def get_all_positions() -> list[dict]:
    """Get all open Alpaca positions"""
    try:
        client = get_trading_client()
        positions = client.get_all_positions()
        return [{
            'ticker':        p.symbol,
            'qty':           float(p.qty),
            'avg_cost':      float(p.avg_entry_price),
            'market_value':  float(p.market_value),
            'unrealized_pl': float(p.unrealized_pl),
            'unrealized_plpc': float(p.unrealized_plpc),
            'current_price': float(p.current_price),
        } for p in positions]
    except Exception as e:
        log.warning(f"Could not fetch Alpaca positions: {e}")
        return []


def get_theta_positions() -> list[dict]:
    """
    Get short options positions for theta-gang tracking.
    Calls Alpaca get_all_positions, filters for short options
    (short_call/short_put), parses option symbols for strike/expiry,
    computes premium (avg_price * qty), and flags assignment_risk
    when underlying price is within 10% of strike.
    Returns list of dicts with:
      ticker, option_symbol, instrument_type (covered_call/cash_secured_put),
      strike, expiry, premium, assignment_risk
    """
    try:
        positions = get_all_positions()
        theta_positions = []
        for pos in positions:
            symbol = pos.get("ticker", "")
            if len(symbol) < 14 or not any(c.isdigit() for c in symbol[3:8]):
                continue
            if symbol[8] not in ("C", "P"):
                continue
            if pos.get("qty", 0) >= 0:
                continue
            try:
                underlying = symbol[:3].strip()
                date_str = symbol[3:8]
                cp = symbol[8]
                strike = float(symbol[9:]) / 1000.0
                year = 2000 + int(date_str[:2])
                month = int(date_str[2:4])
                day = int(date_str[4:6])
                expiry = f"{year}-{month:02d}-{day:02d}"
                instrument_type = "covered_call" if cp == "C" else "cash_secured_put"
                qty = abs(pos.get("qty", 0))
                premium = pos.get("avg_cost", 0) * qty * 100
                current = pos.get("current_price", 0)
                assignment_risk = False
                if current and strike:
                    if cp == "C" and current >= strike * 0.9:
                        assignment_risk = True
                    elif cp == "P" and current <= strike * 1.1:
                        assignment_risk = True
                theta_positions.append({
                    "ticker": underlying,
                    "option_symbol": symbol,
                    "instrument_type": instrument_type,
                    "strike": strike,
                    "expiry": expiry,
                    "premium": round(premium, 2),
                    "assignment_risk": assignment_risk,
                })
            except (ValueError, IndexError) as e:
                log.debug(f"Could not parse option symbol {symbol}: {e}")
                continue
        return theta_positions
    except Exception as e:
        log.warning(f"Could not fetch theta positions: {e}")
        return []


def close_position(ticker: str, reason: str = "") -> dict:
    """Close entire position for a ticker"""
    try:
        client = get_trading_client()
        resp = client.close_position(ticker)
        log.info(f"Alpaca position closed: {ticker} — {reason}")
        return {'success': True, 'ticker': ticker, 'reason': reason}
    except Exception as e:
        log.error(f"Alpaca close position failed: {ticker}: {e}")
        return {'success': False, 'ticker': ticker, 'error': str(e)}


def get_account_summary() -> dict:
    """Get Alpaca account summary for dashboard"""
    try:
        from alpaca_feed.data import get_account_info
        return get_account_info()
    except Exception as e:
        log.warning(f"Could not fetch Alpaca account: {e}")
        return {}


def sync_order_status(order_id: str) -> dict:
    """Check status of a previously placed order"""
    try:
        client = get_trading_client()
        order = client.get_order_by_id(order_id)
        return {
            'order_id':  str(order.id),
            'status':    str(order.status),
            'filled_qty': float(order.filled_qty or 0),
            'filled_avg_price': float(order.filled_avg_price or 0),
        }
    except Exception as e:
        return {'order_id': order_id, 'error': str(e)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    # Test account and positions
    from alpaca_feed.data import get_account_info
    account = get_account_info()
    print(f"Paper account: ${account.get('portfolio_value', 0):,.2f}")
    print(f"Buying power:  ${account.get('buying_power', 0):,.2f}")

    positions = get_all_positions()
    if positions:
        print(f"\nOpen positions ({len(positions)}):")
        for p in positions:
            print(f"  {p['ticker']:6s}: {p['qty']:.0f}sh @ "
                  f"${p['avg_cost']:.2f} "
                  f"P&L={p['unrealized_plpc']*100:+.1f}%")
    else:
        print("\nNo open positions in Alpaca paper account")

