#!/usr/bin/env python3
"""
Rule discovery agent
Uses DeepSeek-R1-70B on Spark to analyze recent signals
and propose new inference rules.
Runs daily via cron.
"""

import json
import logging
import os
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

SPARK_LLAMA_HOST = os.getenv("SPARK_LLAMA_HOST", "http://172.29.10.225:8083")
DISCOVERY_MODEL   = os.getenv("REASONING_MODEL", "deepseek-r1")
SIGNALS_DIR       = Path(os.getenv("TIMESERIES_DIR", "/mnt/qnap/timeseries/signals"))
LOOKBACK_DAYS     = 7

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/mnt/qnap/timeseries/logs/rule_discovery.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

import sys
sys.path.insert(0, str(Path(__file__).parent))
from rule_engine import init_db, propose_rule, get_active_rules

DISCOVERY_PROMPT = """You are a financial market analyst identifying sector inference rules.

Given recent financial news signals, identify NEW cross-sector patterns not already covered.

EXISTING RULES ALREADY COVER: war/conflict, russia/ukraine, iran/opec, china/exports,
taiwan/tsmc, fed/rates, dollar, tariffs, ai/datacenter, memory/chips, specialty-gases,
power-grid, supply-chain, cybersecurity, weather, food/crops

OUTPUT: A JSON array of new rule proposals. ONLY the JSON array, nothing else.
Format: [{"trigger":"3-6 word phrase","sectors":["s1","s2"],"evidence":"one sentence","confidence":0.0-1.0}]
If no new patterns: []

Focus on SPECIFIC emerging themes like:
- New political figures/policies affecting specific sectors
- New technology trends creating cross-sector dependencies  
- Specific company actions with broader sector implications
- Geographic/regional developments affecting supply chains"""


def load_recent_signals(days: int = 7) -> list[dict]:
    signals = []
    for f in sorted(SIGNALS_DIR.glob("scored_*.jsonl"), reverse=True)[:20]:
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        s = json.loads(line)
                        if isinstance(s, dict):
                            signals.append(s)
                    except:
                        continue
    return signals


def discover_rules_via_llm(signals: list[dict],
                            existing_rules: list[dict]) -> list[dict]:
    # Build concise signal context — titles only to avoid summary noise
    signal_lines = []
    for s in signals[-15:]:
        if not isinstance(s, dict):
            continue
        tickers = s.get("tickers", [])
        sectors = s.get("sectors", [])
        title   = s.get("title", "")[:80]
        sent    = s.get("sentiment", "")
        signal_lines.append(
            f"- [{sent}] {title} | sectors:{sectors} tickers:{tickers}"
        )

    context  = "\n".join(signal_lines)
    user_msg = f"Recent signals:\n{context}\n\nPropose NEW rules only. Output JSON array."

    log.info(f"Sending {len(signal_lines)} signals to DeepSeek for rule discovery")

    try:
        # Retry up to 3 times on 500 errors (Spark may still be initializing)
        resp = None
        for attempt in range(3):
            try:
# BEFORE
#
#                resp = requests.post(
#                    f"{SPARK_OLLAMA_HOST}/api/chat",
#                    json={
#                        "model": DISCOVERY_MODEL,
#                        "stream": False,
#                        "options": {"temperature": 0.1, "num_predict": 4096},
#                        "messages": [
#                            {"role": "system", "content": DISCOVERY_PROMPT},
#                            {"role": "user",   "content": user_msg}
#                        ]
#                    },
#                    timeout=600
#                )
# AFTER
                resp = requests.post(
                    f"{SPARK_LLAMA_HOST}/v1/chat/completions",
                    json={
                        "model": DISCOVERY_MODEL,
                        "stream": False,
                        "temperature": 0.1,
                        "max_tokens": 4096,
                        "messages": [
                            {"role": "system", "content": DISCOVERY_PROMPT},
                            {"role": "user",   "content": user_msg}
                        ]
                    },
                    timeout=600
                )
                resp.raise_for_status()
                break
            except Exception as e:
                if attempt < 2:
                    log.warning(f"Attempt {attempt+1} failed: {e} — retrying in 30s")
                    import time; time.sleep(30)
                else:
                    raise
# BEFORE
#        raw     = resp.json()
#        content = raw["message"]["content"].strip()
#        thinking = raw["message"].get("thinking", "")
# AFTER
        raw     = resp.json()
        msg     = raw["choices"][0]["message"]
        content = (msg.get("content") or "").strip()
        thinking = msg.get("reasoning_content", "")

        log.info(f"Response: {len(content)} content chars, {len(thinking)} thinking chars")

        # Strip think tags if present
        if "<think>" in content:
            content = content[content.rfind("</think>") + 8:].strip()

        # Strip markdown fences
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        # Find JSON array in content
        start = content.find("[")
        end   = content.rfind("]") + 1
        if start != -1 and end > start:
            content = content[start:end]

        if not content:
            log.warning("Empty response from DeepSeek")
            return []

        proposals = json.loads(content)
        log.info(f"DeepSeek proposed {len(proposals)} new rules")
        return proposals if isinstance(proposals, list) else []

    except Exception as e:
        log.error(f"LLM discovery failed: {e}")
        return []


def run_discovery() -> None:
    log.info("─── Rule discovery starting ───")
    Path("/mnt/qnap/timeseries/logs").mkdir(parents=True, exist_ok=True)

    conn           = init_db()
    existing_rules = get_active_rules(conn)
    signals        = load_recent_signals(LOOKBACK_DAYS)

    log.info(f"Loaded {len(signals)} signals from last {LOOKBACK_DAYS} days")
    log.info(f"Existing rules: {len(existing_rules)}")

    proposals = discover_rules_via_llm(signals, existing_rules)

    for proposal in proposals:
        trigger  = proposal.get("trigger", "").strip().lower()
        sectors  = proposal.get("sectors", [])
        evidence = proposal.get("evidence", "")

        if trigger and sectors and len(trigger) > 4:
            propose_rule(conn, trigger, sectors, evidence)

    pending  = conn.execute(
        "SELECT COUNT(*) FROM rule_proposals WHERE status = 'pending'"
    ).fetchone()[0]
    promoted = conn.execute(
        "SELECT COUNT(*) FROM inference_rules WHERE source = 'discovered'"
    ).fetchone()[0]

    log.info(f"─── Discovery complete ───")
    log.info(f"    Pending proposals : {pending}")
    log.info(f"    Promoted to active: {promoted} (+0 new)")
    conn.close()


if __name__ == "__main__":
    run_discovery()
