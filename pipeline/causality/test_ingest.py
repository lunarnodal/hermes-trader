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
from datetime import datetime, timedelta, timezone
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
    is_calibration_card,
    get_sector_ltft,
    _check_ranking_claim_date,
    extract_theme,
    find_refire_match,
    CANONICAL_SECTORS,
    DEFAULT_REPORT_DIR,
    REFIRE_WINDOW_DAYS,
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
    report = "/mnt/qnap/timeseries/reports/causality/causality_report_2026-09-17.md"
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
    report = "/mnt/qnap/timeseries/reports/causality/causality_report_2026-09-17.md"
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
                stdout="/mnt/qnap/timeseries/reports/causality/causality_report_2026-09-17.md\n",
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
# Test 17: Sector-trim gate — skip calibration card when LTFT <= -0.10
# -----------------------------------------------------------------------
def test_trim_gate_skip():
    print("\nTest 17: Trim gate — skip when LTFT <= -0.10")

    call_log = []

    def _cmd_str(cmd):
        return " ".join(str(c) for c in cmd) if isinstance(cmd, (list, tuple)) else str(cmd)

    def fake_run(cmd, **kwargs):
        cs = _cmd_str(cmd)
        call_log.append(cmd)
        if "sqlite3" in cs and "sector_trim" in cs:
            return mock.Mock(returncode=0, stdout="-0.187\n", stderr="")
        if "hermes" in cs and "kanban" in cs and "create" in cs:
            return mock.Mock(returncode=0, stdout="Created task t_test001\n")
        return mock.Mock(returncode=0, stdout="", stderr="")

    report_text = """
## Kanban Cards to Create
1. `fin-overconfidence-fix` — **Discount financials overconfidence** (Risk: medium | Sectors: financials | Owner: orchestrator)
   - Evidence: financials showed worst Brier score
   - Expected impact: reduce false bearish signals
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

        check("Skipped 1 (gate)", result["skipped"] == 1, f"skipped={result['skipped']}")
        check("Created 0 (gate blocked)", result["created"] == 0, f"created={result['created']}")

        m = load_manifest(manifest)
        skipped_entries = [c for c in m["cards"] if c.get("skipped")]
        check("Manifest has 1 skipped entry", len(skipped_entries) == 1)
        if skipped_entries:
            check("Skipped reason mentions -0.187", "-0.187" in skipped_entries[0].get("reason", ""))
            check("Skipped reason mentions sector_trim", "sector_trim" in skipped_entries[0].get("reason", ""))

    create_calls = [c for c in call_log if "create" in _cmd_str(c)]
    check("hermes kanban create NOT called", len(create_calls) == 0)


# -----------------------------------------------------------------------
# Test 18: Sector-trim gate — file card when LTFT > -0.10
# -----------------------------------------------------------------------
def test_trim_gate_file():
    print("\nTest 18: Trim gate — file when LTFT > -0.10")

    call_log = []

    def _cmd_str(cmd):
        return " ".join(str(c) for c in cmd) if isinstance(cmd, (list, tuple)) else str(cmd)

    def fake_run(cmd, **kwargs):
        cs = _cmd_str(cmd)
        call_log.append(cmd)
        if "sqlite3" in cs and "sector_trim" in cs:
            return mock.Mock(returncode=0, stdout="-0.02\n", stderr="")
        if "hermes" in cs and "kanban" in cs and "create" in cs:
            return mock.Mock(returncode=0, stdout="Created task t_test002\n")
        if "hermes" in cs and "kanban" in cs and "reclaim" in cs:
            return mock.Mock(returncode=0, stdout="OK\n", stderr="")
        if "hermes" in cs and "kanban" in cs and "block" in cs:
            return mock.Mock(returncode=0, stdout="OK\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    report_text = """
## Kanban Cards to Create
1. `fin-overconfidence-fix2` — **Discount financials overconfidence** (Risk: medium | Sectors: financials | Owner: orchestrator)
   - Evidence: financials showed worst Brier score
   - Expected impact: reduce false bearish signals
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

    check("Created 1 (LTFT > -0.10)", result["created"] == 1, f"created={result['created']}")
    check("Skipped 0", result["skipped"] == 0, f"skipped={result['skipped']}")

    m = load_manifest(manifest)
    skipped_entries = [c for c in m["cards"] if c.get("skipped")]
    check("No skipped entries in manifest", len(skipped_entries) == 0)

    create_calls = [c for c in call_log if "create" in _cmd_str(c)]
    check("hermes kanban create called", len(create_calls) == 1)


# -----------------------------------------------------------------------
# Test 19: Sector-trim gate — fail-open when SSH fails
# -----------------------------------------------------------------------
def test_trim_gate_fail_open():
    print("\nTest 19: Trim gate — fail-open on SSH error")

    call_log = []

    def _cmd_str(cmd):
        return " ".join(str(c) for c in cmd) if isinstance(cmd, (list, tuple)) else str(cmd)

    def fake_run(cmd, **kwargs):
        cs = _cmd_str(cmd)
        call_log.append(cmd)
        if "sqlite3" in cs and "sector_trim" in cs:
            return mock.Mock(returncode=255, stdout="", stderr="Connection refused")
        if "hermes" in cs and "kanban" in cs and "create" in cs:
            return mock.Mock(returncode=0, stdout="Created task t_test003\n")
        if "hermes" in cs and "kanban" in cs and "reclaim" in cs:
            return mock.Mock(returncode=0, stdout="OK\n", stderr="")
        if "hermes" in cs and "kanban" in cs and "block" in cs:
            return mock.Mock(returncode=0, stdout="OK\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    report_text = """
## Kanban Cards to Create
1. `fin-overconfidence-fix3` — **Discount financials overconfidence** (Risk: medium | Sectors: financials | Owner: orchestrator)
   - Evidence: financials Brier rank
   - Expected impact: reduce false bearish signals
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

    check("Created 1 (fail-open)", result["created"] == 1, f"created={result['created']}")
    check("Skipped 0", result["skipped"] == 0, f"skipped={result['skipped']}")

    create_calls = [c for c in call_log if "hermes" in c and "create" in c]
    check("hermes kanban create called (fail-open)", len(create_calls) == 1)


# -----------------------------------------------------------------------
# Test 20: Sector-trim gate — empty row (unknown sector) files card
# -----------------------------------------------------------------------
def test_trim_gate_unknown_sector_row():
    print("\nTest 20: Trim gate — empty stdout (no row) -> filed")

    call_log = []

    def _cmd_str(cmd):
        return " ".join(str(c) for c in cmd) if isinstance(cmd, (list, tuple)) else str(cmd)

    def fake_run(cmd, **kwargs):
        cs = _cmd_str(cmd)
        call_log.append(cmd)
        if "sqlite3" in cs and "sector_trim" in cs:
            return mock.Mock(returncode=0, stdout="", stderr="")
        if "hermes" in cs and "kanban" in cs and "create" in cs:
            return mock.Mock(returncode=0, stdout="Created task t_test004\n")
        if "hermes" in cs and "kanban" in cs and "reclaim" in cs:
            return mock.Mock(returncode=0, stdout="OK\n", stderr="")
        if "hermes" in cs and "kanban" in cs and "block" in cs:
            return mock.Mock(returncode=0, stdout="OK\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    report_text = """
## Kanban Cards to Create
1. `energy-overconfidence-fix` — **Discount energy overconfidence** (Risk: medium | Sectors: energy | Owner: orchestrator)
   - Evidence: energy Brier rank
   - Expected impact: reduce false bearish signals
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

    check("Created 1 (no row -> filed)", result["created"] == 1, f"created={result['created']}")
    check("Skipped 0", result["skipped"] == 0, f"skipped={result['skipped']}")


# -----------------------------------------------------------------------
# Test 21: Ranking claim without date — warning logged, card still filed
# -----------------------------------------------------------------------
def test_ranking_claim_no_date_logged():
    print("\nTest 21: Ranking claim without date -> warning + filed")

    call_log = []

    def _cmd_str(cmd):
        return " ".join(str(c) for c in cmd) if isinstance(cmd, (list, tuple)) else str(cmd)

    def fake_run(cmd, **kwargs):
        cs = _cmd_str(cmd)
        call_log.append(cmd)
        if "sqlite3" in cs and "sector_trim" in cs:
            return mock.Mock(returncode=0, stdout="0.0\n", stderr="")
        if "hermes" in cs and "kanban" in cs and "create" in cs:
            return mock.Mock(returncode=0, stdout="Created task t_test005\n")
        if "hermes" in cs and "kanban" in cs and "reclaim" in cs:
            return mock.Mock(returncode=0, stdout="OK\n", stderr="")
        if "hermes" in cs and "kanban" in cs and "block" in cs:
            return mock.Mock(returncode=0, stdout="OK\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    report_text = """
## Kanban Cards to Create
1. `fin-worst-performer` — **Fix financials worst-performer signal** (Risk: medium | Sectors: financials | Owner: orchestrator)
   - Evidence: financials 2nd-worst Brier score in recent window
   - Expected impact: improve signal quality
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

    check("Created 1 (ranking claim is log-only)", result["created"] == 1, f"created={result['created']}")
    check("Skipped 0", result["skipped"] == 0, f"skipped={result['skipped']}")


# -----------------------------------------------------------------------
# Test 22: is_calibration_card detection
# -----------------------------------------------------------------------
def test_is_calibration_card_detection():
    print("\nTest 22: is_calibration_card detection")

    cal_card = {"card_id": "x", "title": "Discount financials overconfidence",
                "evidence": "", "expected_impact": ""}
    is_cal, sector = is_calibration_card(cal_card)
    check("Detects overconfidence+financials", is_cal and sector == "financials",
          f"is_cal={is_cal}, sector={sector}")

    cal_card2 = {"card_id": "y", "title": "Fix Brier score for technology",
                 "evidence": "", "expected_impact": ""}
    is_cal2, sector2 = is_calibration_card(cal_card2)
    check("Detects Brier+technology", is_cal2 and sector2 == "technology",
          f"is_cal={is_cal2}, sector={sector2}")

    normal_card = {"card_id": "z", "title": "Fix API timeout",
                   "evidence": "", "expected_impact": ""}
    is_cal3, sector3 = is_calibration_card(normal_card)
    check("Non-calibration card not flagged", not is_cal3, f"is_cal={is_cal3}")

    cal_no_sector = {"card_id": "w", "title": "Discount something generic",
                     "evidence": "", "expected_impact": ""}
    is_cal4, sector4 = is_calibration_card(cal_no_sector)
    check("Cal keyword no sector -> not calibration", not is_cal4 and sector4 is None,
          f"is_cal={is_cal4}, sector={sector4}")


# -----------------------------------------------------------------------
# Test 23: get_sector_ltft function
# -----------------------------------------------------------------------
def test_get_sector_ltft():
    print("\nTest 23: get_sector_ltft function")

    def fake_run(cmd, **kwargs):
        return mock.Mock(returncode=0, stdout="-0.187\n", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        val = get_sector_ltft("financials", "trading@172.29.10.225")
    check("Returns float -0.187", val == -0.187, f"got {val}")

    def fake_run_empty(cmd, **kwargs):
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run_empty):
        val2 = get_sector_ltft("defense", "trading@172.29.10.225")
    check("Empty stdout -> None", val2 is None, f"got {val2}")

    def fake_run_error(cmd, **kwargs):
        return mock.Mock(returncode=255, stdout="", stderr="Connection refused")

    with mock.patch("subprocess.run", side_effect=fake_run_error):
        val3 = get_sector_ltft("energy", "trading@172.29.10.225")
    check("SSH error -> None", val3 is None, f"got {val3}")

    val4 = get_sector_ltft("unknown_sector", "trading@172.29.10.225")
    check("Non-canonical sector -> None", val4 is None, f"got {val4}")


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
    test_trim_gate_skip()
    test_trim_gate_file()
    test_trim_gate_fail_open()
    test_trim_gate_unknown_sector_row()
    test_ranking_claim_no_date_logged()
    test_is_calibration_card_detection()
    test_get_sector_ltft()
    test_extract_theme()
    test_refire_within_window_suppress()
    test_refire_different_direction_creates()
    test_refire_no_direction_matches_either()
    test_refire_older_than_window_creates()
    test_refire_no_sector_never_suppresses()
    test_refire_card_exists_refired_ids()
    test_refire_dry_run_no_mutation()
    test_refire_old_manifest_compat()

    print("\n" + "=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")
    print("=" * 60)
    sys.exit(1 if FAIL > 0 else 0)


# -----------------------------------------------------------------------
# Test 24: extract_theme
# -----------------------------------------------------------------------
def test_extract_theme():
    print("\nTest 24: extract_theme")

    # Bullish financials
    card = {"card_id": "t1", "title": "Bullish financials outlook",
            "evidence": "", "expected_impact": ""}
    sector, direction = extract_theme(card)
    check("Sector is financials", sector == "financials", f"sector={sector}")
    check("Direction is bullish", direction == "bullish", f"direction={direction}")

    # Bearish technology
    card2 = {"card_id": "t2", "title": "Bearish on technology",
             "evidence": "", "expected_impact": ""}
    sector2, direction2 = extract_theme(card2)
    check("Sector is technology", sector2 == "technology", f"sector={sector2}")
    check("Direction is bearish", direction2 == "bearish", f"direction={direction2}")

    # No direction
    card3 = {"card_id": "t3", "title": "Fix energy sector pipeline",
             "evidence": "", "expected_impact": ""}
    sector3, direction3 = extract_theme(card3)
    check("Sector is energy", sector3 == "energy", f"sector={sector3}")
    check("Direction is None", direction3 is None, f"direction={direction3}")

    # No canonical sector -> fail-open
    card4 = {"card_id": "t4", "title": "Fix something random",
             "evidence": "", "expected_impact": ""}
    sector4, direction4 = extract_theme(card4)
    check("Sector is None", sector4 is None, f"sector={sector4}")
    check("Direction is None", direction4 is None, f"direction={direction4}")

    # Direction from evidence
    card5 = {"card_id": "t5", "title": "Update macro model",
             "evidence": "bearish reversal detected", "expected_impact": ""}
    sector5, direction5 = extract_theme(card5)
    check("Sector is macro", sector5 == "macro", f"sector={sector5}")
    check("Direction is bearish", direction5 == "bearish", f"direction={direction5}")


# -----------------------------------------------------------------------
# Test 25: Re-fire within 14 days — same sector+direction suppresses
# -----------------------------------------------------------------------
def test_refire_within_window_suppress():
    print("\nTest 25: Re-fire within 14 days — suppresses and stamps")

    now = datetime.now(timezone.utc)
    report_text = """
## Kanban Cards to Create
1. `fin-bullish-refire` — **Bullish financials finding** (Risk: low | Sectors: financials | Owner: orchestrator)
"""

    with tempfile.TemporaryDirectory() as td:
        manifest = os.path.join(td, "manifest.json")
        pre = {
            "cards": [
                {
                    "card_id": "fin-bullish-orig",
                    "title": "Bullish financials finding",
                    "sectors": ["financials"],
                    "theme_sector": "financials",
                    "theme_direction": "bullish",
                    "created_at": (now - timedelta(days=5)).isoformat(),
                }
            ],
            "last_scan": now.isoformat(),
        }
        save_manifest(pre, manifest)

        call_log = []
        def fake_run(cmd, **kwargs):
            call_log.append(cmd)
            return mock.Mock(returncode=0, stdout="Created task t_refire001\n")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = ingest_report(
                report_path="",
                manifest_path=manifest,
                dry_run=False,
                report_text=report_text,
                report_file="test.md",
            )

        check("Created 0 (suppressed)", result["created"] == 0, f"created={result['created']}")
        check("Refired 1", result["refired"] == 1, f"refired={result['refired']}")
        check("Skipped 0", result["skipped"] == 0, f"skipped={result['skipped']}")

        m = load_manifest(manifest)
        entry = m["cards"][0]
        check("last_refired_at stamped", "last_refired_at" in entry)
        check("refired_count is 1", entry["refired_count"] == 1)
        check("refired_card_ids contains new card_id", "fin-bullish-refire" in entry["refired_card_ids"])
        check("No kanban create called", len([c for c in call_log if "create" in str(c)]) == 0)


# -----------------------------------------------------------------------
# Test 26: Same sector, different direction — creates new card
# -----------------------------------------------------------------------
def test_refire_different_direction_creates():
    print("\nTest 26: Same sector, different direction — creates new card")

    now = datetime.now(timezone.utc)
    report_text = """
## Kanban Cards to Create
1. `fin-bearish-new` — **Bearish financials finding** (Risk: low | Sectors: financials | Owner: orchestrator)
"""

    with tempfile.TemporaryDirectory() as td:
        manifest = os.path.join(td, "manifest.json")
        pre = {
            "cards": [
                {
                    "card_id": "fin-bullish-orig",
                    "title": "Bullish financials finding",
                    "sectors": ["financials"],
                    "theme_sector": "financials",
                    "theme_direction": "bullish",
                    "created_at": (now - timedelta(days=3)).isoformat(),
                }
            ],
            "last_scan": now.isoformat(),
        }
        save_manifest(pre, manifest)

        call_log = []
        def fake_run(cmd, **kwargs):
            call_log.append(cmd)
            if "kanban" in str(cmd) and "create" in str(cmd):
                return mock.Mock(returncode=0, stdout="Created task t_refire002\n")
            if "kanban" in str(cmd) and "reclaim" in str(cmd):
                return mock.Mock(returncode=0, stdout="OK\n", stderr="")
            if "kanban" in str(cmd) and "block" in str(cmd):
                return mock.Mock(returncode=0, stdout="OK\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = ingest_report(
                report_path="",
                manifest_path=manifest,
                dry_run=False,
                report_text=report_text,
                report_file="test.md",
            )

        check("Created 1 (different direction)", result["created"] == 1, f"created={result['created']}")
        check("Refired 0", result["refired"] == 0, f"refired={result['refired']}")


# -----------------------------------------------------------------------
# Test 27: No-direction card — suppresses matching entries
# -----------------------------------------------------------------------
def test_refire_no_direction_matches_either():
    print("\nTest 27: No-direction card suppresses entries with any direction")

    now = datetime.now(timezone.utc)
    report_text = """
## Kanban Cards to Create
1. `fin-neutral-fix` — **Fix financials pipeline** (Risk: low | Sectors: financials | Owner: orchestrator)
"""

    with tempfile.TemporaryDirectory() as td:
        manifest = os.path.join(td, "manifest.json")
        pre = {
            "cards": [
                {
                    "card_id": "fin-bearish-orig",
                    "title": "Bearish financials finding",
                    "sectors": ["financials"],
                    "theme_sector": "financials",
                    "theme_direction": "bearish",
                    "created_at": (now - timedelta(days=4)).isoformat(),
                }
            ],
            "last_scan": now.isoformat(),
        }
        save_manifest(pre, manifest)

        call_log = []
        def fake_run(cmd, **kwargs):
            call_log.append(cmd)
            return mock.Mock(returncode=0, stdout="Created task t_refire003\n")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = ingest_report(
                report_path="",
                manifest_path=manifest,
                dry_run=False,
                report_text=report_text,
                report_file="test.md",
            )

        check("Created 0 (no-dir suppresses any-dir)", result["created"] == 0, f"created={result['created']}")
        check("Refired 1", result["refired"] == 1, f"refired={result['refired']}")

        m = load_manifest(manifest)
        entry = m["cards"][0]
        check("Stamped refired_card_ids", "fin-neutral-fix" in entry.get("refired_card_ids", []))


# -----------------------------------------------------------------------
# Test 28: Older than 14 days — creates new card
# -----------------------------------------------------------------------
def test_refire_older_than_window_creates():
    print("\nTest 28: Older than 14 days — creates new card")

    now = datetime.now(timezone.utc)
    report_text = """
## Kanban Cards to Create
1. `fin-bullish-refire-old` — **Bullish financials finding** (Risk: low | Sectors: financials | Owner: orchestrator)
"""

    with tempfile.TemporaryDirectory() as td:
        manifest = os.path.join(td, "manifest.json")
        pre = {
            "cards": [
                {
                    "card_id": "fin-bullish-old",
                    "title": "Bullish financials finding",
                    "sectors": ["financials"],
                    "theme_sector": "financials",
                    "theme_direction": "bullish",
                    "created_at": (now - timedelta(days=20)).isoformat(),
                }
            ],
            "last_scan": now.isoformat(),
        }
        save_manifest(pre, manifest)

        call_log = []
        def fake_run(cmd, **kwargs):
            call_log.append(cmd)
            if "kanban" in str(cmd) and "create" in str(cmd):
                return mock.Mock(returncode=0, stdout="Created task t_refire004\n")
            if "kanban" in str(cmd) and "reclaim" in str(cmd):
                return mock.Mock(returncode=0, stdout="OK\n", stderr="")
            if "kanban" in str(cmd) and "block" in str(cmd):
                return mock.Mock(returncode=0, stdout="OK\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = ingest_report(
                report_path="",
                manifest_path=manifest,
                dry_run=False,
                report_text=report_text,
                report_file="test.md",
            )

        check("Created 1 (outside window)", result["created"] == 1, f"created={result['created']}")
        check("Refired 0", result["refired"] == 0, f"refired={result['refired']}")


# -----------------------------------------------------------------------
# Test 29: No canonical sector — never suppresses
# -----------------------------------------------------------------------
def test_refire_no_sector_never_suppresses():
    print("\nTest 29: No canonical sector — never suppresses")

    now = datetime.now(timezone.utc)
    report_text = """
## Kanban Cards to Create
1. `generic-fix-thing` — **Fix something in the pipeline** (Risk: low | Sectors: infrastructure | Owner: orchestrator)
"""

    with tempfile.TemporaryDirectory() as td:
        manifest = os.path.join(td, "manifest.json")
        pre = {
            "cards": [
                {
                    "card_id": "generic-orig",
                    "title": "Fix something else",
                    "sectors": ["infrastructure"],
                    "theme_sector": None,
                    "theme_direction": None,
                    "created_at": (now - timedelta(days=2)).isoformat(),
                }
            ],
            "last_scan": now.isoformat(),
        }
        save_manifest(pre, manifest)

        call_log = []
        def fake_run(cmd, **kwargs):
            call_log.append(cmd)
            if "kanban" in str(cmd) and "create" in str(cmd):
                return mock.Mock(returncode=0, stdout="Created task t_refire005\n")
            if "kanban" in str(cmd) and "reclaim" in str(cmd):
                return mock.Mock(returncode=0, stdout="OK\n", stderr="")
            if "kanban" in str(cmd) and "block" in str(cmd):
                return mock.Mock(returncode=0, stdout="OK\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = ingest_report(
                report_path="",
                manifest_path=manifest,
                dry_run=False,
                report_text=report_text,
                report_file="test.md",
            )

        check("Created 1 (no sector match)", result["created"] == 1, f"created={result['created']}")
        check("Refired 0", result["refired"] == 0, f"refired={result['refired']}")


# -----------------------------------------------------------------------
# Test 30: card_exists hits refired_card_ids
# -----------------------------------------------------------------------
def test_refire_card_exists_refired_ids():
    print("\nTest 30: card_exists hits refired_card_ids")

    manifest = {
        "cards": [
            {
                "card_id": "original-card",
                "title": "Original",
                "refired_card_ids": ["refired-a", "refired-b"],
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ],
        "last_scan": "2026-01-01",
    }

    check("Finds original by card_id", card_exists(manifest, "original-card"))
    check("Finds refired-a in refired_card_ids", card_exists(manifest, "refired-a"))
    check("Finds refired-b in refired_card_ids", card_exists(manifest, "refired-b"))
    check("Does not find unrelated id", not card_exists(manifest, "unrelated-id"))


# -----------------------------------------------------------------------
# Test 31: dry_run does not mutate manifest on re-fire
# -----------------------------------------------------------------------
def test_refire_dry_run_no_mutation():
    print("\nTest 31: dry_run does not mutate manifest on re-fire")

    now = datetime.now(timezone.utc)
    report_text = """
## Kanban Cards to Create
1. `fin-bullish-dry` — **Bullish financials finding** (Risk: low | Sectors: financials | Owner: orchestrator)
"""

    with tempfile.TemporaryDirectory() as td:
        manifest = os.path.join(td, "manifest.json")
        pre = {
            "cards": [
                {
                    "card_id": "fin-bullish-orig",
                    "title": "Bullish financials finding",
                    "sectors": ["financials"],
                    "theme_sector": "financials",
                    "theme_direction": "bullish",
                    "created_at": (now - timedelta(days=5)).isoformat(),
                }
            ],
            "last_scan": now.isoformat(),
        }
        save_manifest(pre, manifest)

        pre_hash = json.dumps(pre["cards"], sort_keys=True)

        call_log = []
        def fake_run(cmd, **kwargs):
            call_log.append(cmd)
            return mock.Mock(returncode=0, stdout="Created task t_refire006\n")

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = ingest_report(
                report_path="",
                manifest_path=manifest,
                dry_run=True,
                report_text=report_text,
                report_file="test.md",
            )

        check("Refired 1 (dry run)", result["refired"] == 1, f"refired={result['refired']}")
        check("Created 0", result["created"] == 0, f"created={result['created']}")

        m_after = load_manifest(manifest)
        post_hash = json.dumps(m_after["cards"], sort_keys=True)
        check("Manifest unchanged", pre_hash == post_hash, "manifest was mutated in dry run")
        entry = m_after["cards"][0]
        check("No last_refired_at added", "last_refired_at" not in entry)
        check("No refired_count added", "refired_count" not in entry)


# -----------------------------------------------------------------------
# Test 32: Old manifest entries load/save unchanged
# -----------------------------------------------------------------------
def test_refire_old_manifest_compat():
    print("\nTest 32: Old manifest entries load/save unchanged")

    with tempfile.TemporaryDirectory() as td:
        manifest = os.path.join(td, "manifest.json")
        # Old manifest without theme_sector, theme_direction, refired fields
        _recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        pre = {
            "cards": [
                {
                    "card_id": "old-card-1",
                    "title": "Old card without theme fields",
                    "sectors": ["financials"],
                    "created_at": _recent,
                },
                {
                    "card_id": "old-card-2",
                    "title": "Another old card",
                    "sectors": ["technology"],
                    "created_at": _recent,
                },
            ],
            "last_scan": _recent,
        }
        save_manifest(pre, manifest)

        # Reload — should not crash, should preserve old fields
        m = load_manifest(manifest)
        check("Has 2 old entries", len(m["cards"]) == 2)
        check("Old card 1 fields intact", m["cards"][0]["card_id"] == "old-card-1")
        check("No theme_sector added", "theme_sector" not in m["cards"][0])
        check("No theme_direction added", "theme_direction" not in m["cards"][0])

        # Save and reload again
        save_manifest(m, manifest)
        m2 = load_manifest(manifest)
        check("Still 2 entries after save/reload", len(m2["cards"]) == 2)
        check("Old fields preserved", m2["cards"][0]["card_id"] == "old-card-1")

        # find_refire_match should derive sector from stored sectors/title
        match = find_refire_match(m2, "financials", None)
        check("find_refire_match derives sector from old entry", match is not None)
        if match:
            check("Matched old-card-1", match["card_id"] == "old-card-1")


if __name__ == "__main__":
    main()
