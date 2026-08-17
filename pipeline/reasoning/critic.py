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

log = logging.getLogger(__name__)

PAPER_DB   = Path("/home/trading/trading-ai/data/paper_trading.db")
LESSONS_DB = Path("/home/trading/trading-ai/data/lessons.db")
RULES_DB   = Path("/home/trading/trading-ai/data/rules.db")

# Thresholds
MIN_WIN_RATE_FOR_HIGH_CONF = 0.45  # sector needs >45% win rate to support >80% confidence
CONTRADICTION_PENALTY      = 0.10  # reduce confidence when contradictions found
MAX_REDUCTIONS             = 0.25  # never reduce more than 25% total


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

    # Extract sector from query
    sector = 'unknown'
    sector_map = {
        'technology':  ['technology', 'ai', 'semiconductor'],
        'healthcare':  ['healthcare', 'biotech'],
        'energy':      ['energy', 'oil', 'gas'],
        'financials':  ['financial', 'bank'],
        'materials':   ['materials', 'mining', 'metals'],
        'industrials': ['industrials', 'defense', 'aerospace'],
        'consumer':    ['consumer', 'retail'],
        'macro':       ['market outlook', 'macro', 's&p'],
    }
    q_lower = query.lower()
    for s, keywords in sector_map.items():
        if any(kw in q_lower for kw in keywords):
            sector = s
            break

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
        elif wr < 0.40:
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
