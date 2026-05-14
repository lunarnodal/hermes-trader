#!/usr/bin/env python3
"""
Pipeline orchestrator
Chains: ingest → score → embed
Designed to run on a schedule via cron or manually
"""

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add pipeline root to path
sys.path.insert(0, str(Path(__file__).parent))

from feeds.ingest import run_once as ingest
from sentiment.score import run_once as score
from embedding.embed import run_once as embed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/mnt/qnap/timeseries/logs/orchestrator.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


def run_pipeline() -> None:
    start = time.time()
    log.info("═══ Pipeline run starting ═══")
    log.info(f"    {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

    # ── Stage 1: Ingest ──────────────────────────────────────────
    try:
        log.info("── Stage 1: Feed ingestion ──")
        queue_file = ingest()
        if queue_file:
            log.info(f"   Queue file: {queue_file.name}")
        else:
            log.info("   No new articles found")
    except Exception as e:
        log.error(f"   Ingestion failed: {e}")
        return

    # ── Stage 2: Score ───────────────────────────────────────────
    try:
        log.info("── Stage 2: Sentiment scoring ──")
        score()
    except Exception as e:
        log.error(f"   Scoring failed: {e}")
        return

    # ── Stage 3: Embed ───────────────────────────────────────────
    try:
        log.info("── Stage 3: Qdrant embedding ──")
        embed()
    except Exception as e:
        log.error(f"   Embedding failed: {e}")
        return

    elapsed = time.time() - start
    log.info(f"═══ Pipeline complete in {elapsed:.1f}s ═══")


if __name__ == "__main__":
    run_pipeline()
