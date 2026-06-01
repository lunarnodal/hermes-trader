#!/usr/bin/env python3
"""
Qdrant embedding pipeline
Reads scored signal files, generates BGE-M3 embeddings via Ollama,
upserts into Qdrant with full signal metadata as payload
"""

import json
import os
import logging
import requests
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, UpdateStatus
)

# ─── Config ───────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")

QDRANT_HOST   = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT   = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION    = os.getenv("QDRANT_COLLECTION", "trading_signals")
OLLAMA_HOST   = os.getenv("OLLAMA_HOST", "http://172.29.10.225:11434")
EMBED_MODEL   = "bge-m3"
VECTOR_SIZE   = 1024  # BGE-M3 output dimension
TIMESERIES_DIR = Path(os.getenv("TIMESERIES_DIR", "/mnt/qnap/timeseries/signals"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/mnt/qnap/timeseries/logs/embed.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ─── Qdrant ───────────────────────────────────────────────────────────────────

def get_client() -> QdrantClient:
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)


def ensure_collection(client: QdrantClient) -> None:
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION not in existing:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(
                size=VECTOR_SIZE,
                distance=Distance.COSINE
            )
        )
        log.info(f"Created Qdrant collection: {COLLECTION}")
    else:
        log.info(f"Collection exists: {COLLECTION}")


def signal_to_point_id(guid) -> str:
    """Stable UUID-format point ID from article guid"""
    h = hashlib.md5(str(guid).encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"

# ─── Embeddings ───────────────────────────────────────────────────────────────

def embed_text(text: str) -> list[float] | None:
    """Generate BGE-M3 embedding via Ollama"""
    try:
        resp = requests.post(
            f"{OLLAMA_HOST}/api/embed",
            json={"model": EMBED_MODEL, "input": text},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = data.get("embeddings", [])
        if embeddings:
            return embeddings[0]
        return None
    except Exception as e:
        log.error(f"Embedding failed: {e}")
        return None


def build_embed_text(signal: dict) -> str:
    """Construct text for embedding — title + summary + tickers + sectors"""
    parts = [signal.get("title", "")]
    if signal.get("summary"):
        parts.append(signal["summary"])
    if signal.get("tickers"):
        parts.append("Tickers: " + " ".join(signal["tickers"]))
    if signal.get("sectors"):
        parts.append("Sectors: " + " ".join(signal["sectors"]))
    return " | ".join(p for p in parts if p)

# ─── Processing ───────────────────────────────────────────────────────────────

def find_unembedded() -> list[Path]:
    if not TIMESERIES_DIR.exists():
        return []
    scored   = sorted(TIMESERIES_DIR.glob("scored_*.jsonl"))
    embedded = {p.stem.replace("embedded_", "scored_")
                for p in TIMESERIES_DIR.glob("embedded_*.jsonl")}
    return [f for f in scored if f.stem not in embedded]


def process_scored_file(scored_file: Path, client: QdrantClient) -> int:
    signals = []
    with scored_file.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    signals.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not signals:
        return 0

    log.info(f"Embedding {len(signals)} signals from {scored_file.name}")
    points   = []
    embedded = []

    for i, signal in enumerate(signals):
        text      = build_embed_text(signal)
        vector    = embed_text(text)

        if vector is None:
            log.warning(f"  Skipping {signal['guid'][:12]} — embedding failed")
            continue

        point_id  = signal_to_point_id(signal["guid"])

        payload = {
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

        points.append(PointStruct(
            id=point_id,
            vector=vector,
            payload=payload
        ))
        embedded.append(signal)

        if i % 10 == 0:
            log.info(f"  [{i+1}/{len(signals)}] embedded: {signal['title'][:50]}")

    # Batch upsert to Qdrant
    if points:
        result = client.upsert(
            collection_name=COLLECTION,
            points=points
        )
        log.info(f"  Upserted {len(points)} points — status: {result.status}")

    # Write embedded marker file
    out_name = scored_file.name.replace("scored_", "embedded_")
    out_path = TIMESERIES_DIR / out_name
    with out_path.open("w") as f:
        for signal in embedded:
            f.write(json.dumps(signal) + "\n")

    return len(points)


def run_once() -> None:
    log.info("─── Embedding run starting ───")
    client = get_client()
    ensure_collection(client)

    files = find_unembedded()
    if not files:
        log.info("No unembedded scored files found")
        return

    total = 0
    for scored_file in files:
        count = process_scored_file(scored_file, client)
        total += count
        log.info(f"  {scored_file.name}: {count} points upserted")

    # Verify collection count
    info = client.get_collection(COLLECTION)
    log.info(f"─── Embedding complete: {total} new points ───")
    log.info(f"    Collection total: {info.points_count} points")


if __name__ == "__main__":
    run_once()
