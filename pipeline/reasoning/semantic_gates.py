#!/usr/bin/env python3
"""
Semantic Gate Engine
Replaces hardcoded sector taxonomy matching with embedding-based
neighborhood analysis. Win rates, sector relationships, and calibration
all derive from actual signal similarity rather than text matching.

Key functions:
  semantic_win_rate(sector_query, conn) 
    → win rate from semantically similar past predictions

  semantic_sector_neighborhood(signals)
    → cross-sector relationships derived from embedding proximity

  enrich_signals_semantic(signals, target_query)
    → replaces static signal_graph dependency rules with learned ones
"""

import os
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

QDRANT_HOST     = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT     = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION      = os.getenv("QDRANT_COLLECTION", "trading_signals")
LLAMA_EMBED_URL = os.getenv("LLAMA_EMBED_URL", "http://localhost:8081/v1/embeddings")
EMBED_MODEL     = os.getenv("LLAMA_EMBED_MODEL", "bge-m3")

# Cache embeddings for repeated queries within a session
_embed_cache: dict[str, list[float]] = {}


def _embed(text: str) -> list[float] | None:
    """Generate BGE-M3 embedding. Cached per session."""
    if text in _embed_cache:
        return _embed_cache[text]
    try:
        import requests
        resp = requests.post(
            LLAMA_EMBED_URL,
            json={"model": EMBED_MODEL, "input": text},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", [])
        if items and "embedding" in items[0]:
            vec = items[0]["embedding"]
            _embed_cache[text] = vec
            return vec
        return None
    except Exception as e:
        log.warning(f"[SEMANTIC] Embed failed: {e}")
        return None


def _get_qdrant():
    from qdrant_client import QdrantClient
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)


def semantic_win_rate(sector_query: str,
                      conn: sqlite3.Connection = None,
                      top_k: int = 100,
                      hours_back: int = 2160,  # 90 days
                      min_samples: int = 10) -> float | None:
    """
    Calculate win rate from semantically similar past predictions.

    Instead of matching exact sector text ("WHERE query LIKE '%technology%'"),
    embeds the sector query and finds the most similar past predictions
    by vector proximity. Returns the win rate of those predictions.

    This handles organic sector taxonomy — 'biotech' signals surface
    when searching for 'healthcare', 'cybersecurity' for 'technology' etc.

    Args:
        sector_query: sector description to embed (e.g. "technology AI semiconductors")
        conn: predictions DB connection
        top_k: number of similar predictions to evaluate
        hours_back: lookback window in hours (default 90 days)
        min_samples: minimum predictions needed (returns None if insufficient)

    Returns:
        float win rate [0,1] or None if insufficient data
    """
    vector = _embed(sector_query)
    if not vector:
        return None

    try:
        client = _get_qdrant()
        from qdrant_client.models import Filter, FieldCondition, DatetimeRange

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()

        # Search for similar signals in the 90-day window
        results = client.query_points(
            collection_name=COLLECTION,
            query=vector,
            limit=top_k,
            with_payload=True,
            query_filter=Filter(must=[
                FieldCondition(
                    key="published",
                    range=DatetimeRange(gte=cutoff)
                )
            ])
        ).points

        if not results:
            return None

        # Extract titles from similar signals to match against predictions DB
        similar_titles = [r.payload.get("title", "") for r in results if r.payload]
        similar_titles = [t[:100] for t in similar_titles if t]

        if not similar_titles:
            return None

        # Find predictions that were made after seeing these signals
        similar_sectors = set()
        for r in results:
            for s in (r.payload.get("sectors") or []):
                similar_sectors.add(s.lower())

        if not similar_sectors:
            return None

        # Build sector LIKE conditions dynamically from discovered sectors
        sector_conditions = " OR ".join([
            f"query LIKE '%{s}%'" for s in list(similar_sectors)[:10]
        ])

        # Predictions live in paper_trading.db, not portfolio.db
        _paper_db = Path(__file__).parent.parent.parent / "data" / "paper_trading.db"
        _pred_conn = sqlite3.connect(str(_paper_db))
        try:
            row = _pred_conn.execute(f"""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END) as correct
                FROM predictions
                WHERE was_correct IS NOT NULL
                  AND created_at >= datetime('now', '-90 days')
                  AND ({sector_conditions})
            """).fetchone()
        finally:
            _pred_conn.close()

        if not row or row[0] < min_samples:
            log.debug(f"[SEMANTIC] Insufficient samples for '{sector_query[:40]}': "
                      f"{row[0] if row else 0} < {min_samples}")
            return None

        win_rate = round(row[1] / row[0], 3)
        log.debug(f"[SEMANTIC] Win rate for '{sector_query[:40]}': "
                  f"{row[1]}/{row[0]} = {win_rate:.1%} "
                  f"(from {len(similar_sectors)} discovered sectors: "
                  f"{list(similar_sectors)[:5]})")
        return win_rate

    except Exception as e:
        log.warning(f"[SEMANTIC] Win rate lookup failed: {e}")
        return None


def semantic_sector_neighbors(signals: list[dict],
                               top_k: int = 20,
                               min_score: float = 0.75) -> list[dict]:
    """
    Find related sectors by embedding proximity of current signals.

    For each incoming signal, searches Qdrant for semantically similar
    historical signals and extracts what sectors they touched. Returns
    derived signals for sectors that are consistently co-occurring
    with the current signal cluster.

    This replaces the static STATIC_DEPENDENCIES rules in signal_graph.py
    with learned co-occurrence relationships from the actual corpus.

    Args:
        signals: list of current signals
        top_k: number of similar signals to retrieve per input signal
        min_score: minimum similarity score threshold

    Returns:
        list of derived signals for related sectors
    """
    if not signals:
        return []

    client = _get_qdrant()
    derived = []
    processed_pairs: set[tuple] = set()

    for signal in signals[:10]:  # limit to avoid too many embed calls
        # Build rich text for embedding
        parts = [signal.get("title", "")]
        if signal.get("summary"):
            parts.append(signal["summary"][:200])
        if signal.get("sectors"):
            parts.append("Sectors: " + " ".join(signal["sectors"]))
        text = " | ".join(p for p in parts if p)

        if not text.strip():
            continue

        vector = _embed(text)
        if not vector:
            continue

        try:
            # Find historically similar signals
            results = client.query_points(
                collection_name=COLLECTION,
                query=vector,
                limit=top_k,
                with_payload=True,
                score_threshold=min_score,
            ).points

            if not results:
                continue

            # Count sector co-occurrences in similar signals
            from collections import Counter
            sector_counts: Counter = Counter()
            sector_sentiments: dict[str, Counter] = {}

            for r in results:
                payload = r.payload or {}
                for s in (payload.get("sectors") or []):
                    sector_counts[s] += 1
                    if s not in sector_sentiments:
                        sector_sentiments[s] = Counter()
                    sentiment = payload.get("sentiment", "neutral")
                    sector_sentiments[s][sentiment] += 1

            # Source signal sectors — don't derive signals for these
            source_sectors = set(signal.get("sectors") or [])

            # Derive signals for frequently co-occurring sectors
            for sector, count in sector_counts.most_common(5):
                if sector in source_sectors:
                    continue
                if count < 3:  # need at least 3 co-occurrences
                    break

                # Dominant sentiment for this sector in similar signals
                if sector not in sector_sentiments:
                    continue
                dominant_sentiment = sector_sentiments[sector].most_common(1)[0][0]
                sentiment_confidence = sector_sentiments[sector][dominant_sentiment] / count

                if sentiment_confidence < 0.6:  # need 60% agreement
                    continue

                pair_key = (signal.get("title", "")[:40], sector, dominant_sentiment)
                if pair_key in processed_pairs:
                    continue
                processed_pairs.add(pair_key)

                # Scale confidence by similarity count and sentiment agreement
                base_confidence = signal.get("confidence", 0.65)
                derived_confidence = round(
                    base_confidence * 0.75 * sentiment_confidence * (count / top_k),
                    2
                )
                derived_confidence = max(0.40, min(0.85, derived_confidence))

                derived.append({
                    "title":        f"[SEMANTIC] {signal.get('title', '')[:50]} → {sector}",
                    "sentiment":    dominant_sentiment,
                    "confidence":   derived_confidence,
                    "sectors":      [sector],
                    "tickers":      [],
                    "source":       "semantic_graph",
                    "derived_from": signal.get("title", "")[:60],
                    "similarity_count": count,
                    "sentiment_agreement": round(sentiment_confidence, 2),
                })
                log.debug(f"[SEMANTIC] Derived: {sector} {dominant_sentiment} "
                         f"({count} co-occurrences, {sentiment_confidence:.0%} agreement)")

        except Exception as e:
            log.debug(f"[SEMANTIC] Neighbor search failed: {e}")
            continue

    if derived:
        log.info(f"[SEMANTIC] {len(derived)} derived signals from semantic neighborhood")

    return derived


def enrich_signals_semantic(signals: list[dict],
                              target_sector: str = None,
                              use_static_fallback: bool = True) -> list[dict]:
    """
    Enrich signals with semantically derived cross-sector signals.

    Replaces/augments signal_graph.py's static STATIC_DEPENDENCIES with
    learned relationships from Qdrant embedding proximity.

    Args:
        signals: input signals
        target_sector: optional filter — only return derived signals for this sector
        use_static_fallback: if True, also run static rules from signal_graph.py
                             as a safety net when semantic finds nothing

    Returns:
        original signals + derived signals
    """
    # Semantic neighborhood discovery
    semantic_derived = semantic_sector_neighbors(signals)

    # Filter to target sector if specified
    if target_sector and semantic_derived:
        semantic_derived = [
            d for d in semantic_derived
            if target_sector.lower() in [s.lower() for s in d.get("sectors", [])]
        ]

    # Static fallback — keeps existing rules as safety net
    static_derived = []
    if use_static_fallback:
        try:
            from reasoning.signal_graph import enrich_signals as static_enrich
            all_with_static = static_enrich(signals, target_sector=target_sector)
            # Only take the derived ones (source == signal_graph)
            static_derived = [
                s for s in all_with_static
                if s.get("source") == "signal_graph"
            ]
        except Exception as e:
            log.debug(f"[SEMANTIC] Static fallback failed: {e}")

    # Merge — semantic first, static supplements where semantic found nothing
    if semantic_derived:
        derived = semantic_derived
        if static_derived:
            # Add static signals for sectors not covered by semantic
            semantic_sectors = {s for d in semantic_derived for s in d.get("sectors", [])}
            for s in static_derived:
                s_sectors = set(s.get("sectors", []))
                if not s_sectors.intersection(semantic_sectors):
                    derived.append(s)
    else:
        derived = static_derived

    if derived:
        log.info(f"[SEMANTIC] Enriched: {len(signals)} original + "
                 f"{len(semantic_derived)} semantic + "
                 f"{len([s for s in derived if s.get('source')=='signal_graph'])} static "
                 f"= {len(signals)+len(derived)} total")

    return signals + derived


def get_semantic_sector_profile(sector_query: str,
                                 hours_back: int = 720) -> dict:
    """
    Build a semantic profile of a sector from its signal history.

    Returns:
      signal_count, dominant_sentiment, avg_confidence,
      co_occurring_sectors, top_event_types, win_rate_proxy
    """
    vector = _embed(sector_query)
    if not vector:
        return {}

    try:
        client = _get_qdrant()
        from qdrant_client.models import Filter, FieldCondition, DatetimeRange
        from collections import Counter

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()

        results = client.query_points(
            collection_name=COLLECTION,
            query=vector,
            limit=200,
            with_payload=True,
            score_threshold=0.70,
            query_filter=Filter(must=[
                FieldCondition(key="published", range=DatetimeRange(gte=cutoff))
            ])
        ).points

        if not results:
            return {}

        sentiments = Counter()
        event_types = Counter()
        co_sectors = Counter()
        confidences = []

        for r in results:
            p = r.payload or {}
            sentiments[p.get("sentiment", "neutral")] += 1
            event_types[p.get("event_type", "other")] += 1
            for s in (p.get("sectors") or []):
                co_sectors[s] += 1
            if p.get("confidence"):
                confidences.append(float(p["confidence"]))

        return {
            "signal_count":        len(results),
            "dominant_sentiment":  sentiments.most_common(1)[0][0] if sentiments else "neutral",
            "sentiment_breakdown": dict(sentiments.most_common(3)),
            "avg_confidence":      round(sum(confidences) / len(confidences), 2) if confidences else 0,
            "top_event_types":     dict(event_types.most_common(5)),
            "co_occurring_sectors": dict(co_sectors.most_common(8)),
        }

    except Exception as e:
        log.warning(f"[SEMANTIC] Sector profile failed: {e}")
        return {}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    sys.path.insert(0, str(Path(__file__).parent))
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")

    query = sys.argv[1] if len(sys.argv) > 1 else "technology AI semiconductors data centers"
    print(f"\nSemantic sector profile: '{query}'")
    profile = get_semantic_sector_profile(query)
    import json
    print(json.dumps(profile, indent=2))

    print(f"\nSemantic win rate test (no DB — will show None):")
    import sqlite3 as _sql
    try:
        db = Path(__file__).parent.parent / "data" / "paper_trading.db"
        conn = _sql.connect(str(db))
        wr = semantic_win_rate(query, conn)
        print(f"Win rate: {wr}")
        conn.close()
    except Exception as e:
        print(f"DB not available: {e}")
