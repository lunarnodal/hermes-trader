#!/usr/bin/env python3
"""
Shareholder meeting event tracker
Detects upcoming meetings from news signals and flags elevated risk windows
Pre-meeting and day-of signals get confidence boost and extra scrutiny
"""

import sqlite3
import json
import re
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = Path('/home/trading/trading-ai/data/events.db')
log = logging.getLogger(__name__)

# Meeting detection patterns
MEETING_PATTERNS = [
    re.compile(r'shareholder[s]?\s+(?:annual\s+)?meeting', re.I),
    re.compile(r'annual\s+general\s+meeting', re.I),
    re.compile(r'agm\b', re.I),
    re.compile(r'proxy\s+(?:vote|fight|battle|statement)', re.I),
    re.compile(r'activist\s+investor', re.I),
    re.compile(r'board\s+(?:election|vote|seat|director)', re.I),
    re.compile(r'executive\s+compensation\s+vote', re.I),
    re.compile(r'say.on.pay', re.I),
    re.compile(r'glass\s+lewis|iss\s+(?:proxy|recommends)', re.I),
    re.compile(r'schedules\s+(?:annual|special)\s+meeting', re.I),
]

# Date extraction patterns
DATE_PATTERNS = [
    re.compile(r'(?:on|for|scheduled\s+for)\s+(\w+\s+\d{1,2}(?:,\s+\d{4})?)', re.I),
    re.compile(r'(\w+\s+\d{1,2}(?:,\s+\d{4})?)\s+(?:meeting|vote)', re.I),
    re.compile(r'(\d{1,2}/\d{1,2}/\d{4})', re.I),
    re.compile(r'(\d{4}-\d{2}-\d{2})', re.I),
]


def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS shareholder_meetings (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker       TEXT,
            company_name TEXT,
            meeting_date TEXT,
            meeting_type TEXT DEFAULT 'annual',
            detected_at  TEXT NOT NULL,
            source_title TEXT,
            source_url   TEXT,
            notes        TEXT,
            status       TEXT DEFAULT 'upcoming'
        );

        CREATE TABLE IF NOT EXISTS event_windows (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id   INTEGER REFERENCES shareholder_meetings(id),
            ticker       TEXT,
            window_start TEXT,
            window_end   TEXT,
            risk_level   TEXT DEFAULT 'elevated',
            notes        TEXT
        );
    """)
    conn.commit()
    return conn


def is_meeting_signal(title: str, summary: str = "") -> bool:
    """Detect if an article is about a shareholder meeting"""
    text = f"{title} {summary}"
    return any(p.search(text) for p in MEETING_PATTERNS)


def extract_meeting_date(title: str, summary: str = "") -> str | None:
    """Try to extract meeting date from article text"""
    text = f"{title} {summary}"
    for pattern in DATE_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


def record_meeting(conn: sqlite3.Connection, ticker: str,
                   company_name: str, title: str,
                   url: str = "", meeting_date: str = None) -> int:
    """Record a detected shareholder meeting"""
    now = datetime.now(timezone.utc).isoformat()

    # Check if already recorded for this ticker recently
    existing = conn.execute("""
        SELECT id FROM shareholder_meetings
        WHERE ticker = ?
        AND detected_at >= ?
        AND status = 'upcoming'
    """, (ticker, (datetime.now(timezone.utc) - timedelta(days=30)).isoformat())
    ).fetchone()

    if existing:
        log.info(f"Meeting already tracked for {ticker} — updating")
        conn.execute("""
            UPDATE shareholder_meetings
            SET source_title = ?, meeting_date = COALESCE(?, meeting_date)
            WHERE id = ?
        """, (title, meeting_date, existing[0]))
        conn.commit()
        return existing[0]

    cursor = conn.execute("""
        INSERT INTO shareholder_meetings
        (ticker, company_name, meeting_date, detected_at, source_title, source_url)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (ticker, company_name, meeting_date, now, title, url))
    meeting_id = cursor.lastrowid

    # Create elevated risk window around meeting date
    if meeting_date:
        try:
            # 5 days before to 2 days after
            from dateutil import parser as dateparser
            dt = dateparser.parse(meeting_date)
            if dt:
                window_start = (dt - timedelta(days=5)).isoformat()
                window_end   = (dt + timedelta(days=2)).isoformat()
                conn.execute("""
                    INSERT INTO event_windows
                    (meeting_id, ticker, window_start, window_end, risk_level)
                    VALUES (?, ?, ?, ?, 'elevated')
                """, (meeting_id, ticker, window_start, window_end))
        except Exception as e:
            log.warning(f"Could not parse meeting date '{meeting_date}': {e}")

    conn.commit()
    log.info(f"Recorded meeting for {ticker} on {meeting_date or 'TBD'}: {title[:60]}")
    return meeting_id


def is_in_risk_window(conn: sqlite3.Connection, ticker: str) -> dict | None:
    """Check if a ticker is currently in an elevated risk window"""
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute("""
        SELECT w.risk_level, w.window_start, w.window_end,
               m.meeting_date, m.company_name
        FROM event_windows w
        JOIN shareholder_meetings m ON w.meeting_id = m.id
        WHERE w.ticker = ?
        AND w.window_start <= ?
        AND w.window_end >= ?
    """, (ticker, now, now)).fetchone()

    if row:
        return {
            "risk_level":   row[0],
            "window_start": row[1],
            "window_end":   row[2],
            "meeting_date": row[3],
            "company":      row[4]
        }
    return None


def get_upcoming_meetings(conn: sqlite3.Connection,
                          days_ahead: int = 14) -> list[dict]:
    """Get all meetings coming up in the next N days"""
    cutoff = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).isoformat()
    now    = datetime.now(timezone.utc).isoformat()

    rows = conn.execute("""
        SELECT ticker, company_name, meeting_date, source_title, detected_at
        FROM shareholder_meetings
        WHERE status = 'upcoming'
        AND (meeting_date IS NULL OR meeting_date >= ?)
        ORDER BY meeting_date ASC NULLS LAST
    """, (now,)).fetchall()

    return [
        {
            "ticker":       row[0],
            "company":      row[1],
            "meeting_date": row[2],
            "source":       row[3],
            "detected":     row[4]
        }
        for row in rows
    ]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    conn = init_db()

    # Test detection
    test_cases = [
        ("Fiserv Inc. (FISV) Annual Shareholder Meeting Scheduled for June 3",
         "FISV", "Fiserv"),
        ("Proxy Fight: Activist Investor Targets Apple Board Seat",
         "AAPL", "Apple"),
        ("Glass Lewis Recommends Against CEO Pay Package at Tesla",
         "TSLA", "Tesla"),
        ("Microsoft Schedules Annual Meeting for December 4, 2026",
         "MSFT", "Microsoft"),
    ]

    print("Meeting detection tests:\n")
    for title, ticker, company in test_cases:
        detected  = is_meeting_signal(title)
        date      = extract_meeting_date(title)
        if detected:
            record_meeting(conn, ticker, company, title, meeting_date=date)
        print(f"{'✓ MEETING' if detected else '○ skip':10s} [{ticker}] {title[:60]}")
        if date:
            print(f"           Date extracted: {date}")

    print("\nUpcoming meetings:")
    meetings = get_upcoming_meetings(conn)
    for m in meetings:
        print(f"  {m['ticker']:6s} {m['meeting_date'] or 'TBD':15s} {m['company']}")

    conn.close()
