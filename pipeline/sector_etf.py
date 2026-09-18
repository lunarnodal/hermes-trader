"""
Sector-to-ETF mapping module — single source of truth.

Replaces the three local copies of sector/ETF maps that lived in
verify.py, selector.py, and critic.py.  The root cause of the
healthcare→XLK / consumer→AIQ contamination bug was raw substring
matching in verify.py's ``get_sector_etf()``; this module uses
word-boundary matching against an ordered, most-specific-first keyword
table to eliminate that class of false positives.

Provides:
    SECTOR_ETFS      — canonical sector key → ETF ticker
    KEYWORDS         — ordered (keyword, sector_key) pairs for matching
    get_sector_etf() — query string → ETF via ordered keyword matching
"""

import re

# ---------------------------------------------------------------------------
# SECTOR_ETFS  —  canonical sector key → ETF ticker
# ---------------------------------------------------------------------------
# Merged from verify.py SECTOR_ETFS, selector.py SECTOR_ETFS,
# critic.py SECTOR_ETF_MAP, plus new keys from the authoritative
# keyword priority table (causality_report_2026-09-16 Finding A).
#
# Keys that appear in multiple sources with divergent values use the
# authoritative table as tiebreaker.  Notable reconciliations:
#   defense / aerospace  → ITA  (was XAR in critic.py)
#   automotive           → CARZ (was XLY in critic.py)
#   data_center          → AIQ  (was XLK in critic.py)
#   aviation             → ITA  (was XAR in critic.py — aerospace-adjacent)

SECTOR_ETFS: dict[str, str] = {
    # -- Canonical sectors -------------------------------------------------
    "energy":               "XLE",
    "oil_gas":              "XOP",
    "oil_services":         "XOP",
    "technology":           "XLK",
    "ai_infrastructure":    "SOXX",
    "ai_infra":             "SOXX",
    "semiconductors":       "SOXX",
    "semis":                "SOXX",
    "financials":           "XLF",
    "healthcare":           "XLV",
    "defense":              "ITA",
    "utilities":            "XLU",
    "real_estate":          "VNQ",
    "consumer_staples":     "XLP",
    "consumer":             "XLP",
    "consumer_discretionary": "XLY",
    "manufacturing":        "XLI",
    "industrials":          "XLI",
    "materials":            "XLB",
    "agriculture":          "MOO",
    "commodities":          "DJP",
    "macro":                "SPY",

    # -- New keys from authoritative table ---------------------------------
    "shipping":             "IYT",
    "autos":                "CARZ",
    "gold":                 "GDX",
    "insurance":            "KIE",
    "telecom":              "XTL",

    # -- Sub-sector aliases (for critic momentum gate + selector compat) ---
    "banking":              "XLF",
    "biotech":              "XLV",
    "cybersecurity":        "XLK",
    "software":             "XLK",
    "data_center":          "SOXX",
    "aerospace":            "ITA",
    "automotive":           "CARZ",
    "aviation":             "ITA",
    "space":                "ITA",
    "entertainment":        "XLY",
    "chemicals":            "XLB",
    "construction":         "XLI",
    "emerging_markets":     "EEM",
    "india":                "INDA",
    "commercial_real_estate": "VNQ",
}

# ---------------------------------------------------------------------------
# KEYWORDS  —  ordered (keyword, sector_key) table, most-specific-first
# ---------------------------------------------------------------------------
# The authoritative priority order from causality_report_2026-09-16 Finding A.
# ``get_sector_etf()`` scans this table top-to-bottom and returns the first
# word-boundary match.  Generic short keywords (tech, ai, gas) are placed
# AFTER the specific ones they could otherwise shadow.

KEYWORDS: list[tuple[str, str]] = [
    # Healthcare (XLV) — before tech to prevent "tech" in "biotech"
    ("biotech",        "healthcare"),
    ("pharma",         "healthcare"),
    ("healthcare",     "healthcare"),
    ("health care",    "healthcare"),
    ("medical",        "healthcare"),
    ("drug",           "healthcare"),
    ("clinical",       "healthcare"),

    # Semiconductors (SOXX)
    ("semiconductor",  "semiconductors"),
    ("chip",           "semiconductors"),
    ("chips",          "semiconductors"),

    # Consumer discretionary (XLY) — before staples
    ("discretionary",  "consumer_discretionary"),

    # Consumer staples (XLP)
    ("staples",        "consumer_staples"),
    ("retail",         "consumer_staples"),
    ("consumer",       "consumer_discretionary"),

    # Industrials (XLI) — before defense/aerospace
    ("industrials",    "industrials"),
    ("industrial",     "industrials"),
    ("manufacturing",  "industrials"),

    # Defense (ITA)
    ("defense",        "defense"),
    ("aerospace",      "defense"),
    ("military",       "defense"),

    # Shipping (IYT)
    ("freight",        "shipping"),
    ("shipping",       "shipping"),
    ("logistics",      "shipping"),
    ("container",      "shipping"),

    # Real estate (VNQ)
    ("reits",          "real_estate"),
    ("real estate",    "real_estate"),
    ("housing",        "real_estate"),

    # Autos (CARZ)
    ("automotive",     "autos"),
    ("auto",           "autos"),
    ("electric vehicle", "autos"),
    ("ev",             "autos"),

    # Gold (GDX)
    ("precious metals", "gold"),
    ("gold",           "gold"),

    # Materials (XLB)
    ("mining",         "materials"),
    ("materials",      "materials"),
    ("metals",         "materials"),
    ("steel",          "materials"),
    ("copper",         "materials"),
    ("lithium",        "materials"),
    ("chemical",       "materials"),
    ("fertilizer",     "materials"),

    # Insurance (KIE)
    ("insurance",      "insurance"),

    # Financials (XLF)
    ("banks",          "financials"),
    ("bank",           "financials"),
    ("lending",        "financials"),
    ("financial",      "financials"),
    ("rates",          "financials"),
    ("fed",            "financials"),
    ("fomc",           "financials"),
    ("treasury",       "financials"),
    ("yields",         "financials"),

    # Energy (XLE) — before oil/gas
    ("crude",          "energy"),
    ("brent",          "energy"),
    ("wti",            "energy"),
    ("oil",            "energy"),
    ("energy",         "energy"),

    # Oil services / natural gas (XOP) — "natural gas" before "gas"
    ("natural gas",    "oil_services"),
    ("gas",            "oil_services"),

    # Telecom (XTL)
    ("telecom",        "telecom"),

    # Utilities (XLU)
    ("utilities",      "utilities"),
    ("utility",        "utilities"),

    # AI infrastructure (AIQ) — before tech
    ("data center",    "ai_infra"),
    ("datacenters",    "ai_infra"),
    ("ai",             "ai_infra"),

    # Technology (XLK) — last among tech-adjacent
    ("tech",           "technology"),
    ("technology",     "technology"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_sector_etf(query: str) -> str:
    """
    Determine the best ETF for verification based on a query string.

    Uses **word-boundary** matching against the ordered ``KEYWORDS`` table.
    First match wins.  Returns ``"SPY"`` if no keyword matches.

    Word boundary matching ensures:
      - ``"tech"`` does NOT match inside ``"biotech"``
      - ``"ai"``   does NOT match inside ``"retail"``
      - ``"gas"``  does NOT match inside ``"gasoline"``

    Parameters
    ----------
    query : str
        Free-text prediction query (e.g. ``"Healthcare and biotech sector
        outlook"``).

    Returns
    -------
    str
        ETF ticker symbol.
    """
    query_lower = query.lower()
    for keyword, sector_key in KEYWORDS:
        # Build word-boundary regex pattern.
        # ``re.escape`` handles multi-word keywords with spaces safely.
        pattern = r"\b" + re.escape(keyword) + r"\b"
        if re.search(pattern, query_lower):
            return SECTOR_ETFS.get(sector_key, "SPY")
    return "SPY"
