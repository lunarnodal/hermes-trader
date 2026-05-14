#!/usr/bin/env python3
"""
Ticker extraction system
Stage 1: Regex patterns from titles (highest confidence)
Stage 2: Company name → ticker lookup DB
Stage 3: LLM fallback (only when stages 1 & 2 find nothing)
"""

import re
import json
import sqlite3
import logging
import os
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

DB_PATH    = Path(os.environ.get("TICKER_DB_PATH",
             "/home/trading/trading-ai/data/tickers.db"))
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://172.29.10.225:11434")

log = logging.getLogger(__name__)

# ─── Regex patterns ───────────────────────────────────────────────────────────

# Match explicit ticker patterns in text
PATTERNS = [
    re.compile(r'\(([A-Z]{1,5})\)'),                    # Company Name (TICK)
    re.compile(r'NYSE:\s*([A-Z]{1,5})\b'),              # NYSE: TICK
    re.compile(r'NASDAQ:\s*([A-Z]{1,5})\b'),            # NASDAQ: TICK
    re.compile(r'NYSEARCA:\s*([A-Z]{1,5})\b'),          # NYSEARCA: TICK
    re.compile(r'\bstock\s+symbol\s+([A-Z]{1,5})\b'),   # stock symbol TICK
    re.compile(r'\bticker\s+([A-Z]{1,5})\b'),           # ticker TICK
]

# Known false positives — common words that look like tickers
FALSE_POSITIVES = {
    'A', 'I', 'AM', 'PM', 'US', 'UK', 'EU', 'UN', 'AI', 'IT',
    'CEO', 'CFO', 'COO', 'CTO', 'IPO', 'ETF', 'GDP', 'PPI', 'CPI',
    'FED', 'BOJ', 'ECB', 'IMF', 'WTO', 'WHO', 'NATO', 'OPEC',
    'EPS', 'PEG', 'ROI', 'ROE', 'YOY', 'QOQ', 'TTM', 'EBIT',
    'LLC', 'INC', 'LTD', 'PLC', 'AG', 'SA', 'SE', 'NV', 'BV',
    'Q1', 'Q2', 'Q3', 'Q4', 'H1', 'H2', 'FY', 'YTD',
    'OTC', 'ADR', 'ADS', 'GDR', 'SPAC', 'REIT',
    'BUY', 'SELL', 'HOLD', 'STRONG', 'MARKET',
    'HIGH', 'LOW', 'OPEN', 'CLOSE', 'PEAK',
    'NEW', 'OLD', 'BIG', 'TOP', 'KEY',
    'AND', 'FOR', 'THE', 'BUT', 'NOT',
}


def extract_from_patterns(text: str) -> list[str]:
    """Stage 1 — extract tickers via regex patterns"""
    found = set()
    for pattern in PATTERNS:
        for m in pattern.finditer(text):
            ticker = m.group(1).upper().strip()
            if ticker not in FALSE_POSITIVES and len(ticker) >= 2:
                found.add(ticker)
    return sorted(found)


# ─── Ticker DB ────────────────────────────────────────────────────────────────

def init_ticker_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tickers (
            ticker       TEXT PRIMARY KEY,
            company_name TEXT NOT NULL,
            exchange     TEXT,
            sector       TEXT,
            source       TEXT DEFAULT 'manual',
            confidence   REAL DEFAULT 1.0,
            created_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS name_aliases (
            alias        TEXT PRIMARY KEY,
            ticker       TEXT NOT NULL,
            confidence   REAL DEFAULT 0.9
        );
    """)
    conn.commit()
    return conn


def lookup_by_name(conn: sqlite3.Connection,
                   text: str) -> list[tuple[str, float]]:
    """Stage 2 — look up company names in text against ticker DB"""
    text_lower = text.lower()
    results = []

    # Check aliases first (more specific matches)
    aliases = conn.execute(
        "SELECT alias, ticker, confidence FROM name_aliases"
    ).fetchall()

    for alias, ticker, confidence in aliases:
        if alias.lower() in text_lower:
            results.append((ticker, confidence))

    # Check full company names
    companies = conn.execute(
        "SELECT ticker, company_name, confidence FROM tickers"
    ).fetchall()

    for ticker, company_name, confidence in companies:
        if len(company_name) >= 5 and company_name.lower() in text_lower:
            results.append((ticker, confidence))

    # Deduplicate — keep highest confidence per ticker
    ticker_conf = {}
    for ticker, conf in results:
        if ticker not in ticker_conf or conf > ticker_conf[ticker]:
            ticker_conf[ticker] = conf

    return sorted(ticker_conf.items(), key=lambda x: x[1], reverse=True)


def add_ticker(conn: sqlite3.Connection, ticker: str, company_name: str,
               exchange: str = None, sector: str = None,
               source: str = 'manual') -> None:
    """Add a ticker to the DB"""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO tickers
        (ticker, company_name, exchange, sector, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (ticker, company_name, exchange, sector, source, now))
    conn.commit()


def add_alias(conn: sqlite3.Connection, alias: str, ticker: str,
              confidence: float = 0.9) -> None:
    """Add a company name alias for a ticker"""
    conn.execute("""
        INSERT OR REPLACE INTO name_aliases (alias, ticker, confidence)
        VALUES (?, ?, ?)
    """, (alias, ticker, confidence))
    conn.commit()


def seed_common_tickers(conn: sqlite3.Connection) -> None:
    """Seed frequently mentioned tickers from our signal corpus"""
    existing = conn.execute("SELECT COUNT(*) FROM tickers").fetchone()[0]
    if existing > 0:
        return

    common = [
        # Mega cap tech
        ("AAPL",  "Apple",              "NASDAQ", "technology"),
        ("MSFT",  "Microsoft",          "NASDAQ", "technology"),
        ("GOOGL", "Alphabet",           "NASDAQ", "technology"),
        ("GOOG",  "Google",             "NASDAQ", "technology"),
        ("AMZN",  "Amazon",             "NASDAQ", "technology"),
        ("META",  "Meta",               "NASDAQ", "technology"),
        ("NVDA",  "Nvidia",             "NASDAQ", "semiconductors"),
        ("TSLA",  "Tesla",              "NASDAQ", "automotive"),
        ("AMD",   "AMD",                "NASDAQ", "semiconductors"),
        ("INTC",  "Intel",              "NASDAQ", "semiconductors"),
        ("AVGO",  "Broadcom",           "NASDAQ", "semiconductors"),
        ("QCOM",  "Qualcomm",           "NASDAQ", "semiconductors"),
        ("MU",    "Micron",             "NASDAQ", "memory"),
        ("AMAT",  "Applied Materials",  "NASDAQ", "semiconductors"),
        ("LRCX",  "Lam Research",       "NASDAQ", "semiconductors"),
        ("ASML",  "ASML",               "NASDAQ", "semiconductors"),
        ("TSM",   "TSMC",               "NYSE",   "semiconductors"),
        ("SMCI",  "Super Micro",        "NASDAQ", "ai_infrastructure"),
        # AI infrastructure
        ("CSCO",  "Cisco",              "NASDAQ", "technology"),
        ("IBM",   "IBM",                "NYSE",   "technology"),
        ("ORCL",  "Oracle",             "NYSE",   "technology"),
        ("CRM",   "Salesforce",         "NYSE",   "technology"),
        ("NOW",   "ServiceNow",         "NYSE",   "technology"),
        ("PLTR",  "Palantir",           "NYSE",   "technology"),
        # Energy
        ("XOM",   "ExxonMobil",         "NYSE",   "energy"),
        ("CVX",   "Chevron",            "NYSE",   "energy"),
        ("COP",   "ConocoPhillips",     "NYSE",   "energy"),
        ("SLB",   "Schlumberger",       "NYSE",   "energy"),
        ("DVN",   "Devon Energy",       "NYSE",   "energy"),
        ("OXY",   "Occidental",         "NYSE",   "energy"),
        ("MPC",   "Marathon Petroleum", "NYSE",   "energy"),
        ("PSX",   "Phillips 66",        "NYSE",   "energy"),
        ("LNG",   "Cheniere Energy",    "NYSE",   "energy"),
        ("ET",    "Energy Transfer",    "NYSE",   "energy"),
        ("NEE",   "NextEra Energy",     "NYSE",   "utilities"),
        ("GEV",   "GE Vernova",         "NYSE",   "utilities"),
        ("NGG",   "National Grid",      "NYSE",   "utilities"),
        # Financials
        ("JPM",   "JPMorgan",           "NYSE",   "financials"),
        ("BAC",   "Bank of America",    "NYSE",   "financials"),
        ("GS",    "Goldman Sachs",      "NYSE",   "financials"),
        ("MS",    "Morgan Stanley",     "NYSE",   "financials"),
        ("BLK",   "BlackRock",          "NYSE",   "financials"),
        ("BX",    "Blackstone",         "NYSE",   "financials"),
        ("V",     "Visa",               "NYSE",   "financials"),
        ("MA",    "Mastercard",         "NYSE",   "financials"),
        # Healthcare
        ("JNJ",   "Johnson & Johnson",  "NYSE",   "healthcare"),
        ("UNH",   "UnitedHealth",       "NYSE",   "healthcare"),
        ("PFE",   "Pfizer",             "NYSE",   "healthcare"),
        ("ABBV",  "AbbVie",             "NYSE",   "healthcare"),
        ("MRK",   "Merck",              "NYSE",   "healthcare"),
        ("LLY",   "Eli Lilly",          "NYSE",   "healthcare"),
        # Defense
        ("LMT",   "Lockheed Martin",    "NYSE",   "defense"),
        ("RTX",   "Raytheon",           "NYSE",   "defense"),
        ("NOC",   "Northrop Grumman",   "NYSE",   "defense"),
        ("GD",    "General Dynamics",   "NYSE",   "defense"),
        ("BA",    "Boeing",             "NYSE",   "defense"),
        # Retail/Consumer
        ("WMT",   "Walmart",            "NYSE",   "retail"),
        ("COST",  "Costco",             "NASDAQ", "retail"),
        ("TGT",   "Target",             "NYSE",   "retail"),
        ("AMZN",  "Amazon",             "NASDAQ", "retail"),
        # From our signal corpus
        ("PLUG",  "Plug Power",         "NASDAQ", "energy"),
        ("BE",    "Bloom Energy",       "NYSE",   "energy"),
        ("SPWR",  "SunPower",           "NASDAQ", "energy"),
        ("CEZ",   "CEZ",                "PRAGUE", "utilities"),
        ("RELX",  "RELX",               "NYSE",   "technology"),
    ]

    aliases_to_add = [
        ("Devon Energy",        "DVN"),
        ("ConocoPhillips",      "COP"),
        ("Cheniere Energy",     "LNG"),
        ("Energy Transfer",     "ET"),
        ("GE Vernova",          "GEV"),
        ("National Grid",       "NGG"),
        ("Plug Power",          "PLUG"),
        ("Bloom Energy",        "BE"),
        ("NextEra",             "NEE"),
        ("ExxonMobil",          "XOM"),
        ("Exxon Mobil",         "XOM"),
        ("Applied Materials",   "AMAT"),
        ("Lam Research",        "LRCX"),
        ("Super Micro",         "SMCI"),
        ("ServiceNow",          "NOW"),
        ("Goldman",             "GS"),
        ("JPMorgan",            "JPM"),
        ("Eli Lilly",           "LLY"),
        ("Lockheed",            "LMT"),
        ("Northrop",            "NOC"),
        ("Palantir",            "PLTR"),
        ("Cerebras",            "CBRS"),
        ("Spruce Power",        "SPRU"),
        ("ADNOC Distribution",  "ADNOCDIST"),
    ]

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    for ticker, name, exchange, sector in common:
        conn.execute("""
            INSERT OR IGNORE INTO tickers
            (ticker, company_name, exchange, sector, source, created_at)
            VALUES (?, ?, ?, ?, 'seed', ?)
        """, (ticker, name, exchange, sector, now))

    for alias, ticker in aliases_to_add:
        conn.execute("""
            INSERT OR IGNORE INTO name_aliases (alias, ticker, confidence)
            VALUES (?, ?, 0.95)
        """, (alias, ticker))

    conn.commit()
    log.info(f"Seeded {len(common)} tickers and {len(aliases_to_add)} aliases")


# ─── Main extraction function ─────────────────────────────────────────────────

def extract_tickers(title: str, summary: str = "",
                    llm_tickers: list[str] = None) -> list[str]:
    """
    Extract tickers using all three stages.
    llm_tickers: tickers already extracted by the LLM scorer (stage 3 input)
    """
    found = set()
    text  = f"{title} {summary}"

    # Stage 1 — regex patterns (highest confidence)
    regex_tickers = extract_from_patterns(text)
    found.update(regex_tickers)

    # Stage 2 — company name lookup
    conn = init_ticker_db()
    seed_common_tickers(conn)
    name_matches = lookup_by_name(conn, text)
    conn.close()

    for ticker, confidence in name_matches:
        if confidence >= 0.8:
            found.add(ticker)

    # Stage 3 — LLM tickers as supplement (not replacement)
    # Filter LLM tickers through false positive list
    if llm_tickers:
        for ticker in llm_tickers:
            if (ticker not in FALSE_POSITIVES
                    and len(ticker) >= 2
                    and ticker.isupper()):
                found.add(ticker)

    return sorted(found)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    conn = init_ticker_db()
    seed_common_tickers(conn)

    # Test extraction
    test_cases = [
        ("Devon Energy: Optimization Success, Upstream Unpredictable", ""),
        ("Cheniere Energy Q1 2026 sees unexpected EPS loss", ""),
        ("Cisco (CSCO) reports strong earnings on AI demand", ""),
        ("GE Vernova: Why I Believe This Power Infrastructure Winner Has 60% Upside", ""),
        ("Trump Brought An Army Of CEOs To Beijing For A Reason", ""),
        ("ConocoPhillips: More Upside Given Long-Term Cash Flow Tailwinds", ""),
        ("Cerebras Systems (CBRS) IPO priced above range", ""),
        ("Plug Power: Stock Rallies On Operational Progress", ""),
    ]

    print("Ticker extraction test:\n")
    for title, summary in test_cases:
        tickers = extract_tickers(title, summary)
        print(f"Title: {title[:65]}")
        print(f"  → {tickers}\n")

    conn.close()
