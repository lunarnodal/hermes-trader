#!/usr/bin/env python3
"""
Semantic signal query tool for Hermes
Usage: python3 query_qdrant.py "your query text" [limit]
"""
import sys
import json
import requests
from qdrant_client import QdrantClient

QDRANT_HOST = "172.29.10.225"
QDRANT_PORT = 6333
OLLAMA_HOST = "http://localhost:11434"
COLLECTION  = "trading_signals"

def query(query_text: str, limit: int = 10):
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    resp = requests.post(f"{OLLAMA_HOST}/api/embed",
        json={"model": "bge-m3", "input": query_text}, timeout=30)
    vector = resp.json()["embeddings"][0]

    results = client.query_points(
        collection_name=COLLECTION,
        query=vector,
        limit=limit,
        with_payload=True
    ).points

    print(f"\nTop {len(results)} signals for: '{query_text}'\n")
    print(f"{'Score':6s}  {'Sentiment':8s}  {'Conf':4s}  {'Source':25s}  Title")
    print("─" * 100)
    for r in results:
        p = r.payload
        print(f"{r.score:.3f}  {p['sentiment']:8s}  {p['confidence']:.2f}  "
              f"{p['source']:25s}  {p['title'][:50]}")
        if p.get('tickers'):
            print(f"         Tickers: {p['tickers']}")
        if p.get('summary'):
            print(f"         {p['summary'][:120]}")
        print()

if __name__ == "__main__":
    query_text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "market outlook"
    query(query_text)
