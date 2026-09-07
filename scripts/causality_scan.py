#!/usr/bin/env python3
"""
causality_scan.py — Scans causality reports for new Kanban cards to create.
Run via cron job 0679a8e2116a (9:00 AM ET M-F)
Creates new Kanban cards via hermes CLI subprocess.

Workflow:
  1. Read all causality_report_*.md in /home/trading/trading-ai/reports/causality/
  2. Extract ## Kanban Cards to Create section
  3. Dedupe against kanban_manifest.json (known card IDs)
  4. For new cards: write .md card file + call `hermes kanban create`
  5. Update kanban_manifest.json
"""
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPORTS_DIR = Path("/home/trading/trading-ai/reports/causality")
MANIFEST_PATH = REPORTS_DIR / "kanban_manifest.json"


def load_manifest():
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    return {"cards": [], "last_scan": None}


def save_manifest(data):
    data["last_scan"] = datetime.now(timezone.utc).isoformat()
    MANIFEST_PATH.write_text(json.dumps(data, indent=2))


def extract_cards(markdown_text, report_filename):
    """Parse ## Kanban Cards to Create section from a report."""
    cards = []
    section_match = re.search(
        r"## Kanban Cards to Create\s*\n(.*?)(?=\n---\n|\n## |\Z)",
        markdown_text,
        re.DOTALL,
    )
    if not section_match:
        return cards

    text = section_match.group(1)
    # Parse numbered list items: "n. `card-id` — **title**"
    item_pattern = re.compile(
        r"^\d+\.\s+`([a-z0-9_-]+)`\s*—\s*\*\*(.+?)\*\*\s*"
        r"(?:Risk:\s*(\w+)\s*\|?\s*Sectors:\s*([^|]+?)\s*\|?\s*Owner:\s*([^\n]+))?",
        re.MULTILINE | re.IGNORECASE,
    )

    for m in item_pattern.finditer(text):
        card_id = m.group(1).strip()
        title = m.group(2).strip()
        risk = (m.group(3) or "low").strip()
        sectors = (m.group(4) or "pipeline").strip()
        owner = (m.group(5) or "coder").strip()
        cards.append({
            "card_id": card_id,
            "title": title,
            "risk": risk,
            "sectors": [s.strip() for s in re.split(r"[,\s]+", sectors) if s.strip()],
            "owner": owner,
            "discovered_date": datetime.now(timezone.utc).isoformat(),
            "report_file": report_filename,
        })
    return cards


def hermes_create(card_id, title, body, assignee):
    """Call hermes kanban create via subprocess."""
    cmd = [
        "hermes", "kanban", "create",
        f"{card_id}: {title}",
        "--body", body,
        "--assignee", assignee,
        "--initial-status", "todo",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def write_card_file(card):
    """Write a .md card file alongside the report."""
    card_path = REPORTS_DIR / f"{card['card_id']}.md"
    body = f"""\
# {card['title']}

| Field | Value |
|---|---|
| **Card ID** | {card['card_id']} |
| **Discovered** | {card['discovered_date']} |
| **Report** | {card['report_file']} |
| **Risk** | {card['risk']} |
| **Sectors** | {", ".join(card['sectors'])} |
| **Status** | pending_operator_review |

## Finding Summary
See {card['report_file']} for full analysis.

## Recommendation
See {card['report_file']} for full recommendation.
"""
    card_path.write_text(body)
    return card_path


def main():
    manifest = load_manifest()
    existing_ids = {c["card_id"] for c in manifest["cards"]}

    new_cards = []
    for report_file in sorted(REPORTS_DIR.glob("causality_report_*.md")):
        text = report_file.read_text()
        found = extract_cards(text, report_file.name)
        new = [c for c in found if c["card_id"] not in existing_ids]
        new_cards.extend(new)

    if not new_cards:
        print("No new cards discovered.")
        return

    created = []
    for card in new_cards:
        # Write card .md file
        card_path = write_card_file(card)
        print(f"Wrote: {card_path}")

        # Create hermes kanban card
        body = f"""\
## Finding
See report: {card['report_file']}

## Risk: {card['risk']} | Sectors: {", ".join(card['sectors'])}

This card was auto-created by causality_scan.py from the causality analysis report.
"""
        rc, stdout, stderr = hermes_create(card["card_id"], card["title"], body, card["owner"])
        if rc == 0:
            print(f"  Created Kanban card: {card['card_id']}")
            created.append(card)
        else:
            print(f"  FAILED to create {card['card_id']}: {stderr[:200]}")

    # Update manifest
    manifest["cards"].extend(new_cards)
    save_manifest(manifest)

    print(f"\nDone. {len(created)}/{len(new_cards)} cards created.")


if __name__ == "__main__":
    main()
