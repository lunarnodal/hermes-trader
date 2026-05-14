#!/usr/bin/env python3
"""
DeepSeek-R1-70B direct reasoning engine
Queries Qdrant for relevant signals and produces structured predictions
Bypasses Hermes tool limitation — calls DeepSeek directly
"""

import json
import os
import sys
import logging
import requests
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent))

SPARK_OLLAMA  = os.getenv("SPARK_OLLAMA_HOST", "http://172.29.11.225:11434")
OLLAMA_HOST   = os.getenv("OLLAMA_HOST", "http://172.29.10.225:11434")
QDRANT_HOST   = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT   = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION    = os.getenv("QDRANT_COLLECTION", "trading_signals")
REASONING_MODEL = "deepseek-r1:70b"
EMBED_MODEL   = "bge-m3"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/mnt/qnap/timeseries/logs/predict.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

REASONING_SYSTEM = """You are a financial market prediction analyst.
You have access to pre-processed sentiment signals from financial news feeds.
Your role is to reason over these signals and produce a structured directional prediction.

REASONING APPROACH:
- Weigh signals by confidence score and recency
- Look for signal clusters (multiple sources pointing same direction)
- Identify conflicting signals and explain the tension
- Consider cross-sector dependencies (e.g. energy → manufacturing costs)
- Express predictions as probabilities, never certainties

OUTPUT FORMAT — always end with this exact JSON block:
```prediction
{
  "query": "the question being answered",
  "direction": "bullish|bearish|neutral|mixed",
  "probability": 0.0-1.0,
  "timeframe": "24h|48h|1w",
  "supporting_signals": ["signal1", "signal2"],
  "conflicting_signals": ["signal1"],
  "key_risk": "main risk to this prediction",
  "confidence": 0.0-1.0,
  "reasoning_summary": "2-3 sentence summary"
}
```

CONSTRAINTS:
- Never fabricate signals — only use what is provided
- Never recommend trade sizes or leverage
- Always cite specific signals from the data
- This is analysis only, not financial advice"""


def embed_query(query: str) -> list[float]:
    """Generate embedding for semantic search using airig's Ollama"""
    resp = requests.post(
        f"{OLLAMA_HOST}/api/embed",
        json={"model": EMBED_MODEL, "input": query},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


def query_qdrant(query: str, limit: int = 15,
                 sentiment_filter: str = None,
                 hours_back: int = 48) -> list[dict]:
    """Semantic search against Qdrant signal store"""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    vector = embed_query(query)

    # Build optional sentiment filter
    query_filter = None
    if sentiment_filter:
        query_filter = Filter(
            must=[FieldCondition(
                key="sentiment",
                match=MatchValue(value=sentiment_filter)
            )]
        )

    results = client.query_points(
        collection_name=COLLECTION,
        query=vector,
        limit=limit,
        with_payload=True,
        query_filter=query_filter
    ).points

    signals = []
    for r in results:
        p = r.payload
        signals.append({
            "score":      round(r.score, 3),
            "sentiment":  p.get("sentiment"),
            "confidence": p.get("confidence"),
            "source":     p.get("source"),
            "title":      p.get("title"),
            "tickers":    p.get("tickers", []),
            "sectors":    p.get("sectors", []),
            "event_type": p.get("event_type"),
            "summary":    p.get("summary"),
            "published":  p.get("published"),
        })

    return signals


def format_signals_for_reasoning(signals: list[dict]) -> str:
    """Format signals into structured context for DeepSeek"""
    lines = [f"SIGNAL DATA ({len(signals)} signals):", ""]

    bull = [s for s in signals if s["sentiment"] == "bullish"]
    bear = [s for s in signals if s["sentiment"] == "bearish"]
    neut = [s for s in signals if s["sentiment"] == "neutral"]

    lines.append(f"Summary: {len(bull)} bullish, {len(bear)} bearish, {len(neut)} neutral")
    avg_conf = sum(s["confidence"] for s in signals) / len(signals) if signals else 0
    lines.append(f"Avg confidence: {avg_conf:.2f}")
    lines.append("")

    for i, s in enumerate(signals, 1):
        lines.append(f"[{i}] {s['sentiment'].upper()} ({s['confidence']:.2f}) | "
                    f"relevance={s['score']:.3f} | {s['source']}")
        lines.append(f"    Title: {s['title']}")
        if s.get("tickers"):
            lines.append(f"    Tickers: {', '.join(s['tickers'])}")
        if s.get("sectors"):
            lines.append(f"    Sectors: {', '.join(s['sectors'])}")
        if s.get("summary"):
            lines.append(f"    Summary: {s['summary'][:150]}")
        lines.append("")

    return "\n".join(lines)


def run_prediction(query: str, timeframe: str = "24h",
                   limit: int = 15) -> dict:
    """Run a full prediction reasoning cycle"""
    log.info(f"Running prediction: '{query}' ({timeframe})")

    # Query Qdrant
    signals = query_qdrant(query, limit=limit)
    if not signals:
        log.warning("No signals found for query")
        return {"error": "No relevant signals found"}

    log.info(f"Found {len(signals)} relevant signals")

    # Format context
    signal_context = format_signals_for_reasoning(signals)
    user_prompt = f"""Query: {query}
Timeframe: {timeframe}

{signal_context}

Based on these signals, provide your reasoning and prediction."""

    # Call DeepSeek on Spark
    log.info("Calling DeepSeek-R1-70B for reasoning...")
    resp = requests.post(
        f"{SPARK_OLLAMA}/api/chat",
        json={
            "model": REASONING_MODEL,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 8192},
            "messages": [
                {"role": "system", "content": REASONING_SYSTEM},
                {"role": "user",   "content": user_prompt}
            ]
        },
        timeout=600
    )
    resp.raise_for_status()

    raw      = resp.json()
    content  = raw["message"]["content"].strip()
    thinking = raw["message"].get("thinking", "")

    log.info(f"Response: {len(content)} chars, {len(thinking)} thinking chars")

    # Extract prediction JSON
    prediction = {}
    # Try ```prediction block first, then ```json
    for marker in ["```prediction", "```json", "```"]:
        if marker in content:
            try:
                pred_text = content.split(marker)[1].split("```")[0].strip()
                if pred_text.startswith("json"):
                    pred_text = pred_text[4:].strip()
                candidate = json.loads(pred_text)
                if isinstance(candidate, dict) and "direction" in candidate:
                    prediction = candidate
                    log.info(f"Parsed prediction from {marker} block")
                    break
            except Exception as e:
                log.debug(f"Could not parse {marker} block: {e}")
                continue

    return {
        "query":      query,
        "timeframe":  timeframe,
        "signals_used": len(signals),
        "reasoning":  content,
        "thinking_chars": len(thinking),
        "prediction": prediction,
        "timestamp":  datetime.now(timezone.utc).isoformat()
    }


def save_and_record(result: dict) -> Path:
    """Save prediction to QNAP and record in paper trading DB"""
    out_path = save_prediction(result)
    try:
        from paper_trading.db import init_db as init_paper_db, record_prediction as rec_pred
        conn = init_paper_db()
        pred_id = rec_pred(conn, result, str(out_path))
        result["prediction_id"] = pred_id
        conn.close()
        log.info(f"Recorded in paper trading DB as prediction #{pred_id}")
    except Exception as e:
        log.error(f"Failed to record in paper trading DB: {e}")
    return out_path


def save_prediction(result: dict) -> Path:
    """Save prediction to QNAP timeseries"""
    out_dir = Path("/mnt/qnap/timeseries/predictions")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts      = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out     = out_dir / f"prediction_{ts}.json"
    out.write_text(json.dumps(result, indent=2))
    log.info(f"Saved prediction to {out.name}")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DeepSeek market prediction")
    parser.add_argument("query", help="Market query to analyze")
    parser.add_argument("--timeframe", default="24h",
                        choices=["24h", "48h", "1w"])
    parser.add_argument("--limit", type=int, default=15,
                        help="Number of signals to retrieve")
    parser.add_argument("--save", action="store_true",
                        help="Save prediction to QNAP")
    args = parser.parse_args()

    result = run_prediction(args.query, args.timeframe, args.limit)

    # Print reasoning
    print("\n" + "="*60)
    print(f"PREDICTION: {args.query}")
    print("="*60)
    print(result.get("reasoning", "No reasoning returned"))

    # Print structured prediction
    if result.get("prediction"):
        print("\n" + "="*60)
        print("STRUCTURED OUTPUT:")
        print(json.dumps(result["prediction"], indent=2))

    if args.save:
        save_and_record(result)