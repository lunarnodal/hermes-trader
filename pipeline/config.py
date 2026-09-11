"""
Central path configuration — all DB paths resolved from env vars.
Set DATA_DIR to override all paths at once for staging/audit environments.

Environment variables:
    DATA_DIR           — base data directory (default: /home/trading/trading-ai/data)
    PAPER_DB_PATH      — override paper_trading.db path
    PORTFOLIO_DB_PATH  — override portfolio.db path
    RULES_DB_PATH      — override rules.db path
    LESSONS_DB_PATH    — override lessons.db path
    TICKERS_DB_PATH    — override tickers.db path
    ENRICHMENT_DB_PATH — override enrichment_cache.db path
    EVENTS_DB_PATH     — override events.db path
    TRADING_DB_PATH    — override trading_pipeline.db path
"""
import os
from pathlib import Path

_DEFAULT_DATA = Path("/home/trading/trading-ai/data")
DATA_DIR = Path(os.getenv("DATA_DIR", str(_DEFAULT_DATA)))

def _db(env_var: str, filename: str) -> Path:
    return Path(os.getenv(env_var, str(DATA_DIR / filename)))

PAPER_DB      = _db("PAPER_DB_PATH",      "paper_trading.db")
PORTFOLIO_DB  = _db("PORTFOLIO_DB_PATH",  "portfolio.db")
RULES_DB      = _db("RULES_DB_PATH",      "rules.db")
LESSONS_DB    = _db("LESSONS_DB_PATH",    "lessons.db")
TICKERS_DB    = _db("TICKERS_DB_PATH",    "tickers.db")
ENRICHMENT_DB = _db("ENRICHMENT_DB_PATH", "enrichment_cache.db")
EVENTS_DB     = _db("EVENTS_DB_PATH",     "events.db")
TRADING_DB    = _db("TRADING_DB_PATH",    "trading_pipeline.db")
