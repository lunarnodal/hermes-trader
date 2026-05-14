#!/usr/bin/env python3
"""
Sentiment scoring pipeline
Reads queue files, scores each article via Ollama, writes structured signals
"""

import json
import os
import logging
import requests
import time
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from rules.rule_engine import init_db, seed_static_rules, build_prompt_rules

# ─── Config ───────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")

OLLAMA_HOST    = os.getenv("OLLAMA_HOST", "http://172.29.10.225:11434")
OLLAMA_MODEL   = os.getenv("OLLAMA_MODEL", "qwen3:30b")
TIMESERIES_DIR = Path(os.getenv("TIMESERIES_DIR", "/mnt/qnap/timeseries/signals"))
DB_PATH        = Path("/mnt/qnap/timeseries/ingestion.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/mnt/qnap/timeseries/logs/score.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ─── Prompt ───────────────────────────────────────────────────────────────────

def get_system_prompt() -> str:
    """Build system prompt with dynamic inference rules from DB"""
    conn = init_db()
    seed_static_rules(conn)
    rules_text = build_prompt_rules(conn)
    conn.close()
    return f"""You are a financial news sentiment analyst.
Analyze the headline and summary provided and return ONLY a JSON object.
No explanation, no markdown, no preamble. Only the JSON object.

Required fields:
- sentiment: exactly one of "bullish", "bearish", "neutral"
- confidence: float between 0.0 and 1.0
- tickers: array of stock tickers mentioned (e.g. ["AAPL", "NVDA"]) or []
- sectors: array of ALL affected sectors including INDIRECT impacts
- event_type: exactly one of "earnings", "macro", "geopolitical", "regulatory", "merger_acquisition", "product", "other"
- summary: max 2 sentence plain English summary of market impact

{{rules_text}}

Example output:
{{"sentiment":"bearish","confidence":0.82,"tickers":["TSLA"],"sectors":["automotive","ev"],"event_type":"earnings","summary":"Tesla missed Q2 earnings estimates significantly. Analyst downgrades expected."}}
"""


SYSTEM_PROMPT = """You are a financial news sentiment analyst.
Analyze the headline and summary provided and return ONLY a JSON object.
No explanation, no markdown, no preamble. Only the JSON object.

Required fields:
- sentiment: exactly one of "bullish", "bearish", "neutral"
- confidence: float between 0.0 and 1.0
- tickers: array of stock tickers mentioned (e.g. ["AAPL", "NVDA"]) or []
- sectors: array of ALL affected sectors including INDIRECT impacts — see inference rules below
- event_type: exactly one of "earnings", "macro", "geopolitical", "regulatory", "merger_acquisition", "product", "other"
- summary: max 2 sentence plain English summary of market impact

SECTOR INFERENCE RULES — always apply these cross-sector inferences:
- War, conflict, military strikes, sanctions → always add "energy", "defense", "commodities"
- Russia, Ukraine, Middle East conflict → always add "energy", "oil_gas", "commodities"
- Iran, Saudi Arabia, OPEC news → always add "energy", "oil_gas"
- Federal Reserve, interest rates, inflation → always add "financials", "real_estate", "utilities"
- China trade, tariffs, export controls → always add "semiconductors", "technology", "manufacturing"
- Supply chain disruption → always add "manufacturing", "technology", "retail"
- Drought, floods, weather events → always add "agriculture", "insurance", "utilities"
- Cybersecurity attacks → always add "technology", "cybersecurity", "financials"
- Food prices, crop reports → always add "agriculture", "consumer_staples"
- Dollar strength/weakness → always add "commodities", "emerging_markets", "exporters"
- AI infrastructure, data center, GPU demand → always add "semiconductors", "ai_infrastructure", "data_center", "utilities"
- Memory shortage, HBM, DRAM supply → always add "memory", "semiconductors", "ai_infrastructure"
- Ukraine conflict specifically → always add "energy", "oil_gas", "neon_gas", "semiconductors", "chemicals"
- Russia sanctions, Russian exports → always add "energy", "oil_gas", "commodities", "chemicals", "semiconductors"
- China export controls, gallium, germanium → always add "semiconductors", "materials", "ai_infrastructure"
- Taiwan, TSMC, strait tensions → always add "semiconductors", "ai_infrastructure", "technology"
- Data center construction, hyperscaler capex → always add "utilities", "real_estate", "construction", "ai_infrastructure"
- Specialty gases, neon, argon, krypton → always add "semiconductors", "chemicals", "manufacturing"
- Power grid, electricity demand, energy infrastructure attacks → always add "utilities", "energy", "ai_infrastructure", "data_center"

Example output:
{"sentiment":"bearish","confidence":0.82,"tickers":["TSLA"],"sectors":["automotive","ev"],"event_type":"earnings","summary":"Tesla missed Q2 earnings estimates significantly. Analyst downgrades expected."}

Example with cross-sector inference:
{"sentiment":"bearish","confidence":0.78,"tickers":[],"sectors":["geopolitical","energy","oil_gas","defense","commodities"],"event_type":"geopolitical","summary":"Military strikes escalate regional conflict. Energy supply disruption risk elevated, defense sector demand increases."}"""


def build_prompt(article: dict) -> str:
    title   = article.get("title", "")
    summary = article.get("summary", "")
    source  = article.get("source", "")
    return f"Source: {source}\nHeadline: {title}\nSummary: {summary}"


# ─── Ollama ───────────────────────────────────────────────────────────────────

def score_article(article: dict, retries: int = 2) -> dict | None:
    payload = {
        "model":  OLLAMA_MODEL,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 4096},
        "messages": [
            {"role": "system",  "content": get_system_prompt()},
            {"role": "user",    "content": build_prompt(article)},
        ]
    }

    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                f"{OLLAMA_HOST}/api/chat",
                json=payload,
                timeout=300
            )
            resp.raise_for_status()
            raw = resp.json()["message"]["content"].strip()

            # Strip thinking tags if model returns them
            if "<think>" in raw:
                raw = raw[raw.rfind("</think>") + 8:].strip()

            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            scored = json.loads(raw)

            # Validate required fields
            assert scored.get("sentiment") in ("bullish", "bearish", "neutral")
            assert 0.0 <= float(scored.get("confidence", 0)) <= 1.0

            return scored

        except (json.JSONDecodeError, AssertionError) as e:
            log.warning(f"Parse error attempt {attempt+1}: {e} — raw: {raw[:100]}")
            if attempt < retries:
                time.sleep(1)
        except requests.RequestException as e:
            log.error(f"Ollama request failed: {e}")
            if attempt < retries:
                time.sleep(2)

    return None


# ─── Queue processing ─────────────────────────────────────────────────────────

def find_unprocessed_queues() -> list[Path]:
    if not TIMESERIES_DIR.exists():
        return []
    queues    = sorted(TIMESERIES_DIR.glob("queue_*.jsonl"))
    processed = set(TIMESERIES_DIR.glob("scored_*.jsonl"))
    processed_stems = {p.stem.replace("scored_", "queue_") for p in processed}
    return [q for q in queues if q.stem not in processed_stems]


def process_queue(queue_file: Path) -> Path | None:
    articles = []
    with queue_file.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    articles.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not articles:
        return None

    log.info(f"Scoring {len(articles)} articles from {queue_file.name}")
    signals = []

    for i, article in enumerate(articles):
        log.info(f"  [{i+1}/{len(articles)}] {article['source']} — {article['title'][:60]}")
        score = score_article(article)

        if score is None:
            log.warning(f"  Failed to score: {article['guid'][:12]}")
            continue

        signal = {
            "guid":       article["guid"],
            "source":     article["source"],
            "title":      article["title"],
            "url":        article.get("url", ""),
            "published":  article.get("published"),
            "scored_at":  datetime.now(timezone.utc).isoformat(),
            "sentiment":  score["sentiment"],
            "confidence": float(score["confidence"]),
            "tickers":    score.get("tickers", []),
            "sectors":    score.get("sectors", []),
            "event_type": score.get("event_type", "other"),
            "summary":    score.get("summary", ""),
        }
        signals.append(signal)
        log.info(f"  → {score['sentiment']} ({score['confidence']:.2f}) tickers={score.get('tickers', [])}")

    if not signals:
        return None

    # Write scored output
    out_name = queue_file.name.replace("queue_", "scored_")
    out_path = TIMESERIES_DIR / out_name
    with out_path.open("w") as f:
        for signal in signals:
            f.write(json.dumps(signal) + "\n")

    log.info(f"Wrote {len(signals)} signals to {out_path.name}")
    return out_path


def run_once() -> None:
    log.info("─── Sentiment scoring run starting ───")
    queues = find_unprocessed_queues()

    if not queues:
        log.info("No unprocessed queue files found")
        return

    log.info(f"Found {len(queues)} queue file(s) to process")
    for queue_file in queues:
        process_queue(queue_file)

    log.info("─── Scoring complete ───")


if __name__ == "__main__":
    run_once()
