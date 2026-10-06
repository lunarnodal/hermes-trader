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

Sector-trim gate (calibration cards):
    Cards whose title/body mention calibration-related terms and target
    a known sector are checked against the sector_trim LTFT table.
    If the sector's LTFT <= -0.10 the card is skipped (manifest entry
    carries "skipped": true). Skipped cards are NOT frozen: because
    card_exists() only matches on card_id, a re-proposal on a later
    day WILL be re-evaluated by the gate (and skipped again if still
    trimmed). This prevents duplicate calibration work when the nightly
    trim loop already covers the sector.
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

DEFAULT_REPORT_DIR = "/mnt/qnap/timeseries/reports/causality"
MANIFEST_PATH = os.path.join(DEFAULT_REPORT_DIR, "kanban_manifest.json")
REMOTE_MANIFEST_DIR = "/home/sam/.hermes/cron/causality"
REMOTE_MANIFEST_PATH = os.path.join(REMOTE_MANIFEST_DIR, "kanban_manifest.json")

# Canonical sector tokens (longest-first for matching)
CANONICAL_SECTORS = [
    "ai_infrastructure",
    "financials",
    "healthcare",
    "industrials",
    "technology",
    "materials",
    "consumer",
    "energy",
    "macro",
    "defense",
]

# Re-fire suppression window (days)
REFIRE_WINDOW_DAYS = 14

# ---------------------------------------------------------------------------
# Re-fire gate: 14-day theme-sector + direction dedup
# ---------------------------------------------------------------------------


def extract_theme(card: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (canonical_sector, direction) for a card.

    direction is 'bullish', 'bearish', or None (unknown/neutral).
    sector is None when no canonical sector token is found (fail-open).

    When sector is None the caller should treat the card as
    non-suppressible -- there is no theme to match against.
    """
    title = card.get("title", "")
    body = ""
    if card.get("evidence"):
        body += f" {card['evidence']}"
    if card.get("expected_impact"):
        body += f" {card['expected_impact']}"
    combined = f"{title} {body}"
    combined_lower = combined.lower()

    # Derive direction
    direction: str | None = None
    if "bullish" in combined_lower:
        direction = "bullish"
    elif "bearish" in combined_lower:
        direction = "bearish"

    # Derive canonical sector (longest-first for accuracy)
    sector: str | None = None
    for s in CANONICAL_SECTORS:
        pat = r"(?i)(?:^|[^a-zA-Z0-9_])" + re.escape(s) + r"(?:$|[^a-zA-Z0-9_])"
        if re.search(pat, combined):
            sector = s
            break

    return (sector, direction)


def find_refire_match(
    manifest: dict, sector: str, direction: str | None
) -> dict | None:
    """Find a manifest entry matching sector+direction within REFIRE_WINDOW_DAYS.

    An entry matches when:
      - It has theme_sector == sector (or sector is derivable from stored
        title/sectors when theme_sector is absent).
      - It is not skipped.
      - Its theme_direction matches the query direction, OR either side is
        None (direction None means 'either direction').
      - Its last_refired_at (or created_at as fallback) is within
        REFIRE_WINDOW_DAYS of now.

    Returns the matching entry dict or None.
    """
    now = datetime.now(timezone.utc)
    window = REFIRE_WINDOW_DAYS

    for entry in manifest.get("cards", []):
        if entry.get("skipped"):
            continue

        # Resolve entry's sector
        entry_sector = entry.get("theme_sector")
        if entry_sector is None:
            # Derive from stored sectors list or title
            for s in CANONICAL_SECTORS:
                for ts in entry.get("sectors", []):
                    if s in ts.lower():
                        entry_sector = s
                        break
                if entry_sector:
                    break
            if not entry_sector:
                title_lower = (entry.get("title", "") or "").lower()
                for s in CANONICAL_SECTORS:
                    if s in title_lower:
                        entry_sector = s
                        break

        if entry_sector != sector:
            continue

        # Resolve entry's direction
        entry_dir = entry.get("theme_direction")

        # Direction match: exact match, or either side is None
        if direction is not None and entry_dir is not None:
            if direction != entry_dir:
                continue

        # Timestamp check -- prefer last_refired_at, fall back to created_at
        ts_str = entry.get("last_refired_at") or entry.get("created_at")
        if ts_str is None:
            continue
        try:
            ts = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            continue
        if (now - ts).days > window:
            continue

        return entry

    return None


# ---------------------------------------------------------------------------
# Calibration-card detection & sector-trim gate
# ---------------------------------------------------------------------------


def is_calibration_card(card: dict[str, Any]) -> tuple[bool, str | None]:
    """Check if a card is a calibration card targeting a known sector.

    A card qualifies as a calibration card if its title or body matches
    one of: overconfidence, overconfident, discount, trim, sector_trim,
    Brier, or confidence within ~40 chars of calibrat|throttl.

    Returns (is_calibration, sector) where sector is the first canonical
    sector token found (case-insensitive), or None if no sector matched.
    If the calibration keywords match but no sector is found, returns
    (False, None) -- treat as a normal card.
    """
    title = card.get("title", "")
    body = ""
    if card.get("evidence"):
        body += f" {card['evidence']}"
    if card.get("expected_impact"):
        body += f" {card['expected_impact']}"
    combined = f"{title} {body}"
    combined_lower = combined.lower()

    calibration_keywords = ["overconfidence", "overconfident", "discount",
                            "trim", "sector_trim", "brier"]
    is_cal = any(kw in combined_lower for kw in calibration_keywords)

    if not is_cal:
        prox_match = re.search(
            r"confidence.{0,40}(?:calibrat|throttl)|"
            r"(?:calibrat|throttl).{0,40}confidence",
            combined_lower,
        )
        if prox_match:
            is_cal = True

    if not is_cal:
        return (False, None)

    for sector in CANONICAL_SECTORS:
        pat = r"(?i)(?:^|[^a-zA-Z0-9_])" + re.escape(sector) + r"(?:$|[^a-zA-Z0-9_])"
        if re.search(pat, combined):
            log.info("Calibration card detected: %s targets sector %s",
                      card["card_id"], sector)
            return (True, sector)

    log.info("Calibration keywords in %s but no canonical sector found -- filing as normal card.",
             card["card_id"])
    return (False, None)


def get_sector_ltft(sector: str, host: str) -> float | None:
    """Query the sector_trim table for a sector's LTFT value.

    Runs an SSH command to hit the paper_trading.db directly.
    Returns the LTFT as a float, or None on any failure (fail-open).

    This function is designed to be monkeypatchable for tests.
    """
    if sector not in CANONICAL_SECTORS:
        log.warning("get_sector_ltft called with non-canonical sector '%s' -- returning None.", sector)
        return None

    cmd = [
        "ssh", host,
        f'sqlite3 /home/trading/trading-ai/data/paper_trading.db '
        f'"SELECT ROUND(ltft,3) FROM sector_trim WHERE sector=\"{sector}\";"',
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            log.warning("LTFT lookup failed for %s (exit=%d): %s",
                        sector, result.returncode, result.stderr.strip()[-200:])
            return None
        raw = result.stdout.strip()
        if not raw:
            log.warning("LTFT lookup returned empty for %s -- no row found.", sector)
            return None
        try:
            return float(raw)
        except ValueError:
            log.warning("LTFT lookup for %s returned non-float '%s'.", sector, raw)
            return None
    except subprocess.TimeoutExpired:
        log.warning("LTFT lookup timed out for %s -- filing card as-is (fail-open).", sector)
        return None
    except FileNotFoundError:
        log.warning("LTFT lookup: ssh not found -- filing card as-is (fail-open).")
        return None
    except Exception as e:
        log.warning("LTFT lookup failed for %s (%s) -- filing card as-is (fail-open).", sector, e)
        return None


def _check_ranking_claim_date(card: dict[str, Any]) -> None:
    """Log a warning if a card cites a ranking claim without a snapshot date.

    If the card body contains worst and no ISO date token (YYYY-MM-DD) appears,
    logs a warning. This is informational only -- does NOT skip the card.
    """
    body_parts = []
    if card.get("evidence"):
        body_parts.append(card["evidence"])
    if card.get("expected_impact"):
        body_parts.append(card["expected_impact"])
    body_text = " ".join(body_parts)

    has_worst = bool(re.search(r"worst", body_text, re.IGNORECASE))
    has_date = bool(re.search(r"\d{4}-\d{2}-\d{2}", body_text))

    if has_worst and not has_date:
        log.warning(
            "Ranking claim in %s cites no snapshot date -- check it is computed over >=30d, not a 48h window.",
            card["card_id"],
        )


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
    """Check if card_id already in manifest or in any entry's refired_card_ids."""
    for entry in manifest.get("cards", []):
        if entry.get("card_id") == card_id:
            return True
        if card_id in entry.get("refired_card_ids", []):
            return True
    return False


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

    # NOTE: `hermes kanban create` takes the title as a POSITIONAL arg,
    # not a --title flag (verified against the live CLI 2026-09-18).
    cmd = [
        "hermes", "kanban", "create",
        title,
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
                  report_text: str | None = None, report_file: str | None = None,
                  host: str = "trading@172.29.10.225") -> dict:
    """Ingest a single causality report.

    If report_text is provided, parse from text instead of reading a file
    (used by --remote mode).
    The ``host`` kwarg is used for the sector-trim LTFT lookup; defaults
    to the airig box.
    Returns summary: {created: N, skipped: N, errors: N, ungated: N, refired: N}
    """
    manifest_path = os.path.expanduser(manifest_path)

    if report_text is None:
        report_path = os.path.expanduser(report_path)
        if not os.path.exists(report_path):
            log.error("Report not found: %s", report_path)
            return {"created": 0, "skipped": 0, "errors": 1, "ungated": 0, "refired": 0}
        with open(report_path, "r") as f:
            text = f.read()
        if report_file is None:
            report_file = os.path.basename(report_path)
    else:
        text = report_text

    cards = parse_cards_section(text)
    if not cards:
        log.info("No cards to ingest from %s", report_file)
        return {"created": 0, "skipped": 0, "errors": 0, "ungated": 0, "refired": 0}

    manifest = load_manifest(manifest_path)

    created = 0
    skipped = 0
    errors = 0
    ungated = 0
    refired = 0

    for card in cards:
        card_id = card["card_id"]

        # Dedup
        if card_exists(manifest, card_id):
            log.info("Skipping %s — already in manifest.", card_id)
            skipped += 1
            continue

        # --- Re-fire gate: 14-day theme-sector + direction window ---
        card_sector, card_direction = extract_theme(card)
        if card_sector is not None:
            match = find_refire_match(manifest, card_sector, card_direction)
            if match is not None:
                now_iso = datetime.now(timezone.utc).isoformat()
                if dry_run:
                    log.info("[DRY RUN] Would re-fire stamp: %s -> %s (count=%d)",
                             card_id, match["card_id"],
                             match.get("refired_count", 0) + 1)
                else:
                    match["last_refired_at"] = now_iso
                    match["refired_count"] = match.get("refired_count", 0) + 1
                    match.setdefault("refired_card_ids", []).append(card_id)
                    log.info("Re-fire: %s stamped onto %s (count=%d)",
                             card_id, match["card_id"], match["refired_count"])
                refired += 1
                continue

        # Ranking-claim date check (informational warning only)
        _check_ranking_claim_date(card)

        # Sector-trim gate: skip calibration cards for already-trimmed sectors
        is_cal, cal_sector = is_calibration_card(card)
        if is_cal and cal_sector is not None:
            ltft = get_sector_ltft(cal_sector, host)
            if ltft is not None and ltft <= -0.10:
                reason = (f"already trimmed at {ltft} by sector_trim "
                           "(auto-calibration covers it)")
                manifest["cards"].append({
                    "card_id": card_id,
                    "title": card["title"],
                    "skipped": True,
                    "reason": reason,
                    "report_file": report_file,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                if dry_run:
                    log.info("[DRY RUN] Would skip card: %s — %s", card_id, reason)
                else:
                    log.info("Skipping %s — sector_trim gate: %s LTFT=%s <= -0.10.",
                             card_id, cal_sector, ltft)
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

            # Record in manifest — include theme_sector / theme_direction
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
                "theme_sector": card_sector,
                "theme_direction": card_direction,
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

    log.info("Summary: created=%d skipped=%d errors=%d ungated=%d refired=%d (dry_run=%s)",
             created, skipped, errors, ungated, refired, dry_run)
    return {"created": created, "skipped": skipped, "errors": errors, "ungated": ungated, "refired": refired}


def ingest_all(dry_run: bool = False) -> dict:
    """Scan all causality reports and ingest new cards."""
    report_dir = DEFAULT_REPORT_DIR
    if not os.path.isdir(report_dir):
        log.error("Report dir not found: %s", report_dir)
        return {"created": 0, "skipped": 0, "errors": 1, "ungated": 0, "refired": 0}

    reports = sorted(
        Path(report_dir).glob("causality_report_*.md"),
        key=lambda p: p.name,
    )

    totals = {"created": 0, "skipped": 0, "errors": 0, "ungated": 0, "refired": 0}
    for rp in reports:
        r = ingest_report(str(rp), dry_run=dry_run)
        totals["created"] += r["created"]
        totals["skipped"] += r["skipped"]
        totals["errors"] += r["errors"]
        totals["ungated"] += r.get("ungated", 0)
        totals["refired"] += r.get("refired", 0)

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
            host=args.host,
        )
    elif args.report:
        result = ingest_report(args.report, args.manifest or MANIFEST_PATH, args.dry_run)
    else:
        result = ingest_all(args.dry_run)

    # Exit with error code if any failures
    sys.exit(1 if result["errors"] > 0 else 0)


if __name__ == "__main__":
    main()
