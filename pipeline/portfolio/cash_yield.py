"""
Cash yield tracker — shadow BIL/SGOV position

Tracks what idle cash WOULD have earned if parked in a
short-term treasury ETF (BIL or SGOV). No actual trades —
pure opportunity cost calculation for reporting purposes.

When Alpaca integration goes live, this becomes real:
  - Excess cash → auto-buy BIL at end of day
  - Before new position → auto-sell BIL for proceeds

BIL  = iShares 1-3 Month Treasury Bond ETF (~5.2% annualized)
SGOV = iShares 0-3 Month Treasury Bill ETF (~5.3% annualized)
"""

import sqlite3
import logging
import requests
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

log = logging.getLogger(__name__)

YF_BASE        = "https://query1.finance.yahoo.com/v8/finance/chart"
CASH_ETF       = "BIL"   # change to SGOV if preferred
RESERVE_PCT    = 0.10    # keep 10% as true cash reserve (matches CONFIG)
DB_PATH        = Path("/home/trading/trading-ai/data/portfolio.db")


# Current T-bill yields (update periodically as Fed rates change)
# BIL/SGOV yield ≈ Fed Funds Rate minus small expense ratio
# As of June 2026: Fed Funds ~4.33%, BIL yield ~4.28%
CURRENT_TBILL_YIELD = 0.0428  # 4.28% annualized — update if Fed changes rates


def get_etf_yield(ticker: str = CASH_ETF) -> float:
    """
    Return current T-bill ETF yield.
    BIL/SGOV return via distributions not price — use known yield directly.
    Update CURRENT_TBILL_YIELD when Fed changes rates.
    """
    return CURRENT_TBILL_YIELD


def get_deployable_cash(conn: sqlite3.Connection) -> float:
    """
    Cash available for BIL parking — total cash minus reserve.
    Reserve = 10% of starting capital ($50,000) = $5,000
    """
    from portfolio.db import get_cash_balance, CONFIG
    cash    = get_cash_balance(conn)
    reserve = CONFIG["starting_capital"] * CONFIG["min_cash_reserve_pct"]
    return max(0, cash - reserve)


def calculate_shadow_yield(conn: sqlite3.Connection,
                            since: datetime = None) -> dict:
    """
    Calculate what idle cash would have earned in BIL/SGOV
    over a given period.

    Returns daily and period totals for reporting.
    """
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=7)

    # Get cash snapshots over the period
    rows = conn.execute("""
        SELECT snapshot_at, cash, total_value
        FROM portfolio_snapshots
        WHERE snapshot_at >= ?
        ORDER BY snapshot_at
    """, (since.isoformat(),)).fetchall()

    if not rows:
        return {"period_days": 0, "avg_deployable": 0,
                "yield_earned": 0, "annualized_rate": 0}

    from portfolio.db import CONFIG
    reserve      = CONFIG["starting_capital"] * CONFIG["min_cash_reserve_pct"]
    annual_yield = get_etf_yield(CASH_ETF)
    daily_rate   = annual_yield / 365

    # Calculate daily yield on deployable cash
    total_yield    = 0.0
    deployable_sum = 0.0
    prev_dt        = None

    for row in rows:
        snap_at, cash, total = row
        snap_dt = datetime.fromisoformat(snap_at.replace('Z', '+00:00'))
        deployable = max(0, cash - reserve)
        deployable_sum += deployable

        if prev_dt:
            days_elapsed = (snap_dt - prev_dt).total_seconds() / 86400
            total_yield += deployable * daily_rate * days_elapsed

        prev_dt = snap_dt

    period_days    = (datetime.now(timezone.utc) - since).days or 1
    avg_deployable = deployable_sum / len(rows)

    return {
        "etf":             CASH_ETF,
        "period_days":     period_days,
        "avg_deployable":  round(avg_deployable, 2),
        "yield_earned":    round(total_yield, 2),
        "annualized_rate": round(annual_yield * 100, 2),
        "daily_rate":      round(daily_rate * 100, 4),
        "projected_annual": round(avg_deployable * annual_yield, 2),
    }


def get_current_shadow_position(conn: sqlite3.Connection) -> dict:
    """
    Current snapshot — what BIL position would look like right now.
    """
    from portfolio.db import get_cash_balance, CONFIG
    cash         = get_cash_balance(conn)
    reserve      = CONFIG["starting_capital"] * CONFIG["min_cash_reserve_pct"]
    deployable   = max(0, cash - reserve)
    annual_yield = get_etf_yield(CASH_ETF)

    # Fetch current BIL price
    bil_price = None
    try:
        resp = requests.get(
            f"{YF_BASE}/{CASH_ETF}",
            params={"interval": "1d", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        data      = resp.json()["chart"]["result"][0]
        closes    = [c for c in data["indicators"]["quote"][0]["close"] if c]
        bil_price = closes[-1] if closes else None
    except:
        pass

    shares = round(deployable / bil_price, 2) if bil_price else None

    return {
        "etf":            CASH_ETF,
        "cash_total":     round(cash, 2),
        "cash_reserve":   round(reserve, 2),
        "deployable":     round(deployable, 2),
        "etf_price":      round(bil_price, 2) if bil_price else None,
        "shadow_shares":  shares,
        "shadow_value":   round(deployable, 2),
        "annual_yield":   round(annual_yield * 100, 2),
        "daily_earnings": round(deployable * annual_yield / 365, 2),
        "monthly_earnings": round(deployable * annual_yield / 12, 2),
    }


def get_weekly_summary(conn: sqlite3.Connection) -> str:
    """Human readable weekly cash yield summary for reports"""
    since  = datetime.now(timezone.utc) - timedelta(days=7)
    shadow = calculate_shadow_yield(conn, since)
    pos    = get_current_shadow_position(conn)

    lines = [
        f"Shadow {CASH_ETF} position (idle cash tracking):",
        f"  Deployable cash:    ${pos['deployable']:,.2f}",
        f"  {CASH_ETF} price:          ${pos['etf_price'] or 'N/A'}",
        f"  Shadow shares:      {pos['shadow_shares'] or 'N/A'}",
        f"  Annual yield:       {pos['annual_yield']:.2f}%",
        f"  Daily earnings:     ${pos['daily_earnings']:.2f}/day",
        f"  Monthly projection: ${pos['monthly_earnings']:.2f}/month",
        f"  This week earned:   ${shadow['yield_earned']:.2f} "
        f"(on avg ${shadow['avg_deployable']:,.0f} deployable)",
        f"  Projected annual:   ${shadow['projected_annual']:,.2f}",
    ]
    return '\n'.join(lines)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    from portfolio.db import init_db
    conn = init_db()
    print(get_weekly_summary(conn))
    conn.close()
