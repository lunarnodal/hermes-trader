#!/usr/bin/env python3
"""
Re-embedding script for rescored signals
Deletes old Qdrant points by guid and re-embeds with corrected sector tags
"""

import json
import logging
import os
import requests
import hashlib
from pathlib import Path
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance

load_dotenv(Path(__file__).parent.parent / ".env")

QDRANT_HOST    = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT    = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION     = os.getenv("QDRANT_COLLECTION", "trading_signals")
OLLAMA_HOST    = os.getenv("OLLAMA_HOST", "http://172.29.10.225:11434")
EMBED_MODEL    = "bge-m3"
VECTOR_SIZE    = 1024
SIGNALS_DIR    = Path(os.getenv("TIMESERIES_DIR", "/mnt/qnap/timeseries/signals"))

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def signal_to_point_id(guid: str) -> str:
    h = hashlib.md5(guid.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def embed_text(text: str) -> list[float] | None:
    try:
        resp = requests.post(f"{OLLAMA_HOST}/api/embed",
            json={"model": EMBED_MODEL, "input": text}, timeout=30)
        resp.raise_for_status()
        embeddings = resp.json().get("embeddings", [])
        return embeddings[0] if embeddings else None
    except Exception as e:
        log.error(f"Embedding failed: {e}")
        return None


def build_embed_text(signal: dict) -> str:
    parts = [signal.get("title", "")]
    if signal.get("summary"):
        parts.append(signal["summary"])
    if signal.get("tickers"):
        parts.append("Tickers: " + " ".join(signal["tickers"]))
    if signal.get("sectors"):
        parts.append("Sectors: " + " ".join(signal["sectors"]))
    return " | ".join(p for p in parts if p)


def reembed_scored_file(scored_file: Path, client: QdrantClient) -> int:
    signals = []
    with scored_file.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    signals.append(json.loads(line))
                except:
                    continue

    if not signals:
        return 0

    log.info(f"Re-embedding {len(signals)} signals from {scored_file.name}")

    # Delete existing points for these guids
    point_ids = [signal_to_point_id(s["guid"]) for s in signals]
    try:
        client.delete(
            collection_name=COLLECTION,
            points_selector=point_ids
        )
        log.info(f"Deleted {len(point_ids)} old points from Qdrant")
    except Exception as e:
        log.warning(f"Delete failed (may not exist): {e}")

    # Re-embed and upsert
    points = []
    for i, signal in enumerate(signals):
        text   = build_embed_text(signal)
        vector = embed_text(text)
        if vector is None:
            continue

        point_id = signal_to_point_id(signal["guid"])
        payload  = {
            "guid":       signal["guid"],
            "source":     signal["source"],
            "title":      signal["title"],
            "url":        signal.get("url", ""),
            "published":  signal.get("published"),
            "scored_at":  signal.get("scored_at"),
            "sentiment":  signal["sentiment"],
            "confidence": signal["confidence"],
            "tickers":    signal.get("tickers", []),
            "sectors":    signal.get("sectors", []),
            "event_type": signal.get("event_type", "other"),
            "summary":    signal.get("summary", ""),
        }
        points.append(PointStruct(id=point_id, vector=vector, payload=payload))

        if (i + 1) % 10 == 0:
            log.info(f"  [{i+1}/{len(signals)}] embedded")

    if points:
        client.upsert(collection_name=COLLECTION, points=points)
        log.info(f"Upserted {len(points)} points")

    return len(points)


def run_reembed(scored_file: str = None) -> None:
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    if scored_file:
        files = [Path(scored_file)]
    else:
        # Find scored files that have a matching rescore prefix
        files = list(SIGNALS_DIR.glob("scored_rescore_*.jsonl"))

    if not files:
        log.info("No rescore files found")
        return

    total = 0
    for f in files:
        count = reembed_scored_file(f, client)
        total += count

    info = client.get_collection(COLLECTION)
    log.info(f"Re-embedding complete: {total} points updated")
    log.info(f"Collection total: {info.points_count} points")


if __name__ == "__main__":
    import sys
    scored_file = sys.argv[1] if len(sys.argv) > 1 else None
    run_reembed(scored_file)
