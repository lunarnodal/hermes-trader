#!/usr/bin/env python3
"""
ingest_cards.py — Parse causality reports and create Kanban cards.

Reads the '## Kanban Cards to Create' section from a causality report
markdown file, deduplicates against a manifest, and creates inert
Kanban cards (sticky-blocked) via the hermes kanban CLI.

Execution model:
    This script RUNS ON THE HERMES VM (172.29.10.220), where the hermes
    CLI and kanban.db live. The report directory and production copy of
    this script are on airig (/home/trading/trading-ai/), reachable as
    trading@172.29.10.225. There is NO hermes binary on airig.

    Cron trigger (always runs current prod copy with --remote):
        ssh trading@172.29.10.225 "cat /home/trading/trading-ai/pipeline/causality/ingest_cards.py" \
            > /tmp/ingest_cards.py && python3 /tmp/ingest_cards.py --remote

Usage:
    python ingest_cards.py --remote [--host trading@172.29.10.225] [--dry-run]
    python ingest_cards.py --report /path/to/causality_report_YYYY-MM-DD.md [--dry-run]
    python ingest_cards.py --dry-run  # scan all reports in default dir

Manifest:
    --remote: /home/sam/.hermes/cron/causality/kanban_manifest.json
    local:    /home/trading/trading-ai/reports/causality/kanban_manifest.json
    — Atomic write (tmp + rename)
    — Skips cards whose card_id already exists
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest_cards")

DEFAULT_REPORT_DIR = "/home/trading/trading-ai/reports/causality"
MANIFEST_PATH = os.path.join(DEFAULT_REPORT_DIR, "kanban_manifest.json")
REMOTE_MANIFEST_DIR = "/home/sam/.hermes/cron/causality"
REMOTE_MANIFEST_PATH = os.path.join(REMOTE_MANIFEST_DIR, "kanban_manifest.json")

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_cards_section(text: str) -> list[dict[str, Any]]:
    """Extract cards from the '## Kanban Cards to Create' section.

    Supports three formats found in causality reports:

    1. Inline with backticks (current format):
       1. `card-id-slug` — **Title** (Risk: low | Sectors: a, b | Owner: x)
          - Evidence: ...
          - Expected impact: ...

    2. Inline without backticks (fallback):
       1. card-id-slug — **Title** (Risk: low | Sectors: a, b | Owner: x)

    3. Legacy block format:
       1. `card-id-slug`
          - Title: ...
          - Risk: ...
          - Sectors: ...
    """
    section_match = re.search(
        r"##\s+Kanban Cards to Create\s*\n(.*?)(?=## |\Z)",
        text,
        re.DOTALL,
    )
    if not section_match:
        log.info("No 'Kanban Cards to Create' section found.")
        return []

    section = section_match.group(1)
    cards: list[dict[str, Any]] = []

    # Split into numbered items — handles "1. `slug` — ..."
    items = re.split(r"\n(?=\d+\.\s)", section.strip())

    for item in items:
        card = _parse_item(item)
        if card:
            cards.append(card)

    return cards


def _parse_item(item: str) -> dict[str, Any] | None:
    """Parse a single numbered card item."""
    # Strip leading number + period
    body = re.sub(r"^\d+\.\s*", "", item.strip())
    if not body:
        return None

    # --- Format 1 & 2: inline ---
    # Try: `slug` — **Title** (Risk: ... | Sectors: ... | Owner: ...)
    inline_match = re.match(
        r"`([^`]+)`\s*—\s*\*\*(.+?)\*\*\s*\(([^)]+)\)",
        body,
    )
    # Fallback without backticks
    if not inline_match:
        inline_match = re.match(
            r"([a-z0-9_-]+)\s*—\s*\*\*(.+?)\*\*\s*\(([^)]+)\)",
            body,
        )

    if inline_match:
        slug = inline_match.group(1)
        title = inline_match.group(2).strip()
        meta_raw = inline_match.group(3)

        card = {
            "card_id": slug,
            "title": title,
            "risk": _extract_field(meta_raw, "Risk"),
            "sectors": _extract_list(meta_raw, "Sectors"),
            "owner": _extract_field(meta_raw, "Owner"),
            "evidence": "",
            "expected_impact": "",
        }

        # Extract sub-bullets (Evidence, Expected impact, Urgency, etc.)
        lines = body.split("\n")[1:]
        for line in lines:
            line_stripped = line.strip().lstrip("- ").strip()
            if line_stripped.startswith("Evidence:"):
                card["evidence"] = line_stripped[len("Evidence:"):].strip()
            elif line_stripped.startswith("Expected impact:"):
                card["expected_impact"] = line_stripped[len("Expected impact:"):].strip()

        return card

    # --- Format 3: legacy block ---
    slug_match = re.search(r"`([^`]+)`", body)
    if not slug_match:
        slug_match = re.match(r"([a-z0-9_-]+)", body)

    if slug_match:
        card = {"card_id": slug_match.group(1), "title": "", "risk": "", "sectors": [], "owner": ""}
        for line in body.split("\n"):
            line_stripped = line.strip().lstrip("- ").strip()
            if line_stripped.startswith("Title:"):
                card["title"] = line_stripped[len("Title:"):].strip()
            elif line_stripped.startswith("Risk:"):
                card["risk"] = line_stripped[len("Risk:"):].strip()
            elif line_stripped.startswith("Sectors:"):
                card["sectors"] = [s.strip() for s in line_stripped[len("Sectors:"):].split(",")]
            elif line_stripped.startswith("Owner:"):
                card["owner"] = line_stripped[len("Owner:"):].strip()
        return card if card["title"] else None

    return None


def _extract_field(meta: str, key: str) -> str:
    """Extract a key-value pair from inline metadata like 'Risk: low | Sectors: a, b'."""
    pattern = rf"{key}:\s*([^|]+)"
    m = re.search(pattern, meta)
    return m.group(1).strip() if m else ""


def _extract_list(meta: str, key: str) -> list[str]:
    """Extract a comma-separated list from inline metadata."""
    val = _extract_field(meta, key)
    return [s.strip() for s in val.split(",") if s.strip()] if val else []


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def load_manifest(path: str) -> dict:
    """Load manifest, returning default structure on missing/corrupt file."""
    if not os.path.exists(path):
        log.info("No manifest at %s — starting fresh.", path)
        return {"cards": [], "last_scan": None}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Manifest corrupt or unreadable (%s) — starting fresh.", e)
        return {"cards": [], "last_scan": None}


def save_manifest(manifest: dict, path: str) -> None:
    """Atomically write manifest via tmp + rename."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(manifest, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        os.unlink(tmp_path)
        raise


def card_exists(manifest: dict, card_id: str) -> bool:
    """Check if card_id already in manifest."""
    return any(c.get("card_id") == card_id for c in manifest.get("cards", []))


# ---------------------------------------------------------------------------
# Remote fetch
# ---------------------------------------------------------------------------


def fetch_remote_report(host: str, report_dir: str) -> tuple[str, str] | None:
    """Fetch the newest causality report from a remote host via SSH.

    Returns (filename, text) or None on failure.
    Designed to be monkeypatchable for tests — NO test should open a real SSH connection.
    """
    ls_cmd = f"ls -1 {report_dir}/causality_report_*.md 2>/dev/null | sort | tail -1"
    result = subprocess.run(
        ["ssh", host, ls_cmd], capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        log.error("No reports found on remote %s: %s", host, result.stderr.strip())
        return None

    remote_path = result.stdout.strip()
    filename = os.path.basename(remote_path)

    cat_cmd = f"cat {remote_path}"
    result = subprocess.run(
        ["ssh", host, cat_cmd], capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        log.error("Failed to fetch %s from %s: %s", filename, host, result.stderr.strip())
        return None

    log.info("Fetched %s from %s", filename, host)
    return (filename, result.stdout)


# ---------------------------------------------------------------------------
# Kanban creation
# ---------------------------------------------------------------------------


def create_kanban_card(card: dict, report_file: str) -> dict | None:
    """Create a Kanban card via hermes kanban create CLI.

    Returns {"task_id": str, "gated": bool} on success, None on failure.
    After creation, reclaims and sticky-blocks the card so it does not
    auto-promote to ready until an operator explicitly unblocks it.
    """
    title = card.get("title", "Causality finding")
    card_id = card["card_id"]
    risk = card.get("risk", "low")
    sectors = ", ".join(card.get("sectors", []))
    owner = card.get("owner", "orchestrator")
    evidence = card.get("evidence", "")
    impact = card.get("expected_impact", "")

    body = (
        f"**Source:** Causality report auto-ingestion\n"
        f"**Card ID:** {card_id}\n"
        f"**Risk:** {risk}\n"
        f"**Sectors:** {sectors}\n"
        f"**Owner:** {owner}\n"
    )
    if evidence:
        body += f"**Evidence:** {evidence}\n"
    if impact:
        body += f"**Expected impact:** {impact}\n"

    # idempotency_key prevents duplicates if this script reruns
    idempotency_key = f"causality-ingest-{card_id}"

    cmd = [
        "hermes", "kanban", "create",
        "--title", title,
        "--body", body,
        "--assignee", "orchestrator",
        "--idempotency-key", idempotency_key,
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.error("hermes kanban create failed: %s", result.stderr.strip())
            return None

        # Extract task id from output (e.g., "Created task t_abcdef123456")
        out = result.stdout.strip()
        task_match = re.search(r"t_[a-f0-9]+", out)
        task_id = task_match.group(0) if task_match else out
        log.info("Created card %s -> task %s", card_id, task_id)

        # --- Sticky-block approval gate ---
        gated = True

        # 1. Reclaim (clears any claim landed between create and block)
        try:
            result = subprocess.run(
                ["hermes", "kanban", "reclaim", task_id, "--reason", "pre-block reclaim"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                log.warning("Reclaim failed for %s: %s", task_id, result.stderr.strip())
        except subprocess.TimeoutExpired:
            log.warning("Reclaim timed out for %s", task_id)
        except FileNotFoundError:
            log.warning("hermes CLI not found for reclaim")
        except Exception as e:
            log.warning("Reclaim error for %s: %s", task_id, e)

        # 2. Sticky-block (operator-only to lift)
        try:
            result = subprocess.run(
                ["hermes", "kanban", "block", task_id,
                 "APPROVAL GATE (ingest): awaiting operator review"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                log.error("Block failed for %s: %s", task_id, result.stderr.strip())
                gated = False
        except subprocess.TimeoutExpired:
            log.error("Block timed out for %s", task_id)
            gated = False
        except FileNotFoundError:
            log.error("hermes CLI not found for block")
            gated = False
        except Exception as e:
            log.error("Block error for %s: %s", task_id, e)
            gated = False

        log.info("Card %s (%s) gated=%s", card_id, task_id, gated)
        return {"task_id": task_id, "gated": gated}
    except subprocess.TimeoutExpired:
        log.error("hermes kanban create timed out for %s", card_id)
        return None
    except FileNotFoundError:
        log.error("hermes CLI not found — is Hermes installed in PATH?")
        return None


# ---------------------------------------------------------------------------
# Main ingestion logic
# ---------------------------------------------------------------------------


def ingest_report(report_path: str, manifest_path: str = MANIFEST_PATH, dry_run: bool = False,
                  report_text: str | None = None, report_file: str | None = None) -> dict:
    """Ingest a single causality report.

    If report_text is provided, parse from text instead of reading a file
    (used by --remote mode).
    Returns summary: {created: N, skipped: N, errors: N, ungated: N}
    """
    manifest_path = os.path.expanduser(manifest_path)

    if report_text is None:
        report_path = os.path.expanduser(report_path)
        if not os.path.exists(report_path):
            log.error("Report not found: %s", report_path)
            return {"created": 0, "skipped": 0, "errors": 1, "ungated": 0}
        with open(report_path, "r") as f:
            text = f.read()
        if report_file is None:
            report_file = os.path.basename(report_path)
    else:
        text = report_text

    cards = parse_cards_section(text)
    if not cards:
        log.info("No cards to ingest from %s", report_file)
        return {"created": 0, "skipped": 0, "errors": 0, "ungated": 0}

    manifest = load_manifest(manifest_path)

    created = 0
    skipped = 0
    errors = 0
    ungated = 0

    for card in cards:
        card_id = card["card_id"]

        # Dedup
        if card_exists(manifest, card_id):
            log.info("Skipping %s — already in manifest.", card_id)
            skipped += 1
            continue

        # Create
        if not dry_run:
            result = create_kanban_card(card, report_file)
            if result is None:
                errors += 1
                continue

            task_id = result["task_id"]
            gated = result["gated"]

            if not gated:
                ungated += 1

            # Record in manifest
            entry = {
                "card_id": card_id,
                "title": card["title"],
                "risk": card.get("risk", ""),
                "sectors": card.get("sectors", []),
                "owner": card.get("owner", ""),
                "created_task_id": task_id,
                "gated": gated,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "report_file": report_file,
            }
            manifest["cards"].append(entry)
        else:
            log.info("[DRY RUN] Would create card: %s — %s", card_id, card["title"])

        created += 1

    # Update last_scan
    manifest["last_scan"] = datetime.now(timezone.utc).isoformat()

    if not dry_run:
        save_manifest(manifest, manifest_path)
        log.info("Manifest saved to %s", manifest_path)

    log.info("Summary: created=%d skipped=%d errors=%d ungated=%d (dry_run=%s)",
             created, skipped, errors, ungated, dry_run)
    return {"created": created, "skipped": skipped, "errors": errors, "ungated": ungated}


def ingest_all(dry_run: bool = False) -> dict:
    """Scan all causality reports and ingest new cards."""
    report_dir = DEFAULT_REPORT_DIR
    if not os.path.isdir(report_dir):
        log.error("Report dir not found: %s", report_dir)
        return {"created": 0, "skipped": 0, "errors": 1, "ungated": 0}

    reports = sorted(
        Path(report_dir).glob("causality_report_*.md"),
        key=lambda p: p.name,
    )

    totals = {"created": 0, "skipped": 0, "errors": 0, "ungated": 0}
    for rp in reports:
        r = ingest_report(str(rp), dry_run=dry_run)
        totals["created"] += r["created"]
        totals["skipped"] += r["skipped"]
        totals["errors"] += r["errors"]
        totals["ungated"] += r.get("ungated", 0)

    log.info("All reports scanned. Totals: %s", totals)
    return totals


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Ingest causality report findings as Kanban cards. "
                    "Runs on Hermes VM (172.29.10.220); fetches reports from airig via SSH when --remote."
    )
    parser.add_argument(
        "--report",
        help="Path to a single causality report markdown file.",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Path to kanban_manifest.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and log what would be created without actually creating cards.",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        help="Fetch the newest report from airig via SSH (default execution mode).",
    )
    parser.add_argument(
        "--host",
        default="trading@172.29.10.225",
        help="SSH host for --remote mode (default: %(default)s).",
    )

    args = parser.parse_args()

    if args.remote:
        manifest_path = args.manifest or REMOTE_MANIFEST_PATH
        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)

        fetched = fetch_remote_report(args.host, DEFAULT_REPORT_DIR)
        if fetched is None:
            log.error("No remote report fetched — aborting.")
            sys.exit(1)

        filename, text = fetched
        result = ingest_report(
            report_path="",
            manifest_path=manifest_path,
            dry_run=args.dry_run,
            report_text=text,
            report_file=filename,
        )
    elif args.report:
        result = ingest_report(args.report, args.manifest or MANIFEST_PATH, args.dry_run)
    else:
        result = ingest_all(args.dry_run)

    # Exit with error code if any failures
    sys.exit(1 if result["errors"] > 0 else 0)


if __name__ == "__main__":
    main()
