"""
Marketaux news feed integration
5,000+ sources, NLP pre-processed with sentiment scores and ticker extraction
Free tier: 100 requests/day
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

MARKETAUX_TOKEN = os.getenv("MARKETAUX_TOKEN", "")
BASE_URL        = "https://api.marketaux.com/v1"


def fetch_news(filter_entities: bool = True,
               language: str = "en",
               limit: int = 50) -> list[dict]:
    """Fetch latest financial news with NLP entity extraction"""
    if not MARKETAUX_TOKEN:
        log.warning("MARKETAUX_TOKEN not set — skipping Marketaux feed")
        return []
    try:
        resp = requests.get(
            f"{BASE_URL}/news/all",
            params={
                "filter_entities": filter_entities,
                "language":        language,
                "api_token":       MARKETAUX_TOKEN,
                "limit":           min(limit, 50),
            },
            timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        articles = data.get("data", [])
        log.info(f"[marketaux] fetched {len(articles)} articles")
        return articles
    except Exception as e:
        log.error(f"[marketaux] fetch failed: {e}")
        return []


def normalize_article(article: dict) -> dict:
    """Convert Marketaux article format to our pipeline format"""
    # Extract tickers from entities
    tickers = []
    for entity in article.get("entities", []):
        sym = entity.get("symbol")
        if sym and 1 <= len(sym) <= 5:
            tickers.append(sym.upper())

    # Marketaux provides sentiment — use it as a hint for scoring
    sentiment_score = None
    for entity in article.get("entities", []):
        if entity.get("sentiment_score") is not None:
            sentiment_score = entity["sentiment_score"]
            break

    pub_dt = article.get("published_at", "")

    return {
        "guid":            article.get("uuid", article.get("url", "")),
        "source":          "marketaux",
        "title":           article.get("title", ""),
        "url":             article.get("url", ""),
        "summary":         article.get("description", "")[:500],
        "published":       pub_dt,
        "tickers":         tickers,
        "sentiment_hint":  sentiment_score,  # bonus — pre-computed NLP sentiment
    }


def run_marketaux_ingest(conn: sqlite3.Connection,
                         queue_dir: Path) -> int:
    """
    Main entry point — fetch marketaux news
    Rate limited to once per hour to stay within free tier (100 req/day)
    Returns number of new articles written to queue
    """
    now     = datetime.now(timezone.utc)
    max_age = timedelta(hours=int(os.getenv("MAX_ARTICLE_AGE_HOURS", 24)))

    # Rate limit — only run at the top of each hour
    if now.minute != 0:
        return 0

    raw      = fetch_news(limit=50)
    articles = [normalize_article(a) for a in raw]

    new_articles = []
    for article in articles:
        guid = str(article.get("guid", ""))
        if not guid or not article.get("title"):
            continue

        exists = conn.execute(
            "SELECT 1 FROM articles WHERE guid = ?", (guid,)
        ).fetchone()
        if exists:
            continue

        if article.get("published"):
            try:
                pub = datetime.fromisoformat(
                    article["published"].replace("Z", "+00:00")
                )
                if now - pub > max_age:
                    continue
            except:
                pass

        new_articles.append(article)
        conn.execute(
            "INSERT OR IGNORE INTO articles (guid, source, title, url, published, ingested_at, scored) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (guid, article["source"], article["title"],
             article["url"], article["published"],
             now.isoformat())
        )

    conn.commit()

    if new_articles:
        queue_file = queue_dir / f"queue_marketaux_{now.strftime('%Y%m%dT%H%M%SZ')}.jsonl"
        queue_dir.mkdir(parents=True, exist_ok=True)
        with open(queue_file, 'w') as f:
            for a in new_articles:
                f.write(json.dumps(a) + '\n')
        log.info(f"Marketaux: wrote {len(new_articles)} new articles to {queue_file.name}")

    return len(new_articles)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    from feeds.ingest import DB_PATH, TIMESERIES_DIR as QUEUE_DIR
    conn = sqlite3.connect(DB_PATH)
    n = run_marketaux_ingest(conn, QUEUE_DIR)
    print(f"Ingested {n} new articles")
    conn.close()
