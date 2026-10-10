"""
Theta-gang order execution layer.
Handles sell-to-open for CSPs and CCs via Alpaca options API.
Manages early close (50% profit), assignment handling, and position monitoring.
"""

import os
import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

# Close at 50% of max profit (standard theta-gang rule)
PROFIT_CLOSE_PCT = 0.50
# Close/roll when < 21 DTE to avoid gamma risk
MIN_DTE_BEFORE_ROLL = 21


def _get_trading_client():
    from alpaca.trading.client import TradingClient
    key    = os.getenv("ALPACA_API_KEY", "")
    secret = os.getenv("ALPACA_SECRET_KEY", "")
    paper  = os.getenv("ALPACA_PAPER", "true").lower() == "true"
    return TradingClient(key, secret, paper=paper)


def place_sell_to_open(option_symbol: str,
                        qty: int,
                        reason: str = "") -> dict:
    """
    Place a sell-to-open limit order for a short options position.
    Uses LimitOrderRequest with the OCC option symbol directly.
    """
    try:
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.data.historical import OptionHistoricalDataClient
        from alpaca.data.requests import OptionLatestQuoteRequest

        client = _get_trading_client()
        data_client = OptionHistoricalDataClient(
            os.getenv("ALPACA_API_KEY", ""),
            os.getenv("ALPACA_SECRET_KEY", "")
        )

        # Get current quote for limit price
        quote_req = OptionLatestQuoteRequest(symbol_or_symbols=option_symbol)
        quotes = data_client.get_option_latest_quote(quote_req)

        if option_symbol not in quotes:
            return {"success": False, "error": "No quote available"}

        q = quotes[option_symbol]
        bid = float(q.bid_price or 0)
        ask = float(q.ask_price or 0)

        if bid <= 0:
            return {"success": False, "error": "No bid price — illiquid"}

        # Use mid price for limit order
        mid = round((bid + ask) / 2, 2)

        req = LimitOrderRequest(
            symbol        = option_symbol,
            qty           = qty,
            side          = OrderSide.SELL,
            limit_price   = mid,
            time_in_force = TimeInForce.DAY,
        )

        order = client.submit_order(req)

        result = {
            "success":       True,
            "order_id":      str(order.id),
            "option_symbol": option_symbol,
            "qty":           qty,
            "limit_price":   mid,
            "status":        str(order.status),
            "reason":        reason,
        }

        log.info(f"[THETA] STO placed: SELL {qty}x {option_symbol} "
                 f"@ ${mid:.2f} [{order.id}] — {reason}")
        return result

    except Exception as e:
        log.error(f"[THETA] STO failed: {option_symbol} x{qty}: {e}")
        return {
            "success":       False,
            "option_symbol": option_symbol,
            "qty":           qty,
            "error":         str(e),
        }


def place_buy_to_close(option_symbol: str,
                        qty: int,
                        reason: str = "") -> dict:
    """
    Place a buy-to-close order to exit a short options position.
    Used for profit-taking (50% close) or defensive close.
    """
    try:
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, PositionIntent
        from alpaca.data.historical import OptionHistoricalDataClient
        from alpaca.data.requests import OptionLatestQuoteRequest

        client = _get_trading_client()
        data_client = OptionHistoricalDataClient(
            os.getenv("ALPACA_API_KEY", ""),
            os.getenv("ALPACA_SECRET_KEY", "")
        )

        quote_req = OptionLatestQuoteRequest(symbol_or_symbols=option_symbol)
        quotes = data_client.get_option_latest_quote(quote_req)

        if option_symbol not in quotes:
            return {"success": False, "error": "No quote available"}

        q = quotes[option_symbol]
        ask = float(q.ask_price or 0)

        if ask <= 0:
            return {"success": False, "error": "No ask price"}

        # Use ask + small buffer to ensure fill on close
        limit_price = round(ask * 1.02, 2)

        req = LimitOrderRequest(
            symbol        = option_symbol,
            qty           = qty,
            side          = OrderSide.BUY,
            limit_price   = limit_price,
            time_in_force = TimeInForce.DAY,
            # Explicit intent: if no short exists the broker rejects the order
            # instead of opening a long option position.
            position_intent = PositionIntent.BUY_TO_CLOSE,
        )

        order = client.submit_order(req)

        result = {
            "success":       True,
            "order_id":      str(order.id),
            "option_symbol": option_symbol,
            "qty":           qty,
            "limit_price":   limit_price,
            "status":        str(order.status),
            "reason":        reason,
        }

        log.info(f"[THETA] BTC placed: BUY {qty}x {option_symbol} "
                 f"@ ${limit_price:.2f} [{order.id}] — {reason}")
        return result

    except Exception as e:
        log.error(f"[THETA] BTC failed: {option_symbol} x{qty}: {e}")
        return {
            "success":       False,
            "option_symbol": option_symbol,
            "qty":           qty,
            "error":         str(e),
        }


def check_theta_exits(conn) -> list[dict]:
    """
    Check all open theta positions for exit conditions.
    Called during portfolio cycle (9:35 AM, 12 PM, 3:35 PM).

    Exit conditions:
      1. 50% of max profit captured (standard theta-gang close)
      2. < 21 DTE (avoid gamma risk near expiry)
      3. Assignment risk: underlying within 10% of strike
      4. IV crush: IV dropped significantly (premium evaporated)

    Returns list of exit actions taken.
    """
    exits = []
    try:
        from alpaca.data.historical import OptionHistoricalDataClient
        from alpaca.data.requests import OptionLatestQuoteRequest

        data_client = OptionHistoricalDataClient(
            os.getenv("ALPACA_API_KEY", ""),
            os.getenv("ALPACA_SECRET_KEY", "")
        )

        # Get open theta positions from DB
        rows = conn.execute("""
            SELECT id, ticker, sector, instrument_type, strike, expiry,
                   premium_collected, entry_date, option_symbol
            FROM theta_positions
            WHERE status = 'open'
              AND notes LIKE '%confirmed_fill%'
        """).fetchall()

        if not rows:
            return []

        # Batch quote request
        symbols = [r[8] for r in rows if r[8]]
        if not symbols:
            return []

        quote_req = OptionLatestQuoteRequest(symbol_or_symbols=symbols)
        quotes = data_client.get_option_latest_quote(quote_req)

        for row in rows:
            pos_id, ticker, sector, instrument_type, strike, expiry, \
                premium_collected, entry_date, option_symbol = row

            if not option_symbol or option_symbol not in quotes:
                continue

            q = quotes[option_symbol]
            bid = float(q.bid_price or 0)
            ask = float(q.ask_price or 0)
            current_mid = (bid + ask) / 2 if bid and ask else ask or bid

            # Expected fill price (ask) — evaluate 50% against what we'll actually pay
            close_cost = ask

            # P&L: premium collected - expected fill cost (per-share, 100x in DB)
            pnl = premium_collected - close_cost
            pnl_pct = pnl / premium_collected * 100 if premium_collected else 0

            # Days to expiry
            try:
                expiry_dt = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                dte = (expiry_dt - datetime.now(timezone.utc)).days
            except Exception:
                dte = 30

            close_reason = None

            # Exit condition 1: 50% profit
            if pnl_pct >= PROFIT_CLOSE_PCT * 100:
                close_reason = f"profit_50pct (+{pnl_pct:.1f}%)"

            # Exit condition 2: < 21 DTE
            elif dte < MIN_DTE_BEFORE_ROLL:
                close_reason = f"dte_roll (DTE={dte})"

            # Exit condition 3: Assignment risk
            elif instrument_type == "cash_secured_put":
                # Get underlying current price
                from alpaca_feed.data import get_live_prices
                prices = get_live_prices([ticker])
                current_price = prices.get(ticker)
                # Assignment risk: price drops within 2% ABOVE strike or below it
                # (not when price is well above strike)
                if current_price and current_price <= strike * 1.00:  # only flag at or below strike
                    close_reason = f"assignment_risk (price=${current_price:.2f} strike=${strike:.2f})"

            if close_reason:
                log.info(f"[THETA] EXIT {instrument_type} {ticker} — {close_reason} "
                         f"P&L=${pnl:+.2f} ({pnl_pct:+.1f}%)")

                # Place buy-to-close
                btc_result = place_buy_to_close(
                    option_symbol, 1, f"theta exit: {close_reason}"
                )

                if btc_result.get("success"):
                    from portfolio.db import transition_theta_closing
                    event_type = "profit_close"
                    if "assignment_risk" in close_reason:
                        event_type = "defensive_close"
                    elif "dte_roll" in close_reason:
                        event_type = "dte_close"

                    # Transition to 'closing' — DB stays open until BTC fill confirmed
                    transition_theta_closing(
                        conn, pos_id, event_type,
                        btc_order_id=btc_result["order_id"],
                        expected_fill_price=close_cost,
                        notes=close_reason
                    )
                    log.info(f"[THETA] Position #{pos_id} in closing state — "
                             f"awaiting BTC fill for {option_symbol}")
                    exits.append({
                        "ticker":    ticker,
                        "action":    "BTC_PENDING",
                        "reason":    close_reason,
                        "pnl":       round(pnl, 2),
                        "pnl_pct":   round(pnl_pct, 1),
                    })

    except Exception as e:
        log.error(f"[THETA] Theta exit check failed: {e}")

    return exits


_ORDER_ID_RE = None


def _btc_order_id_from_notes(notes: str) -> str | None:
    """Return the BTC order id that transition_theta_closing() wrote into notes."""
    global _ORDER_ID_RE
    import re
    if _ORDER_ID_RE is None:
        _ORDER_ID_RE = re.compile(r"closing: order=([0-9a-fA-F-]{36})")
    matches = _ORDER_ID_RE.findall(notes or "")
    return matches[-1] if matches else None


def check_theta_closing_fills(conn, client=None) -> list[dict]:
    """
    Resolve every theta position in 'closing' state using the BTC order id
    stored when the order was placed.

      FILLED                         -> confirm close at the actual fill price,
                                        release the CSP cash reservation
      EXPIRED / CANCELED / REJECTED  -> revert to 'open' (retry next cycle)
      anything else (new, accepted, pending, partially filled)
                                     -> leave in 'closing'

    Enum members are compared directly. str(OrderSide.BUY) is 'OrderSide.BUY'
    on Python 3.11+, so string comparison against 'buy' never matches.
    Called from manager.run_portfolio_cycle() after check_theta_exits().
    """
    from alpaca.trading.enums import OrderStatus
    from portfolio.db import confirm_theta_close, release_cash_for_put

    results = []
    rows = conn.execute("""
        SELECT id, ticker, strike, option_symbol, premium_collected,
               instrument_type, notes
        FROM theta_positions
        WHERE status = 'closing'
    """).fetchall()
    if not rows:
        return results

    if client is None:
        client = _get_trading_client()

    terminal_unfilled = {OrderStatus.EXPIRED, OrderStatus.CANCELED,
                         OrderStatus.REJECTED, OrderStatus.DONE_FOR_DAY}

    for pos_id, ticker, strike, option_symbol, premium, instrument_type, notes in rows:
        order_id = _btc_order_id_from_notes(notes)
        if not order_id:
            log.warning(f"[THETA] Closing position #{pos_id} {option_symbol} has no "
                        f"BTC order id in notes; leaving in 'closing' for manual review")
            results.append({"ticker": ticker, "action": "BTC_UNKNOWN", "pos_id": pos_id})
            continue
        try:
            order = client.get_order_by_id(order_id)
        except Exception as e:
            log.warning(f"[THETA] Could not fetch BTC order {order_id} for #{pos_id}: {e}")
            continue

        if order.status == OrderStatus.FILLED:
            fill = float(order.filled_avg_price)
            closed = confirm_theta_close(
                conn, pos_id, fill, notes=f"btc_order={order_id}")
            if closed and instrument_type == "cash_secured_put":
                release_cash_for_put(
                    conn, ticker, strike, option_symbol=option_symbol,
                    notes=f"closed: {closed.get('assignment_event')} "
                          f"fill=${fill:.2f} order={order_id}")
            pnl = closed.get("pnl") if closed else None
            results.append({"ticker": ticker, "action": "BTC_FILLED",
                            "fill_price": fill, "pnl": pnl, "pos_id": pos_id})
            log.info(f"[THETA] BTC fill confirmed: {option_symbol} @ ${fill:.2f} "
                     f"P&L=${pnl if pnl is not None else float('nan'):.2f}")

        elif order.status in terminal_unfilled:
            conn.execute("""
                UPDATE theta_positions
                SET status = 'open', assignment_event = NULL, exit_price = NULL,
                    notes = notes || ' | btc_' || ? || '_reverted order=' || ?
                WHERE id = ? AND status = 'closing'
            """, (order.status.value, order_id, pos_id))
            conn.commit()
            log.info(f"[THETA] BTC {order.status.value} unfilled; reverted "
                     f"{ticker} #{pos_id} to open")
            results.append({"ticker": ticker, "action": "BTC_REVERTED",
                            "status": order.status.value, "pos_id": pos_id})
        else:
            log.debug(f"[THETA] BTC {order_id} for #{pos_id} still {order.status.value}")

    return results


def execute_theta_recommendation(rec: dict, conn) -> dict | None:
    """
    Execute a THETA_CSP or THETA_CC recommendation from selector.py.
    Called from manager.py execute_recommendations().

    Workflow:
      1. Select strike and expiry
      2. Check earnings veto
      3. Check cash/share requirements
      4. Place sell-to-open order
      5. Record in theta_positions DB
    """
    from alpaca_feed.theta_strike_selector import (
        select_csp_contract, select_covered_call_contract, check_earnings_veto
    )
    from portfolio.db import open_theta_position

    ticker          = rec["ticker"]
    instrument      = rec.get("instrument", "THETA_CSP")
    underlying_price = rec.get("current_price", 0)
    sector          = rec.get("sector", "")

    if not underlying_price:
        log.warning(f"[THETA] No price for {ticker} — skipping")
        return None

    # Select contract
    if instrument == "THETA_CSP":
        contract = select_csp_contract(ticker, underlying_price)
    else:
        contract = select_covered_call_contract(ticker, underlying_price)

    if not contract:
        log.info(f"[THETA] No suitable contract found for {ticker} {instrument}")
        return None

    # Earnings veto
    if check_earnings_veto(ticker, contract["dte"]):
        log.info(f"[THETA] VETO {ticker} — earnings within expiry window")
        return None

    # Cash check for CSP: need strike * 100 per contract
    if instrument == "THETA_CSP":
        cash_required = contract["cash_required"]
        available_cash = conn.execute(
            "SELECT balance FROM cash_ledger ORDER BY id DESC LIMIT 1"
        ).fetchone()
        available = float(available_cash[0]) if available_cash else 0

        # Reserve max 20% of portfolio per theta position
        port_val = conn.execute(
            "SELECT total_value FROM portfolio_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        port = float(port_val[0]) if port_val else 100000
        max_theta_cash = port * 0.20

        # Check TOTAL theta exposure — cap at 35% of portfolio across all open positions
        existing_theta_cash = conn.execute(
            "SELECT COALESCE(SUM(strike * 100), 0) FROM theta_positions WHERE status IN ('open', 'closing')"
        ).fetchone()[0]
        max_total_theta = port * 0.35
        if existing_theta_cash + cash_required > max_total_theta:
            log.info(f"[THETA] SKIP {ticker} CSP — total theta exposure "
                     f"${existing_theta_cash + cash_required:.0f} would exceed "
                     f"35% limit (${max_total_theta:.0f})")
            return None

        if cash_required > min(available * 0.25, max_theta_cash):
            log.info(f"[THETA] SKIP {ticker} CSP — cash required ${cash_required:.0f} "
                     f"exceeds limit")
            return None

    # CC check: need 100 shares of underlying
    elif instrument == "THETA_CC":
        shares = conn.execute(
            "SELECT shares FROM positions WHERE ticker = ? AND status = 'open'",
            (ticker,)
        ).fetchone()
        if not shares or float(shares[0]) < 100:
            log.info(f"[THETA] SKIP {ticker} CC — need 100 shares "
                     f"(have {shares[0] if shares else 0:.0f})")
            return None

    # Place the order
    result = place_sell_to_open(
        contract["option_symbol"], 1,
        f"theta {instrument} {sector} score={rec.get('theta_score', 0):.2f}"
    )

    if not result.get("success"):
        log.error(f"[THETA] STO failed for {ticker}: {result.get('error')}")
        return None

    # Record in DB
    pos_id = open_theta_position(
        conn=conn,
        ticker=ticker,
        sector=sector,
        instrument_type="cash_secured_put" if instrument == "THETA_CSP" else "covered_call",
        strike=contract["strike"],
        expiry=contract["expiry"],
        premium_collected=contract["mid_price"],
        option_symbol=contract["option_symbol"],
        notes=(f"delta={contract['delta']:.2f} dte={contract['dte']} "
               f"order_id={result['order_id']}")
    )

    log.info(f"[THETA] Position opened: {instrument} {ticker} "
             f"strike=${contract['strike']:.2f} expiry={contract['expiry']} "
             f"premium=${contract['mid_price']:.2f} pos_id={pos_id}")

    return {
        "success":    True,
        "ticker":     ticker,
        "instrument": instrument,
        "strike":     contract["strike"],
        "expiry":     contract["expiry"],
        "premium":    contract["mid_price"],
        "order_id":   result["order_id"],
        "pos_id":     pos_id,
    }
