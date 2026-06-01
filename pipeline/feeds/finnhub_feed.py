"""
Finnhub news feed integration
Fetches: general market news + company news for held positions
Free tier: 60 API calls/minute
"""

import os
import logging
import requests
from pathlib import Path as _Path
from dotenv import load_dotenv
load_dotenv(_Path(__file__).parent.parent / ".env")
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3
import json
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

log = logging.getLogger(__name__)

FINNHUB_TOKEN = os.getenv("FINNHUB_TOKEN", "")
BASE_URL      = "https://finnhub.io/api/v1"

# Categories available on free tier
CATEGORIES = ["general", "forex", "crypto", "merger"]

def fetch_market_news(category: str = "general", limit: int = 50) -> list[dict]:
    """Fetch general market news by category"""
    if not FINNHUB_TOKEN:
        log.warning("FINNHUB_TOKEN not set — skipping Finnhub feed")
        return []
    try:
        resp = requests.get(
            f"{BASE_URL}/news",
            params={"category": category, "token": FINNHUB_TOKEN},
            timeout=10
        )
        resp.raise_for_status()
        articles = resp.json()
        log.info(f"[finnhub-{category}] fetched {len(articles)} articles")
        return articles[:limit]
    except Exception as e:
        log.error(f"[finnhub-{category}] fetch failed: {e}")
        return []


def fetch_company_news(ticker: str, days_back: int = 1) -> list[dict]:
    """Fetch news for a specific ticker"""
    if not FINNHUB_TOKEN:
        return []
    try:
        date_to   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        date_from = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
        resp = requests.get(
            f"{BASE_URL}/company-news",
            params={
                "symbol": ticker,
                "from":   date_from,
                "to":     date_to,
                "token":  FINNHUB_TOKEN
            },
            timeout=10
        )
        resp.raise_for_status()
        articles = resp.json()
        log.info(f"[finnhub-{ticker}] fetched {len(articles)} articles")
        return articles
    except Exception as e:
        log.error(f"[finnhub-{ticker}] fetch failed: {e}")
        return []


def normalize_article(article: dict, source_name: str) -> dict:
    """Convert Finnhub article format to our pipeline format"""
    pub_dt = datetime.fromtimestamp(
        article.get("datetime", 0), tz=timezone.utc
    ).isoformat() if article.get("datetime") else None

    return {
        "guid":    article.get("id", article.get("url", "")),
        "source":  source_name,
        "title":   article.get("headline", ""),
        "url":     article.get("url", ""),
        "summary": article.get("summary", "")[:500],
        "published": pub_dt,
        "tickers": [article["related"]] if article.get("related") else [],
    }


def run_finnhub_ingest(conn: sqlite3.Connection,
                       queue_dir: Path,
                       held_tickers: list[str] = None) -> int:
    """
    Main entry point — fetch general news + company news for held tickers
    Returns number of new articles written to queue
    """
    now       = datetime.now(timezone.utc)
    max_age   = timedelta(hours=int(os.getenv("MAX_ARTICLE_AGE_HOURS", 24)))
    articles  = []

    # General market news
    for category in ["general", "merger"]:
        raw = fetch_market_news(category, limit=30)
        for a in raw:
            articles.append(normalize_article(a, f"finnhub-{category}"))

    # Company news for held tickers
    if held_tickers:
        for ticker in held_tickers[:10]:  # rate limit safety
            raw = fetch_company_news(ticker, days_back=1)
            for a in raw:
                norm = normalize_article(a, f"finnhub-{ticker.lower()}")
                norm["tickers"] = [ticker]
                articles.append(norm)

    # Dedup + age filter
    new_articles = []
    for article in articles:
        guid = str(article.get("guid", ""))
        if not guid or not article.get("title"):
            continue

        # Check if already seen
        exists = conn.execute(
            "SELECT 1 FROM articles WHERE guid = ?", (guid,)
        ).fetchone()
        if exists:
            continue

        # Age filter
        if article.get("published"):
            try:
                pub = datetime.fromisoformat(article["published"])
                if now - pub > max_age:
                    continue
            except:
                pass

        new_articles.append(article)

        # Mark as seen
        conn.execute(
            "INSERT OR IGNORE INTO articles (guid, source, title, url, published, ingested_at, scored) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (guid, article["source"], article["title"],
             article["url"], article["published"],
             now.isoformat())
        )

    conn.commit()

    if new_articles:
        # Write to queue file
        queue_file = queue_dir / f"queue_finnhub_{now.strftime('%Y%m%dT%H%M%SZ')}.jsonl"
        queue_dir.mkdir(parents=True, exist_ok=True)
        with open(queue_file, 'w') as f:
            for a in new_articles:
                f.write(json.dumps(a) + '\n')
        log.info(f"Finnhub: wrote {len(new_articles)} new articles to {queue_file.name}")

    return len(new_articles)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    from feeds.ingest import DB_PATH, TIMESERIES_DIR as QUEUE_DIR
    conn = sqlite3.connect(DB_PATH)
    n = run_finnhub_ingest(conn, QUEUE_DIR, held_tickers=["NVDA", "AVGO"])
    print(f"Ingested {n} new articles")
    conn.close()
