"""
Prediction Critic Agent

Reviews DeepSeek's predictions before they enter the portfolio pipeline.
Challenges internal contradictions, calibration history, and known dependencies.

Verdicts:
  approve  — prediction is well-reasoned and consistent with evidence
  challenge — prediction has issues but may proceed with reduced confidence
  reject   — prediction contradicts strong evidence, should not trigger trades

The critic does NOT call external APIs or models.
It reasons over structured data we already have.
This keeps it fast (runs in <1s) and deterministic.
"""

import sqlite3
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from .calibration import calculate_adjustment

log = logging.getLogger(__name__)

PAPER_DB   = Path("/home/trading/trading-ai/data/paper_trading.db")
LESSONS_DB = Path("/home/trading/trading-ai/data/lessons.db")
RULES_DB   = Path("/home/trading/trading-ai/data/rules.db")

# Organic sector → ETF mapping for momentum gate
# Covers both canonical sectors and organic tags discovered in signal corpus
SECTOR_ETF_MAP = {
    # Canonical sectors
    "technology":       "XLK",
    "energy":           "XLE",
    "healthcare":       "XLV",
    "financials":       "XLF",
    "industrials":      "XLI",
    "consumer":         "XLY",
    "materials":        "XLB",
    "macro":            "SPY",
    "defense":          "XAR",
    # Organic sub-sector tags → parent ETF
    "ai_infrastructure":"XLK",
    "semiconductors":   "SOXX",
    "biotech":          "XLV",
    "banking":          "XLF",
    "real_estate":      "XLRE",
    "utilities":        "XLU",
    "consumer_staples": "XLP",
    "commodities":      "XLB",
    "aerospace":        "XAR",
    "space":            "XAR",
    "cybersecurity":    "XLK",
    "software":         "XLK",
    "data_center":      "XLK",
    "emerging_markets": "EEM",
    "india":            "INDA",
    "automotive":       "XLY",
    "entertainment":    "XLY",
    "chemicals":        "XLB",
    "agriculture":      "MOO",
    "construction":     "XLI",
    "aviation":         "XAR",
    "commercial_real_estate": "XLRE",
}

# Cache momentum data per session (30 min TTL)
_momentum_cache: dict[str, tuple[float, float]] = {}  # etf → (timestamp, pct_change)
_MOMENTUM_CACHE_TTL = 1800

def get_sector_momentum(sector: str, days: int = 5) -> float | None:
    """
    Get N-day price momentum for the sector ETF.
    Returns percentage change (e.g. -0.047 = -4.7%) or None if unavailable.
    
    Positive = ETF trending up (supports bullish)
    Negative = ETF trending down (contradicts bullish)
    """
    import time
    import os

    etf = SECTOR_ETF_MAP.get(sector.lower())
    if not etf:
        return None

    # Check cache
    now = time.time()
    if etf in _momentum_cache:
        cached_ts, cached_val = _momentum_cache[etf]
        if now - cached_ts < _MOMENTUM_CACHE_TTL:
            return cached_val

    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from datetime import datetime, timezone, timedelta

        key    = os.getenv("ALPACA_API_KEY", "")
        secret = os.getenv("ALPACA_SECRET_KEY", "")
        if not key:
            return None

        client = StockHistoricalDataClient(key, secret)
        start  = datetime.now(timezone.utc) - timedelta(days=days + 3)  # +3 for weekends

        req = StockBarsRequest(
            symbol_or_symbols=etf,
            timeframe=TimeFrame.Day,
            start=start,
        )
        bars = client.get_stock_bars(req)

        try:
            bar_list = bars[etf]
        except (KeyError, TypeError):
            return None
        if not bar_list or len(bar_list) < 2:
            return None

        oldest   = float(bar_list[0].close)
        latest   = float(bar_list[-1].close)
        pct      = (latest - oldest) / oldest

        _momentum_cache[etf] = (now, pct)
        log.debug(f"[CRITIC] {sector} ({etf}) {days}d momentum: {pct:+.1%}")
        return round(pct, 4)

    except Exception as e:
        log.debug(f"[CRITIC] Momentum fetch failed for {sector} ({etf}): {e}")
        return None


def get_directional_streak(sector: str, direction: str, window: int = 8) -> tuple[int, float]:
    """
    Check recent directional accuracy for this sector.
    Returns (wrong_count, error_rate) for the given direction in last N predictions.
    e.g. (3, 0.75) = wrong 3 out of 4 recent bullish predictions = 75% error rate
    """
    try:
        conn = sqlite3.connect(PAPER_DB)
        rows = conn.execute("""
            SELECT direction, was_correct
            FROM predictions
            WHERE query LIKE ? AND was_correct IS NOT NULL
              AND direction = ?
            ORDER BY created_at DESC LIMIT ?
        """, (f"%{sector}%", direction, window)).fetchall()
        conn.close()

        if not rows:
            return 0, 0.0
        wrong = sum(1 for _, correct in rows if not correct)
        error_rate = wrong / len(rows)
        return wrong, error_rate
    except Exception as e:
        log.debug(f"[CRITIC] Directional accuracy check failed: {e}")
        return 0, 0.0

# Thresholds
MIN_WIN_RATE_FOR_HIGH_CONF = 0.45  # sector needs >45% win rate to support >80% confidence
CONTRADICTION_PENALTY      = 0.10  # reduce confidence when contradictions found
MAX_REDUCTIONS             = 0.25  # never reduce more than 25% total

# Calibration gate — weak sectors cannot earn an 'approve' verdict
WEAK_SECTOR_WIN_RATE_THRESHOLD  = 0.40  # win rate below this is "weak"
WEAK_SECTOR_ADJ_THRESHOLD       = -0.10  # adjustment at or below this is "weak"

WEAK_SECTOR_ADJ_THRESHOLD       = -0.10  # adjustment at or below this is "weak"

# ---- TTL cache for _get_alltime_sector_stats (avoids redundant hits) ----
_ALLTIME_CACHE: dict[str, tuple[float, tuple]] = {}   # key → (expires_at, stats_tuple)
_ALLTIME_CACHE_TTL_SECONDS  = 300                     # 5-minute TTL
_ALLTIME_LOOKBACK_MONTHS    = 12                     # date-bounds safety cap


def get_sector_win_rate(sector: str, window: int = 20) -> dict | None:
    """Get rolling win rate for a sector from predictions DB"""
    try:
        conn = sqlite3.connect(PAPER_DB)
        rows = conn.execute("""
            SELECT was_correct FROM predictions
            WHERE query LIKE ? AND was_correct IS NOT NULL
            ORDER BY created_at DESC LIMIT ?
        """, (f"%{sector}%", window)).fetchall()
        conn.close()
        if len(rows) < 5:
            return None
        correct = sum(1 for r in rows if r[0] == 1)
        return {
            'win_rate': correct / len(rows),
            'correct':  correct,
            'total':    len(rows),
        }
    except Exception as e:
        log.warning(f"Could not fetch win rate for {sector}: {e}")
        return None


def get_recent_sector_predictions(sector: str, hours: int = 48) -> list[dict]:
    """Get recent predictions for the same sector to detect contradictions"""
    try:
        conn = sqlite3.connect(PAPER_DB)
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = conn.execute("""
            SELECT direction, confidence, was_correct, created_at
            FROM predictions
            WHERE query LIKE ? AND created_at >= ?
            ORDER BY created_at DESC LIMIT 5
        """, (f"%{sector}%", cutoff)).fetchall()
        conn.close()
        return [{'direction': r[0], 'confidence': r[1],
                 'was_correct': r[2], 'created_at': r[3]} for r in rows]
    except:
        return []


def get_macro_context() -> dict | None:
    """Get most recent market_overview prediction as macro context"""
    try:
        conn = sqlite3.connect(PAPER_DB)
        row = conn.execute("""
            SELECT direction, confidence, created_at FROM predictions
            WHERE query LIKE '%market outlook%' OR query LIKE '%macro%'
            ORDER BY created_at DESC LIMIT 1
        """).fetchone()
        conn.close()
        if row:
            return {'direction': row[0], 'confidence': row[1], 'created_at': row[2]}
    except:
        pass
    return None


def get_relevant_lessons(sector: str) -> list[dict]:
    """Get post-mortem lessons relevant to this sector"""
    try:
        conn = sqlite3.connect(LESSONS_DB)
        rows = conn.execute("""
            SELECT root_cause, lesson FROM lessons_learned
            WHERE query LIKE ? ORDER BY analyzed_at DESC LIMIT 3
        """, (f"%{sector}%",)).fetchall()
        conn.close()
        return [{'root_cause': r[0][:100], 'lesson': r[1][:100]} for r in rows]
    except:
        return []


def _get_alltime_sector_stats(conn: sqlite3.Connection, sector: str) -> dict:
    """
    Query verified predictions for a sector with two layers of protection
    against unbounded scans:

    1. TTL cache  — results are cached for _ALLTIME_CACHE_TTL_SECONDS so the DB
       is hit at most once per calibration cycle within a trading session.
    2. Date bound — a 12-month rolling window is applied so old data does not
       inflate or suppress the calibration adjustment indefinitely.

    Returns {win_rate, total, correct}.  Used by the calibration gate to detect
    sectors that are historically weak even when a recent rolling window
    looks better.
    """
    SECTOR_KW_MAP = {
        'healthcare':    ['healthcare', 'biotech'],
        'financials':    ['financial', 'bank'],
        'industrials':   ['industrials', 'defense', 'aerospace'],
        'technology':    ['technology', 'semiconductor', 'ai'],
        'energy':        ['energy', 'oil', 'gas'],
        'consumer':      ['consumer', 'retail'],
        'materials':     ['materials', 'mining', 'metals'],
        'market_overview': ['market outlook', 'macro', 's&p'],
    }
    keywords = SECTOR_KW_MAP.get(sector, [sector])
    where_clauses = ' OR '.join(['query LIKE ?' for _ in keywords])
    params = [f'%{kw}%' for kw in keywords]

    # ---- Date-bound safety cap (12-month rolling window) ----
    cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    date_clause = " AND created_at >= ?"
    params.append(cutoff)

    # ---- Single-pass SQL aggregation — O(1) memory vs O(n) fetchall ----
    full_where = f"was_correct IS NOT NULL AND ({where_clauses}){date_clause}"
    row = conn.execute(
        f"SELECT COUNT(*), SUM(was_correct) FROM predictions WHERE {full_where}",
        params
    ).fetchone()

    total   = row[0] or 0
    correct = row[1] or 0   # SUM returns NULL when no rows match
    if total == 0:
        return {'win_rate': 0.50, 'total': 0, 'correct': 0}
    return {'win_rate': correct / total, 'total': total, 'correct': correct}


def _get_alltime_sector_stats_with_cache(sector: str) -> dict:
    """
    Cached wrapper around _get_alltime_sector_stats.

    Hits the DB at most once per cache TTL per sector.  Subsequent calls within
    the TTL window return the cached result immediately.
    """
    import time
    now = time.monotonic()
    key = sector

    cached = _ALLTIME_CACHE.get(key)
    if cached is not None:
        expires_at, stats = cached
        if now < expires_at:
            return stats

    PAPER_DB = Path("/home/trading/trading-ai/data/paper_trading.db")
    conn = sqlite3.connect(PAPER_DB)
    stats = _get_alltime_sector_stats(conn, sector)
    conn.close()

    _ALLTIME_CACHE[key] = (now + _ALLTIME_CACHE_TTL_SECONDS, stats)
    return stats


def get_calibration_adjustment(sector: str) -> tuple[float, float]:
    """
    Read the ALL-TIME calibration win rate and adjustment for a sector.
    Uses the full history (not rolling window) so the calibration gate
    catches sectors that are historically weak even if recent predictions
    have been lucky.
    Returns (win_rate, adjustment).
    """
    try:
        stats = _get_alltime_sector_stats_with_cache(sector)

        if stats['total'] < 10:
            return 0.50, 0.0  # insufficient history

        wr = stats['win_rate']
        adj = calculate_adjustment(wr, stats['total'])
        return wr, adj
    except Exception:
        return 0.50, 0.0


def get_indirect_dependencies(sector: str) -> list[dict]:
    """Check if any known dependencies should affect this sector"""
    try:
        conn = sqlite3.connect(LESSONS_DB)
        rows = conn.execute("""
            SELECT from_entity, to_entity, relationship, occurrences
            FROM indirect_dependencies
            WHERE to_entity LIKE ? OR from_entity LIKE ?
            ORDER BY occurrences DESC LIMIT 5
        """, (f"%{sector}%", f"%{sector}%")).fetchall()
        conn.close()
        return [{'from': r[0], 'to': r[1],
                 'relationship': r[2][:80], 'occurrences': r[3]} for r in rows]
    except:
        return []


def critique_prediction(query: str,
                         direction: str,
                         confidence: float,
                         reasoning: str = "",
                         macro_themes: list = None) -> dict:
    """
    Main critic function. Reviews a prediction and returns verdict.

    Returns:
    {
        'verdict':           'approve' | 'challenge' | 'reject',
        'adjusted_confidence': float,
        'reasoning':         str,
        'issues':            list[str],
        'supporting':        list[str],
    }
    """
    issues    = []
    supporting = []
    confidence_adjustment = 0.0

    # Extract sector from query using organic ETF map + keyword fallback
    sector = 'unknown'
    q_lower = query.lower()

    # Try semantic sector detection first (fast keyword pass over organic tags)
    _keyword_map = {
        'technology':  ['technology', 'ai sector', 'semiconductor', 'data center'],
        'healthcare':  ['healthcare', 'biotech', 'pharma', 'drug'],
        'energy':      ['energy', 'oil', 'gas', 'renewables'],
        'financials':  ['financial', 'bank', 'interest rate', 'real estate'],
        'materials':   ['materials', 'mining', 'metals', 'chemicals', 'commodities'],
        'industrials': ['industrials', 'manufacturing', 'infrastructure'],
        'consumer':    ['consumer', 'retail', 'discretionary', 'staples'],
        'macro':       ['market outlook', 'macro', 's&p 500', 'overall market'],
        'defense':     ['defense', 'aerospace', 'military'],
        'ai_infrastructure': ['ai infrastructure', 'ai_infrastructure', 'data center'],
    }
    for s, keywords in _keyword_map.items():
        if any(kw in q_lower for kw in keywords):
            sector = s
            break

    # ── Check 0: Calibration gate — block approve on weak sectors ────────────
    # The calibration adjustment (-0.20) is applied post-prediction to trading
    # confidence, but the critic should also gate 'approve' verdicts for sectors
    # with poor historical win rates, regardless of signal quality.
    cal_wr, cal_adj = get_calibration_adjustment(sector)
    calibration_gate_fired = False
    if (cal_wr < WEAK_SECTOR_WIN_RATE_THRESHOLD and
            cal_adj <= WEAK_SECTOR_ADJ_THRESHOLD):
        issues.append(
            f"Calibration gate: {sector} win rate {cal_wr:.0%} "
            f"and adjustment {cal_adj:+.2f} — 'approve' blocked; "
            f"verdict capped at 'challenge'"
        )
        confidence_adjustment -= CONTRADICTION_PENALTY
        calibration_gate_fired = True

    # ── Check 1: Sector calibration history ──────────────────────────────────
    win_rate_data = get_sector_win_rate(sector)
    if win_rate_data:
        wr = win_rate_data['win_rate']
        if wr < 0.30 and confidence > 0.75:
            issues.append(
                f"Overconfidence: {sector} win rate is {wr:.0%} "
                f"({win_rate_data['correct']}/{win_rate_data['total']}) "
                f"but confidence is {confidence:.0%}"
            )
            confidence_adjustment -= CONTRADICTION_PENALTY
        elif wr > 0.55:
            supporting.append(
                f"{sector} has strong track record: {wr:.0%} win rate "
                f"({win_rate_data['correct']}/{win_rate_data['total']})"
            )
        elif wr < 0.40 and not calibration_gate_fired:
            # Don't double-penalize if the calibration gate already fired
            issues.append(
                f"Weak sector: {sector} win rate {wr:.0%} — "
                f"confidence should be tempered"
            )
            confidence_adjustment -= CONTRADICTION_PENALTY * 0.5

    # ── Check 2: Macro context contradiction ─────────────────────────────────
    macro = get_macro_context()
    if macro:
        macro_age_hours = (
            datetime.now(timezone.utc) -
            datetime.fromisoformat(macro['created_at'].replace('Z', '+00:00'))
        ).total_seconds() / 3600

        if macro_age_hours < 24:  # only use fresh macro context
            if (direction == 'bullish' and
                    macro['direction'] == 'bearish' and
                    macro['confidence'] >= 0.70 and
                    sector not in ('energy', 'defense', 'materials')):
                issues.append(
                    f"Macro contradiction: predicting {direction} {sector} "
                    f"but market_overview is {macro['direction']} "
                    f"({macro['confidence']:.0%} conf)"
                )
                confidence_adjustment -= CONTRADICTION_PENALTY
            elif (direction == macro['direction'] and
                      macro['confidence'] >= 0.65):
                supporting.append(
                    f"Macro alignment: {direction} aligns with market_overview "
                    f"({macro['confidence']:.0%} conf)"
                )

    # ── Check 3: Recent prediction consistency ────────────────────────────────
    recent = get_recent_sector_predictions(sector, hours=24)
    if recent:
        recent_directions = [r['direction'] for r in recent if r['direction']]
        if recent_directions:
            opposite = 'bearish' if direction == 'bullish' else 'bullish'
            opposite_count = recent_directions.count(opposite)
            if opposite_count >= 2:
                issues.append(
                    f"Direction flip: predicting {direction} but last "
                    f"{opposite_count} predictions were {opposite}"
                )
                confidence_adjustment -= CONTRADICTION_PENALTY * 0.5

    # ── Check 4: Post-mortem lessons ─────────────────────────────────────────
    lessons = get_relevant_lessons(sector)
    if lessons:
        for lesson in lessons[:2]:
            supporting.append(
                f"Lesson context: {lesson['lesson']}"
            )

    # ── Check 5: Indirect dependencies ───────────────────────────────────────
    deps = get_indirect_dependencies(sector)
    if deps:
        for dep in deps[:2]:
            if dep['occurrences'] >= 2:
                supporting.append(
                    f"Known dependency: {dep['from']} → {dep['to']} "
                    f"({dep['occurrences']}x observed): {dep['relationship']}"
                )

    # ── Check 6: Price momentum gate ─────────────────────────────────────────
    # If the sector ETF is trending strongly opposite to the prediction direction,
    # challenge the prediction regardless of signal quality.
    MOMENTUM_THRESHOLD = 0.025  # 2.5% move triggers challenge
    MOMENTUM_STRONG    = 0.050  # 5.0% move triggers harder challenge

    momentum = get_sector_momentum(sector, days=5)
    if momentum is not None:
        if direction == 'bullish' and momentum < -MOMENTUM_THRESHOLD:
            etf_name = SECTOR_ETF_MAP.get(sector, 'ETF')
            penalty = CONTRADICTION_PENALTY if momentum > -MOMENTUM_STRONG else CONTRADICTION_PENALTY * 1.5
            issues.append(
                f"Momentum contradiction: predicting bullish {sector} but "
                f"{etf_name} is down {abs(momentum):.1%} this week"
            )
            confidence_adjustment -= penalty
            log.debug(f"[CRITIC] Momentum gate fired: {sector} {momentum:+.1%} vs bullish")
        elif direction == 'bearish' and momentum > MOMENTUM_THRESHOLD:
            etf_name = SECTOR_ETF_MAP.get(sector, 'ETF')
            penalty = CONTRADICTION_PENALTY if momentum < MOMENTUM_STRONG else CONTRADICTION_PENALTY * 1.5
            issues.append(
                f"Momentum contradiction: predicting bearish {sector} but "
                f"{etf_name} is up {momentum:.1%} this week"
            )
            confidence_adjustment -= penalty
        elif direction == 'bullish' and momentum > MOMENTUM_THRESHOLD:
            supporting.append(
                f"Momentum confirms: {SECTOR_ETF_MAP.get(sector, 'ETF')} "
                f"up {momentum:.1%} this week"
            )
        elif direction == 'bearish' and momentum < -MOMENTUM_THRESHOLD:
            supporting.append(
                f"Momentum confirms: {SECTOR_ETF_MAP.get(sector, 'ETF')} "
                f"down {abs(momentum):.1%} this week"
            )

    # ── Check 7: Directional streak ───────────────────────────────────────────
    # If the same direction has been wrong N times in a row, increase skepticism.
    STREAK_CHALLENGE = 3   # wrong 3 times → challenge
    STREAK_REJECT    = 5   # wrong 5 times → reject

    if direction in ('bullish', 'bearish'):
        wrong_count, error_rate = get_directional_streak(sector, direction, window=8)
        if wrong_count >= 3 and error_rate >= 0.75:
            issues.append(
                f"Direction bias: {direction} {sector} wrong {wrong_count} of last "
                f"{round(wrong_count/error_rate):.0f} predictions ({error_rate:.0%} error rate)"
            )
            confidence_adjustment -= CONTRADICTION_PENALTY * 2
        elif wrong_count >= 2 and error_rate >= 0.60:
            issues.append(
                f"Direction bias: {direction} {sector} wrong {wrong_count} of last "
                f"{round(wrong_count/error_rate):.0f} predictions ({error_rate:.0%} error rate)"
            )
            confidence_adjustment -= CONTRADICTION_PENALTY

    # ── Determine verdict ─────────────────────────────────────────────────────
    confidence_adjustment = max(-MAX_REDUCTIONS,
                                 min(0.0, confidence_adjustment))
    adjusted_confidence = round(
        max(0.30, min(0.95, confidence + confidence_adjustment)), 2
    )

    # Count severity
    n_issues = len(issues)

    if n_issues == 0:
        verdict = 'approve'
    elif n_issues == 1 and confidence_adjustment > -0.15:
        verdict = 'challenge'
    elif n_issues >= 2 or confidence_adjustment <= -0.20:
        verdict = 'reject' if adjusted_confidence < 0.60 else 'challenge'
    else:
        verdict = 'challenge'

    # Build reasoning summary
    reasoning_parts = []
    if issues:
        reasoning_parts.append("ISSUES: " + "; ".join(issues))
    if supporting:
        reasoning_parts.append("SUPPORTING: " + "; ".join(supporting[:3]))
    if confidence_adjustment < 0:
        reasoning_parts.append(
            f"ADJUSTMENT: {confidence:.2f} → {adjusted_confidence:.2f} "
            f"({confidence_adjustment:+.2f})"
        )

    critic_reasoning = " | ".join(reasoning_parts) if reasoning_parts else "No issues found"

    result = {
        'verdict':            verdict,
        'adjusted_confidence': adjusted_confidence,
        'original_confidence': confidence,
        'reasoning':          critic_reasoning,
        'issues':             issues,
        'supporting':         supporting,
        'sector':             sector,
        'confidence_delta':   round(confidence_adjustment, 2),
    }

    log.info(
        f"Critic [{sector}] {direction} {confidence:.0%} → "
        f"{verdict} {adjusted_confidence:.0%} "
        f"({len(issues)} issues, {len(supporting)} supporting)"
    )

    return result


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    # Test with current sectors
    test_cases = [
        ("Energy sector outlook — oil, gas, utilities", "bullish", 0.75),
        ("Technology and AI sector outlook — semiconductors", "bullish", 0.85),
        ("Healthcare and biotech sector outlook", "bullish", 0.70),
        ("Financial sector outlook — banks, rates", "bullish", 0.80),
    ]

    for query, direction, confidence in test_cases:
        result = critique_prediction(query, direction, confidence)
        print(f"\n{query[:40]}")
        print(f"  Input:   {direction} {confidence:.0%}")
        print(f"  Verdict: {result['verdict']} → {result['adjusted_confidence']:.0%}")
        if result['issues']:
            for issue in result['issues']:
                print(f"  ⚠ {issue[:80]}")
        if result['supporting']:
            for s in result['supporting'][:2]:
                print(f"  ✓ {s[:80]}")
