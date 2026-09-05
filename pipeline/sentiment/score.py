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
sys.path.insert(0, str(Path(__file__Power grid, electricity demand, energy infrastructure attacks → always add "utilities", "energy", "ai_infrastructure", "data_center"

Example output:
{"sentiment":"bearish","confidence":0.82,"tickers":["TSLA"],"sectors":["automotive","ev"],"event_type":"earnings","macro_themes":["earnings_miss"],"summary":"Tesla missed Q2 earnings estimates significantly. Analyst downgrades expected."}

Example with cross-sector inference:
{"sentiment":"bearish","confidence":0.78,"tickers":[],"sectors":["geopolitical","energy","oil_gas","defense","commodities"],"event_type":"geopolitical","summary":"Military strikes escalate regional conflict. Energy supply disruption risk elevated, defense sector demand increases."}
{"sentiment":"neutral","confidence":0.65,"tickers":["AAPL"],"sectors":["technology"],"event_type":"leadership_transition","macro_themes":["succession_risk","management_change"],"summary":"Apple CEO Tim Cook stepping down. John Ternus named successor. Market reaction depends on transition smoothness and new leadership strategy."}"""


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

            # Fix common qwen3 JSON malformations:
            import re as _re
            # Fix doubled sectors key: "sectors":"sectors": -> "sectors":
            raw = raw.replace('"sectors":"sectors":', '"sectors":')
            # Fix double comma: ,, -> ,
            raw = _re.sub(r',\s*,', ',', raw)
            # Fix missing tickers entirely: "confidence":0.75,,"sectors" -> add tickers
            raw = _re.sub(
                r'("confidence"\s*:\s*[\d.]+)\s*,\s*("sectors")',
                r'\1,"tickers":[],\2',
                raw
            )
            # Fix missing tickers value: "tickers":, -> "tickers":[],
            raw = _re.sub(r'"tickers"\s*:\s*,', '"tickers":[],', raw)
            # Fix bare array after tickers: ["NI"],["energy"] -> ["NI"],"sectors":["energy"]
            raw = _re.sub(
                r'("tickers"\s*:\s*\[[^\]]*\])\s*,\s*(\[)',
                r',"sectors":',
                raw
            )
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