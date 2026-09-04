#!/usr/bin/env python3
"""
Stock selection engine
Bridges sector predictions → individual stock recommendations
Uses signal corpus to rank stocks within bullish sectors
Option C: individual stocks when signals strong, ETF as fallback
"""

import json
import logging
import os
import requests
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import sys

try:
    import sys as _sys
    import os as _os
    _sys.path.insert(0, str(Path(__file__).parent.parent))
    from feeds.finnhub_enrichment import get_full_enrichment
    ENRICHMENT_ENABLED = True
except Exception:
    ENRICHMENT_ENABLED = False
    def get_full_enrichment(ticker, **kw):
        return {"score": 0.0, "signal": "neutral", "reasons": []}
sys.path.insert(0, str(Path(__file__).parent.parent))

OLLAMA_HOST  = os.getenv("OLLAMA_HOST", "http://172.29.10.225:11434")
SIGNALS_DIR  = Path(os.getenv("TIMESERIES_DIR", "/mnt/qnap/timeseries/signals"))
QDRANT_HOST  = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT  = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION   = os.getenv("QDRANT_COLLECTION", "trading_signals")

log = logging.getLogger(__name__)

# Tickers to exclude from stock selection
# These are exchange/index names misidentified as tickers
EXCLUDED_TICKERS = {
    "BRK/A", "BRK/B",  # Yahoo Finance can't handle slash in ticker
    'NDAQ', 'NYSE', 'CBOE', 'SPX', 'DJIA', 'VIX', 'DXY',
    'DOW', 'SP', 'ETF', 'IPO', 'AI', 'EV',
}

# Sector → ETF fallback mapping
SECTOR_ETFS = {
    "energy":          "XLE",
    "oil_gas":         "XOP",
    "technology":      "XLK",
    "ai_infrastructure": "AIQ",
    "semiconductors":  "SOXX",
    "financials":      "XLF",
    "healthcare":      "XLV",
    "defense":         "ITA",
    "utilities":       "XLU",
    "real_estate":     "VNQ",
    "consumer":        "XLP",
    "consumer_staples": "XLP",
    "materials":       "XLB",
    "industrials":     "XLI",
    "macro":           "SPY",
}

# Minimum signals required to buy individual stock vs ETF
MIN_SIGNALS_FOR_STOCK = 2
MIN_SIGNALS_BEARISH_OVERRIDE = 3  # bearish signals needed to suppress bullish entry


def get_recent_signals(hours_back: int = 48) -> list[dict]:
    """Load recent scored signals from QNAP"""
    signals = []
    cutoff  = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()

    for f in sorted(SIGNALS_DIR.glob("scored_*.jsonl"), reverse=True)[:20]:
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    s = json.loads(line)
                    if isinstance(s, dict) and s.get("scored_at", "") >= cutoff:
                        signals.append(s)
                except:
                    continue

    return signals


def score_ticker(ticker: str, signals: list[dict]) -> dict:
    """
    Score a ticker based on its signal history
    Returns composite score for ranking
    """
    ticker_signals = [
        s for s in signals
        if ticker in s.get("tickers", [])
    ]

    if not ticker_signals:
        return {"ticker": ticker, "score": 0, "signal_count": 0}

    bull    = sum(1 for s in ticker_signals if s["sentiment"] == "bullish")
    bear    = sum(1 for s in ticker_signals if s["sentiment"] == "bearish")
    avg_conf = sum(s["confidence"] for s in ticker_signals) / len(ticker_signals)

    # Recency weighting — more recent signals worth more
    now = datetime.now(timezone.utc)
    recency_scores = []
    for s in ticker_signals:
        try:
            scored_at = datetime.fromisoformat(
                s.get("scored_at", "").replace("Z", "+00:00"))
            hours_ago = (now - scored_at).total_seconds() / 3600
            recency   = max(0, 1 - (hours_ago / 48))  # decay over 48h
            recency_scores.append(recency)
        except:
            recency_scores.append(0.5)

    avg_recency = sum(recency_scores) / len(recency_scores)

    # Enrichment boost from Finnhub analyst ratings + earnings
    enrichment_boost = 0.0
    if ENRICHMENT_ENABLED:
        try:
            _enrich = get_full_enrichment(ticker)
            enrichment_boost = _enrich["score"] * 0.3
        except Exception:
            pass

    # Composite score
    sentiment_score = (bull - bear) / len(ticker_signals)  # -1 to +1
    # Blend signal score (70%) with analyst enrichment (30%)
    signal_composite = (
        sentiment_score * 0.4 +
        avg_conf        * 0.4 +
        avg_recency     * 0.2
    )
    composite = signal_composite * 0.7 + enrichment_boost

    return {
        "ticker":       ticker,
        "score":        round(composite, 3),
        "signal_count": len(ticker_signals),
        "bullish":      bull,
        "bearish":      bear,
        "avg_conf":     round(avg_conf, 3),
        "avg_recency":  round(avg_recency, 3),
        "sentiment":    "bullish" if bull > bear else
                       "bearish" if bear > bull else "neutral",
    }


def get_sector_tickers(sector: str, signals: list[dict]) -> list[str]:
    """Get all tickers mentioned in signals for a given sector"""
    tickers = set()
    for s in signals:
        if sector in s.get("sectors", []):
            for ticker in s.get("tickers", []):
                if (ticker
                        and len(ticker) <= 5
                        and ticker not in EXCLUDED_TICKERS):
                    tickers.add(ticker)
    return list(tickers)


def fetch_current_price(ticker: str) -> float | None:
    """Fetch current price from Yahoo Finance"""
    try:
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            params={"interval": "1m", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        resp.raise_for_status()
        data   = resp.json()
        result = data["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        return round(closes[-1], 2) if closes else None
    except Exception as e:
        log.warning(f"Could not fetch price for {ticker}: {e}")
        return None


# Minimum price to avoid penny stocks and crypto
MIN_STOCK_PRICE = 5.00
MAX_STOCK_PRICE = 5000.00  # Avoid Berkshire A class etc


def get_primary_sector(ticker: str) -> str | None:
    """Get primary sector for a ticker from our DB"""
    try:
        import sqlite3
        conn = sqlite3.connect("/home/trading/trading-ai/data/tickers.db")
        row  = conn.execute(
            "SELECT sector FROM tickers WHERE ticker = ?", (ticker,)
        ).fetchone()
        conn.close()
        return row[0].lower().replace(" ", "_") if row and row[0] else None
    except:
        return None


# Sector compatibility map — which DB sectors are compatible with prediction sectors
SECTOR_COMPATIBILITY = {
    "energy":        ["energy", "utilities", "oil_gas", "industrials",
                      "basic_materials", "materials"],
    "technology":    ["technology", "communications", "semiconductors",
                      "consumer_electronics", "software", "hardware"],
    "financials":    ["financials", "financial_services", "banks",
                      "insurance", "real_estate"],
    "healthcare":    ["healthcare", "pharmaceuticals", "biotechnology",
                      "medical_devices"],
    "defense":       ["industrials", "defense", "aerospace"],
    "utilities":     ["utilities", "energy"],
    "consumer":      ["consumer_discretionary", "consumer_staples",
                      "retail", "food"],
    "industrials":   ["industrials", "manufacturing", "transportation"],
    "materials":     ["basic_materials", "materials", "chemicals", "mining"],
    "semiconductors": ["technology", "semiconductors"],
    "ai_infrastructure": ["technology", "semiconductors", "communications"],
}


def is_sector_compatible(ticker: str, prediction_sector: str) -> bool:
    """Check if ticker's primary sector is compatible with prediction sector"""
    primary = get_primary_sector(ticker)
    if not primary:
        return True  # Unknown sector — allow through
    compatible = SECTOR_COMPATIBILITY.get(prediction_sector, [prediction_sector])
    # Check if any compatible sector is a substring of primary (handles variants)
    for comp in compatible:
        if comp in primary or primary in comp:
            return True
    return False


def is_sp500_or_nasdaq(ticker: str) -> bool:
    """Check if ticker is in our known universe (NASDAQ API loaded tickers)"""
    try:
        import sqlite3
        conn = sqlite3.connect("/home/trading/trading-ai/data/tickers.db")
        row  = conn.execute(
            "SELECT ticker FROM tickers WHERE ticker = ?", (ticker,)
        ).fetchone()
        conn.close()
        return row is not None
    except:
        return True  # assume valid if DB unavailable


def select_stocks_for_sector(sector: str,
                              prediction_confidence: float,
                              signals: list[dict],
                              exclude_tickers: set = None) -> list[dict]:
    """
    Option C implementation:
    - 2+ bullish signals on specific ticker → recommend that stock
    - Fewer signals → recommend sector ETF
    """
    recommendations = []

    # Get tickers in this sector from recent signals
    sector_tickers = get_sector_tickers(sector, signals)
    log.info(f"Sector {sector}: {len(sector_tickers)} tickers in signals")

    # Score each ticker
    scored = []
    for ticker in sector_tickers:
        if not is_sp500_or_nasdaq(ticker):
            continue
        if not is_sector_compatible(ticker, sector):
            log.debug(f"  {ticker} skipped — sector mismatch")
            continue
        score_data = score_ticker(ticker, signals)
        meets_threshold = (
            # Standard: 2+ signals any confidence
            score_data["signal_count"] >= MIN_SIGNALS_FOR_STOCK or
            # High confidence: 1 signal OK if conf >= 0.80
            (score_data["signal_count"] == 1 and score_data["avg_conf"] >= 0.75)
        )
        if meets_threshold and score_data["sentiment"] == "bullish":
            scored.append(score_data)

    # Sort by composite score
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Remove already-held tickers so both slots go to new positions
    if exclude_tickers:
        scored = [s for s in scored if s["ticker"] not in exclude_tickers]

    if scored:
        # Individual stocks — top 2 ranked
        for stock in scored[:2]:
            price = fetch_current_price(stock["ticker"])
            if price and MIN_STOCK_PRICE <= price <= MAX_STOCK_PRICE:
                recommendations.append({
                    "ticker":      stock["ticker"],
                    "type":        "stock",
                    "sector":      sector,
                    "score":       stock["score"],
                    "signal_count": stock["signal_count"],
                    "avg_conf":    stock["avg_conf"],
                    "current_price": price,
                    "rationale":   (
                        f"{stock['signal_count']} bullish signals, "
                        f"avg conf={stock['avg_conf']:.0%}, "
                        f"score={stock['score']:.3f}"
                    )
                })
        log.info(f"  → {len(recommendations)} individual stocks selected")
    else:
        # ETF fallback
        etf    = SECTOR_ETFS.get(sector, "SPY")
        price  = fetch_current_price(etf)
        if price:
            recommendations.append({
                "ticker":        etf,
                "type":          "etf",
                "sector":        sector,
                "score":         prediction_confidence,
                "signal_count":  len([s for s in signals
                                     if sector in s.get("sectors", [])]),
                "avg_conf":      prediction_confidence,
                "current_price": price,
                "rationale":     f"ETF fallback — no individual stocks met threshold"
            })
        log.info(f"  → ETF fallback: {etf}")

    return recommendations


def generate_recommendations(predictions: list[dict],
                              signals: list[dict],
                              open_positions: list[dict],
                              cash: float,
                              portfolio_value: float,
                              conn=None) -> list[dict]:
    """
    Generate buy/hold/sell recommendations from predictions + signals
    """
    from portfolio.db import CONFIG, calculate_position_size, get_reentry_status

    recommendations = []
    open_tickers    = {p["ticker"] for p in open_positions}

    for pred in predictions:
        p         = pred.get("prediction", {})
        direction = p.get("direction", "neutral")
        confidence = p.get("confidence", 0)
        query     = pred.get("query", "")

        # Only act on bullish predictions with sufficient confidence
        if direction != "bullish" or confidence < 0.70:
            log.info(f"Skipping {direction} prediction (conf={confidence:.2f})")
            _log_rejected_signal(
                sector="unknown", query=query, direction=direction,
                raw_conf=confidence, adj_conf=confidence,
                gate="direction_confidence",
                reason=f"direction={direction} conf={confidence:.2f} < 0.70"
            )
            continue

        # Extract sector from query
        query_lower = query.lower()
        sector = "macro"
        sector_keywords = {
            "energy":      ["energy", "oil", "gas", "utilities", "renewables"],
            "technology":  ["technology", "ai", "semiconductor", "data center", "gpu", "hyperscaler", "ai infrastructure", "data centre"],
            "financials":  ["financial", "bank", "rates", "real estate"],
            "healthcare":  ["healthcare", "biotech", "pharma"],
            "defense":     ["defense", "aerospace", "defense"],
            "materials":   ["materials", "mining", "metals", "chemicals", "commodities"],
            "industrials": ["industrials", "manufacturing", "infrastructure"],
            "consumer":    ["consumer", "retail", "discretionary", "staples"],
            "macro":       ["market outlook", "macro", "s&p 500"],
        }
        for s, keywords in sector_keywords.items():
            if any(kw in query_lower for kw in keywords):
                sector = s
                break

        # Hard-block: sector win rate < 35% AND bullish AND confidence < 0.65
        # Hermes analysis (2026-08-31): challenge verdict reduces confidence but doesn't
        # block execution. This gate prevents the critic's "weak sector" warnings from
        # being overridden by marginal confidence scores.
        sector_win_rates = {
            "technology": 0.44, "energy": 0.38, "healthcare": 0.35,
            "consumer": 0.30, "industrials": 0.14, "macro": 0.28,
            "materials": 0.27, "financials": 0.21, "defense": 0.50,
        }
        sector_win_rate = sector_win_rates.get(sector, 0.40)

        # Hard-block: sector win rate < 35% AND bullish AND confidence < 0.65
        if sector_win_rate < 0.35 and direction == "bullish" and confidence < 0.65:
            log.info(
                f"HARD BLOCK {sector}: win_rate={sector_win_rate:.0%} < 35%, "
                f"bullish, conf={confidence:.0%} < 65% — auto-reject"
            )
            _log_rejected_signal(
                sector=sector, query=query, direction=direction,
                raw_conf=confidence, adj_conf=confidence,
                gate="hard_block",
                reason=f"win_rate={sector_win_rate:.0%} < 35%, conf={confidence:.0%} < 65%",
                sector_win_rate=sector_win_rate
            )
            continue

        # Meta-correction: adjust confidence by sector trim (LTFT + STFT)
        # Dynamic fuel-trim system — LTFT absorbs STFT over time
        # Falls back to hardcoded defaults if DB unavailable
        _default_penalties = {
            "technology": 0.10, "energy": 0.05, "healthcare": 0.05,
            "consumer": 0.05, "industrials": 0.35, "macro": 0.20,
            "materials": 0.20, "financials": 0.20, "defense": 0.0,
        }
        try:
            import sqlite3 as _sql
            _tconn = _sql.connect(str(Path(__file__).parent.parent.parent / "data" / "paper_trading.db"))
            _trim = _tconn.execute(
                "SELECT ltft, stft FROM sector_trim WHERE sector=?", (sector,)
            ).fetchone()
            _tconn.close()
            if _trim:
                # Combined trim — LTFT is negative (penalty), STFT adjusts up or down
                penalty = abs(_trim[0] + _trim[1])
                log.debug(f"Trim {sector}: LTFT={_trim[0]:+.3f} STFT={_trim[1]:+.3f} → penalty={penalty:.3f}")
            else:
                penalty = _default_penalties.get(sector, 0.10)
        except Exception as _te:
            log.warning(f"Trim DB read failed (using default): {_te}")
            penalty = _default_penalties.get(sector, 0.10)
        adjusted_confidence = confidence * (1 - penalty)
        if adjusted_confidence != confidence:
            log.info(
                f"Meta-correction {sector}: {confidence:.0%} × {1-penalty:.2f} "
                f"= {adjusted_confidence:.0%} (penalty={penalty:.0%})"
            )
            if adjusted_confidence < 0.70:
                _log_rejected_signal(
                    sector=sector, query=query, direction=direction,
                    raw_conf=confidence, adj_conf=adjusted_confidence,
                    gate="meta_correction",
                    reason=f"raw={confidence:.0%} adj={adjusted_confidence:.0%} < 0.70 after sector penalty",
                    sector_win_rate=sector_win_rate
                )
        confidence = adjusted_confidence

        # Direction-specific calibration (from predictions DB, 2026-08-31)
        # bullish: 448 predictions, 34% win rate
        # neutral: 32 predictions, 31% win rate
        # bearish: 48 predictions, 21% win rate
        # mixed:   20 predictions,  0% win rate
        direction_penalties = {
            "bullish": 0.15,
            "neutral": 0.15,
            "bearish": 0.25,
            "mixed":   1.00,  # hard block — 0% win rate
        }
        dir_penalty = direction_penalties.get(direction, 0.15)
        if dir_penalty >= 1.0:
            log.info(f"HARD BLOCK direction={direction} — 0% historical win rate")
            _log_rejected_signal(
                sector=sector, query=query, direction=direction,
                raw_conf=confidence, adj_conf=0.0,
                gate="direction_hard_block",
                reason=f"direction={direction} has 0% historical win rate",
                sector_win_rate=sector_win_rate
            )
            continue
        dir_adjusted = confidence * (1 - dir_penalty)
        if dir_adjusted != confidence:
            log.info(
                f"Direction correction {direction}: {confidence:.0%} × {1-dir_penalty:.2f} "
                f"= {dir_adjusted:.0%} (penalty={dir_penalty:.0%})"
            )
        confidence = dir_adjusted

        # Get stock recommendations for this sector
        stocks = select_stocks_for_sector(sector, confidence, signals,
                                              exclude_tickers=open_tickers)

        for stock in stocks:
            ticker = stock["ticker"]

            # Bearish signal suppression (Hermes rec #9 2026-08-31)
            # If bearish signals >= MIN_SIGNALS_BEARISH_OVERRIDE, suppress bullish entry
            # Even though we only trade bullish, strong bearish signals are a warning
            bearish_count = stock.get("bearish", 0)
            bullish_count = stock.get("bullish", 0)
            if bearish_count >= MIN_SIGNALS_BEARISH_OVERRIDE and bearish_count >= bullish_count:
                log.info(
                    f"BEARISH SUPPRESSION {ticker}: {bearish_count} bearish vs "
                    f"{bullish_count} bullish signals — suppressing entry"
                )
                continue

            # Skip if already holding
            if ticker in open_tickers:
                recommendations.append({
                    "ticker":      ticker,
                    "action":      "HOLD",
                    "sector":      sector,
                    "signal_count": stock["signal_count"],
                    "avg_confidence": stock["avg_conf"],
                    "rationale":   f"Already in portfolio — maintain position",
                    "suggested_shares": 0,
                    "suggested_value":  0,
                })
                continue

            # Check re-entry eligibility — only applies to previously closed positions
            if conn is not None:
                reentry = get_reentry_status(conn, ticker)
                # Only restrict if there IS a prior closed position
                if reentry.get("reason") != "no prior position":
                    if not reentry["eligible"]:
                        log.info(f"SKIP {ticker} — {reentry['reason']}")
                        continue
                    # Stricter thresholds for re-entry after stop loss
                    required_signals = reentry.get("min_signals", 2)
                    required_conf    = reentry.get("min_confidence", 0.60)
                    if stock["signal_count"] < required_signals:
                        log.info(f"SKIP {ticker} — re-entry requires {required_signals} signals "
                                 f"(have {stock['signal_count']})")
                        continue
                    if stock["avg_conf"] < required_conf:
                        log.info(f"SKIP {ticker} — re-entry requires {required_conf:.0%} confidence "
                                 f"(have {stock['avg_conf']:.0%})")
                        continue

            # Calculate position size
            sizing = calculate_position_size(
                confidence, stock["current_price"],
                portfolio_value, cash
            )

            if sizing["shares"] <= 0:
                log.info(f"Skipping {ticker} — insufficient cash or size too small")
                continue

            recommendations.append({
                "ticker":          ticker,
                "action":          "BUY",
                "sector":          sector,
                "signal_count":    stock["signal_count"],
                "avg_confidence":  stock["avg_conf"],
                "current_price":   stock["current_price"],
                "suggested_shares": sizing["shares"],
                "suggested_value":  sizing["value"],
                "position_pct":    sizing["position_pct"],
                "stop_loss":       sizing["stop_loss"],
                "take_profit":     sizing["take_profit"],
                "rationale":       stock["rationale"],
                "type":            stock["type"],
            })

    # Generate SELL recommendations for open positions
    for pos in open_positions:
        ticker  = pos["ticker"]
        pnl_pct = pos["unrealized_pct"]

        if pnl_pct >= CONFIG["profit_tiers"][0][0] * 100:
            recommendations.append({
                "ticker":  ticker,
                "action":  "SELL",
                "sector":  pos.get("sector", ""),
                "rationale": f"Take profit: +{pnl_pct:.1f}% (target: +{CONFIG['profit_tiers'][0][0]*100:.0f}%)",
                "suggested_shares": pos["shares"],
                "suggested_value":  pos["current_value"],
                "exit_reason": "take_profit",
            })
        elif pos.get("current_price", pos["entry_price"]) <= pos["stop_loss"]:
            recommendations.append({
                "ticker":  ticker,
                "action":  "SELL",
                "sector":  pos.get("sector", ""),
                "rationale": f"Stop loss: {pnl_pct:.1f}% (stop at ${pos['stop_loss']:.2f})",
                "suggested_shares": pos["shares"],
                "suggested_value":  pos["current_value"],
                "exit_reason": "stop_loss",
            })
        elif pos["hold_days"] >= CONFIG["max_hold_days"]:
            recommendations.append({
                "ticker":  ticker,
                "action":  "REVIEW",
                "sector":  pos.get("sector", ""),
                "rationale": f"Hold period expired ({pos['hold_days']} days) — re-evaluate",
                "suggested_shares": 0,
                "suggested_value":  0,
                "exit_reason": "time_exit",
            })

    return recommendations


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    pass  # logging configured by entry point

    signals = get_recent_signals(48)
    print(f"Loaded {len(signals)} recent signals")

    # Test sector stock selection
    for sector in ["energy", "technology", "financials"]:
        print(f"\n--- {sector.upper()} ---")
        recs = select_stocks_for_sector(sector, 0.75, signals)
        for r in recs:
            print(f"  {r['ticker']:6s} ({r['type']:5s}) "
                  f"${r['current_price']:7.2f} | "
                  f"{r['signal_count']} signals | "
                  f"{r['rationale']}")