#!/usr/bin/env python3
"""
Toolset frequency detector and skill auto-generator

Monitors mcp_call_log table in trading_pipeline.db to identify toolsets
whose call count exceeds a configurable threshold across distinct tasks.
When a threshold is breached, generates a Hermes skill definition that
encapsulates the toolset so future tasks can reference it by name.

Usage:
    python toolset_detector.py [--db PATH] [--threshold N] [--min-tasks K] [--dry-run]

Runs periodically via cron or as part of the nightly pipeline cycle.
"""

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Default paths — override with --db or TOOLSET_DB env var
DEFAULT_DB = Path("/home/trading/trading-ai/data/trading_pipeline.db")

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurable thresholds
# ---------------------------------------------------------------------------

# Default: trigger when a toolset has >=5 calls from >=3 distinct tasks
DEFAULT_CALL_THRESHOLD    = 5
DEFAULT_TASK_THRESHOLD    = 3
# Only consider calls within this many hours of now
DEFAULT_LOOKBACK_HOURS    = 72

# Track which toolsets already have generated skills to avoid duplicates
GENERATED_SKILLS_TABLE = "toolset_skills_generated"


def init_tracking(conn: sqlite3.Connection) -> None:
    """Create the tracking table idempotently."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS toolset_skills_generated (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            toolset         TEXT NOT NULL UNIQUE,
            skill_name      TEXT NOT NULL,
            triggered_at    TEXT NOT NULL,
            call_count      INTEGER,
            task_count      INTEGER,
            tools           TEXT,
            status          TEXT DEFAULT 'active'
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tsg_toolset ON toolset_skills_generated(toolset)"
    )
    conn.commit()


def get_existing_skills(conn: sqlite3.Connection) -> set[str]:
    """Return set of toolsets that already have generated skills (active)."""
    rows = conn.execute(
        "SELECT toolset FROM toolset_skills_generated WHERE status = 'active'"
    ).fetchall()
    return {row[0] for row in rows}


def query_threshold_breaches(
    conn: sqlite3.Connection,
    call_threshold: int = DEFAULT_CALL_THRESHOLD,
    task_threshold: int = DEFAULT_TASK_THRESHOLD,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
) -> list[dict]:
    """
    Query mcp_call_log for toolsets exceeding thresholds.

    Returns list of dicts with toolset, call_count, task_count, tools,
    avg_duration_ms, and sample tasks.
    """
    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    
    query = """
        SELECT
            toolset,
            COUNT(*) AS call_count,
            COUNT(DISTINCT task_id) AS task_count,
            GROUP_CONCAT(DISTINCT tool_name) AS tools,
            ROUND(AVG(duration_ms), 1) AS avg_duration_ms,
            MIN(timestamp) AS first_call,
            MAX(timestamp) AS last_call
        FROM mcp_call_log
        WHERE timestamp >= ?
          AND toolset IS NOT NULL
          AND toolset != ''
        GROUP BY toolset
        HAVING COUNT(*) >= ? AND COUNT(DISTINCT task_id) >= ?
        ORDER BY call_count DESC, task_count DESC
    """
    
    rows = conn.execute(query, (
        cutoff_dt.isoformat(),
        call_threshold,
        task_threshold
    )).fetchall()
    
    results = []
    for row in rows:
        results.append({
            "toolset": row[0],
            "call_count": row[1],
            "task_count": row[2],
            "tools": row[3].split(",") if row[3] else [],
            "avg_duration_ms": row[4],
            "first_call": row[5],
            "last_call": row[6],
        })
    
    return results


def generate_skill_definition(toolset_breach: dict) -> dict:
    """
    Generate a Hermes skill definition for a frequently-used toolset.

    Returns a dict with skill_name, category, and SKILL.md content.
    """
    toolset = toolset_breach["toolset"]
    tools = toolset_breach["tools"]
    call_count = toolset_breach["call_count"]
    task_count = toolset_breach["task_count"]
    avg_duration = toolset_breach["avg_duration_ms"]

    # Derive a clean skill name from toolset
    skill_name = toolset.replace("_", "-").lower()
    # Ensure it follows Hermes skill naming convention
    if not skill_name.replace("-", "").isalnum():
        skill_name = f"toolset-{toolset}"

    category = _infer_category(tools)

    # Build tool list for SKILL.md
    tool_list_md = "\n".join(f"- `{t}`" for t in tools)

    # Build usage summary
    usage_summary = (
        f"This skill was auto-generated because the `{toolset}` toolset "
        f"was called {call_count} times across {task_count} distinct tasks "
        f"within the lookback window."
    )

    skill_md = f"""---
name: {skill_name}
description: "Auto-generated skill for {toolset} toolset — encapsulates {len(tools)} MCP tools."
version: "1.0.0"
author: Hermes Agent (auto-generated)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [mcp, toolset, auto-generated, trading-pipeline]
    provenance:
      created_by: "agent"
      trigger: "toolset_frequency_threshold"
      toolset: "{toolset}"
      generated_at: "{datetime.now(timezone.utc).isoformat()}"
      triggering_stats:
        call_count: {call_count}
        task_count: {task_count}
        avg_duration_ms: {avg_duration}
        tools: {json.dumps(tools)}
---

# {toolset.replace("_", " ").title()} Toolset

{usage_summary}

## Tools

This skill encapsulates the following MCP tools:

{tool_list_md}

## When to Use

Use when <trigger> — the {toolset} toolset provides access to trading pipeline data including recent signals, predictions, portfolio state, and rule analysis.

## Quick Reference

```python
# Access tools via MCP
from pipeline.tools.toolset_detector import query_threshold_breaches

# Or call individual tools through the FastMCP server
mcp_client.call_tool("get_daily_predictions")
mcp_client.call_tool("get_sector_calibration")
```

## Notes

- This skill was automatically generated by the toolset frequency detector.
- The Curator will manage its lifecycle (usage tracking, staleness, archival).
- Pin this skill (`hermes curator pin {skill_name}`) to prevent auto-archival.
"""

    return {
        "skill_name": skill_name,
        "category": category,
        "skill_md": skill_md,
        "toolset": toolset,
        "tools": tools,
    }


def _infer_category(tools: list[str]) -> str:
    """Infer a category subdirectory from the tool names."""
    tool_lower = " ".join(tools)
    
    if any(w in tool_lower for w in ["signal", "score", "sentiment", "qdrant"]):
        return "sentiment"
    if any(w in tool_lower for w in ["predict", "calibration", "critic"]):
        return "reasoning"
    if any(w in tool_lower for w in ["portfolio", "trade", "cash", "position"]):
        return "portfolio"
    if any(w in tool_lower for w in ["rule", "inference", "dependency"]):
        return "rules"
    if any(w in tool_lower for w in ["report", "history", "health", "accuracy"]):
        return "monitoring"
    
    return "mcp-tools"


def register_generated_skill(
    conn: sqlite3.Connection,
    toolset: str,
    skill_name: str,
    call_count: int,
    task_count: int,
    tools: list[str],
) -> None:
    """Record that a skill was generated for this toolset."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO toolset_skills_generated
            (toolset, skill_name, triggered_at, call_count, task_count, tools, status)
        VALUES (?, ?, ?, ?, ?, ?, 'active')
    """, (toolset, skill_name, now, call_count, task_count, json.dumps(tools)))
    conn.commit()


def run_detection(
    db_path: Path = DEFAULT_DB,
    call_threshold: int = DEFAULT_CALL_THRESHOLD,
    task_threshold: int = DEFAULT_TASK_THRESHOLD,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    dry_run: bool = False,
) -> list[dict]:
    """
    Main detection loop.

    Returns list of generated skill definitions (even in dry-run mode).
    In dry-run, skills are reported but not persisted.
    """
    if not db_path.exists():
        log.warning(f"DB not found: {db_path} — instrumentation may not be deployed yet")
        return []

    conn = sqlite3.connect(str(db_path))
    try:
        init_tracking(conn)
        existing = get_existing_skills(conn)
    except Exception as e:
        log.error(f"Failed to init tracking: {e}")
        conn.close()
        return []

    # Query for threshold breaches
    breaches = query_threshold_breaches(
        conn,
        call_threshold=call_threshold,
        task_threshold=task_threshold,
        lookback_hours=lookback_hours,
    )
    
    if not breaches:
        log.info(f"No threshold breaches found (calls>={call_threshold}, tasks>={task_threshold}, {lookback_hours}h window)")
        conn.close()
        return []

    log.info(f"Found {len(breaches)} threshold breach(es)")
    
    generated = []
    for breach in breaches:
        toolset = breach["toolset"]
        
        # Skip if we already generated a skill for this toolset
        if toolset in existing:
            log.info(f"Skipping {toolset} — skill already generated")
            continue

        # Generate skill definition
        skill_def = generate_skill_definition(breach)
        log.info(
            f"Toolset '{toolset}' breached threshold: "
            f"{breach['call_count']} calls across {breach['task_count']} tasks "
            f"-> skill '{skill_def['skill_name']}'"
        )
        
        if not dry_run:
            try:
                # Persist the skill definition
                skill_dir = Path.home() / ".hermes" / "skills" / skill_def["category"] / skill_def["skill_name"]
                skill_dir.mkdir(parents=True, exist_ok=True)
                skill_file = skill_dir / "SKILL.md"
                skill_file.write_text(skill_def["skill_md"])
                log.info(f"Skill written to {skill_file}")
                
                # Register in tracking table
                register_generated_skill(
                    conn,
                    toolset=toolset,
                    skill_name=skill_def["skill_name"],
                    call_count=breach["call_count"],
                    task_count=breach["task_count"],
                    tools=breach["tools"],
                )
            except Exception as e:
                log.error(f"Failed to generate skill for {toolset}: {e}")
                continue
        
        generated.append(skill_def)
    
    conn.close()
    return generated


def main():
    parser = argparse.ArgumentParser(
        description="Detect frequent MCP toolsets and auto-generate skills"
    )
    parser.add_argument("--db", type=str, default=None,
                        help="Path to trading_pipeline.db (default: env or default)")
    parser.add_argument("--threshold", type=int, default=DEFAULT_CALL_THRESHOLD,
                        help=f"Min call count to trigger (default: {DEFAULT_CALL_THRESHOLD})")
    parser.add_argument("--min-tasks", type=int, default=DEFAULT_TASK_THRESHOLD,
                        help=f"Min distinct tasks to trigger (default: {DEFAULT_TASK_THRESHOLD})")
    parser.add_argument("--lookback-hours", type=int, default=DEFAULT_LOOKBACK_HOURS,
                        help=f"Lookback window in hours (default: {DEFAULT_LOOKBACK_HOURS})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report breaches without generating skills")
    
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler("/home/trading/trading-ai/logs/toolset_detector.log"),
            logging.StreamHandler(),
        ],
    )
    
    db_path = Path(args.db) if args.db else DEFAULT_DB
    
    log.info("=" * 60)
    log.info("Toolset frequency detector starting")
    log.info(f"  DB:          {db_path}")
    log.info(f"  Call thresh: {args.threshold}")
    log.info(f"  Task thresh: {args.min_tasks}")
    log.info(f"  Lookback:    {args.lookback_hours}h")
    log.info(f"  Dry run:     {args.dry_run}")
    log.info("=" * 60)
    
    results = run_detection(
        db_path=db_path,
        call_threshold=args.threshold,
        task_threshold=args.min_tasks,
        lookback_hours=args.lookback_hours,
        dry_run=args.dry_run,
    )
    
    if results:
        log.info(f"Generated {len(results)} skill(s):")
        for r in results:
            log.info(f"  - {r['skill_name']} ({r['toolset']})")
    else:
        log.info("No skills generated — no thresholds breached or all already covered")
    
    return results


if __name__ == "__main__":
    main()
