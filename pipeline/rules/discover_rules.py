#!/usr/bin/env python3
"""
Rule discovery agent
Analyzes recent signals for emerging co-occurrence patterns
Proposes new inference rules when patterns exceed threshold
Runs daily via cron
"""

import json
import logging
import os
import requests
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

OLLAMA_HOST   = os.getenv("OLLAMA_HOST", "http://172.29.10.225:11434")
SIGNALS_DIR   = Path(os.getenv("TIMESERIES_DIR", "/mnt/qnap/timeseries/signals"))
LOOKBACK_DAYS = 7

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/mnt/qnap/timeseries/logs/rule_discovery.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# Import rule engine
import sys
sys.path.insert(0, str(Path(__file__).parent))
from rule_engine import init_db, propose_rule, get_active_rules


DISCOVERY_PROMPT = """You are a financial news pattern analyst.
Given a list of recent news signal summaries, identify any EMERGING THEMES
that suggest new sector inference rules should be added.

Look for:
- New policy changes (tariffs, regulations, sanctions) with sector impacts
- Emerging technologies affecting multiple sectors
- New geopolitical developments with supply chain implications
- Recurring economic patterns linking topics to sectors

For each discovered pattern, output a JSON array of rule proposals:
[
  {
    "trigger": "short phrase describing the trigger (3-6 words)",
    "sectors": ["sector1", "sector2"],
    "evidence": "1 sentence explaining why this rule makes sense",
    "confidence": 0.0-1.0
  }
]

Only propose rules that are NEW and not already obvious.
Only output the JSON array, nothing else.
If no new patterns found, output: []"""


def load_recent_signals(days: int = 7) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    signals = []
    for f in sorted(SIGNALS_DIR.glob("scored_*.jsonl"), reverse=True)[:50]:
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        signals.append(json.loads(line))
                    except:
                        continue
    return signals


def find_cooccurrences(signals: list[dict]) -> dict:
    """Find topics and sectors that appear together frequently"""
    cooccur = defaultdict(Counter)

    for s in signals:
        sectors = s.get("sectors", [])
        title_words = s.get("title", "").lower().split()
        summary = s.get("summary", "").lower()

        # Look for keyword-sector co-occurrences
        keywords = []
        for word in title_words:
            if len(word) > 4:  # Skip short words
                keywords.append(word)

        for kw in keywords:
            for sector in sectors:
                cooccur[kw][sector] += 1

    # Return pairs that co-occur 3+ times
    strong = {}
    for kw, sector_counts in cooccur.items():
        if sum(sector_counts.values()) >= 3:
            strong[kw] = dict(sector_counts.most_common(5))

    return strong


def discover_rules_via_llm(signals: list[dict],
                            existing_rules: list[dict]) -> list[dict]:
    """Use LLM to identify emerging patterns from recent signals"""

    # Build context from recent signals
    signal_summaries = []
    for s in signals[-50:]:  # Last 50 signals
        if s.get("summary"):
            signal_summaries.append(
                f"- [{s['sentiment']}] {s['title'][:60]} | "
                f"sectors: {s.get('sectors',[])} | {s['summary'][:80]}"
            )

    existing_triggers = [r["trigger"] for r in existing_rules]
    context = "\n".join(signal_summaries)

    user_msg = f"""Existing rules already cover: {existing_triggers[:10]}

Recent signals from the last {LOOKBACK_DAYS} days:
{context}

Identify any NEW emerging patterns not covered by existing rules."""

    try:
        resp = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model": "qwen3:30b",
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 2048},
                "messages": [
                    {"role": "system", "content": DISCOVERY_PROMPT},
                    {"role": "user",   "content": user_msg}
                ]
            },
            timeout=300
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"].strip()

        if "<think>" in content:
            content = content[content.rfind("</think>") + 8:].strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        proposals = json.loads(content)
        log.info(f"LLM proposed {len(proposals)} new rules")
        return proposals

    except Exception as e:
        log.error(f"LLM discovery failed: {e}")
        return []


def run_discovery() -> None:
    log.info("─── Rule discovery starting ───")

    conn = init_db()
    existing_rules = get_active_rules(conn)
    signals = load_recent_signals(LOOKBACK_DAYS)

    log.info(f"Loaded {len(signals)} signals from last {LOOKBACK_DAYS} days")
    log.info(f"Existing rules: {len(existing_rules)}")

    # Statistical co-occurrence analysis
    cooccur = find_cooccurrences(signals)
    log.info(f"Found {len(cooccur)} keyword co-occurrences")

    # LLM-based pattern discovery
    proposals = discover_rules_via_llm(signals, existing_rules)

    # Record proposals
    for proposal in proposals:
        trigger  = proposal.get("trigger", "").strip().lower()
        sectors  = proposal.get("sectors", [])
        evidence = proposal.get("evidence", "")

        if trigger and sectors:
            propose_rule(conn, trigger, sectors, evidence)

    # Summary
    pending = conn.execute(
        "SELECT COUNT(*) FROM rule_proposals WHERE status = 'pending'"
    ).fetchone()[0]
    promoted = conn.execute(
        "SELECT COUNT(*) FROM inference_rules WHERE source = 'discovered'"
    ).fetchone()[0]

    log.info(f"─── Discovery complete ───")
    log.info(f"    Pending proposals : {pending}")
    log.info(f"    Promoted to active: {promoted}")
    conn.close()


if __name__ == "__main__":
    run_discovery()
