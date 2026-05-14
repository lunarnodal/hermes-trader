#!/usr/bin/env python3
"""
Feed ingestion pipeline
Fetches RSS feeds, deduplicates, and queues articles for sentiment scoring
"""

import feedparser
import json
import hashlib
import os
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

# ─── Config ───────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")

TIMESERIES_DIR = Path(os.getenv("TIMESERIES_DIR", "/mnt/qnap/timeseries/signals"))
RAW_FEEDS_DIR  = Path(os.getenv("RAW_FEEDS_DIR", "/mnt/qnap/raw-feeds"))
MAX_AGE_HOURS  = int(os.getenv("MAX_ARTICLE_AGE_HOURS", 24))
DB_PATH        = Path("/mnt/qnap/timeseries/ingestion.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/mnt/qnap/timeseries/logs/ingest.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ─── Feed Sources ─────────────────────────────────────────────────────────────

FEEDS = [
    {"name": "marketwatch-top",      "url": "https://www.marketwatch.com/rss/topstories"},
    {"name": "marketwatch-bulletins","url": "https://feeds.content.dowjones.io/public/rss/mw_bulletins"},
    {"name": "wsj-markets",          "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"},
    {"name": "seeking-alpha",        "url": "https://seekingalpha.com/feed.xml"},
    {"name": "ft-markets",           "url": "https://www.ft.com/rss/home/uk"},
    {"name": "investing-com",        "url": "https://www.investing.com/rss/news.rss"},
    {"name": "bbc-business",         "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
]

# ─── Database ─────────────────────────────────────────────────────────────────

def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            guid        TEXT PRIMARY KEY,
            source      TEXT NOT NULL,
            title       TEXT NOT NULL,
            url         TEXT,
            published   TEXT,
            ingested_at TEXT NOT NULL,
            scored      INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def is_duplicate(conn: sqlite3.Connection, guid: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM articles WHERE guid = ?", (guid,)
    ).fetchone()
    return row is not None


def mark_ingested(conn: sqlite3.Connection, guid: str, source: str,
                  title: str, url: str, published: str) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO articles
           (guid, source, title, url, published, ingested_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (guid, source, title, url, published,
         datetime.now(timezone.utc).isoformat())
    )
    conn.commit()

# ─── Helpers ──────────────────────────────────────────────────────────────────

def article_guid(entry, source: str) -> str:
    if hasattr(entry, "id") and entry.id:
        return entry.id
    base = getattr(entry, "link", "") or getattr(entry, "title", source)
    return hashlib.sha256(base.encode()).hexdigest()


def parse_published(entry) -> datetime | None:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            import time
            ts = time.mktime(entry.published_parsed)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pass
    return None


def is_too_old(pub_dt: datetime | None, max_age_hours: int) -> bool:
    if pub_dt is None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    return pub_dt < cutoff


def save_raw(article: dict, source: str) -> None:
    day_dir = RAW_FEEDS_DIR / source / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    slug = hashlib.sha256(article["guid"].encode()).hexdigest()[:12]
    out  = day_dir / f"{slug}.json"
    if not out.exists():
        out.write_text(json.dumps(article, indent=2))

# ─── Core ─────────────────────────────────────────────────────────────────────

# ─── Noise filter ─────────────────────────────────────────────────────────────

NOISE_PATTERNS = [
    # Personal finance lifestyle content
    "i'm in my", "i am in my", "i'm 7", "i am 7",
    "my husband", "my wife and i", "my mortgage",
    "retire on dividends", "social security at",
    "pto gap", "envy", "extravagant spender",
    "roast my house", "restaurant failed",
    "live below my means", "enjoy working",
    # Fund commentary without market signals
    "q1 2026 commentary", "q2 2026 commentary",
    "q3 2026 commentary", "q4 2026 commentary",
    "quarterly scorecard", "quarterly commentary",
    "portfolio movers",
    # Annual meeting notices
    "schedules annual meeting",
    "annual general meeting",
    # Personal finance / lifestyle (MarketWatch)
    "dead people claiming",
    "buying homes to fit",
    "social security?",
    "more americans are",
    "we're in our",
    "here's one —",
    "cost of living concerns",
    "business daily",
    "st helier",
    # Form 13F filings — low signal value
    "form 13f",
    # Fund shareholder meetings
    "shareholder/an",
    "shareholder meeting",
]

def is_noise(title: str) -> bool:
    """Filter out personal finance and non-market content"""
    title_lower = title.lower()
    return any(pattern in title_lower for pattern in NOISE_PATTERNS)


def fetch_feed(feed: dict, conn: sqlite3.Connection) -> list[dict]:
    name = feed["name"]
    url  = feed["url"]
    new_articles = []

    try:
        parsed = feedparser.parse(url, request_headers={"User-Agent": "trading-ai/1.0"})
        if parsed.bozo and not parsed.entries:
            log.warning(f"[{name}] feed parse warning: {parsed.bozo_exception}")
            return []

        log.info(f"[{name}] fetched {len(parsed.entries)} entries")

        for entry in parsed.entries:
            guid      = article_guid(entry, name)
            pub_dt    = parse_published(entry)
            title     = getattr(entry, "title", "").strip()
            url_entry = getattr(entry, "link",  "").strip()
            summary   = getattr(entry, "summary", "").strip()

            if not title:
                continue
            if is_noise(title):
                continue
            if is_too_old(pub_dt, MAX_AGE_HOURS):
                continue
            if is_duplicate(conn, guid):
                continue

            article = {
                "guid":      guid,
                "source":    name,
                "title":     title,
                "url":       url_entry,
                "summary":   summary[:500] if summary else "",
                "published": pub_dt.isoformat() if pub_dt else None,
            }

            mark_ingested(conn, guid, name, title, url_entry,
                          article["published"] or "")
            save_raw(article, name)
            new_articles.append(article)

    except Exception as e:
        log.error(f"[{name}] fetch failed: {e}")

    return new_articles


def write_queue(articles: list[dict]) -> Path | None:
    if not articles:
        return None
    TIMESERIES_DIR.mkdir(parents=True, exist_ok=True)
    ts  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = TIMESERIES_DIR / f"queue_{ts}.jsonl"
    with out.open("w") as f:
        for article in articles:
            f.write(json.dumps(article) + "\n")
    log.info(f"Wrote {len(articles)} articles to {out.name}")
    return out


def run_once() -> Path | None:
    log.info("─── Ingestion run starting ───")
    Path("/mnt/qnap/timeseries/logs").mkdir(parents=True, exist_ok=True)
    conn    = init_db(DB_PATH)
    all_new = []

    for feed in FEEDS:
        articles = fetch_feed(feed, conn)
        all_new.extend(articles)
        log.info(f"[{feed['name']}] {len(articles)} new articles")

    queue_file = write_queue(all_new)
    log.info(f"─── Ingestion complete: {len(all_new)} total new articles ───")
    conn.close()
    return queue_file


if __name__ == "__main__":
    run_once()
