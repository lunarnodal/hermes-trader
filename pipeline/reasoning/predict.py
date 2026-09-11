#!/usr/bin/env python3
"""
Qwen3.6-27B direct reasoning engine
Queries Qdrant for relevant signals and produces structured predictions
Bypasses Hermes tool limitation — calls Qwen3.6-27B directly
"""

import json
import os
import sys
import logging

AUDIT_MODE = os.getenv("AUDIT_MODE", "false").lower() == "true"
import requests
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent))
from tickers.taxonomy import sectors_for_query, normalize_sectors

SPARK_LLAMA   = os.getenv("SPARK_LLAMA_HOST", "http://172.29.10.225:8083")
OLLAMA_HOST   = os.getenv("OLLAMA_HOST", "http://172.29.10.225:11434")  # legacy
LLAMA_EMBED_URL = os.getenv("LLAMA_EMBED_URL", "http://localhost:8081/v1/embeddings")
QDRANT_HOST   = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT   = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION    = os.getenv("QDRANT_COLLECTION", "trading_signals")
REASONING_MODEL = os.getenv("REASONING_MODEL", "deepseek-r1")  # llama-server ignores the value but keep it
EMBED_MODEL   = "bge-m3"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(
        Path(os.getenv("AUDIT_LOG_DIR", "/opt/hermes-audit/logs") if AUDIT_MODE
             else os.getenv("LOG_DIR", "/mnt/qnap/timeseries/logs"))
        / "predict.log"
    )),
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

try:
    from reasoning.semantic_gates import enrich_signals_semantic as enrich_signals
    SIGNAL_GRAPH_ENABLED = True
except ImportError:
    try:
        from reasoning.signal_graph import enrich_signals
        SIGNAL_GRAPH_ENABLED = True
    except ImportError:
        SIGNAL_GRAPH_ENABLED = False
        log.warning("Signal graph module not available")

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
        LLAMA_EMBED_URL,
        json={"model": EMBED_MODEL, "input": query},
        timeout=30
    )
    resp.raise_for_status()
    data = resp.json()
    # llama.cpp returns {"data": [{"embedding": [...]}]}
    if "data" in data:
        return data["data"][0]["embedding"]
    # fallback for older format
    return data["embeddings"][0]


def query_qdrant(query: str, limit: int = 15,
                 sentiment_filter: str = None,
                 hours_back: int = 48,
                 sector_hint: list[str] = None) -> list[dict]:
    """Semantic search against Qdrant signal store with taxonomy expansion"""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    vector = embed_query(query)

    # Build filters — recency + optional sentiment
    from datetime import datetime, timezone, timedelta
    from qdrant_client.models import DatetimeRange, Range

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()

    must_conditions = [
        FieldCondition(
            key="published",
            range=DatetimeRange(gte=cutoff)
        )
    ]
    if sentiment_filter:
        must_conditions.append(FieldCondition(
            key="sentiment",
            match=MatchValue(value=sentiment_filter)
        ))

    query_filter = Filter(must=must_conditions)

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
    """Format signals into structured context for Qwen3.6-27B"""
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
        lines.append(f"{sentiment_icon} {s['sentiment'].upper()} | conf={s['confidence']:.2f} | rel={s.get('score', 0.0):.3f} | {s['source']}{risk_flag}")
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
    signals = query_qdrant(query, limit=limit, sector_hint=sector_hints or None, hours_back=168)  # 7 days
    if not signals:
        log.warning("No signals found for query")
        return {"error": "No relevant signals found"}

    log.info(f"Found {len(signals)} relevant signals")

    # Enrich with indirect signals from dependency graph
    if SIGNAL_GRAPH_ENABLED and signals:
        # Extract sector from query for targeted enrichment
        query_lower = query.lower()
        target_sector = None
        for sector, keywords in [
            ('technology', ['technology', 'semiconductor', 'ai']),
            ('healthcare', ['healthcare', 'biotech']),
            ('energy', ['energy', 'oil']),
            ('financials', ['financial', 'bank']),
            ('real_estate', ['real estate', 'reit']),
            ('consumer', ['consumer', 'retail']),
            ('industrials', ['industrial', 'defense']),
            ('materials', ['materials', 'mining']),
        ]:
            if any(kw in query_lower for kw in keywords):
                target_sector = sector
                break
        signals = enrich_signals(signals, target_sector=target_sector)
        log.info(f"After enrichment: {len(signals)} total signals")

    # Format context
    signal_context = format_signals_for_reasoning(signals)
    # Build track record context for this sector
    track_record_context = ""
    try:
        import sqlite3 as _sql
        _db = Path(__file__).parent.parent / "data" / "paper_trading.db"
        _conn = _sql.connect(_db)
        _sector_hint = query.split("—")[0].strip()[:25]
        _rows = _conn.execute("""
            SELECT direction, actual_direction, was_correct
            FROM predictions
            WHERE query LIKE ? AND was_correct IS NOT NULL
            ORDER BY created_at DESC LIMIT 20
        """, (f"%{_sector_hint}%",)).fetchall()
        _conn.close()
        if len(_rows) >= 5:
            _total   = len(_rows)
            _correct = sum(1 for r in _rows if r[2] == 1)
            _wr      = _correct / _total * 100
            _dirs = {}
            for r in _rows:
                key = f"{r[0]}→{r[1]}"
                _dirs[key] = _dirs.get(key, 0) + 1
            _dir_str = ", ".join(
                f"{k}:{v}" for k, v in
                sorted(_dirs.items(), key=lambda x: -x[1])[:4]
            )
            track_record_context = f"""
PREDICTION TRACK RECORD (last {_total} verified predictions for this sector):
  Win rate: {_wr:.0f}% ({_correct}/{_total} correct)
  Direction outcomes: {_dir_str}

  IMPORTANT: If win rate is below 40%, you have been systematically wrong.
  If you have been predicting bullish when actual was often bearish or neutral,
  you MUST actively consider the bearish and neutral cases even when signals
  appear bullish. Financial news skews positive — do not let that bias you.
"""
    except Exception as _e:
        log.debug(f"Could not load track record: {_e}")

    user_prompt = f"""Query: {query}
Timeframe: {timeframe}

{track_record_context}
{signal_context}

Based on these signals, provide your reasoning and prediction."""

    # Call Qwen3.6-27B for reasoning
    log.info("Calling Qwen3.6-27B for reasoning...")
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

    # Apply critic review — challenge or reject weak predictions
    critic_verdict   = None
    critic_reasoning = ""
    critic_confidence = None
    if prediction:
        try:
            from reasoning.critic import critique_prediction
            critic_result = critique_prediction(
                query,
                prediction.get("direction", "neutral"),
                prediction.get("confidence", 0.65),
                reasoning=thinking,
            )
            critic_verdict    = critic_result["verdict"]
            critic_reasoning  = critic_result["reasoning"]
            critic_confidence = critic_result["adjusted_confidence"]

            if critic_result["verdict"] == "reject":
                log.warning(f"Critic REJECTED: {critic_result['reasoning'][:100]}")
            elif critic_result["verdict"] == "challenge":
                log.info(f"Critic challenged: {critic_result['reasoning'][:80]}")
                if critic_confidence < prediction.get("confidence", 0.65):
                    prediction["confidence"] = critic_confidence
                    prediction["probability"] = critic_confidence
            else:
                log.info("Critic approved prediction")

            prediction["critic_verdict"]    = critic_verdict
            prediction["critic_reasoning"]  = critic_reasoning
            prediction["critic_confidence"] = critic_confidence
        except Exception as _ce:
            log.warning(f"Critic failed (non-fatal): {_ce}")

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
    if AUDIT_MODE:
        # Audit mode: save to audit-specific path, never write to production DB
        audit_dir = Path(os.getenv("AUDIT_PREDICTIONS_DIR",
                                    "/opt/hermes-audit/audit_output/predictions"))
        audit_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = audit_dir / f"audit_prediction_{ts}.json"
        import json as _json
        out.write_text(_json.dumps(result, indent=2))
        log.info(f"[AUDIT] Prediction saved to audit path: {out.name} (production DB skipped)")
        return out
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




def compute_theta_eligibility_score(sector: str, market_data: dict) -> tuple[float, dict]:
    """
    Composite theta eligibility score 0.0-1.0 for a sector.

    Score >= THETA_CONFIG["theta_eligibility_threshold"] (default 0.65)
    unlocks theta-gang mode (covered-call / cash-secured-put).

    Args:
        sector: Sector name (e.g. "energy", "technology")
        market_data: Dict with keys:
            - iv_rank: float [0, 100]; higher = more expensive premiums
            - premium_yield: float annualized premium as fraction (e.g. 0.02 = 2%)
            - market_regime: str one of "sideways", "trending_bull", "trending_bear", "volatile"
            - assignment_history: dict mapping sector -> {total, assigned, rate}
            - liquidity: dict with open_interest and volume (both absolute counts)

    Returns:
        (score, breakdown) where score is float [0.0, 1.0] and breakdown is a dict
        of sub-component values for debugging/logging, plus an "eligible" boolean.
    """
    iv_rank = max(0.0, min(100.0, float(market_data.get("iv_rank", 50.0))))
    premium_yield = max(0.0, float(market_data.get("premium_yield", 0.0)))
    market_regime = market_data.get("market_regime", "sideways")
    assignment_history = market_data.get("assignment_history", {})
    liquidity = market_data.get("liquidity", {})

    # --- Component 1: IV Rank (weight 0.30) ---
    iv_score = iv_rank / 100.0
    iv_component = iv_score * 0.30

    # --- Component 2: Premium Yield (weight 0.30) ---
    yield_cap = 0.05
    yield_score = min(premium_yield / yield_cap, 1.0)
    yield_component = yield_score * 0.30

    # --- Component 3: Market Regime (weight 0.15) ---
    regime_scores = {
        "sideways": 1.0,
        "volatile": 0.6,
        "trending_bull": 0.3,
        "trending_bear": 0.3,
    }
    regime_score = regime_scores.get(market_regime, 0.5)
    regime_component = regime_score * 0.15

    # --- Component 4: Assignment History Penalty (weight 0.15) ---
    sector_history = assignment_history.get(sector, {"rate": 0.0})
    assignment_rate = sector_history.get("rate", 0.0)
    assignment_penalty = min(assignment_rate / 0.50, 1.0)
    assignment_component = 0.15 * (1.0 - assignment_penalty)

    # --- Component 5: Liquidity (weight 0.10) ---
    oi = liquidity.get("open_interest", 0)
    vol = liquidity.get("volume", 0)
    oi_score = min(oi / 500.0, 1.0) if oi else 0.0
    vol_score = min(vol / 100.0, 1.0) if vol else 0.0
    liq_score = (oi_score + vol_score) / 2.0
    liq_component = liq_score * 0.10

    # --- Composite score, bounded [0.0, 1.0] ---
    raw_score = iv_component + yield_component + regime_component + assignment_component + liq_component
    score = round(max(0.0, min(1.0, raw_score)), 3)

    breakdown = {
        "iv_rank": iv_rank,
        "iv_component": round(iv_component, 4),
        "premium_yield": round(premium_yield, 4),
        "yield_component": round(yield_component, 4),
        "market_regime": market_regime,
        "regime_component": round(regime_component, 4),
        "assignment_rate": round(assignment_rate, 4),
        "assignment_component": round(assignment_component, 4),
        "open_interest": oi,
        "volume": vol,
        "liquidity_component": round(liq_component, 4),
        "eligible": score >= 0.65,
    }

    return score, breakdown


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
