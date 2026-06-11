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
from tickers.taxonomy import sectors_for_query, normalize_sectors

SPARK_LLAMA   = os.getenv("SPARK_LLAMA_HOST", "http://172.29.11.225:8080")
OLLAMA_HOST   = os.getenv("OLLAMA_HOST", "http://172.29.10.225:11434")
QDRANT_HOST   = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT   = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION    = os.getenv("QDRANT_COLLECTION", "trading_signals")
REASONING_MODEL = os.getenv("REASONING_MODEL", "deepseek-r1")  # llama-server ignores the value but keep it
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

try:
    from reasoning.calibration import calibrate_confidence
    CALIBRATION_ENABLED = True
except ImportError:
    CALIBRATION_ENABLED = False
    log.warning("Calibration module not available")

REASONING_SYSTEM = """You are a disciplined financial market analyst combining
sentiment signal analysis with evidence-based investing principles.

SIGNAL ANALYSIS APPROACH:
- Weigh signals by confidence score and recency
- Look for signal CLUSTERS — multiple independent sources pointing same direction
- A single loud headline is noise; 3+ corroborating signals is a pattern
- Identify conflicting signals and explain the tension explicitly
- Consider cross-sector dependencies (e.g. bond yields → real estate, energy → manufacturing)
- Express predictions as probabilities, never certainties

BOGLEHEAD-INSPIRED PRE-TRADE CHECKLIST:
Before issuing any bullish recommendation, verify all of the following:
□ MULTI-SOURCE: Are there 2+ independent signals supporting this direction?
□ NOT PRICED IN: Has this sector/asset already moved >5% in the past 5 days?
  If yes, much of the gain may already be captured — reduce confidence.
□ DIVERSIFICATION: Does this add to concentration risk in one sector?
  High sector concentration = lower confidence score.
□ COUNTER-ARGUMENT: What is the strongest case AGAINST this trade?
  Always name it explicitly in conflicting_signals.
□ SIGNAL vs NOISE: Is this a sustained pattern or a single reactive headline?
  Single-article spikes should be weighted 50% less than multi-day trends.
□ ETF PREFERENCE: When individual stock signals are weak (<3 signals),
  prefer the sector ETF over individual picks.
□ MACRO CONTEXT: Does the broader macro environment support this trade?
  A bullish energy call during a bond selloff/risk-off environment
  should have reduced confidence.

CONFIDENCE CALIBRATION:
- Start at 0.50 (coin flip)
- +0.10 per additional corroborating signal (max +0.30)
- +0.10 if macro environment is aligned
- -0.10 if fewer than 2 independent sources
- -0.10 if sector already moved >5% recently (priced in risk)
- -0.15 if strong conflicting signals present
- -0.20 if risk-off macro environment (bond selloff, dollar rally, yields rising)
- Cap bullish predictions at 0.80 without exceptional signal strength
- Cap bearish predictions at 0.85

CRITICAL OUTPUT RULES:
- supporting_signals and conflicting_signals MUST contain exact TITLE text from signals
- Never use index numbers or placeholders
- Always end your response with the ```prediction JSON block
- reasoning_summary must address the strongest counter-argument

OUTPUT FORMAT:
```prediction
{
  "query": "the question being answered",
  "direction": "bullish|bearish|neutral|mixed",
  "probability": 0.0-1.0,
  "timeframe": "24h|48h|1w",
  "supporting_signals": ["exact title from TITLE field"],
  "conflicting_signals": ["exact title from TITLE field"],
  "key_risk": "main risk to this prediction",
  "confidence": 0.0-1.0,
  "checklist": {
    "multi_source": true,
    "not_priced_in": true,
    "macro_aligned": true,
    "etf_preferred": false
  },
  "reasoning_summary": "2-3 sentences including strongest counter-argument"
}
```

CONSTRAINTS:
- Never fabricate signals — only use what is provided
- Never recommend trade sizes or leverage
- Always cite exact TITLE text — no index numbers
- conflicting_signals must never be empty — always find the strongest bull case
  even in a bearish environment. If truly no conflicting signals exist, state why.
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
                 hours_back: int = 48,
                 sector_hint: list[str] = None) -> list[dict]:
    """Semantic search against Qdrant signal store with taxonomy expansion"""
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

    for s in signals:
        sentiment_icon = "↑" if s["sentiment"] == "bullish" else "↓" if s["sentiment"] == "bearish" else "→"
        # Check if any ticker is in elevated risk window
        risk_flag = ""
        if s.get("tickers"):
            try:
                from events.meetings import init_db as init_ev_db, is_in_risk_window
                ev_conn = init_ev_db()
                for ticker in s["tickers"]:
                    window = is_in_risk_window(ev_conn, ticker)
                    if window:
                        risk_flag = f" ⚠️ MEETING RISK ({ticker} meeting: {window.get('meeting_date','TBD')})"
                        break
                ev_conn.close()
            except Exception:
                pass
        lines.append(f"{sentiment_icon} {s['sentiment'].upper()} | conf={s['confidence']:.2f} | rel={s['score']:.3f} | {s['source']}{risk_flag}")
        lines.append(f"  TITLE: {s['title']}")
        if s.get("tickers"):
            lines.append(f"  TICKERS: {', '.join(s['tickers'])}")
        if s.get("sectors"):
            from tickers.taxonomy import normalize_sectors
            norm_sectors = normalize_sectors(s["sectors"])
            lines.append(f"  SECTORS: {', '.join(norm_sectors)}")
        if s.get("summary"):
            lines.append(f"  SUMMARY: {s['summary'][:150]}")
        lines.append("")

    return "\n".join(lines)


def run_prediction(query: str, timeframe: str = "24h",
                   limit: int = 15) -> dict:
    """Run a full prediction reasoning cycle"""
    log.info(f"Running prediction: '{query}' ({timeframe})")

    # Extract sector hints from query for taxonomy expansion
    query_lower = query.lower()
    from tickers.taxonomy import TAXONOMY, normalize_sector
    sector_hints = []
    for parent in TAXONOMY:
        if parent in query_lower:
            sector_hints.append(parent)
        for child in TAXONOMY[parent].get("children", []):
            if child.replace("_", " ") in query_lower or child in query_lower:
                sector_hints.append(child)

    log.info(f"Sector hints from query: {sector_hints}")

    # Query Qdrant
    signals = query_qdrant(query, limit=limit, sector_hint=sector_hints or None)
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
# BEFORE
#    resp = requests.post(
#        f"{SPARK_OLLAMA}/api/chat",
#        json={
#            "model": REASONING_MODEL,
#            "stream": False,
#            "options": {"temperature": 0.1, "num_predict": 8192},
#            "messages": [
#                {"role": "system", "content": REASONING_SYSTEM},
#                {"role": "user",   "content": user_prompt}
#            ]
#        },
#        timeout=600
#    )
#    resp.raise_for_status()
#
#    raw      = resp.json()
#    content  = raw["message"]["content"].strip()
#    thinking = raw["message"].get("thinking", "")

# AFTER
    resp = requests.post(
        f"{SPARK_LLAMA}/v1/chat/completions",
        json={
            "model": REASONING_MODEL,
            "stream": False,
            "temperature": 0.1,
            "max_tokens": 8192,
            "messages": [
                {"role": "system", "content": REASONING_SYSTEM},
                {"role": "user",   "content": user_prompt}
            ]
        },
        timeout=600
    )
    resp.raise_for_status()

    raw      = resp.json()
    msg      = raw["choices"][0]["message"]
    content  = (msg.get("content") or "").strip()
    thinking = msg.get("reasoning_content", "")


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

    # Apply sector calibration to confidence score
    if CALIBRATION_ENABLED and prediction:
        raw_conf = prediction.get("confidence", 0.65)
        cal_conf, cal_explanation = calibrate_confidence(query, 
                                        prediction.get("direction", "neutral"),
                                        raw_conf)
        if cal_conf != raw_conf:
            prediction["confidence"]            = cal_conf
            prediction["probability"]           = cal_conf
            prediction["calibration_applied"]   = cal_explanation
            log.info(f"Calibrated: {cal_explanation}")

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
