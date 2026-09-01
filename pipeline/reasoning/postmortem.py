"""
Phase 2 Learning — Post-mortem analysis engine

When a high-confidence prediction is wrong, DeepSeek investigates why:
  - What signals were present at prediction time?
  - What signals appeared afterward that predicted the move?
  - What indirect signals were missed (supply chain, sector correlation)?
  - What new rule should be added?

Findings are saved to the lessons_learned DB and promoted to inference rules.
"""

import sqlite3
import logging
import json
import requests
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

log = logging.getLogger(__name__)

PAPER_DB    = Path("/home/trading/trading-ai/data/paper_trading.db")
RULES_DB    = Path("/home/trading/trading-ai/data/rules.db")
LESSONS_DB  = Path("/home/trading/trading-ai/data/lessons.db")
SIGNALS_DIR = Path("/mnt/qnap/timeseries/signals")

SPARK_HOST  = os.getenv("SPARK_LLAMA_HOST",
              os.getenv("SPARK_LLAMA_HOST", "http://172.29.10.225:8083"))
MODEL       = "deepseek-r1"

# Only analyze predictions above this confidence threshold
MIN_CONFIDENCE_FOR_POSTMORTEM = 0.70

POSTMORTEM_PROMPT = """You are an expert financial analyst conducting a post-mortem on a failed prediction.

A trading AI made the following prediction:
  Query: {query}
  Direction: {direction}
  Confidence: {confidence:.0%}
  Timeframe: {timeframe}

The prediction was WRONG. Actual market movement was: {actual_direction}

Signals the AI had at prediction time:
{signals_at_prediction}

Your task:
1. Identify what the AI got wrong or missed
2. Find any indirect signals it should have weighted more heavily
   (e.g. supplier problems → manufacturer stock impact,
    bond yields rising → growth stock impact,
    geopolitical events → energy/defense sector impact)
3. Identify cross-sector dependencies it missed
4. Propose 1-3 specific inference rules that would help catch this in future

Respond in JSON format only:
{{
  "root_cause": "brief explanation of why the prediction was wrong",
  "missed_signals": ["signal type or pattern that was missed"],
  "indirect_dependencies": [
    {{"from": "source sector/ticker", "to": "affected sector/ticker", "relationship": "explanation"}}
  ],
  "proposed_rules": [
    {{
      "trigger": "short trigger phrase (3-6 words)",
      "sectors": ["affected", "sectors"],
      "confidence": 0.80,
      "rationale": "why this rule would help"
    }}
  ],
  "lesson": "one sentence summary of the key lesson learned"
}}"""


def init_lessons_db() -> sqlite3.Connection:
    """Initialize lessons learned database"""
    conn = sqlite3.connect(LESSONS_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lessons_learned (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_id   INTEGER,
            query           TEXT,
            direction       TEXT,
            confidence      REAL,
            actual_direction TEXT,
            root_cause      TEXT,
            missed_signals  TEXT,
            indirect_deps   TEXT,
            proposed_rules  TEXT,
            lesson          TEXT,
            analyzed_at     TEXT,
            rules_promoted  INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS indirect_dependencies (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            from_entity TEXT,
            to_entity   TEXT,
            relationship TEXT,
            confidence  REAL DEFAULT 0.70,
            occurrences INTEGER DEFAULT 1,
            first_seen  TEXT,
            last_seen   TEXT
        )
    """)
    conn.commit()
    return conn


def get_signals_at_prediction_time(created_at: str,
                                   window_hours: int = 24) -> list[dict]:
    """Fetch signals that were in the corpus when prediction was made"""
    cutoff_start = datetime.fromisoformat(
        created_at.replace('Z', '+00:00')
    ) - timedelta(hours=window_hours)
    cutoff_end   = datetime.fromisoformat(
        created_at.replace('Z', '+00:00')
    )

    signals = []
    try:
        # Filter files by date prefix to avoid scanning all 5000+ files
        cutoff_start_str = cutoff_start.strftime('%Y%m%d')
        cutoff_end_str   = cutoff_end.strftime('%Y%m%d')
        all_files = sorted(SIGNALS_DIR.glob('scored_*.jsonl'))
        # Keep files within date range (filename contains date)
        relevant_files = [
            f for f in all_files
            if cutoff_start_str <= f.name[7:15] <= cutoff_end_str
        ]
        if not relevant_files:
            # Fallback — try day before and after
            relevant_files = [
                f for f in all_files
                if (cutoff_start - timedelta(days=1)).strftime('%Y%m%d')
                   <= f.name[7:15]
                   <= (cutoff_end + timedelta(days=1)).strftime('%Y%m%d')
            ]
        log.info(f"Scanning {len(relevant_files)} signal files for window {cutoff_start_str} to {cutoff_end_str}")
        for f in relevant_files[:200]:
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        s = json.loads(line)
                        scored_at = s.get('scored_at', '')
                        if not scored_at:
                            continue
                        scored_dt = datetime.fromisoformat(
                            scored_at.replace('Z', '+00:00')
                        )
                        if cutoff_start <= scored_dt <= cutoff_end:
                            signals.append({
                                'title':      s.get('title', '')[:80],
                                'sentiment':  s.get('sentiment', ''),
                                'confidence': s.get('confidence', 0),
                                'sectors':    s.get('sectors', []),
                                'tickers':    s.get('tickers', []),
                                'source':     s.get('source', ''),
                            })
                    except:
                        continue
    except Exception as e:
        log.warning(f"Could not fetch signals: {e}")

    return signals[:30]  # limit to 30 most relevant


def call_deepseek(prompt: str) -> dict | None:
    """Call DeepSeek for post-mortem analysis"""
    try:
        resp = requests.post(
            f"{SPARK_HOST}/v1/chat/completions",
            json={
                "model":      MODEL,
                "stream":     False,
                "max_tokens": 2048,
                "temperature": 0.1,
                "messages": [
                    {"role": "user", "content": prompt}
                ]
            },
            timeout=600
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Strip thinking tags
        if "</think>" in content:
            content = content[content.rfind("</think>") + 8:].strip()

        # Parse JSON
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        return json.loads(content)

    except Exception as e:
        log.error(f"DeepSeek post-mortem call failed: {e}")
        return None


def promote_rules_from_lesson(lesson_data: dict,
                               rules_conn: sqlite3.Connection) -> int:
    """Add proposed rules from post-mortem to inference rules"""
    promoted = 0
    now = datetime.now(timezone.utc).isoformat()

    for rule in lesson_data.get('proposed_rules', []):
        trigger  = rule.get('trigger', '').strip().lower()
        sectors  = rule.get('sectors', [])
        conf     = rule.get('confidence', 0.75)
        rationale = rule.get('rationale', '')

        if not trigger or len(trigger) < 5:
            continue

        # Check if rule already exists
        existing = rules_conn.execute(
            "SELECT id FROM inference_rules WHERE trigger = ?",
            (trigger,)
        ).fetchone()

        if existing:
            log.info(f"Rule already exists: {trigger}")
            continue

        rules_conn.execute("""
            INSERT INTO inference_rules
            (trigger, sectors, confidence, source, created_at, updated_at)
            VALUES (?, ?, ?, 'postmortem', ?, ?)
        """, (trigger, json.dumps(sectors), conf, now, now))
        promoted += 1
        log.info(f"Promoted rule from post-mortem: '{trigger}' → {sectors}")

    rules_conn.commit()
    return promoted


def save_indirect_dependency(dep: dict,
                              lessons_conn: sqlite3.Connection) -> None:
    """Track indirect dependencies between sectors/tickers"""
    from_e = dep.get('from', '').lower()
    to_e   = dep.get('to', '').lower()
    rel    = dep.get('relationship', '')
    now    = datetime.now(timezone.utc).isoformat()

    if not from_e or not to_e:
        return

    existing = lessons_conn.execute("""
        SELECT id, occurrences FROM indirect_dependencies
        WHERE from_entity = ? AND to_entity = ?
    """, (from_e, to_e)).fetchone()

    if existing:
        lessons_conn.execute("""
            UPDATE indirect_dependencies
            SET occurrences = ?, last_seen = ?
            WHERE id = ?
        """, (existing[1] + 1, now, existing[0]))
    else:
        lessons_conn.execute("""
            INSERT INTO indirect_dependencies
            (from_entity, to_entity, relationship, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
        """, (from_e, to_e, rel, now, now))


def run_postmortem(prediction_id: int = None) -> int:
    """
    Run post-mortem on high-confidence wrong predictions.
    Returns number of predictions analyzed.
    """
    paper_conn   = sqlite3.connect(PAPER_DB)
    lessons_conn = init_lessons_db()
    rules_conn   = sqlite3.connect(RULES_DB)

    # Find high-confidence wrong predictions not yet analyzed
    already_analyzed = {
        row[0] for row in lessons_conn.execute(
            "SELECT prediction_id FROM lessons_learned"
        ).fetchall()
    }

    query = """
        SELECT id, query, direction, confidence, timeframe,
               actual_direction, created_at
        FROM predictions
        WHERE was_correct = 0
          AND confidence >= ?
    """
    params = [MIN_CONFIDENCE_FOR_POSTMORTEM]

    if prediction_id:
        query += " AND id = ?"
        params.append(prediction_id)

    candidates = paper_conn.execute(query, params).fetchall()
    paper_conn.close()

    to_analyze = [c for c in candidates if c[0] not in already_analyzed]

    if not to_analyze:
        log.info("No new high-confidence wrong predictions to analyze")
        return 0

    log.info(f"Post-mortem: analyzing {len(to_analyze)} predictions")
    analyzed = 0

    for pred in to_analyze:
        pred_id, query_text, direction, confidence, timeframe, \
            actual_dir, created_at = pred

        log.info(f"Analyzing prediction #{pred_id}: {direction} "
                 f"({confidence:.0%}) — actual: {actual_dir}")

        # Get signals from that time window
        signals = get_signals_at_prediction_time(created_at)
        if not signals:
            log.warning(f"No signals found for prediction #{pred_id}")
            continue

        signals_text = "\n".join([
            f"  [{s['sentiment']:7s}] {s['confidence']:.0%} "
            f"{s['title']} [{', '.join(s['tickers'][:3])}]"
            for s in signals[:20]
        ])

        prompt = POSTMORTEM_PROMPT.format(
            query=query_text,
            direction=direction,
            confidence=confidence,
            timeframe=timeframe,
            actual_direction=actual_dir or "opposite direction",
            signals_at_prediction=signals_text
        )

        log.info(f"Calling DeepSeek for post-mortem analysis...")
        result = call_deepseek(prompt)

        if not result:
            log.error(f"Post-mortem failed for prediction #{pred_id}")
            continue

        now = datetime.now(timezone.utc).isoformat()

        # Save lesson
        lessons_conn.execute("""
            INSERT INTO lessons_learned
            (prediction_id, query, direction, confidence, actual_direction,
             root_cause, missed_signals, indirect_deps, proposed_rules,
             lesson, analyzed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pred_id, query_text, direction, confidence, actual_dir,
            result.get('root_cause', ''),
            json.dumps(result.get('missed_signals', [])),
            json.dumps(result.get('indirect_dependencies', [])),
            json.dumps(result.get('proposed_rules', [])),
            result.get('lesson', ''),
            now
        ))
        lessons_conn.commit()

        # Save indirect dependencies
        for dep in result.get('indirect_dependencies', []):
            save_indirect_dependency(dep, lessons_conn)
        lessons_conn.commit()

        # Promote rules
        promoted = promote_rules_from_lesson(result, rules_conn)

        log.info(f"Post-mortem #{pred_id} complete:")
        log.info(f"  Root cause: {result.get('root_cause', 'N/A')[:80]}")
        log.info(f"  Lesson: {result.get('lesson', 'N/A')[:80]}")
        log.info(f"  Rules promoted: {promoted}")

        analyzed += 1

    lessons_conn.close()
    rules_conn.close()
    return analyzed


def get_lessons_report() -> str:
    """Print recent lessons learned"""
    try:
        conn = init_lessons_db()
        rows = conn.execute("""
            SELECT prediction_id, direction, confidence, actual_direction,
                   root_cause, lesson, analyzed_at
            FROM lessons_learned
            ORDER BY analyzed_at DESC LIMIT 10
        """).fetchall()
        conn.close()

        if not rows:
            return "No lessons learned yet"

        lines = [f"Lessons learned ({len(rows)} recent):"]
        for r in rows:
            lines.append(f"\n  Prediction #{r[0]} [{r[1]} {r[2]:.0%}] → actual: {r[3]}")
            lines.append(f"  Root cause: {r[4][:80]}")
            lines.append(f"  Lesson: {r[5][:80]}")
        return '\n'.join(lines)
    except Exception as e:
        return f"Lessons report error: {e}"


def get_dependency_map() -> str:
    """Print learned indirect dependencies"""
    try:
        conn = init_lessons_db()
        rows = conn.execute("""
            SELECT from_entity, to_entity, relationship, occurrences
            FROM indirect_dependencies
            ORDER BY occurrences DESC LIMIT 20
        """).fetchall()
        conn.close()

        if not rows:
            return "No dependencies learned yet"

        lines = ["Indirect dependency map:"]
        for r in rows:
            lines.append(f"  [{r[3]}x] {r[0]} → {r[1]}: {r[2][:60]}")
        return '\n'.join(lines)
    except Exception as e:
        return f"Dependency map error: {e}"


if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", type=int, help="Analyze specific prediction ID")
    parser.add_argument("--report", action="store_true", help="Show lessons report")
    parser.add_argument("--deps", action="store_true", help="Show dependency map")
    args = parser.parse_args()

    if args.report:
        print(get_lessons_report())
    elif args.deps:
        print(get_dependency_map())
    else:
        n = run_postmortem(prediction_id=args.id)
        print(f"\nAnalyzed {n} predictions")
        if n > 0:
            print(get_lessons_report())
