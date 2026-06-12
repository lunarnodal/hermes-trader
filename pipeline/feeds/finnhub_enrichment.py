"""
Finnhub enrichment — analyst ratings, price targets, earnings surprises

Provides structured fundamental data to enrich:
  1. Pre-market gap classification (hold vs exit decision)
  2. Stock selection (weight by analyst consensus)
  3. Prediction context (DeepSeek gets analyst sentiment)

Free tier endpoints used:
  /stock/recommendation  — analyst buy/hold/sell counts
  /stock/price-target    — consensus price target
  /stock/eps-surprise    — recent earnings beat/miss
  /stock/insider-sentiment — insider buying/selling
"""

import os
import logging
import requests
import sqlite3
from pathlib import Path as _Path
from dotenv import load_dotenv
load_dotenv(_Path(__file__).parent.parent / ".env")
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

FINNHUB_TOKEN = os.getenv("FINNHUB_TOKEN", "")
BASE_URL      = "https://finnhub.io/api/v1"
CACHE_DB      = Path("/home/trading/trading-ai/data/enrichment_cache.db")
CACHE_TTL_HOURS = 6  # refresh every 6 hours


def init_cache() -> sqlite3.Connection:
    """Initialize enrichment cache DB"""
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analyst_ratings (
            ticker      TEXT PRIMARY KEY,
            buy         INTEGER DEFAULT 0,
            overweight  INTEGER DEFAULT 0,
            hold        INTEGER DEFAULT 0,
            underweight INTEGER DEFAULT 0,
            sell        INTEGER DEFAULT 0,
            total       INTEGER DEFAULT 0,
            buy_pct     REAL DEFAULT 0,
            sell_pct    REAL DEFAULT 0,
            consensus   TEXT DEFAULT 'hold',
            fetched_at  TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_targets (
            ticker          TEXT PRIMARY KEY,
            target_mean     REAL,
            target_high     REAL,
            target_low      REAL,
            target_median   REAL,
            upside_pct      REAL,
            last_price      REAL,
            fetched_at      TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS earnings_surprises (
            ticker      TEXT,
            period      TEXT,
            actual      REAL,
            estimate    REAL,
            surprise    REAL,
            surprise_pct REAL,
            fetched_at  TEXT,
            PRIMARY KEY (ticker, period)
        )
    """)
    conn.commit()
    return conn


def _is_cache_valid(fetched_at: str) -> bool:
    """Check if cached data is still fresh"""
    if not fetched_at:
        return False
    try:
        fetched = datetime.fromisoformat(fetched_at)
        return (datetime.now(timezone.utc) - fetched).total_seconds() < CACHE_TTL_HOURS * 3600
    except:
        return False


def _finnhub_get(endpoint: str, params: dict) -> dict | None:
    """Make a Finnhub API call"""
    if not FINNHUB_TOKEN:
        log.warning("FINNHUB_TOKEN not set")
        return None
    try:
        params["token"] = FINNHUB_TOKEN
        resp = requests.get(
            f"{BASE_URL}/{endpoint}",
            params=params,
            timeout=10
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning(f"Finnhub {endpoint} failed for {params.get('symbol', '?')}: {e}")
        return None


def get_analyst_ratings(ticker: str,
                         force_refresh: bool = False) -> dict | None:
    """
    Get analyst buy/hold/sell ratings for a ticker.
    Returns consensus rating and detailed breakdown.
    """
    conn = init_cache()

    # Check cache
    if not force_refresh:
        row = conn.execute(
            "SELECT * FROM analyst_ratings WHERE ticker = ?", (ticker,)
        ).fetchone()
        if row and _is_cache_valid(row[10]):
            conn.close()
            return {
                "ticker":      row[0],
                "buy":         row[1],
                "overweight":  row[2],
                "hold":        row[3],
                "underweight": row[4],
                "sell":        row[5],
                "total":       row[6],
                "buy_pct":     row[7],
                "sell_pct":    row[8],
                "consensus":   row[9],
                "cached":      True,
            }

    # Fetch from Finnhub
    data = _finnhub_get("stock/recommendation", {"symbol": ticker})
    if not data or not isinstance(data, list) or not data:
        conn.close()
        return None

    # Most recent recommendation period
    latest = data[0]
    buy         = latest.get("buy", 0) + latest.get("strongBuy", 0)
    overweight  = 0  # Finnhub combines into buy
    hold        = latest.get("hold", 0)
    underweight = 0
    sell        = latest.get("sell", 0) + latest.get("strongSell", 0)
    total       = buy + hold + sell or 1

    buy_pct  = round(buy / total * 100, 1)
    sell_pct = round(sell / total * 100, 1)

    # Determine consensus
    if buy_pct >= 60:
        consensus = "strong_buy"
    elif buy_pct >= 45:
        consensus = "buy"
    elif sell_pct >= 30:
        consensus = "sell"
    elif sell_pct >= 15:
        consensus = "underweight"
    else:
        consensus = "hold"

    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO analyst_ratings
        (ticker, buy, overweight, hold, underweight, sell, total,
         buy_pct, sell_pct, consensus, fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (ticker, buy, overweight, hold, underweight, sell, total,
          buy_pct, sell_pct, consensus, now))
    conn.commit()
    conn.close()

    result = {
        "ticker":     ticker,
        "buy":        buy,
        "overweight": overweight,
        "hold":       hold,
        "underweight":underweight,
        "sell":       sell,
        "total":      total,
        "buy_pct":    buy_pct,
        "sell_pct":   sell_pct,
        "consensus":  consensus,
        "cached":     False,
    }
    log.info(f"Analyst ratings {ticker}: {buy}B/{hold}H/{sell}S "
             f"({buy_pct:.0f}% buy) → {consensus}")
    return result


def get_price_target(ticker: str,
                      current_price: float = None,
                      force_refresh: bool = False) -> dict | None:
    """Get consensus price target and upside/downside"""
    conn = init_cache()

    if not force_refresh:
        row = conn.execute(
            "SELECT * FROM price_targets WHERE ticker = ?", (ticker,)
        ).fetchone()
        if row and _is_cache_valid(row[7]):
            conn.close()
            return {
                "ticker":       row[0],
                "target_mean":  row[1],
                "target_high":  row[2],
                "target_low":   row[3],
                "target_median":row[4],
                "upside_pct":   row[5],
                "last_price":   row[6],
                "cached":       True,
            }

    data = _finnhub_get("stock/price-target", {"symbol": ticker})
    if not data or not data.get("targetMean"):
        conn.close()
        return None

    target_mean   = data.get("targetMean")
    target_high   = data.get("targetHigh")
    target_low    = data.get("targetLow")
    target_median = data.get("targetMedian")
    last_price    = data.get("lastUpdated") and current_price

    upside_pct = None
    if current_price and target_mean:
        upside_pct = round((target_mean - current_price) / current_price * 100, 1)

    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO price_targets
        (ticker, target_mean, target_high, target_low, target_median,
         upside_pct, last_price, fetched_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (ticker, target_mean, target_high, target_low, target_median,
          upside_pct, current_price, now))
    conn.commit()
    conn.close()

    log.info(f"Price target {ticker}: mean=${target_mean:.2f} "
             f"upside={upside_pct:+.1f}%" if upside_pct else
             f"Price target {ticker}: mean=${target_mean:.2f}")

    return {
        "ticker":       ticker,
        "target_mean":  target_mean,
        "target_high":  target_high,
        "target_low":   target_low,
        "target_median":target_median,
        "upside_pct":   upside_pct,
        "last_price":   current_price,
        "cached":       False,
    }


def get_earnings_surprise(ticker: str,
                           force_refresh: bool = False) -> list[dict] | None:
    """Get recent earnings surprises (beat/miss history)"""
    conn = init_cache()

    if not force_refresh:
        rows = conn.execute("""
            SELECT period, actual, estimate, surprise, surprise_pct, fetched_at
            FROM earnings_surprises WHERE ticker = ?
            ORDER BY period DESC LIMIT 4
        """, (ticker,)).fetchall()
        if rows and _is_cache_valid(rows[0][5]):
            conn.close()
            return [{"period": r[0], "actual": r[1], "estimate": r[2],
                     "surprise": r[3], "surprise_pct": r[4]} for r in rows]

    data = _finnhub_get("stock/earnings", {"symbol": ticker})
    if not data or not isinstance(data, list):
        conn.close()
        return None

    now = datetime.now(timezone.utc).isoformat()
    results = []
    for e in data[:4]:
        actual   = e.get("actual")
        estimate = e.get("estimate")
        period   = e.get("period", "")
        if actual is None or estimate is None:
            continue
        surprise     = round(actual - estimate, 4)
        surprise_pct = round((actual - estimate) / abs(estimate) * 100, 1) if estimate else 0

        conn.execute("""
            INSERT OR REPLACE INTO earnings_surprises
            (ticker, period, actual, estimate, surprise, surprise_pct, fetched_at)
            VALUES (?,?,?,?,?,?,?)
        """, (ticker, period, actual, estimate, surprise, surprise_pct, now))
        results.append({
            "period":       period,
            "actual":       actual,
            "estimate":     estimate,
            "surprise":     surprise,
            "surprise_pct": surprise_pct,
        })

    conn.commit()
    conn.close()

    beats = sum(1 for r in results if r["surprise"] > 0)
    log.info(f"Earnings {ticker}: {beats}/{len(results)} beats in last {len(results)} quarters")
    return results


def get_full_enrichment(ticker: str,
                         current_price: float = None) -> dict:
    """
    Get complete fundamental enrichment for a ticker.
    Combines analyst ratings, price target, earnings history.
    Returns a unified enrichment dict.
    """
    ratings  = get_analyst_ratings(ticker)
    target   = get_price_target(ticker, current_price)
    earnings = get_earnings_surprise(ticker)

    # Calculate enrichment score (-1.0 to +1.0)
    score = 0.0
    reasons = []

    if ratings:
        buy_pct  = ratings['buy_pct'] / 100
        sell_pct = ratings['sell_pct'] / 100
        rating_score = (buy_pct - sell_pct) * 2 - 1  # -1 to +1
        score += rating_score * 0.5
        reasons.append(f"analysts: {ratings['buy_pct']:.0f}% buy ({ratings['consensus']})")

    if target and target.get('upside_pct') is not None:
        upside = target['upside_pct']
        target_score = max(-1.0, min(1.0, upside / 20))  # normalize ±20% = ±1.0
        score += target_score * 0.3
        reasons.append(f"price target: {upside:+.1f}% upside")

    if earnings:
        beats     = sum(1 for e in earnings if e['surprise'] > 0)
        beat_rate = beats / len(earnings) if earnings else 0.5
        earn_score = (beat_rate - 0.5) * 2  # -1 to +1
        score += earn_score * 0.2
        recent = earnings[0] if earnings else None
        if recent:
            reasons.append(f"earnings: {beats}/{len(earnings)} beats, "
                          f"last {recent['surprise_pct']:+.1f}%")

    score = round(max(-1.0, min(1.0, score)), 3)

    # Translate score to signal
    if score >= 0.4:
        signal = "strong_bullish"
    elif score >= 0.1:
        signal = "bullish"
    elif score <= -0.4:
        signal = "strong_bearish"
    elif score <= -0.1:
        signal = "bearish"
    else:
        signal = "neutral"

    return {
        "ticker":   ticker,
        "score":    score,
        "signal":   signal,
        "reasons":  reasons,
        "ratings":  ratings,
        "target":   target,
        "earnings": earnings,
    }


def get_gap_context(ticker: str,
                    gap_pct: float,
                    current_price: float = None) -> dict:
    """
    Classify a pre-market gap as company-specific or market-wide.
    Used by premarket.py to decide hold vs exit.
    
    Returns recommendation: 'exit', 'hold', 'monitor'
    """
    enrichment = get_full_enrichment(ticker, current_price)
    ratings    = enrichment.get("ratings")
    earnings   = enrichment.get("earnings")

    reasons  = []
    exit_signals = 0
    hold_signals = 0

    # Check analyst consensus
    if ratings:
        if ratings['consensus'] in ('sell', 'underweight'):
            exit_signals += 2
            reasons.append(f"analysts bearish ({ratings['sell_pct']:.0f}% sell)")
        elif ratings['consensus'] in ('strong_buy', 'buy'):
            hold_signals += 1
            reasons.append(f"analysts bullish ({ratings['buy_pct']:.0f}% buy)")

    # Check recent earnings
    if earnings:
        recent = earnings[0]
        if recent['surprise_pct'] < -5:
            exit_signals += 2
            reasons.append(f"recent earnings miss ({recent['surprise_pct']:+.1f}%)")
        elif recent['surprise_pct'] > 5:
            hold_signals += 1
            reasons.append(f"recent earnings beat ({recent['surprise_pct']:+.1f}%)")

    # Check price target vs gap
    target = enrichment.get("target")
    if target and target.get("upside_pct") is not None:
        if target["upside_pct"] < -10:
            exit_signals += 1
            reasons.append(f"below price target ({target['upside_pct']:+.1f}% upside)")
        elif target["upside_pct"] > 15:
            hold_signals += 1
            reasons.append(f"significant upside to target ({target['upside_pct']:+.1f}%)")

    # Decide
    if exit_signals >= 2:
        recommendation = "exit"
    elif hold_signals >= 2 and abs(gap_pct) < 0.08:
        recommendation = "hold"
    else:
        recommendation = "monitor"

    return {
        "ticker":         ticker,
        "gap_pct":        gap_pct,
        "recommendation": recommendation,
        "exit_signals":   exit_signals,
        "hold_signals":   hold_signals,
        "reasons":        reasons,
        "enrichment":     enrichment,
    }


if __name__ == "__main__":
    import logging
    from dotenv import load_dotenv
    from pathlib import Path as _Path
    load_dotenv(_Path(__file__).parent.parent / ".env")

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["AMAT", "GEN", "SNAP"]

    for ticker in tickers:
        print(f"\n{'='*50}")
        print(f"Enrichment: {ticker}")
        result = get_full_enrichment(ticker)
        print(f"Score:   {result['score']:+.3f} ({result['signal']})")
        for r in result['reasons']:
            print(f"  {r}")
        if result['ratings']:
            r = result['ratings']
            print(f"Analysts: {r['buy']}B / {r['hold']}H / {r['sell']}S "
                  f"({r['buy_pct']:.0f}% buy)")
        if result['target']:
            t = result['target']
            print(f"Target:  ${t['target_mean']:.2f} "
                  f"({t['upside_pct']:+.1f}% upside)" if t.get('upside_pct') else
                  f"Target:  ${t['target_mean']:.2f}")
        if result['earnings']:
            for e in result['earnings'][:2]:
                beat = "✓" if e['surprise'] > 0 else "✗"
                print(f"Earnings {e['period']}: {beat} "
                      f"actual={e['actual']:.2f} est={e['estimate']:.2f} "
                      f"({e['surprise_pct']:+.1f}%)")
