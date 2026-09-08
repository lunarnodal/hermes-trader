#!/usr/bin/env python3
"""
INHD-style merger arbitrage signal pathway

Detects M&A deal announcements, calculates arbitrage spreads,
and generates event-driven signals based on deal lifecycle events.

Signal flow:
  scored signals (event_type=merger_arbitrage or merger_acquisition)
    -> detect_deal_announcement()       : tag deal terms
    -> calculate_arb_spread()           : price vs. consideration
    -> generate_event_signal()          : lifecycle event signals
    -> enrich_with_dependency_graph()   : indirect sector impacts
    -> output structured signals for downstream consumption
"""

import json
import logging
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from alpaca_feed.data import fetch_current_price

# ─── Config ───────────────────────────────────────────────────────────────────

# Arbitrage spread thresholds for signal generation
ARB_SPREAD_THRESHOLDS = {
    "low_risk":     5.0,   # spread < 5%: high probability of completion
    "medium_risk": 15.0,   # spread 5-15%: standard deal risk
    "high_risk":    15.0,  # spread > 15%: elevated deal failure risk
}

# Deal lifecycle events that trigger signals
DEAL_LIFECYCLE_EVENTS = [
    "announcement",       # Initial deal announcement
    "regulatory_review",  # Regulatory review initiated
    "regulatory_approval",# Regulatory approval received
    "regulatory_rejection",# Regulatory rejection
    "shareholder_vote",   # Shareholder vote scheduled
    "shareholder_approved",# Shareholder vote passed
    "shareholder_rejected",# Shareholder vote failed
    "closing",            # Deal closing confirmed
    "termination",        # Deal terminated
    "extension",          # Deal timeline extended
    "price_increase",     # Acquirer raised offer
    "price_decrease",     # Acquirer lowered offer
]

# Sector impact weights for merger arbitrage signals
# When arb_spread > threshold, these sectors get price pressure
SECTOR_IMPACT = {
    "energy": {"weight": 0.30, "threshold": 15.0},
    "financials": {"weight": 0.25, "threshold": 10.0},
    "commodities": {"weight": 0.20, "threshold": 12.0},
    "technology": {"weight": 0.15, "threshold": 18.0},
}

# ─── Deal Detection ───────────────────────────────────────────────────────────

# Patterns for M&A deal announcements
DEAL_PATTERNS = {
    "acquisition": [
        r"acqui(?:res|ring|sition|red)\s+[\w\s]+",
        r"buys?\s+[\w\s]+(?:for|at)\s+\$?\s*[\d,.]+",
        r"takeover\s+[\w\s]+",
        r"merger\s+with\s+[\w\s]+",
        r"[a-z]+\s+to\s+be\s+(?:acquired|bought|merged)",
        r"agrees?\s+to\s+be\s+(?:acquired|purchased|bought)",
        r"agrees?\s+to\s+(?:acquire|purchase|buy)\s+[\w\s]+",
    ],
    "consideration": [
        r"\$\s*[\d,.]+(?:\s*(?:billion|million|B|M|b|m))?",
        r"(\$)?\s*[\d,.]+\s*(?:per\s+share|/share|a\s+share)",
        r"(\d+)\s*(?:percent|%)\s*(?:premium|up)",
    ],
    "timeline": [
        r"expected\s+to\s+(?:close|complete)\s+[\w\s]+",
        r"(?:closing|completion)\s+(?:date|expected|in)\s+[\w\s]+",
        r"Q\d\s+\d{2,4}",
        r"by\s+(?:the\s+)?(?:end\s+of\s+)?\w+\s+\d{2,4}",
    ],
    "event": [
        r"shareholder\s+vote",
        r"regulatory\s+(?:approval|review|clearance)",
        r"ftc\s+(?:approval|clearance|investigation)",
        r"deal\s+(?:terminated|collapsed|ended|abandoned)",
        r"deal\s+(?:completed|closed|finalized)",
        r"raised\s+(?:its\s+)?offer",
        r"lowered\s+(?:its\s+)?offer",
    ],
}


def detect_deal_announcement(signal: dict) -> Optional[dict]:
    """
    Detect and tag M&A deal announcements from scored signals.

    Args:
        signal: Scored signal dict with title, summary, event_type, tickers.

    Returns:
        Deal dict with extracted terms, or None if not a deal.
    """
    event_type = signal.get("event_type", "").lower()
    title = signal.get("title", "")
    summary = signal.get("summary", "")
    text = (title + " " + summary).lower()
    tickers = signal.get("tickers", [])

    # Only process merger_acquisition or merger_arbitrage signals
    if event_type not in ("merger_acquisition", "merger_arbitrage"):
        return None

    # Must have at least one deal pattern match
    has_deal = False
    for pattern_group in DEAL_PATTERNS.values():
        for pat in pattern_group:
            if re.search(pat, text):
                has_deal = True
                break
        if has_deal:
            break

    if not has_deal:
        return None

    # Extract deal components
    deal = {
        "source_signal": signal.get("guid", ""),
        "title": title,
        "source": signal.get("source", ""),
        "published": signal.get("published", ""),
        "event_type": event_type,
        "tickers": tickers,
    }

    # Extract consideration value
    deal["consideration"] = _extract_consideration(text)

    # Extract per-share price
    deal["per_share_price"] = _extract_per_share_price(text)

    # Extract premium
    deal["premium_pct"] = _extract_premium(text)

    # Extract expected close date
    deal["expected_close_date"] = _extract_timeline(text)

    # Classify lifecycle event
    deal["lifecycle_event"] = _classify_lifecycle_event(text)

    # Determine target and acquirer if possible
    if len(tickers) >= 2:
        deal["target_ticker"] = tickers[0]
        deal["acquirer_ticker"] = tickers[1]
    elif len(tickers) == 1:
        deal["target_ticker"] = tickers[0]
        deal["acquirer_ticker"] = None

    return deal


def _extract_consideration(text: str) -> Optional[float]:
    """Extract total deal consideration in billions."""
    # Pattern: $X billion/million or $X.XB/M
    patterns = [
        r"\$(\d[\d,.]*)\s*(billion|B\b)",
        r"\$(\d[\d,.]*)\s*(million|M\b)",
    ]
    for pat, unit in patterns:
        match = re.search(pat, text)
        if match:
            value_str = match.group(1).replace(",", "")
            try:
                value = float(value_str)
                return value / 1e9 if unit in ("billion", "B") else value / 1e6
            except ValueError:
                continue
    return None


def _extract_per_share_price(text: str) -> Optional[float]:
    """Extract per-share offer price."""
    match = re.search(r"\$(\d[\d,.]*)\s*(?:per\s+share|/share|a\s+share)", text)
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            pass
    return None


def _extract_premium(text: str) -> Optional[float]:
    """Extract premium percentage over current price."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:percent|%)\s*(?:premium|up)", text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


def _extract_timeline(text: str) -> Optional[str]:
    """Extract expected close date/timeline."""
    patterns = [
        r"(?:expected|planned)\s+(?:to\s+)?(?:close|complete)\s+(?:in|by)\s+(\w+\s+\d{4})",
        r"(?:closing|completion)\s+(?:date|expected)\s*(?:is|:)?\s*(\w+\s+\d{4})",
    ]
    for pat in patterns:
        match = re.search(pat, text)
        if match:
            return match.group(1)
    return None


def _classify_lifecycle_event(text: str) -> str:
    """Classify the deal lifecycle event from text."""
    event_patterns = {
        "announcement": [
            r"(?:announces?|agrees?\s+to|to\s+(?:acquire|buy|merge))",
            r"(?:merger|acquisition)\s+(?:deal|announcement)",
        ],
        "regulatory_review": [
            r"regulatory\s+(?:review|scrutiny|investigation)",
            r"ftc\s+(?:review|investigation|inquiry)",
            r"waiting\s+(?:for|on)\s+regulatory",
        ],
        "regulatory_approval": [
            r"regulatory\s+(?:approval|approved|cleared)",
            r"ftc\s+(?:approved|clears?|approves?)",
            r"(?:european|eu)\s+(?:commission|antitrust)\s+(?:approved|cleared)",
        ],
        "regulatory_rejection": [
            r"regulatory\s+(?:rejected|rejection|blocks?|blocked)",
            r"(?:ftc|eu)\s+(?:blocks?|blocked|rejected)",
        ],
        "shareholder_vote": [
            r"shareholder\s+(?:vote|ballot)",
            r"awaiting\s+shareholder",
        ],
        "shareholder_approved": [
            r"shareholders?\s+(?:approve|approved|approved)",
            r"shareholder\s+vote\s+(?:passes|passed|won)",
        ],
        "shareholder_rejected": [
            r"shareholders?\s+(?:reject|rejected|rejected)",
            r"shareholder\s+vote\s+(?:fails?|failed|loses?|lost)",
        ],
        "closing": [
            r"deal\s+(?:closed|completed|finalized)",
            r"(?:closes?|completes?)\s+(?:the\s+)?deal",
        ],
        "termination": [
            r"deal\s+(?:terminated|collapsed|ended|abandoned|terminated)",
            r"(?:terminates?|collapses?)\s+(?:the\s+)?deal",
            r"(?:walking|pulled)\s+away\s+(?:from|out\s+of)",
        ],
        "price_increase": [
            r"raised?\s+(?:its?\s+)?offer",
            r"increase\s+(?:the\s+)?offer",
        ],
        "price_decrease": [
            r"lowered?\s+(?:its?\s+)?offer",
            r"cut\s+(?:the\s+)?offer",
        ],
        "extension": [
            r"(?:deadline|timeline)\s+(?:extended|pushed?\s+back)",
            r"(?:extended|extended)\s+(?:the\s+)?deadline",
        ],
    }

    for event, patterns in event_patterns.items():
        for pat in patterns:
            if re.search(pat, text):
                return event

    return "announcement"  # default for unrecognized deal events


# ─── Spread Calculation ──────────────────────────────────────────────────────


def calculate_arb_spread(deal: dict, current_price: Optional[float] = None) -> dict:
    """
    Calculate arbitrage spread between current trading price and deal consideration.

    Args:
        deal: Deal dict from detect_deal_announcement().
        current_price: Current market price. If None, fetches via alpaca_feed.

    Returns:
        Spread dict with spread_pct, risk_level, and completion probability estimate.
    """
    target = deal.get("target_ticker")
    if not target:
        return {
            "spread_pct": None,
            "risk_level": "unknown",
            "completion_probability": 0.5,
            "reason": "no target ticker identified",
        }

    # Use provided price or fetch live
    if current_price is None:
        current_price = fetch_current_price(target)

    if current_price is None:
        return {
            "spread_pct": None,
            "risk_level": "unknown",
            "completion_probability": 0.5,
            "reason": f"could not fetch price for {target}",
        }

    # Determine deal price
    deal_price = deal.get("per_share_price")
    if deal_price is None:
        return {
            "spread_pct": None,
            "risk_level": "unknown",
            "completion_probability": 0.5,
            "reason": "no per-share deal price extracted",
        }

    # Calculate spread: (deal_price - current_price) / deal_price * 100
    spread_pct = round((deal_price - current_price) / deal_price * 100, 2)
    spread_pct = max(0.0, spread_pct)  # Can't have negative arb spread

    # Classify risk level
    if spread_pct < ARB_SPREAD_THRESHOLDS["low_risk"]:
        risk_level = "low"
    elif spread_pct < ARB_SPREAD_THRESHOLDS["high_risk"]:
        risk_level = "medium"
    else:
        risk_level = "high"

    # Estimate completion probability based on spread + lifecycle event
    completion_prob = _estimate_completion_probability(
        spread_pct, deal.get("lifecycle_event", "announcement")
    )

    return {
        "spread_pct": spread_pct,
        "risk_level": risk_level,
        "completion_probability": completion_prob,
        "current_price": current_price,
        "deal_price": deal_price,
        "target_ticker": target,
    }


def _estimate_completion_probability(spread_pct: float, event: str) -> float:
    """
    Estimate deal completion probability based on spread and lifecycle stage.

    INHD-style probability model:
      - Base probability starts at 80% for announced deals
      - Higher spreads = lower probability (market pricing in failure risk)
      - Lifecycle progression increases probability
    """
    # Base probability
    prob = 0.80

    # Spread penalty: wider spreads indicate more market skepticism
    # At 15% spread, probability drops to ~50%
    spread_penalty = min(spread_pct / 30, 0.30)
    prob -= spread_penalty

    # Lifecycle event adjustments
    event_adjustments = {
        "announcement": 0.0,
        "regulatory_review": -0.05,  # Under review, slight uncertainty
        "regulatory_approval": +0.15, # Approved, much higher
        "regulatory_rejection": -0.80, # Rejected, near-certain failure
        "shareholder_vote": -0.03,
        "shareholder_approved": +0.15,
        "shareholder_rejected": -0.75,
        "closing": +0.20,
        "termination": -0.90,
        "extension": -0.08,
        "price_increase": +0.05,
        "price_decrease": -0.10,
    }

    prob += event_adjustments.get(event, 0.0)
    return round(max(0.05, min(0.95, prob)), 2)


# ─── Event Signal Generation ─────────────────────────────────────────────────


def generate_event_signal(deal: dict, spread: dict) -> Optional[dict]:
    """
    Generate an actionable INHD-style event-driven signal from a deal + spread.

    Returns structured signal dict for downstream consumption by
    selector.py, signal_graph.py, and prediction engine.
    """
    if spread.get("spread_pct") is None:
        return None

    spread_pct = spread["spread_pct"]
    risk_level = spread["risk_level"]
    completion_prob = spread["completion_probability"]
    event = deal.get("lifecycle_event", "announcement")
    target = deal.get("target_ticker", "UNKNOWN")
    event_type = deal.get("event_type", "merger_arbitrage")

    # Determine signal direction
    # Bullish on target (buy spread), bearish if spread too wide
    if risk_level == "high" and spread_pct > ARB_SPREAD_THRESHOLDS["high_risk"]:
        direction = "bearish"
        rationale = (
            f"Arb spread {spread_pct:.1f}% on {target} exceeds high-risk threshold "
            f"(>{ARB_SPREAD_THRESHOLDS['high_risk']}%). Deal failure risk elevated."
        )
        confidence = min(0.85, 0.50 + spread_pct / 100)
    elif event in ("termination", "regulatory_rejection", "shareholder_rejected"):
        direction = "bearish"
        rationale = (
            f"Deal {event} for {target}. Target stock likely to revert to pre-announcement price."
        )
        confidence = 0.80
    elif event in ("closing", "regulatory_approval", "shareholder_approved"):
        direction = "bullish"
        rationale = (
            f"Deal {event} for {target}. Spread should compress to near-zero on completion."
        )
        confidence = min(0.90, completion_prob + 0.10)
    elif completion_prob >= 0.65:
        direction = "bullish"
        rationale = (
            f"Merger arb spread {spread_pct:.1f}% on {target}. "
            f"Completion probability {completion_prob:.0%}. "
            f"Risk level: {risk_level}."
        )
        confidence = min(0.80, completion_prob * 0.9)
    else:
        direction = "neutral"
        rationale = (
            f"Merger arb spread {spread_pct:.1f}% on {target}. "
            f"Completion probability {completion_prob:.0%} —观望 stance."
        )
        confidence = 0.55

    # Sectors to tag
    sectors = ["financials"]  # Always tag financials for M&A activity
    if spread_pct > 15.0:
        sectors.extend(["energy", "commodities"])  # INHD-style energy trigger

    # Build signal
    signal = {
        "type": "merger_arbitrage",
        "direction": direction,
        "confidence": confidence,
        "tickers": [target],
        "sectors": sectors,
        "event_type": event_type,
        "lifecycle_event": event,
        "deal_terms": {
            "target": target,
            "acquirer": deal.get("acquirer_ticker"),
            "deal_price": spread.get("deal_price"),
            "current_price": spread.get("current_price"),
            "spread_pct": spread_pct,
            "risk_level": risk_level,
            "completion_probability": completion_prob,
            "consideration_b": deal.get("consideration"),
            "premium_pct": deal.get("premium_pct"),
            "expected_close": deal.get("expected_close_date"),
        },
        "rationale": rationale,
        "source_title": deal.get("title", ""),
        "source_signal": deal.get("source_signal", ""),
        "published": deal.get("published", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    return signal


# ─── Dependency Graph Integration ────────────────────────────────────────────


def enrich_with_dependency_graph(signal: dict) -> list[dict]:
    """
    Enrich merger_arbitrage signal with indirect sector impacts.

    When arb_spread > 15%, triggers energy sector price moves.
    Additional impacts based on deal size and sectors involved.
    """
    derived = []
    deal_terms = signal.get("deal_terms", {})
    spread_pct = deal_terms.get("spread_pct", 0)
    consideration = deal_terms.get("consideration_b", 0)
    event_type = signal.get("event_type", "")

    # Load indirect dependencies from lessons.db
    try:
        lessons_db = Path("/home/trading/trading-ai/data/lessons.db")
        conn = sqlite3.connect(str(lessons_db))
        rows = conn.execute("""
            SELECT from_entity, to_entity, relationship, confidence, occurrences
            FROM indirect_dependencies
            WHERE from_entity LIKE '%merger%' OR from_entity LIKE '%arb%'
        """).fetchall()
        conn.close()
        for from_e, to_e, rel, conf, occ in rows:
            derived.append({
                "type": "derived_merger_arb",
                "direction": signal["direction"],
                "confidence": round(signal["confidence"] * conf, 2),
                "sectors": [to_e.replace("_sector", "")],
                "tickers": [],
                "event_type": event_type,
                "derived_from": signal.get("source_signal", ""),
                "relationship": rel,
                "rationale": f"[INDIRECT] Merger arb signal → {to_e}: {rel[:60]}",
                "generated_at": signal["generated_at"],
            })
    except Exception:
        pass

    # Static: spread > 15% triggers energy sector (INHD-style)
    if spread_pct and spread_pct > 15.0:
        energy_conf = round(signal["confidence"] * 0.75, 2)
        derived.append({
            "type": "derived_merger_arb",
            "direction": "bullish",
            "confidence": energy_conf,
            "sectors": ["energy"],
            "tickers": [],
            "event_type": event_type,
            "derived_from": signal.get("source_signal", ""),
            "relationship": f"arb_spread {spread_pct:.1f}% > 15% triggers energy sector move",
            "rationale": (
                f"[INDIRECT] High arb spread ({spread_pct:.1f}%) on {deal_terms.get('target', '')} "
                f"signals market uncertainty → energy sector price move"
            ),
            "generated_at": signal["generated_at"],
        })

    # Static: large deals (> $5B) affect financials sector
    if consideration and consideration > 5.0:
        fin_conf = round(signal["confidence"] * 0.70, 2)
        derived.append({
            "type": "derived_merger_arb",
            "direction": "bullish",
            "confidence": fin_conf,
            "sectors": ["financials"],
            "tickers": [],
            "event_type": event_type,
            "derived_from": signal.get("source_signal", ""),
            "relationship": f"Large deal (${consideration:.1f}B) affects financial sector",
            "rationale": (
                f"[INDIRECT] Large M&A deal (${consideration:.1f}B) signals "
                f"market confidence → financials sector impact"
            ),
            "generated_at": signal["generated_at"],
        })

    return derived


# ─── Pipeline Integration ────────────────────────────────────────────────────


def process_scored_signals(signals: list[dict]) -> list[dict]:
    """
    Process a batch of scored signals and extract merger_arbitrage events.

    This is the main entry point for the merger_arbitrage pathway.
    Called by the scoring pipeline or as a standalone processor.

    Args:
        signals: List of scored signal dicts.

    Returns:
        List of structured merger_arbitrage signals (including derived).
    """
    merger_signals = []

    for signal in signals:
        deal = detect_deal_announcement(signal)
        if deal is None:
            continue

        spread = calculate_arb_spread(deal)
        event_signal = generate_event_signal(deal, spread)

        if event_signal:
            merger_signals.append(event_signal)

            # Add derived signals from dependency graph
            derived = enrich_with_dependency_graph(event_signal)
            merger_signals.extend(derived)

    return merger_signals


def process_scored_file(filepath: str) -> list[dict]:
    """
    Process a scored_*.jsonl file for merger_arbitrage signals.

    Args:
        filepath: Path to scored_*.jsonl file.

    Returns:
        List of merger_arbitrage signals extracted from file.
    """
    signals = []
    try:
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        signals.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        logging.getLogger(__name__).warning(
            f"Could not read scored file {filepath}: {e}"
        )
        return []

    return process_scored_signals(signals)


def get_active_deals(
    hours_back: int = 168,
    min_spread: float = 0.0,
    max_spread: float = None
) -> list[dict]:
    """
    Get all active merger_arbitrage signals within a time window.

    Args:
        hours_back: Lookback window in hours.
        min_spread: Minimum spread percentage to include.
        max_spread: Maximum spread percentage to include (None = no cap).

    Returns:
        List of active merger_arbitrage signals filtered by spread and recency.
    """
    from pathlib import Path as P
    from datetime import datetime, timezone, timedelta

    signals_dir = P("/mnt/qnap/timeseries/signals")
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()

    all_signals = []
    for f in sorted(signals_dir.glob("scored_*.jsonl"), reverse=True)[:50]:
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    s = json.loads(line)
                    if s.get("scored_at", "") >= cutoff:
                        all_signals.append(s)
                except json.JSONDecodeError:
                    continue

    merger_signals = process_scored_signals(all_signals)

    # Filter by spread
    if min_spread > 0 or max_spread is not None:
        filtered = []
        for ms in merger_signals:
            spread = ms.get("deal_terms", {}).get("spread_pct", 0)
            if spread is None:
                continue
            if min_spread > 0 and spread < min_spread:
                continue
            if max_spread is not None and spread > max_spread:
                continue
            filtered.append(ms)
        merger_signals = filtered

    return merger_signals


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Demo: test with sample signals
    test_signals = [
        {
            "guid": "test-001",
            "title": "TechCorp agrees to acquire DataSystems for $4.2 billion",
            "summary": "TechCorp announces plans to acquire DataSystems for $4.2 billion, "
                       "paying $85 per share — a 22% premium. Deal expected to close Q2 2027.",
            "source": "Reuters",
            "published": "2026-09-01T10:00:00Z",
            "scored_at": "2026-09-01T10:30:00Z",
            "sentiment": "bullish",
            "confidence": 0.85,
            "tickers": ["DATA", "TECH"],
            "sectors": ["technology"],
            "event_type": "merger_acquisition",
        },
        {
            "guid": "test-002",
            "title": "EnergyMerge spread widens to 18% as regulatory review stalls",
            "summary": "Arbitrage spread on EnergyMerge target widens to 18% as FTC review "
                       "enters second phase. Deal consideration $120 per share.",
            "source": "Bloomberg",
            "published": "2026-09-02T14:00:00Z",
            "scored_at": "2026-09-02T14:30:00Z",
            "sentiment": "bearish",
            "confidence": 0.75,
            "tickers": ["EMRG"],
            "sectors": ["energy"],
            "event_type": "merger_arbitrage",
        },
        {
            "guid": "test-003",
            "title": "Regular earnings report for Apple",
            "summary": "Apple reports Q3 earnings beating estimates.",
            "source": "CNBC",
            "published": "2026-09-01T08:00:00Z",
            "scored_at": "2026-09-01T08:30:00Z",
            "sentiment": "bullish",
            "confidence": 0.80,
            "tickers": ["AAPL"],
            "sectors": ["technology"],
            "event_type": "earnings",
        },
    ]

    print("=" * 60)
    print("Merger Arbitrage Signal Pathway — Demo")
    print("=" * 60)

    results = process_scored_signals(test_signals)
    for i, r in enumerate(results):
        print(f"\n--- Signal {i+1} ---")
        print(f"  Type:      {r['type']}")
        print(f"  Direction: {r['direction']}")
        print(f"  Confidence:{r['confidence']:.2f}")
        print(f"  Tickers:   {r['tickers']}")
        print(f"  Sectors:   {r['sectors']}")
        if r.get("deal_terms"):
            dt = r["deal_terms"]
            print(f"  Spread:    {dt.get('spread_pct')}%")
            print(f"  Risk:      {dt.get('risk_level')}")
            print(f"  Completion:{dt.get('completion_probability')}")
        print(f"  Rationale: {r['rationale'][:100]}")

    print(f"\nTotal signals generated: {len(results)}")
