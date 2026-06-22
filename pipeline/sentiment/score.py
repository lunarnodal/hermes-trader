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
from tickers.extract import extract_tickers, init_ticker_db, seed_common_tickers
from tickers.taxonomy import normalize_sectors
from events.meetings import is_meeting_signal, extract_meeting_date, record_meeting, init_db as init_events_db, is_in_risk_window
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from rules.rule_engine import init_db, seed_static_rules, build_prompt_rules

# ─── Config ───────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")

LLAMA_URL      = os.getenv("LLAMA_SCORE_URL", "http://localhost:8080/v1/chat/completions")
LLAMA_MODEL    = os.getenv("LLAMA_SCORE_MODEL", "qwen3")
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

    # Load macro themes dynamically from taxonomy file
    try:
        _taxonomy_path = Path(__file__).parent.parent / "data" / "theme_taxonomy.json"
        _taxonomy = json.loads(_taxonomy_path.read_text())
        _theme_list = []
        for _cat, _themes in _taxonomy.items():
            if _cat != 'discovered':
                _theme_list.extend(_themes[:8])
        _theme_sample = ', '.join(_theme_list[:80])
    except Exception:
        _theme_sample = 'interest_rate_increase, oil_price_increase, earnings_beat, geopolitical_tension, ipo_listing'

    return f"""You are a financial news sentiment analyst.
Analyze the headline and summary provided and return ONLY a JSON object.
No explanation, no markdown, no preamble. Only the JSON object.

Required fields:
- sentiment: exactly one of "bullish", "bearish", "neutral"
- confidence: float between 0.0 and 1.0
- tickers: array of stock tickers mentioned (e.g. ["AAPL", "NVDA"]) or []
- sectors: array of ALL affected sectors including INDIRECT impacts
- event_type: exactly one of "earnings", "macro", "geopolitical", "regulatory", "merger_acquisition", "ipo", "product", "other"
- macro_themes: array of applicable themes (pick all that apply, or []): {_theme_sample}. If the article covers a genuinely novel theme not in this list, you may add new snake_case theme names prefixed with "new:" e.g. "new:space_economy"
- summary: max 2 sentence plain English summary of market impact

{rules_text}

Example output:
{{"sentiment":"bearish","confidence":0.82,"tickers":["TSLA"],"sectors":["automotive","ev"],"event_type":"earnings","macro_themes":["earnings_miss"],"summary":"Tesla missed Q2 earnings estimates significantly. Analyst downgrades expected."}}
"""


SYSTEM_PROMPT = """You are a financial news sentiment analyst.
Analyze the headline and summary provided and return ONLY a JSON object.
No explanation, no markdown, no preamble. Only the JSON object.

Required fields:
- sentiment: exactly one of "bullish", "bearish", "neutral"
- confidence: float between 0.0 and 1.0
- tickers: array of stock tickers mentioned (e.g. ["AAPL", "NVDA"]) or []
- sectors: array of ALL affected sectors including INDIRECT impacts — see inference rules below
- event_type: exactly one of "earnings", "macro", "geopolitical", "regulatory", "merger_acquisition", "ipo", "product", "other"
- macro_themes: array of applicable themes (pick all that apply, or []): interest_rate_increase, interest_rate_decrease, fed_policy, central_bank_policy, yield_curve, bond_yields, inflation_fighting, real_yields, military_conflict, trade_sanctions, diplomatic_tension, iran
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
{"sentiment":"bearish","confidence":0.82,"tickers":["TSLA"],"sectors":["automotive","ev"],"event_type":"earnings","macro_themes":["earnings_miss"],"summary":"Tesla missed Q2 earnings estimates significantly. Analyst downgrades expected."}

Example with cross-sector inference:
{"sentiment":"bearish","confidence":0.78,"tickers":[],"sectors":["geopolitical","energy","oil_gas","defense","commodities"],"event_type":"geopolitical","summary":"Military strikes escalate regional conflict. Energy supply disruption risk elevated, defense sector demand increases."}"""


def build_prompt(article: dict) -> str:
    title   = article.get("title", "")
    summary = article.get("summary", "")
    source  = article.get("source", "")
    return f"Source: {source}\nHeadline: {title}\nSummary: {summary}"


# ─── Ollama ───────────────────────────────────────────────────────────────────

def _save_new_themes(new_themes: list[str]) -> None:
    """Save newly proposed themes to the taxonomy file"""
    import json as _json
    taxonomy_path = Path(__file__).parent.parent / "data" / "theme_taxonomy.json"
    try:
        taxonomy = _json.loads(taxonomy_path.read_text())
        added = []
        discovered = taxonomy.setdefault("discovered", [])
        for theme in new_themes:
            theme = theme.strip().lower().replace(' ', '_')
            if theme and theme not in discovered:
                discovered.append(theme)
                added.append(theme)
        if added:
            taxonomy_path.write_text(_json.dumps(taxonomy, indent=2))
            log.info(f"New themes discovered and saved: {added}")
    except Exception as e:
        log.warning(f"Could not save new themes: {e}")


def score_article(article: dict, retries: int = 2) -> dict | None:
    payload = {
        "model":  LLAMA_MODEL,
        "stream": False,
        "temperature": 0.1,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system",  "content": get_system_prompt()},
            {"role": "user",    "content": build_prompt(article)},
        ]
    }

    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                LLAMA_URL,
                json=payload,
                timeout=300
            )
            resp.raise_for_status()
            raw = (resp.json()["choices"][0]["message"].get("content") or "").strip()

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

            # Ensure all required fields present with defaults
            scored.setdefault("confidence", 0.65)
            scored.setdefault("tickers", [])
            scored.setdefault("sectors", [])
            scored.setdefault("event_type", "other")
            scored.setdefault("macro_themes", [])
            scored.setdefault("summary", "")

            # Auto-save any new themes proposed by the model
            themes = scored.get("macro_themes", [])
            # Collect themes with "new:" prefix AND unknown themes not in taxonomy
            try:
                import json as _json
                _tax = _json.loads(
                    (Path(__file__).parent.parent / "data" / "theme_taxonomy.json").read_text()
                )
                _known = {t for v in _tax.values() for t in (v if isinstance(v, list) else [])}
            except:
                _known = set()

            new_themes = []
            for t in themes:
                if t.startswith("new:"):
                    new_themes.append(t[4:])
                elif t not in _known and len(t) >= 2:
                    new_themes.append(t)  # unknown theme — save it

            if new_themes:
                _save_new_themes(new_themes)

            # Clean the new: prefix from stored themes
            scored["macro_themes"] = [
                t[4:] if t.startswith("new:") else t for t in themes
            ]

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

        # Enhanced ticker extraction — combine LLM tickers with
        # regex and company name lookup for higher accuracy
        enhanced_tickers = extract_tickers(
            title=article["title"],
            summary=article.get("summary", ""),
            llm_tickers=score.get("tickers", [])
        )

        # Shareholder meeting detection
        title   = article["title"]
        summary = article.get("summary", "")
        if is_meeting_signal(title, summary):
            try:
                meeting_date = extract_meeting_date(title, summary)
                ev_conn = init_events_db()
                # Use first ticker if available, else unknown
                primary_ticker = enhanced_tickers[0] if enhanced_tickers else "UNKNOWN"
                record_meeting(ev_conn, primary_ticker, title[:50],
                               title, article.get("url", ""),
                               meeting_date=meeting_date)
                ev_conn.close()
                log.info(f"Meeting detected: {primary_ticker} — {title[:50]}")
            except Exception as e:
                log.warning(f"Meeting tracking failed: {e}")

        signal = {
            "guid":       article["guid"],
            "source":     article["source"],
            "title":      article["title"],
            "url":        article.get("url", ""),
            "published":  article.get("published"),
            "scored_at":  datetime.now(timezone.utc).isoformat(),
            "sentiment":  score["sentiment"],
            "confidence": float(score["confidence"]),
            "tickers":    enhanced_tickers,
            "sectors":    normalize_sectors(score.get("sectors", [])),
            "event_type":  score.get("event_type", "other"),
            "macro_themes": score.get("macro_themes", []),
            "summary":     score.get("summary", ""),
            "confidence":  score.get("confidence", 0.65),
            "sentiment":   score.get("sentiment", "neutral"),
        }
        signals.append(signal)
        log.info(f"  → {score.get('sentiment','?')} ({score.get('confidence',0):.2f}) tickers={score.get('tickers', [])}")

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