#!/usr/bin/env python3
"""
test_ingest.py — Dry-run tests for ingest_cards.py.

Verifies parsing of all three report formats, dedup logic, and manifest
write behavior without creating actual Kanban cards.

Run:
    python test_ingest.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Allow importing ingest_cards from the same directory
sys.path.insert(0, os.path.dirname(__file__) or ".")
from ingest_cards import (
    parse_cards_section,
    load_manifest,
    save_manifest,
    card_exists,
    ingest_report,
)

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)


# -----------------------------------------------------------------------
# Test 1: Parse current inline-backtick format (2026-09-17 style)
# -----------------------------------------------------------------------
def test_parse_inline_backtick():
    print("\nTest 1: Parse inline-backtick format")
    text = """
## Kanban Cards to Create
1. `fix-verification-etf-keyword-shadowing` — **Fix residual keyword shadowing in sector_etf.py** (Risk: low | Sectors: pipeline, risk | Owner: coder)
   - Evidence: live get_sector_etf() run 09-17 13:05 UTC
   - Expected impact: restores valid market/financials win rates
"""
    cards = parse_cards_section(text)
    check("Found 1 card", len(cards) == 1)
    if cards:
        check("card_id matches", cards[0]["card_id"] == "fix-verification-etf-keyword-shadowing")
        check("title matches", "Fix residual keyword shadowing" in cards[0]["title"])
        check("risk is low", cards[0]["risk"] == "low")
        check("sectors parsed", set(cards[0]["sectors"]) == {"pipeline", "risk"})
        check("owner is coder", cards[0]["owner"] == "coder")
        check("evidence extracted", "live get_sector_etf()" in cards[0]["evidence"])
        check("impact extracted", "restores valid" in cards[0]["expected_impact"])


# -----------------------------------------------------------------------
# Test 2: Parse legacy block format
# -----------------------------------------------------------------------
def test_parse_legacy_block():
    print("\nTest 2: Parse legacy block format")
    text = """
## Kanban Cards to Create
1. `test-legacy-format`
   - Title: Test legacy format parsing
   - Risk: medium
   - Sectors: rules, reasoning
   - Owner: reviewer
"""
    cards = parse_cards_section(text)
    check("Found 1 card", len(cards) == 1)
    if cards:
        check("card_id matches", cards[0]["card_id"] == "test-legacy-format")
        check("title matches", cards[0]["title"] == "Test legacy format parsing")
        check("risk is medium", cards[0]["risk"] == "medium")
        check("sectors parsed", set(cards[0]["sectors"]) == {"rules", "reasoning"})
        check("owner is reviewer", cards[0]["owner"] == "reviewer")


# -----------------------------------------------------------------------
# Test 3: Parse multiple cards in one section
# -----------------------------------------------------------------------
def test_parse_multiple_cards():
    print("\nTest 3: Parse multiple cards")
    text = """
## Kanban Cards to Create
1. `card-alpha` — **First card title** (Risk: low | Sectors: pipeline | Owner: coder)
   - Evidence: first evidence line
   - Expected impact: first impact line
2. `card-beta` — **Second card title** (Risk: medium | Sectors: rules, reasoning | Owner: orchestrator)
   - Evidence: second evidence
   - Expected impact: second impact
3. `card-gamma` — **Third card** (Risk: high | Sectors: portfolio | Owner: coder)
"""
    cards = parse_cards_section(text)
    check("Found 3 cards", len(cards) == 3)
    if len(cards) == 3:
        check("First card id", cards[0]["card_id"] == "card-alpha")
        check("Second card id", cards[1]["card_id"] == "card-beta")
        check("Third card id", cards[2]["card_id"] == "card-gamma")
        check("Third risk is high", cards[2]["risk"] == "high")


# -----------------------------------------------------------------------
# Test 4: Empty section
# -----------------------------------------------------------------------
def test_empty_section():
    print("\nTest 4: Empty / missing section")
    text = """
# Report
No cards section at all.
"""
    cards = parse_cards_section(text)
    check("Returns empty list", cards == [])

    text2 = """
## Kanban Cards to Create
"""
    cards2 = parse_cards_section(text2)
    check("Empty section returns empty list", cards2 == [])


# -----------------------------------------------------------------------
# Test 5: Deduplication via manifest
# -----------------------------------------------------------------------
def test_dedup():
    print("\nTest 5: Dedup via manifest")
    manifest = {
        "cards": [
            {"card_id": "existing-card", "title": "Already exists"},
        ],
        "last_scan": "2026-01-01",
    }
    check("Existing card detected", card_exists(manifest, "existing-card"))
    check("New card not found", not card_exists(manifest, "new-card"))
    check("Empty manifest has no cards", not card_exists({"cards": [], "last_scan": None}, "anything"))


# -----------------------------------------------------------------------
# Test 6: Manifest load / save (atomic)
# -----------------------------------------------------------------------
def test_manifest_io():
    print("\nTest 6: Manifest load/save")
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "manifest.json")

        # Load missing file
        m = load_manifest(path)
        check("Missing file returns default", m == {"cards": [], "last_scan": None})

        # Save and reload
        m["cards"].append({"card_id": "test-card", "title": "Test"})
        m["last_scan"] = "2026-09-17"
        save_manifest(m, path)
        check("File exists after save", os.path.exists(path))

        m2 = load_manifest(path)
        check("Reloaded manifest has 1 card", len(m2["cards"]) == 1)
        check("Card id preserved", m2["cards"][0]["card_id"] == "test-card")

        # Save corrupt file — should recover
        with open(path, "w") as f:
            f.write("{invalid json!!!")
        m3 = load_manifest(path)
        check("Corrupt file returns default", m3 == {"cards": [], "last_scan": None})


# -----------------------------------------------------------------------
# Test 7: Dry-run ingestion on real report
# -----------------------------------------------------------------------
def test_dry_run_real_report():
    print("\nTest 7: Dry-run on real report (09-17)")
    report = "/home/trading/trading-ai/reports/causality/causality_report_2026-09-17.md"
    if not os.path.exists(report):
        print("  SKIP: Report not found (local test)")
        return

    with tempfile.TemporaryDirectory() as td:
        manifest = os.path.join(td, "manifest.json")
        result = ingest_report(report, manifest, dry_run=True)
        check("No errors in dry run", result["errors"] == 0, f"errors={result['errors']}")
        check("Created > 0 cards", result["created"] > 0, f"created={result['created']}")


# -----------------------------------------------------------------------
# Test 8: Idempotent dry run (manifest pre-populated)
# -----------------------------------------------------------------------
def test_idempotent_dry_run():
    print("\nTest 8: Idempotent dry run")
    report = "/home/trading/trading-ai/reports/causality/causality_report_2026-09-17.md"
    if not os.path.exists(report):
        print("  SKIP: Report not found (local test)")
        return

    with tempfile.TemporaryDirectory() as td:
        manifest = os.path.join(td, "manifest.json")
        # Pre-populate with the card from 09-17
        pre = {
            "cards": [{"card_id": "fix-verification-etf-keyword-shadowing"}],
            "last_scan": "2026-09-17",
        }
        save_manifest(pre, manifest)

        result = ingest_report(report, manifest, dry_run=True)
        check("Skipped existing card", result["skipped"] == 1, f"skipped={result['skipped']}")
        check("Created 0 (all deduped)", result["created"] == 0, f"created={result['created']}")


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------

def main():
    global PASS, FAIL
    print("=" * 60)
    print("ingest_cards.py — Dry-Run Tests")
    print("=" * 60)

    test_parse_inline_backtick()
    test_parse_legacy_block()
    test_parse_multiple_cards()
    test_empty_section()
    test_dedup()
    test_manifest_io()
    test_dry_run_real_report()
    test_idempotent_dry_run()

    print("\n" + "=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")
    print("=" * 60)
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
