#!/usr/bin/env python3
"""
test_ingest.py — Dry-run tests for ingest_cards.py.

Verifies parsing of all three report formats, dedup logic, manifest
write behavior, sticky-block gate, and remote fetch — without creating
actual Kanban cards or opening real SSH connections.

Run:
    python test_ingest.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

# Allow importing ingest_cards from the same directory
sys.path.insert(0, os.path.dirname(__file__) or ".")
from ingest_cards import (
    parse_cards_section,
    load_manifest,
    save_manifest,
    card_exists,
    ingest_report,
    create_kanban_card,
    fetch_remote_report,
    DEFAULT_REPORT_DIR,
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
# Test 9: Sticky-block gate — reclaim + block invoked with exact args
# -----------------------------------------------------------------------
def test_sticky_block_gate():
    print("\nTest 9: Sticky-block gate (reclaim + block with exact args)")

    call_log = []

    def fake_run(cmd, **kwargs):
        call_log.append(cmd)
        task_id = "t_f9a8b7c6"
        if cmd[2] == "create":
            return mock.Mock(returncode=0, stdout=f"Created task {task_id}\n")
        elif cmd[2] == "reclaim":
            return mock.Mock(returncode=0, stdout="Reclaimed\n", stderr="")
        elif cmd[2] == "block":
            return mock.Mock(returncode=0, stdout="Blocked\n", stderr="")
        return mock.Mock(returncode=1, stdout="", stderr="unexpected")

    card = {
        "card_id": "test-gate",
        "title": "Test Gate Card",
        "risk": "low",
        "sectors": ["pipeline"],
        "owner": "orchestrator",
        "evidence": "test evidence",
        "expected_impact": "test impact",
    }

    with mock.patch("subprocess.run", side_effect=fake_run):
        result = create_kanban_card(card, "test.md")

    check("Returns dict with task_id and gated",
          isinstance(result, dict) and "task_id" in result and "gated" in result)
    if result:
        check("task_id returned", result["task_id"] == "t_f9a8b7c6")
        check("gated is True", result["gated"] is True)

    # Verify create called
    check("create called", any(c[2] == "create" for c in call_log))

    # Verify reclaim called with exact args
    reclaim_calls = [c for c in call_log if c[2] == "reclaim"]
    check("reclaim called once", len(reclaim_calls) == 1)
    if reclaim_calls:
        rc = reclaim_calls[0]
        check("reclaim task_id arg", rc[3] == "t_f9a8b7c6")
        check("reclaim --reason flag", "--reason" in rc)
        check("reclaim reason value", "pre-block reclaim" in rc)

    # Verify block called with exact args (reason is POSITIONAL, not --reason)
    block_calls = [c for c in call_log if c[2] == "block"]
    check("block called once", len(block_calls) == 1)
    if block_calls:
        bc = block_calls[0]
        check("block task_id arg", bc[3] == "t_f9a8b7c6")
        check("block reason is positional", bc[4] == "APPROVAL GATE (ingest): awaiting operator review")
        check("block reason NOT --reason flag", "--reason" not in bc)


# -----------------------------------------------------------------------
# Test 10: Block failure — ERROR log + digest shows ungated
# -----------------------------------------------------------------------
def test_block_fail_ungated():
    print("\nTest 10: Block failure -> ungated=True")

    call_log = []

    def fake_run(cmd, **kwargs):
        call_log.append(cmd)
        task_id = "t_f1a2b3c4"
        if cmd[2] == "create":
            return mock.Mock(returncode=0, stdout=f"Created task {task_id}\n")
        elif cmd[2] == "reclaim":
            return mock.Mock(returncode=0, stdout="Reclaimed\n", stderr="")
        elif cmd[2] == "block":
            return mock.Mock(returncode=1, stdout="", stderr="block failed: card locked")
        return mock.Mock(returncode=1, stdout="", stderr="unexpected")

    card = {
        "card_id": "test-ungated",
        "title": "Test Ungated Card",
        "risk": "medium",
        "sectors": ["rules"],
        "owner": "orchestrator",
        "evidence": "",
        "expected_impact": "",
    }

    with mock.patch("subprocess.run", side_effect=fake_run):
        result = create_kanban_card(card, "test.md")

    check("Result not None (create succeeded)", result is not None)
    if result:
        check("gated is False", result["gated"] is False)
        check("task_id still returned", result["task_id"] == "t_f1a2b3c4")


# -----------------------------------------------------------------------
# Test 11: Idempotent re-run skips (create returns idempotent, no re-create)
# -----------------------------------------------------------------------
def test_idempotent_rerun():
    print("\nTest 11: Idempotent re-run skips (manifest has card)")

    with tempfile.TemporaryDirectory() as td:
        manifest = os.path.join(td, "manifest.json")
        # Pre-populate manifest
        pre = {
            "cards": [{"card_id": "already-ingested", "title": "Already done"}],
            "last_scan": "2026-09-17",
        }
        save_manifest(pre, manifest)

        report_text = """
## Kanban Cards to Create
1. `already-ingested` — **Duplicate card** (Risk: low | Sectors: pipeline | Owner: orchestrator)
"""
        result = ingest_report(
            report_path="",
            manifest_path=manifest,
            dry_run=False,
            report_text=report_text,
            report_file="test.md",
        )

        check("Skipped 1 (already in manifest)", result["skipped"] == 1, f"skipped={result['skipped']}")
        check("Created 0", result["created"] == 0, f"created={result['created']}")
        check("Errors 0", result["errors"] == 0, f"errors={result['errors']}")


# -----------------------------------------------------------------------
# Test 12: fetch_remote_report via mocked transport picks newest report
# -----------------------------------------------------------------------
def test_fetch_remote_report():
    print("\nTest 12: fetch_remote_report (mocked transport)")

    def fake_ssh(cmd, **kwargs):
        """Mock SSH subprocess.run — ls returns newest, cat returns content."""
        if isinstance(cmd, list):
            # cmd = ["ssh", host, ls_cmd] or ["ssh", host, cat_cmd]
            host = cmd[1] if len(cmd) > 1 else ""
            shell_cmd = cmd[2] if len(cmd) > 2 else ""
        else:
            shell_cmd = cmd
            host = ""

        # LS command — return newest file
        if "ls" in shell_cmd and "tail" in shell_cmd:
            return mock.Mock(
                returncode=0,
                stdout="/home/trading/trading-ai/reports/causality/causality_report_2026-09-17.md\n",
                stderr="",
            )
        # CAT command — return report content
        elif "cat" in shell_cmd:
            content = """# Causality Report 2026-09-17

## Kanban Cards to Create
1. `mock-remote-card` — **Mock remote card** (Risk: high | Sectors: portfolio | Owner: coder)
"""
            return mock.Mock(returncode=0, stdout=content, stderr="")
        else:
            return mock.Mock(returncode=1, stdout="", stderr="unknown command")

    with mock.patch("subprocess.run", side_effect=fake_ssh):
        result = fetch_remote_report("trading@172.29.10.225", DEFAULT_REPORT_DIR)

    check("Returns tuple", result is not None and isinstance(result, tuple) and len(result) == 2)
    if result:
        filename, text = result
        check("Filename is newest report", filename == "causality_report_2026-09-17.md")
        check("Report text contains section", "## Kanban Cards to Create" in text)
        check("Report text contains mock card", "mock-remote-card" in text)


# -----------------------------------------------------------------------
# Test 13: fetch_remote_report — no reports found returns None
# -----------------------------------------------------------------------
def test_fetch_remote_no_reports():
    print("\nTest 13: fetch_remote_report — no reports returns None")

    def fake_ssh_empty(cmd, **kwargs):
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_ssh_empty):
        result = fetch_remote_report("trading@172.29.10.225", DEFAULT_REPORT_DIR)

    check("Returns None when no reports", result is None)


# -----------------------------------------------------------------------
# Test 14: Ingest with ungated cards — summary includes ungated count
# -----------------------------------------------------------------------
def test_ingest_ungated_summary():
    print("\nTest 14: Ingest summary includes ungated count")

    call_log = []

    def fake_run(cmd, **kwargs):
        call_log.append(cmd)
        task_id = "t_a5b6c7d8"
        if cmd[2] == "create":
            return mock.Mock(returncode=0, stdout=f"Created task {task_id}\n")
        elif cmd[2] == "reclaim":
            return mock.Mock(returncode=0, stdout="OK\n", stderr="")
        elif cmd[2] == "block":
            # Simulate block failure
            return mock.Mock(returncode=1, stdout="", stderr="lock conflict")
        return mock.Mock(returncode=1, stdout="", stderr="unexpected")

    report_text = """
## Kanban Cards to Create
1. `ungated-test` — **Ungated test** (Risk: low | Sectors: pipeline | Owner: orchestrator)
"""

    with tempfile.TemporaryDirectory() as td:
        manifest = os.path.join(td, "manifest.json")
        with mock.patch("subprocess.run", side_effect=fake_run):
            result = ingest_report(
                report_path="",
                manifest_path=manifest,
                dry_run=False,
                report_text=report_text,
                report_file="test.md",
            )

    check("ungated key in result", "ungated" in result)
    check("ungated count is 1", result.get("ungated") == 1, f"ungated={result.get('ungated')}")
    check("created count is 1", result["created"] == 1)
    check("errors count is 0", result["errors"] == 0)


# -----------------------------------------------------------------------
# Test 15: Inline without backticks format
# -----------------------------------------------------------------------
def test_parse_inline_no_backtick():
    print("\nTest 15: Parse inline without backticks (fallback)")
    text = """
## Kanban Cards to Create
1. no-backtick-card — **Fallback format test** (Risk: low | Sectors: test | Owner: coder)
"""
    cards = parse_cards_section(text)
    check("Found 1 card", len(cards) == 1)
    if cards:
        check("card_id matches", cards[0]["card_id"] == "no-backtick-card")
        check("title matches", "Fallback format test" in cards[0]["title"])


# -----------------------------------------------------------------------
# Test 16: ingest_all with remote-style text
# -----------------------------------------------------------------------
def test_ingest_all_text():
    print("\nTest 16: ingest_report with report_text (remote mode)")
    report_text = """
# Causality Report 2026-09-18

## Kanban Cards to Create
1. `remote-card-1` — **Remote card one** (Risk: medium | Sectors: sentiment | Owner: orchestrator)
   - Evidence: signal strength dropped 15%
   - Expected impact: restore scoring accuracy
2. `remote-card-2` — **Remote card two** (Risk: high | Sectors: portfolio | Owner: coder)
"""
    with tempfile.TemporaryDirectory() as td:
        manifest = os.path.join(td, "manifest.json")
        result = ingest_report(
            report_path="",
            manifest_path=manifest,
            dry_run=True,
            report_text=report_text,
            report_file="causality_report_2026-09-18.md",
        )
        check("Parsed 2 cards", result["created"] == 2, f"created={result['created']}")
        check("No errors", result["errors"] == 0)
        check("ungated key present", "ungated" in result)


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------

def main():
    global PASS, FAIL
    print("=" * 60)
    print("ingest_cards.py — Tests (Round 2)")
    print("=" * 60)

    test_parse_inline_backtick()
    test_parse_legacy_block()
    test_parse_multiple_cards()
    test_empty_section()
    test_dedup()
    test_manifest_io()
    test_dry_run_real_report()
    test_idempotent_dry_run()
    test_sticky_block_gate()
    test_block_fail_ungated()
    test_idempotent_rerun()
    test_fetch_remote_report()
    test_fetch_remote_no_reports()
    test_ingest_ungated_summary()
    test_parse_inline_no_backtick()
    test_ingest_all_text()

    print("\n" + "=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")
    print("=" * 60)
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
