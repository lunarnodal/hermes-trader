#!/usr/bin/env python3
"""
Round 2 data fixes for paper_trading.db (run AFTER the code deploy).

  DATA-3  strip the ' [WARNING: ...]' suffix that the old repeating-failure
          code appended to predictions.query
  DATA-4  relabel predictions.model_used rows that hold a raw llama-server
          path (e.g. /opt/models/.../Qwen3.6-27B-Q4_K_M.gguf) to the
          normalized label (qwen3.6-27b)

Default is a dry run. With --apply it first writes an online-backup copy of the
DB to --backup-dir, then applies both fixes in one transaction and verifies.

  python3 ops/data_fixes_round2.py                       # dry run
  python3 ops/data_fixes_round2.py --apply
"""
import argparse
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE))
from config import PAPER_DB  # noqa: E402
from reasoning.model_id import normalize_model_id  # noqa: E402

WARN_RE = re.compile(r"\s*\[WARNING: .*\]\s*$", re.S)


def plan(conn):
    warn_rows = [(i, q, WARN_RE.sub("", q))
                 for i, q in conn.execute(
                     "SELECT id, query FROM predictions WHERE query LIKE '%[WARNING:%'")]
    label_rows = [(i, m, normalize_model_id(m))
                  for i, m in conn.execute(
                      "SELECT id, model_used FROM predictions "
                      "WHERE model_used LIKE '%/%' OR model_used LIKE '%.gguf'")]
    return warn_rows, label_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(PAPER_DB))
    ap.add_argument("--backup-dir", default="/mnt/qnap/timeseries/db-backups")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    conn = sqlite3.connect(a.db)
    warn_rows, label_rows = plan(conn)
    total = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    print(f"DB: {a.db}  predictions={total}")
    print(f"DATA-3: {len(warn_rows)} queries carry an appended warning")
    for i, old, new in warn_rows:
        print(f"  #{i}: {old[:70]!r} -> {new[:70]!r}")
    print(f"DATA-4: {len(label_rows)} raw model labels")
    for i, old, new in label_rows[:5]:
        print(f"  #{i}: {old!r} -> {new!r}")
    if len(label_rows) > 5:
        print(f"  ... {len(label_rows) - 5} more")
    labels_before = dict(conn.execute(
        "SELECT model_used, COUNT(*) FROM predictions GROUP BY 1").fetchall())
    print(f"model_used before: {labels_before}")

    if not a.apply:
        print("\nDry run — nothing changed. Re-run with --apply.")
        return 0
    if not warn_rows and not label_rows:
        print("Nothing to do.")
        return 0

    dest = Path(a.backup_dir) / f"manual-pre-round2-data-{datetime.now():%Y%m%d-%H%M%S}"
    dest.mkdir(parents=True, exist_ok=False)
    bak = dest / Path(a.db).name
    with sqlite3.connect(bak) as b:
        conn.backup(b)
    with sqlite3.connect(bak) as b:
        assert b.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert b.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == total
    print(f"Backup: {bak} (integrity ok, {total} rows)")

    try:
        with conn:
            for i, _, new in warn_rows:
                conn.execute("UPDATE predictions SET query = ? WHERE id = ?", (new, i))
            for i, _, new in label_rows:
                conn.execute("UPDATE predictions SET model_used = ? WHERE id = ?", (new, i))
    except Exception as e:
        print(f"FAILED, rolled back: {e}")
        return 1

    left_w, left_l = plan(conn)
    after_total = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
    print(f"After: predictions={after_total} warnings_left={len(left_w)} raw_labels_left={len(left_l)}")
    print(f"model_used after: {dict(conn.execute('SELECT model_used, COUNT(*) FROM predictions GROUP BY 1').fetchall())}")
    if left_w or left_l or after_total != total:
        print("VERIFY FAILED — restore from the backup above")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
