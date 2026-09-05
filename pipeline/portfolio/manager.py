#!/usr/bin/env python3
"""
Portfolio manager
Orchestrates recommendations → paper trades
Runs daily within market hours
Respects PDT rules, position limits, sector concentration limits
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio.db import (
    init_db, get_cash_balance, get_open_positions, get_portfolio_value,
    get_sector_exposure, positions_this_week, open_position, close_position,
    partial_close_position, take_snapshot, calculate_position_size, CONFIG
)
from portfolio.selector import (
    get_recent_signals, generate_recommendations, fetch_current_price
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)
# US Market holidays (update annually)
MARKET_HOLIDAYS_2026 = {
    date(2026, 1,  1),   # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Presidents Day
    date(2026, 4,  3),   # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 7,  3),   # Independence Day (observed)
    date(2026, 9,  7),   # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 11, 27),  # Black Friday (early close — treat as holiday)
    date(2026, 12, 25),  # Christmas
}


try:
    from portfolio.market_calendar import is_trading_day, get_holiday_name
    from portfolio.earnings_calendar import has_earnings_risk
    from portfolio.vix_gate import get_vix_gate
except ImportError:
    from market_calendar import is_trading_day, get_holiday_name
    from earnings_calendar import has_earnings_risk
    from vix_gate import get_vix_gate


def is_drawdown_breaker(conn) -> bool:
    """
    Portfolio-level circuit breaker.
    Only triggers on catastrophic drawdown (5%+) — not minor corrections.
    Minor drawdowns are handled by sector-specific circuit breakers instead.
    """
    if not CONFIG.get("drawdown_circuit_breaker_pct"):
        return False

    # Raise threshold to 5% for portfolio-wide freeze
    # Sector breakers handle smaller drawdowns more precisely
    threshold = 0.05

    rows = conn.execute("""
        SELECT total_value FROM portfolio_snapshots
        ORDER BY snapshot_at DESC LIMIT 10
    """).fetchall()
    if len(rows) < 2:
        return False
    peak    = max(r[0] for r in rows)
    current = get_portfolio_value(conn)
    drawdown = (peak - current) / peak
    if drawdown >= threshold:
        log.warning(f"Portfolio circuit breaker: down {drawdown:.1%} from peak "
                    f"${peak:,.2f} — ALL entries paused")
        return True
    return False


def get_sector_stop_count(conn, sector: str, days: int = 7) -> int:
    """Count stop loss exits in a sector over the last N days"""
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    row = conn.execute("""
        SELECT COUNT(*) FROM positions
        WHERE sector = ?
          AND status = 'closed'
          AND exit_reason LIKE 'stop_loss%'
          AND exit_date >= ?
    """, (sector, cutoff)).fetchone()
    return row[0] if row else 0


def is_sector_breaker(sector: str, conn) -> bool:
    """
    Sector-specific circuit breaker.
    Pauses entries in a specific sector if it has had 2+ stop losses
    in the last 7 days. Other sectors remain open for trading.

    This allows rotation — if tech is down, healthcare/energy can still trade.
    """
    stop_count = get_sector_stop_count(conn, sector, days=7)
    if stop_count >= 2:
        log.warning(f"Sector breaker [{sector}]: {stop_count} stop losses "
                    f"in last 7 days — pausing {sector} entries")
        return True
    return False


def _alpaca_mirror(action: str, ticker: str, shares: float, reason: str = "") -> None:
    """Mirror trade to Alpaca paper account — non-fatal if it fails"""
    try:
        from alpaca_feed.trading import place_market_order
        if action == "BUY":
            place_market_order(ticker, shares, "buy", reason)
        elif action in ("SELL", "PARTIAL_SELL"):
            place_market_order(ticker, shares, "sell", reason)
    except Exception as _e:
        log.warning(f"Alpaca mirror failed (non-fatal): {_e}")
    # Also mirror to hackathon account
    _alpaca_mirror_hackathon(action, ticker, shares, reason)


def _alpaca_mirror_hackathon(action: str, ticker: str, shares: float, reason: str = "") -> None:
    """Mirror trade to hackathon Alpaca account — validates position before selling"""
    try:
        from alpaca_feed.trading_hackathon import place_market_order, get_position
        if action == "BUY":
            place_market_order(ticker, shares, "buy", reason)
        elif action in ("SELL", "PARTIAL_SELL"):
            # Only sell if position exists in hackathon account
            pos = get_position(ticker)
            if not pos:
                log.info(f"Hackathon mirror: skipping {action} {ticker} — no position in hackathon account")
                return
            available = pos.get('qty', 0)
            sell_shares = min(shares, available)
            if sell_shares <= 0:
                return
            place_market_order(ticker, sell_shares, "sell", reason)
    except Exception as _e:
        log.warning(f"Hackathon mirror failed (non-fatal): {_e}")


def is_macro_bearish() -> bool:
    """Return True if most recent market_overview prediction is bearish"""
    if not CONFIG.get("macro_gate_enabled"):
        return False
    try:
        import sqlite3 as _sql
        from pathlib import Path as _Path
        _db = _Path(os.environ.get("PAPER_DB_PATH",
                   "/home/trading/trading-ai/data/paper_trading.db"))
        _conn = _sql.connect(_db)
        row = _conn.execute("""
            SELECT direction, confidence FROM predictions
            WHERE query LIKE \'%market outlook%\'
               OR query LIKE \'%S&P 500%\'
               OR query LIKE \'%macro%\'
            ORDER BY created_at DESC LIMIT 1
        """).fetchone()
        _conn.close()
        if row and row[0] == "bearish" and row[1] >= 0.70:
            log.warning(f"Macro gate: market_overview is bearish ({row[1]:.0%}) — blocking new entries")
            return True
    except Exception as e:
        log.warning(f"Macro gate check failed: {e}")
    return False


def is_market_holiday() -> bool:
    """Check if today is a US market holiday"""
    return date.today() in MARKET_HOLIDAYS_2026


def is_market_hours() -> bool:
    """Check if currently within US market hours (server is America/New_York)"""
    now = datetime.now()
    if now.weekday() >= 5:  # Skip weekends
        return False
    if is_market_holiday():
        return False
    market_open  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= now <= market_close


def is_entry_window() -> bool:
    """Check if within preferred entry windows (morning or close)"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    if is_market_holiday():
        return False
    windows = [
        (now.replace(hour=9,  minute=30), now.replace(hour=10, minute=0)),
        (now.replace(hour=15, minute=30), now.replace(hour=16, minute=0)),
    ]
    return any(start <= now <= end for start, end in windows)



def check_concentration_risk(conn, min_trades: int = 3, max_pct: float = 80.0) -> dict:
    """
    Reject new entries if >max_pct of cumulative closed-trade P&L comes from
    <=min_trades tickers.

    Edge cases that pass (no concentration possible yet):
      - No closed trades at all
      - total_closed_pnl <= 0  (all positions closed at a loss)
      - Fewer than min_trades tickers with positive P&L
    """
    rows = conn.execute("""
        SELECT ticker, SUM(pnl) AS ticker_pnl
        FROM positions
        WHERE status = 'closed' AND pnl IS NOT NULL
        GROUP BY ticker
        HAVING ticker_pnl > 0
        ORDER BY ticker_pnl DESC
    """).fetchall()

    if not rows:
        return {"approved": True, "reason": "no closed trades"}

    tickers = [r[0] for r in rows]
    if len(tickers) < min_trades:
        return {"approved": True,
                "reason": "only %d closed tickers (min=%d)" % (len(tickers), min_trades)}

    total_closed_pnl = sum(r[1] for r in rows)
    if total_closed_pnl <= 0:
        return {"approved": True, "reason": "no profitable closed trades"}

    dominating = []
    for ticker, pnl in rows:
        pct = (pnl / total_closed_pnl) * 100
        if pct > max_pct:
            dominating.append((ticker, pct))

    # Only flag if the dominating tickers are within the top `min_trades`
    top_n = rows[:min_trades]
    top_n_tickers = {r[0] for r in top_n}
    risky = [(t, p) for t, p in dominating if t in top_n_tickers]

    if risky:
        names = ", ".join("%s (%.0f%%)" % (t, p) for t, p in risky)
        return {
            "approved": False,
            "reason": ">%.0f%% P&L from top %d tickers: %s" % (max_pct, len(risky), names)
        }

    return {"approved": True,
            "reason": "top ticker=%.0f%% (limit=%.0f%%)" % (
                rows[0][1] / total_closed_pnl * 100, max_pct)}


def check_stop_loss_take_profit(conn, dry_run: bool = False) -> list[dict]:
    """Check all open positions for stop loss / take profit triggers"""
    positions = get_open_positions(conn)
    actions   = []

    for pos in positions:
        ticker        = pos["ticker"]
        current_price = fetch_current_price(ticker)

        if not current_price:
            continue

        # Update current price in DB
        conn.execute("""
            UPDATE positions SET current_price = ?, last_price_update = ?
            WHERE id = ? AND status = 'open'
        """, (current_price, datetime.now(timezone.utc).isoformat(), pos["id"]))

        pnl_pct = (current_price - pos["entry_price"]) / pos["entry_price"] * 100

        # Check minimum hold period before allowing stop loss
        entry_date = datetime.fromisoformat(pos["entry_date"].replace("Z", "+00:00"))
        hold_hours = (datetime.now(timezone.utc) - entry_date).total_seconds() / 3600
        min_hold_hours = CONFIG.get("min_hold_before_stop_days", 1) * 6.5  # trading hours
        if hold_hours < min_hold_hours:
            log.debug(f"  {ticker} held {hold_hours:.1f}h — min hold not reached, skip stop check")
            continue

        # Check tiered profit taking
        tiers_triggered = pos.get("tiers_triggered", 0)
        for tier_idx, (gain_pct, fraction, stop_rule) in enumerate(CONFIG["profit_tiers"]):
            if tier_idx < tiers_triggered:
                continue  # Already triggered this tier
            if pnl_pct >= gain_pct * 100:
                new_stop = None
                if stop_rule == "breakeven":
                    new_stop = pos["entry_price"]
                elif stop_rule == "previous_tier" and tier_idx > 0:
                    prev_gain = CONFIG["profit_tiers"][tier_idx - 1][0]
                    new_stop = round(pos["entry_price"] * (1 + prev_gain), 2)
                reason = f"profit_tier_{tier_idx+1} (+{pnl_pct:.1f}%)"
                log.info(f"PROFIT TIER {tier_idx+1}: {ticker} @ ${current_price:.2f} "
                         f"sell {fraction:.0%} | stop→${new_stop or 0:.2f}")
                if not dry_run:
                    result = partial_close_position(
                        conn, pos["id"], current_price,
                        fraction, reason, new_stop
                    )
                    conn.execute(
                        "UPDATE positions SET tiers_triggered = ? WHERE id = ?",
                        (tier_idx + 1, pos["id"])
                    )
                    _alpaca_mirror("PARTIAL_SELL", ticker,
                                   round(pos["shares"] * fraction, 0), reason)
                    actions.append({
                        "action": "PARTIAL_SELL",
                        "reason": reason,
                        "tier":   tier_idx + 1,
                        **result
                    })
                break  # Only trigger one tier per cycle

        if current_price <= pos["stop_loss"]:
            reason = f"stop_loss ({pnl_pct:.1f}%)"
            log.info(f"STOP LOSS: {ticker} @ ${current_price:.2f} "
                     f"(entry=${pos['entry_price']:.2f}, {pnl_pct:.1f}%)")
            if not dry_run:
                result = close_position(conn, pos["id"], current_price, reason)
                _alpaca_mirror("SELL", ticker, pos["shares"], reason)
                actions.append({"action": "SELL", "reason": "stop_loss", **result})

    conn.commit()
    return actions


def check_time_exits(conn, dry_run: bool = False) -> list[dict]:
    """Check positions that have exceeded max hold days"""
    positions = get_open_positions(conn)
    actions   = []
    now       = datetime.now(timezone.utc)

    for pos in positions:
        entry_date = datetime.fromisoformat(
            pos["entry_date"].replace("Z", "+00:00"))
        hold_days  = (now - entry_date).days

        # Update hold days
        conn.execute(
            "UPDATE positions SET hold_days = ? WHERE id = ?",
            (hold_days, pos["id"])
        )

        # Volatility-adaptive hold period (Hermes recommendation 2026-08-31)
        # Low VIX (<15): extend hold — trends persist longer
        # Normal VIX (15-20): use configured max_hold_days
        # Elevated VIX (20-25): shorten hold — cut losers faster
        # High VIX (>25): shorten significantly — protect capital
        try:
            from portfolio.vix_gate import get_vix
            vix = get_vix() or 18.0
        except Exception:
            vix = 18.0

        if vix < 15:
            adaptive_hold = int(CONFIG["max_hold_days"] * 1.4)   # extend 40%
        elif vix < 20:
            adaptive_hold = CONFIG["max_hold_days"]               # normal
        elif vix < 25:
            adaptive_hold = int(CONFIG["max_hold_days"] * 0.75)  # shorten 25%
        else:
            adaptive_hold = int(CONFIG["max_hold_days"] * 0.50)  # shorten 50%

        if hold_days >= adaptive_hold:
            ticker        = pos["ticker"]
            current_price = fetch_current_price(ticker) or pos["entry_price"]
            pnl_pct       = (current_price - pos["entry_price"]) / pos["entry_price"] * 100
            reason        = f"time_exit ({hold_days} days, {pnl_pct:+.1f}%, VIX={vix:.1f})"

            log.info(f"TIME EXIT: {ticker} held {hold_days} days "
                     f"P&L={pnl_pct:+.1f}% VIX={vix:.1f} "
                     f"adaptive_hold={adaptive_hold} days")
            if not dry_run:
                result = close_position(conn, pos["id"], current_price, reason)
                _alpaca_mirror("SELL", ticker, pos["shares"], reason)
                _alpaca_mirror_hackathon("SELL", ticker, pos["shares"], reason)
                actions.append({"action": "SELL", "reason": "time_exit", **result})

    conn.commit()
    return actions


def execute_recommendations(conn, recommendations: list[dict],
                            dry_run: bool = False) -> list[dict]:
    """Execute BUY recommendations respecting all constraints"""
    executed  = []
    cash      = get_cash_balance(conn)
    port_val  = get_portfolio_value(conn)
    week_buys = positions_this_week(conn)
    sector_exp = get_sector_exposure(conn)

    for rec in recommendations:
        if rec["action"] != "BUY":
            continue

        ticker  = rec["ticker"]
        sector  = rec["sector"]
        price   = rec.get("current_price", 0)
        shares  = rec.get("suggested_shares", 0)
        value   = rec.get("suggested_value", 0)

        # Check weekly trade limit
        if week_buys >= CONFIG["max_new_positions_week"]:
            log.info(f"SKIP {ticker} — weekly limit reached ({week_buys} trades)")
            continue

        # VIX volatility gate — reduce or pause based on market fear
        _vix = get_vix_gate()
        if _vix['action'] == 'pause':
            log.warning(f"SKIP {ticker} — {_vix['reason']}")
            continue
        elif _vix['action'] == 'reduce':
            log.info(f"VIX gate: {_vix['reason']}")
            rec['size_multiplier'] = min(
                rec.get('size_multiplier', 1.0),
                _vix['size_multiplier']
            )

        # Macro gate — block all entries if market is broadly bearish
        if is_macro_bearish():
            log.info(f"SKIP {ticker} — macro gate active (market bearish)")
            continue

        # Portfolio circuit breaker — only triggers on catastrophic loss (5%+)
        if is_drawdown_breaker(conn):
            log.info(f"SKIP {ticker} — portfolio circuit breaker active (>5% drawdown)")
            continue

        # Sector circuit breaker — pause entries in sectors with 2+ stops this week
        # Allows rotation to other sectors while protecting the troubled one
        ticker_sector = rec.get("sector", "")
        if ticker_sector and is_sector_breaker(ticker_sector, conn):
            log.info(f"SKIP {ticker} — sector breaker active "
                     f"({ticker_sector} has 2+ stop losses this week)")
            continue

        # Earnings calendar gate — skip or reduce if earnings within hold window
        try:
            earnings_check = has_earnings_risk(ticker)
            if earnings_check['action'] == 'skip':
                log.warning(f"SKIP {ticker} — {earnings_check['reason']}")
                continue
            elif earnings_check['action'] == 'reduce':
                log.warning(f"REDUCE {ticker} — {earnings_check['reason']}")
                rec['size_multiplier'] = 0.5  # flag for position sizing
        except Exception as _e:
            log.warning(f"Earnings check failed for {ticker}: {_e}")

        # Check max concurrent open positions
        open_count = len(get_open_positions(conn))
        if open_count >= CONFIG.get("max_open_positions", 8):
            log.info(f"SKIP {ticker} — max open positions reached ({open_count})")
            continue

        # Check per-sector position limit
        sector_limits = CONFIG.get("max_positions_per_sector", {})
        sector_limit = sector_limits.get(rec.get("sector", ""), sector_limits.get("default", 2))
        sector_count = sum(1 for p in get_open_positions(conn)
                          if p.get("sector") == rec.get("sector", ""))
        if sector_count >= sector_limit:
            log.info(f"SKIP {ticker} — {rec.get('sector')} sector at limit ({sector_count}/{sector_limit})")
            continue

        # Check sector concentration
        current_sector_pct = sector_exp.get(sector, 0)
        if current_sector_pct >= CONFIG["max_sector_pct"] * 100:
            log.info(f"SKIP {ticker} — sector {sector} at {current_sector_pct:.1f}% "
                     f"(max {CONFIG['max_sector_pct']*100:.0f}%)")
            continue

        # Check concentration risk — reject if top tickers dominate closed P&L
        conc = check_concentration_risk(conn,
            min_trades=CONFIG.get("concentration_min_trades", 3),
            max_pct=CONFIG.get("concentration_max_pct", 80.0)
        )
        if not conc["approved"]:
            log.warning(f"CONCENTRATION RISK — blocking new entries: {conc['reason']}")
            return [], None

        # Check cash reserve
        reserve = port_val * CONFIG["min_cash_reserve_pct"]
        if cash - value < reserve:
            log.info(f"SKIP {ticker} — would breach cash reserve "
                     f"(need ${reserve:.0f} reserve)")
            continue

        # Check entry window
        if not is_entry_window() and not dry_run:
            log.info(f"SKIP {ticker} — outside entry window")
            continue

        if shares <= 0 or price <= 0:
            continue

        log.info(f"{'[DRY RUN] ' if dry_run else ''}BUY {shares} {ticker} "
                 f"@ ${price:.2f} = ${value:.2f} "
                 f"(sector={sector}, conf={rec.get('avg_confidence', 0):.0%})")

        if not dry_run:
            pos_id = open_position(
                conn, ticker, sector, shares, price,
                notes=rec.get("rationale", "")
            )
            if pos_id > 0:
                _alpaca_mirror("BUY", ticker, shares,
                               f"sector={sector} conf={rec.get('avg_confidence',0):.0%}")
                executed.append({
                    "action":  "BUY",
                    "ticker":  ticker,
                    "shares":  shares,
                    "price":   price,
                    "value":   value,
                    "pos_id":  pos_id
                })
                week_buys += 1
                cash      -= value
        else:
            executed.append({
                "action":  "BUY [DRY RUN]",
                "ticker":  ticker,
                "shares":  shares,
                "price":   price,
                "value":   value,
            })

    return executed


def save_recommendations(conn, recommendations: list[dict]) -> None:
    """Save recommendations to DB for dashboard display — deduplicated"""
    now = datetime.now(timezone.utc).isoformat()
    today = now[:10]
    # Deduplicate — one entry per ticker/action per day
    seen = set()
    unique_recs = []
    for rec in recommendations:
        key = (rec.get("ticker",""), rec.get("action",""))
        if key not in seen:
            seen.add(key)
            unique_recs.append(rec)
    # Delete today's existing recommendations before saving fresh ones
    conn.execute(
        "DELETE FROM recommendations WHERE DATE(generated_at) = ?", (today,)
    )
    for rec in unique_recs:
        conn.execute("""
            INSERT INTO recommendations
            (generated_at, ticker, action, sector, signal_count,
             avg_confidence, suggested_shares, suggested_value, rationale)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now,
            rec.get("ticker", ""),
            rec.get("action", ""),
            rec.get("sector", ""),
            rec.get("signal_count", 0),
            rec.get("avg_confidence", rec.get("avg_conf", 0)),
            rec.get("suggested_shares", 0),
            rec.get("suggested_value", 0),
            rec.get("rationale", "")[:200],
        ))
    conn.commit()


def run_portfolio_cycle(dry_run: bool = True, exits_only: bool = False) -> dict:
    """
    Main portfolio management cycle
    dry_run=True: generate recommendations only, don't execute
    dry_run=False: execute trades
    """
    log.info(f"═══ Portfolio cycle starting "
             f"({'DRY RUN' if dry_run else 'LIVE'}) ═══")

    conn     = init_db()
    results  = {"exits": [], "entries": [], "recommendations": []}

    # ── Step 1: Check exits (stop loss / take profit / time) ──────────────────
    if is_market_hours() or dry_run:
        exits = check_stop_loss_take_profit(conn, dry_run)
        exits += check_time_exits(conn, dry_run)
        results["exits"] = exits
        if exits:
            log.info(f"Exit actions: {len(exits)}")

    # ── Step 2: Load signals and predictions ──────────────────────────────────
    signals   = get_recent_signals(48)
    positions = get_open_positions(conn)
    cash      = get_cash_balance(conn)
    port_val  = get_portfolio_value(conn)

    log.info(f"Portfolio: ${cash:,.2f} cash + "
             f"${port_val - cash:,.2f} positions = "
             f"${port_val:,.2f} total")

    # Load recent predictions from paper trading DB
    try:
        from paper_trading.db import init_db as init_paper_db
        paper_conn = init_paper_db()
        pred_rows  = paper_conn.execute("""
            SELECT query, timeframe, prediction_file
            FROM predictions
            WHERE created_at >= ?
            ORDER BY created_at DESC LIMIT 10
        """, ((datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(),)
        ).fetchall()
        paper_conn.close()

        # Load prediction details from files
        predictions = []
        pred_dir    = Path("/mnt/qnap/timeseries/predictions")
        for query, timeframe, pred_file in pred_rows:
            if pred_file and Path(pred_file).exists():
                try:
                    with open(pred_file) as f:
                        pred_data = json.load(f)
                        predictions.append(pred_data)
                except:
                    pass

        log.info(f"Loaded {len(predictions)} recent predictions")

    except Exception as e:
        log.error(f"Could not load predictions: {e}")
        predictions = []

    # ── Step 3: Generate recommendations ─────────────────────────────────────
    if predictions:
        if exits_only:
            log.info("Exits-only mode — skipping new entry recommendations")
            recommendations = []
        else:
            recommendations = generate_recommendations(
                predictions, signals, positions, cash, port_val, conn
            )
        # Deduplicate — keep highest-value BUY per ticker
        seen_tickers = {}
        deduped = []
        for rec in recommendations:
            ticker = rec["ticker"]
            action = rec["action"]
            if action == "BUY":
                if ticker not in seen_tickers:
                    seen_tickers[ticker] = rec
                elif rec.get("suggested_value", 0) > seen_tickers[ticker].get("suggested_value", 0):
                    seen_tickers[ticker] = rec
            else:
                deduped.append(rec)
        deduped.extend(seen_tickers.values())
        recommendations = deduped
        save_recommendations(conn, recommendations)
        results["recommendations"] = recommendations

        log.info(f"Generated {len(recommendations)} recommendations:")
        for rec in recommendations:
            action = rec["action"]
            ticker = rec["ticker"]
            if action == "BUY":
                log.info(f"  {action:6s} {ticker:6s} "
                         f"{rec.get('suggested_shares', 0)} shares @ "
                         f"${rec.get('current_price', 0):.2f} = "
                         f"${rec.get('suggested_value', 0):.2f} "
                         f"({rec.get('type', 'stock')})")
            elif action in ("SELL", "REVIEW"):
                log.info(f"  {action:6s} {ticker:6s} — {rec.get('rationale', '')}")
            else:
                log.info(f"  {action:6s} {ticker:6s}")

    # ── Step 4: Execute (if not dry run and in entry window) ──────────────────
        if not dry_run and (is_entry_window() or True):  # remove 'or True' for live
            entries = execute_recommendations(
                conn, recommendations, dry_run=False
            )
            results["entries"] = entries

    # ── Step 5: Snapshot ──────────────────────────────────────────────────────
    snap = take_snapshot(conn)
    log.info(f"Portfolio snapshot: ${snap['total']:,.2f} "
             f"({snap['return_pct']:+.2f}%)")

    conn.close()
    log.info("═══ Portfolio cycle complete ═══")
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true",
                        help="Execute trades (default: dry run)")
    parser.add_argument("--exits-only", action="store_true",
                        help="Execute exits only (stop loss + profit tiers), no new entries")
    args = parser.parse_args()

    # Prevent simultaneous runs using PID file
    import os
    pidfile = Path("/tmp/trading-portfolio.pid")
    if pidfile.exists():
        try:
            pid = int(pidfile.read_text().strip())
            os.kill(pid, 0)  # Check if process still running
            print(f"Portfolio manager already running (PID {pid}) — exiting")
            raise SystemExit(0)
        except (ProcessLookupError, ValueError):
            pass  # Process dead, stale pidfile — continue

    pidfile.write_text(str(os.getpid()))
    try:
        results = run_portfolio_cycle(
            dry_run=not args.execute,
            exits_only=getattr(args, 'exits_only', False)
        )
    finally:
        pidfile.unlink(missing_ok=True)

    print("\n─── Summary ───")
    print(f"Exits:           {len(results['exits'])}")
    print(f"Recommendations: {len(results['recommendations'])}")
    print(f"Entries:         {len(results['entries'])}")

    if results["recommendations"]:
        print("\nRecommendations:")
        for rec in results["recommendations"]:
            action = rec["action"]
            ticker = rec["ticker"]
            if action == "BUY":
                print(f"  {action:6s} {ticker:6s} "
                      f"{rec.get('suggested_shares',0)} shares @ "
                      f"${rec.get('current_price',0):.2f} "
                      f"= ${rec.get('suggested_value',0):.2f} "
                      f"[{rec.get('type','stock')}]")
            else:
                print(f"  {action:6s} {ticker:6s} — {rec.get('rationale','')[:60]}")