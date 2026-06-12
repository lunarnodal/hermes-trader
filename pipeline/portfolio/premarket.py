"""
Pre-market gap detector

Runs at 9:00 AM ET weekdays, checks all held positions against
pre-market prices. If any position has gapped down significantly,
exits at market open rather than waiting for 9:35 AM entry window.

Gap thresholds:
  > 3%  drop: WARNING — flag for human review
  > 5%  drop: AUTO-EXIT at market open
  > 10% drop: URGENT — exit immediately, log as earnings gap
"""

import sqlite3
import logging
import requests
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

log = logging.getLogger(__name__)

YF_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"

# Gap thresholds
WARN_THRESHOLD  = 0.03   # 3% — flag
EXIT_THRESHOLD  = 0.05   # 5% — auto-exit
URGENT_THRESHOLD = 0.10  # 10% — earnings gap


def fetch_premarket_price(ticker: str) -> dict | None:
    """
    Fetch pre-market price and change for a ticker.
    Returns dict with current_price, prev_close, gap_pct, is_premarket
    """
    try:
        url  = f"{YF_BASE}/{ticker}"
        resp = requests.get(
            url,
            params={
                "interval":       "1m",
                "range":          "1d",
                "includePrePost": True,   # include pre/post market data
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15
        )
        resp.raise_for_status()
        data = resp.json()

        result = data["chart"]["result"][0]
        meta   = result["meta"]

        prev_close    = meta.get("chartPreviousClose") or meta.get("previousClose")
        current_price = meta.get("regularMarketPrice")
        pre_price     = meta.get("preMarketPrice") or meta.get("postMarketPrice")

        # Use pre-market price if available, else regular market
        effective_price = pre_price or current_price

        if not prev_close or not effective_price:
            return None

        gap_pct = (effective_price - prev_close) / prev_close

        return {
            "ticker":         ticker,
            "prev_close":     round(prev_close, 2),
            "premarket_price": round(pre_price, 2) if pre_price else None,
            "current_price":  round(current_price, 2) if current_price else None,
            "effective_price": round(effective_price, 2),
            "gap_pct":        round(gap_pct, 4),
            "gap_pct_display": f"{gap_pct*100:+.1f}%",
            "is_premarket":   pre_price is not None,
        }

    except Exception as e:
        log.warning(f"Could not fetch pre-market price for {ticker}: {e}")
        return None


def check_premarket_gaps(execute: bool = False) -> list[dict]:
    """
    Check all open positions for pre-market gaps.
    If execute=True, close positions that exceed EXIT_THRESHOLD.
    Returns list of gap events.
    """
    from portfolio.db import init_db as init_portfolio_db, get_open_positions
    from portfolio.db import get_cash_balance, CONFIG

    port_conn = init_portfolio_db()
    positions = get_open_positions(port_conn)

    if not positions:
        log.info("No open positions to check")
        port_conn.close()
        return []

    log.info(f"Pre-market gap check: {len(positions)} positions")

    gap_events = []
    exits_triggered = 0

    for pos in positions:
        ticker = pos['ticker']
        entry  = pos['entry_price']
        shares = pos['shares']
        sl     = pos['stop_loss']

        data = fetch_premarket_price(ticker)
        if not data:
            continue

        gap_pct  = data['gap_pct']
        eff_price = data['effective_price']
        abs_gap   = abs(gap_pct)

        # Determine severity
        if abs_gap >= URGENT_THRESHOLD and gap_pct < 0:
            severity = "URGENT"
        elif abs_gap >= EXIT_THRESHOLD and gap_pct < 0:
            severity = "EXIT"
        elif abs_gap >= WARN_THRESHOLD and gap_pct < 0:
            severity = "WARN"
        elif gap_pct > WARN_THRESHOLD:
            severity = "UP"
        else:
            severity = "OK"

        event = {
            "ticker":         ticker,
            "severity":       severity,
            "gap_pct":        gap_pct,
            "gap_display":    data['gap_pct_display'],
            "prev_close":     data['prev_close'],
            "premarket_price": data['premarket_price'],
            "entry_price":    entry,
            "stop_loss":      sl,
            "position_value": round(shares * eff_price, 2),
            "unrealized_pnl": round((eff_price - entry) * shares, 2),
        }

        log.info(
            f"  {ticker:6s} prev_close=${data['prev_close']:.2f} "
            f"premarket=${data['effective_price']:.2f} "
            f"gap={data['gap_pct_display']} [{severity}]"
        )

        gap_events.append(event)

        # Auto-exit if gap exceeds threshold
        if execute and severity in ("EXIT", "URGENT"):
            log.warning(
                f"  AUTO-EXIT {ticker}: gap {data['gap_pct_display']} "
                f"exceeds {EXIT_THRESHOLD*100:.0f}% threshold"
            )
            try:
                now = datetime.now(timezone.utc).isoformat()
                pnl     = (eff_price - entry) * shares
                pnl_pct = (eff_price - entry) / entry * 100

                port_conn.execute("""
                    UPDATE positions
                    SET status='closed', exit_price=?, exit_date=?,
                        pnl=?, pnl_pct=?, exit_reason=?
                    WHERE ticker=? AND status='open'
                """, (
                    eff_price, now, round(pnl, 2), round(pnl_pct, 2),
                    f"premarket_gap ({data['gap_pct_display']})",
                    ticker
                ))
                port_conn.execute(
                    "INSERT INTO cash_ledger (amount, note, created_at) VALUES (?,?,?)",
                    (round(eff_price * shares, 2),
                     f"premarket_gap exit {ticker}", now)
                )
                port_conn.commit()
                exits_triggered += 1
                log.warning(
                    f"  CLOSED {ticker}: {shares:.0f} shares @ "
                    f"${eff_price:.2f} P&L=${pnl:+.2f} ({pnl_pct:+.1f}%)"
                )
                event['action'] = 'closed'
            except Exception as e:
                log.error(f"  Failed to close {ticker}: {e}")
                event['action'] = 'error'
        elif severity == "WARN":
            log.warning(
                f"  FLAGGED {ticker}: gap {data['gap_pct_display']} "
                f"— monitor at open"
            )
            event['action'] = 'flagged'

    port_conn.close()

    # Summary
    urgent = [e for e in gap_events if e['severity'] == 'URGENT']
    exits  = [e for e in gap_events if e['severity'] == 'EXIT']
    warns  = [e for e in gap_events if e['severity'] == 'WARN']
    ups    = [e for e in gap_events if e['severity'] == 'UP']

    if gap_events:
        log.info(f"Gap summary: {len(urgent)} urgent, {len(exits)} exit, "
                 f"{len(warns)} warn, {len(ups)} up, "
                 f"{exits_triggered} auto-exits triggered")
    else:
        log.info("No significant gaps detected")

    return gap_events


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true",
                        help="Auto-exit positions with gaps > 5%")
    parser.add_argument("--test",    action="store_true",
                        help="Test with current positions (dry run)")
    args = parser.parse_args()

    if args.test or not args.execute:
        log.info("Pre-market gap check (DRY RUN)")
        events = check_premarket_gaps(execute=False)
    else:
        log.info("Pre-market gap check (LIVE — auto-exit enabled)")
        events = check_premarket_gaps(execute=True)

    if events:
        print(f"\nGap events detected: {len(events)}")
        for e in events:
            print(f"  {e['ticker']:6s} {e['gap_display']:8s} [{e['severity']}] "
                  f"P&L=${e['unrealized_pnl']:+.2f}")
    else:
        print("No gaps detected")
