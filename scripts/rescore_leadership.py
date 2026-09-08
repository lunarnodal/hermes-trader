#!/usr/bin/env python3
"""
Batch re-scoring for leadership_transition misclassified signals.
Extracts signals currently tagged as 'other' that match the leadership_transition
pattern (appoints + person + corporate title), then re-classifies them.
"""

import json
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SIGNALS_DIR = Path("/mnt/qnap/timeseries/signals")
OUTPUT_DIR = Path("/mnt/qnap/timeseries/signals")

TITLES = re.compile(r"\b(CEO|CFO|COO|CTO|CDO|Vice President|President|Director|Officer|Board)\b", re.IGNORECASE)
APPOINTED = re.compile(r"\bappoints?\b", re.IGNORECASE)
AUDITOR = re.compile(r"\bappoints?\s+\w+\s+(as\s+)?(auditor|independent auditor)\b", re.IGNORECASE)
NAMED_SP500 = re.compile(r"\bnamed\s+to\s+S&P\s*500\b", re.IGNORECASE)
BECOMES_GEN = re.compile(r"\bbecomes?\b", re.IGNORECASE)

def looks_like_leadership(signal: dict) -> bool:
    text = signal.get("title", "") + " " + signal.get("summary", "")
    text_lower = text.lower()
    if not APPOINTED.search(text_lower):
        return False
    if AUDITOR.search(text_lower):
        return False
    if NAMED_SP500.search(text_lower):
        return False
    if TITLES.search(text):
        return True
    return False

def classify_text(title: str, summary: str = "") -> dict:
    """Classify based on the rules in score.py"""
    text = (title + " " + summary).lower()
    
    # Check exclusions first
    if AUDITOR.search(text):
        return {"event_type": "regulatory", "confidence": 0.65, "macro_themes": ["audit_regulation"]}
    if NAMED_SP500.search(text):
        return {"event_type": "corporate_governance", "confidence": 0.65, "macro_themes": ["sp500_inclusion"]}
    if BECOMES_GEN.search(text) and not TITLES.search(title + " " + summary):
        return {"event_type": "other", "confidence": 0.50, "macro_themes": []}
    
    # Positive match: appoints + title
    if APPOINTED.search(text) and TITLES.search(title + " " + summary):
        return {"event_type": "leadership_transition", "confidence": 0.70, 
                "macro_themes": ["succession_risk", "management_change"]}
    
    return {"event_type": "other", "confidence": 0.50, "macro_themes": []}

def rescore_file(src_path: Path, out_path: Path) -> tuple[int, int]:
    misclassified = []
    with src_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                sig = json.loads(line)
            except:
                continue
            if sig.get("event_type") == "other" and looks_like_leadership(sig):
                misclassified.append(sig)

    log.info(f"Found {len(misclassified)} misclassified signals in {src_path.name}")

    results = []
    for sig in misclassified:
        title = sig.get("title", "")
        summary = sig.get("summary", "")
        try:
            result = classify_text(title, summary)
            sig["event_type"] = result.get("event_type", "leadership_transition")
            sig["confidence"] = result.get("confidence", 0.65)
            sig["macro_themes"] = result.get("macro_themes", [])
        except Exception as e:
            log.warning(f"Classification failed for {sig.get('guid')}: {e}")
            sig["event_type"] = "leadership_transition"
        results.append(sig)

    with out_path.open("w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    return len(misclassified), len(results)

def main():
    import glob
    
    scored_files = []
    for pattern in ["scored_2026*.jsonl", "scored_finnhub_2026*.jsonl", "scored_marketaux_2026*.jsonl"]:
        scored_files.extend(sorted(glob.glob(str(SIGNALS_DIR / pattern))))

    # Filter to Aug-Sep 2026
    scored_files = [f for f in scored_files if "202608" in f or "202609" in f]
    log.info(f"Scanning {len(scored_files)} scored files for Aug-Sep 2026")

    all_outputs = []
    for fpath in scored_files:
        path = Path(fpath)
        temp_out = OUTPUT_DIR / f"temp_rescore_{path.name}"
        count, written = rescore_file(path, temp_out)
        if written > 0:
            all_outputs.append(temp_out)

    # Merge all into final output
    final_out = OUTPUT_DIR / "scored_rescore_leadership.jsonl"
    total = 0
    with final_out.open("w") as fout:
        for tmp in all_outputs:
            with tmp.open() as fin:
                for line in fin:
                    fout.write(line)
                    total += 1
            tmp.unlink()

    log.info(f"Total misclassified signals re-scored: {total}")
    log.info(f"Output: {final_out}")
    return total

if __name__ == "__main__":
    main()
