"""
NYSE/Nasdaq market holiday calendar.

Both exchanges share the same holiday schedule. Used by manager.py,
premarket.py, and daily_predictions.py to skip trading on holidays —
previously only weekday (Mon-Fri) was checked, which incorrectly
treated holidays like Juneteenth as normal trading days.

Source: NYSE Group official holiday calendar
"""

from datetime import date

# Full-day closures (NYSE Group official schedule)
MARKET_HOLIDAYS_2026 = {
    date(2026, 1, 1):   "New Year's Day",
    date(2026, 1, 19):  "Martin Luther King Jr. Day",
    date(2026, 2, 16):  "Washington's Birthday",
    date(2026, 4, 3):   "Good Friday",
    date(2026, 5, 25):  "Memorial Day",
    date(2026, 6, 19):  "Juneteenth National Independence Day",
    date(2026, 7, 3):   "Independence Day (observed)",
    date(2026, 9, 7):   "Labor Day",
    date(2026, 11, 26): "Thanksgiving Day",
    date(2026, 12, 25): "Christmas Day",
}

# Early closes — 1:00 PM ET instead of 4:00 PM ET
MARKET_EARLY_CLOSE_2026 = {
    date(2026, 11, 27): "Day after Thanksgiving",
    date(2026, 12, 24): "Christmas Eve",
}


def is_market_holiday(check_date: date = None) -> bool:
    """Return True if the market is fully closed on this date"""
    check_date = check_date or date.today()
    return check_date in MARKET_HOLIDAYS_2026


def is_early_close(check_date: date = None) -> bool:
    """Return True if the market closes early (1 PM ET) on this date"""
    check_date = check_date or date.today()
    return check_date in MARKET_EARLY_CLOSE_2026


def get_holiday_name(check_date: date = None) -> str | None:
    """Return the holiday name if check_date is a market holiday"""
    check_date = check_date or date.today()
    return MARKET_HOLIDAYS_2026.get(check_date)


def is_trading_day(check_date: date = None) -> bool:
    """
    Return True only if the market is actually open:
    weekday AND not a holiday.
    """
    check_date = check_date or date.today()
    if check_date.weekday() >= 5:  # Sat=5, Sun=6
        return False
    if is_market_holiday(check_date):
        return False
    return True


if __name__ == "__main__":
    import sys
    today = date.today()
    if is_market_holiday(today):
        print(f"MARKET CLOSED: {get_holiday_name(today)}")
        sys.exit(1)
    elif today.weekday() >= 5:
        print("MARKET CLOSED: Weekend")
        sys.exit(1)
    elif is_early_close(today):
        print("MARKET OPEN: Early close at 1:00 PM ET")
        sys.exit(0)
    else:
        print("MARKET OPEN: Regular hours")
        sys.exit(0)
