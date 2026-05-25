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

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/mnt/qnap/timeseries/logs/portfolio.log"),
        logging.StreamHandler()
    ]
)

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
    windows = [
        (now.replace(hour=9,  minute=30), now.replace(hour=10, minute=0)),
        (now.replace(hour=15, minute=30), now.replace(hour=16, minute=0)),
    ]
    return any(start <= now <= end for start, end in windows)


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

        if hold_days >= CONFIG["max_hold_days"]:
            ticker        = pos["ticker"]
            current_price = fetch_current_price(ticker) or pos["entry_price"]
            pnl_pct       = (current_price - pos["entry_price"]) / pos["entry_price"] * 100
            reason        = f"time_exit ({hold_days} days, {pnl_pct:+.1f}%)"

            log.info(f"TIME EXIT: {ticker} held {hold_days} days "
                     f"P&L={pnl_pct:+.1f}%")
            if not dry_run:
                result = close_position(conn, pos["id"], current_price, reason)
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

        # Check max concurrent open positions
        open_count = len(get_open_positions(conn))
        if open_count >= CONFIG.get("max_open_positions", 8):
            log.info(f"SKIP {ticker} — max open positions reached ({open_count})")
            continue

        # Check sector concentration
        current_sector_pct = sector_exp.get(sector, 0)
        if current_sector_pct >= CONFIG["max_sector_pct"] * 100:
            log.info(f"SKIP {ticker} — sector {sector} at {current_sector_pct:.1f}% "
                     f"(max {CONFIG['max_sector_pct']*100:.0f}%)")
            continue

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


def run_portfolio_cycle(dry_run: bool = True) -> dict:
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
        results = run_portfolio_cycle(dry_run=not args.execute)
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