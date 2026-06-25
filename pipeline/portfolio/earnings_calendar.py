"""
Earnings calendar gate

Before opening any position, check if the company has earnings
scheduled within the hold window (10 days). If so, skip or
reduce position size to avoid gap-down risk like ADBE.

Data source: Finnhub earnings calendar (free tier)
"""

import os
import logging
import requests
import sqlite3
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
log = logging.getLogger(__name__)

FINNHUB_TOKEN = os.getenv("FINNHUB_TOKEN", "")
BASE_URL      = "https://finnhub.io/api/v1"
CACHE_DB      = Path("/home/trading/trading-ai/data/enrichment_cache.db")


def get_earnings_date(ticker: str) -> date | None:
    """
    Get the next scheduled earnings date for a ticker.
    Returns None if no earnings found within 30 days.
    """
    if not FINNHUB_TOKEN:
        return None
    try:
        today    = date.today()
        end_date = today + timedelta(days=30)
        resp = requests.get(
            f"{BASE_URL}/calendar/earnings",
            params={
                "from":   today.strftime("%Y-%m-%d"),
                "to":     end_date.strftime("%Y-%m-%d"),
                "symbol": ticker,
                "token":  FINNHUB_TOKEN,
            },
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        earnings = data.get("earningsCalendar", [])
        if not earnings:
            return None

        # Return the nearest earnings date
        dates = []
        for e in earnings:
            try:
                dates.append(datetime.strptime(e["date"], "%Y-%m-%d").date())
            except:
                continue
        return min(dates) if dates else None

    except Exception as e:
        log.warning(f"Earnings calendar fetch failed for {ticker}: {e}")
        return None


def has_earnings_risk(ticker: str,
                       hold_days: int = 10,
                       warn_days: int = 7) -> dict:
    """
    Check if a ticker has earnings within the hold window.

    Returns:
      {
        'has_risk': bool,
        'earnings_date': date or None,
        'days_until': int or None,
        'action': 'skip' | 'reduce' | 'ok',
        'reason': str
      }
    """
    earnings_date = get_earnings_date(ticker)

    if earnings_date is None:
        return {
            'has_risk':     False,
            'earnings_date': None,
            'days_until':   None,
            'action':       'ok',
            'reason':       'No earnings scheduled in next 30 days'
        }

    days_until = (earnings_date - date.today()).days

    if days_until <= hold_days:
        if days_until <= 3:
            action = 'skip'
            reason = (f"Earnings in {days_until} days ({earnings_date}) — "
                      f"too close, skipping to avoid gap risk")
        elif days_until <= warn_days:
            action = 'reduce'
            reason = (f"Earnings in {days_until} days ({earnings_date}) — "
                      f"reducing position size by 50%")
        else:
            action = 'reduce'
            reason = (f"Earnings in {days_until} days ({earnings_date}) — "
                      f"within hold window, reducing size")

        return {
            'has_risk':     True,
            'earnings_date': earnings_date,
            'days_until':   days_until,
            'action':       action,
            'reason':       reason
        }

    return {
        'has_risk':     False,
        'earnings_date': earnings_date,
        'days_until':   days_until,
        'action':       'ok',
        'reason':       f"Earnings in {days_until} days — outside hold window"
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["MSFT", "AAPL", "NVDA", "ADBE"]
    for ticker in tickers:
        result = has_earnings_risk(ticker)
        print(f"{ticker:6s}: {result['action']:6s} — {result['reason']}")
