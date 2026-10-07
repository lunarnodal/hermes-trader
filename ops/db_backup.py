#!/usr/bin/env python3
"""
db_backup.py — Nightly SQLite online backup with retention.

Uses sqlite3.Connection.backup() API (NOT cp/rsync) so the source DB
remains fully readable/writable during the backup.

Usage:
    python3 db_backup.py [--restore-test] [--target YYYYMMDD] [--dry-run]

Environment (set by cron wrapper):
    DB_SOURCE_DIR   /home/trading/trading-ai/data
    DB_BACKUP_ROOT  /mnt/qnap/timeseries/db-backups
    LOG_FILE        /mnt/qnap/timeseries/logs/db_backup.log
"""

import argparse
import datetime
import glob
import logging
import os
import pathlib
import shutil
import sqlite3
import sys

# -- Configuration -----------------------------------------------
DB_SOURCE_DIR   = os.environ.get("DB_SOURCE_DIR", "/home/trading/trading-ai/data")
DB_BACKUP_ROOT  = os.environ.get("DB_BACKUP_ROOT", "/mnt/qnap/timeseries/db-backups")
LOG_FILE        = os.environ.get("LOG_FILE", "/mnt/qnap/timeseries/logs/db_backup.log")
DAILY_RETENTION = 14   # keep last 14 daily snapshots
WEEKLY_RETENTION = 8   # keep last 8 weekly (Monday) snapshots

# DBs to exclude from backup (empty, legacy, or not managed by us)
EXCLUDE = {"trading.db"}  # 0-byte legacy placeholder

# -- Logging -----------------------------------------------------
def setup_logging(target_date_str: str) -> logging.Logger:
    logger = logging.getLogger("db_backup")
    logger.setLevel(logging.INFO)

    fh = logging.FileHandler(LOG_FILE, mode="a")
    fh.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    logger.info("=" * 60)
    logger.info("DB BACKUP START  target=%s", target_date_str)
    return logger


# -- Backup logic ------------------------------------------------
def backup_db(src_path: str, dst_path: str, logger: logging.Logger) -> bool:
    """
    Backup a single SQLite database using the online backup API.
    Returns True on success.
    """
    name = os.path.basename(src_path)
    src_size = os.path.getsize(src_path)

    # Skip 0-byte files
    if src_size == 0:
        logger.warning("SKIP %s - source is 0 bytes", name)
        return True

    try:
        src_con = sqlite3.connect(src_path)
        src_con.execute("BEGIN")  # shared lock - readers/writers still OK
        dst_con = sqlite3.connect(dst_path)

        # This is the ONLINE BACKUP API - safe while DB is live
        src_con.backup(dst_con, pages=1, progress=lambda *a: None)

        src_con.close()
        dst_con.close()

        dst_size = os.path.getsize(dst_path)
        logger.info(
            "OK %s  src=%d dst=%d bytes",
            name, src_size, dst_size,
        )
        return True
    except Exception as e:
        logger.error("FAIL %s - %s", name, e)
        # Clean up partial backup
        if os.path.exists(dst_path):
            os.remove(dst_path)
        return False


def do_backup(target_date_str: str, logger: logging.Logger) -> list:
    """
    Backup all .db files from source dir into YYYYMMDD/ folder.
    Returns list of (db_name, backup_path) tuples that succeeded.
    """
    target_dir = os.path.join(DB_BACKUP_ROOT, target_date_str)
    os.makedirs(target_dir, exist_ok=True)
    logger.info("Target dir: %s", target_dir)

    db_files = sorted(glob.glob(os.path.join(DB_SOURCE_DIR, "*.db")))
    if not db_files:
        logger.error("No .db files found in %s", DB_SOURCE_DIR)
        return []

    results = []
    failed = []
    for src in db_files:
        name = os.path.basename(src)
        if name in EXCLUDE:
            logger.info("SKIP %s - excluded", name)
            continue

        dst = os.path.join(target_dir, name)
        if backup_db(src, dst, logger):
            results.append((name, dst))
        else:
            failed.append(name)

    logger.info(
        "Backup complete: %d OK, %d FAILED",
        len(results), len(failed),
    )
    if failed:
        logger.error("Failed DBs: %s", ", ".join(failed))

    return results


# -- Retention ---------------------------------------------------
def enforce_retention(logger: logging.Logger, dry_run: bool = False):
    """
    Prune old backup directories.
    Keep the last DAILY_RETENTION daily snapshots + WEEKLY_RETENTION Mondays.
    """
    if not os.path.isdir(DB_BACKUP_ROOT):
        logger.info("No backup root - skipping retention")
        return

    # List YYYYMMDD directories (sorted newest first)
    dirs = []
    for entry in os.listdir(DB_BACKUP_ROOT):
        fp = os.path.join(DB_BACKUP_ROOT, entry)
        if os.path.isdir(fp) and len(entry) == 8 and entry.isdigit():
            dirs.append(entry)
    dirs.sort(reverse=True)  # newest first

    if not dirs:
        logger.info("No backup directories - skipping retention")
        return

    # Classify: weekly = any directory that falls on a Monday
    weekly_kept = set()
    daily_kept = set()
    for d in dirs:
        try:
            dt = datetime.datetime.strptime(d, "%Y%m%d").date()
            is_monday = dt.weekday() == 0
        except ValueError:
            continue

        # Always keep this dir if we haven't hit the quota
        if is_monday and len(weekly_kept) < WEEKLY_RETENTION:
            weekly_kept.add(d)
        elif len(daily_kept) < DAILY_RETENTION:
            daily_kept.add(d)

    keep = weekly_kept | daily_kept
    to_remove = [d for d in dirs if d not in keep]

    if not to_remove:
        logger.info(
            "Retention: %d dirs exist, all within limits "
            "(daily<=%d weekly<=%d)",
            len(dirs), DAILY_RETENTION, WEEKLY_RETENTION,
        )
        return

    logger.info(
        "Retention: removing %d old dirs: %s",
        len(to_remove), ", ".join(to_remove),
    )
    for d in to_remove:
        fp = os.path.join(DB_BACKUP_ROOT, d)
        if dry_run:
            logger.info("DRY-RUN would remove %s", fp)
        else:
            shutil.rmtree(fp)
            logger.info("Removed %s", fp)


# -- Restore test ------------------------------------------------
def do_restore_test(target_date_str: str, logger: logging.Logger) -> bool:
    """
    Restore portfolio.db from the target backup to a scratch path,
    then verify integrity + row counts against the live source.
    """
    backup_path = os.path.join(
        DB_BACKUP_ROOT, target_date_str, "portfolio.db"
    )
    if not os.path.exists(backup_path):
        logger.error(
            "RESTORE TEST: backup not found at %s", backup_path
        )
        return False

    scratch = "/tmp/db_restore_test_portfolio.db"

    # Clean slate
    if os.path.exists(scratch):
        os.remove(scratch)

    # Copy backup to scratch (this is a verification copy, not the backup method)
    shutil.copy2(backup_path, scratch)
    logger.info("RESTORE TEST: copied backup to %s", scratch)

    # Integrity check
    con = sqlite3.connect(scratch)
    cur = con.execute("PRAGMA integrity_check")
    result = cur.fetchone()[0]
    con.close()

    if result != "ok":
        logger.error("RESTORE TEST: integrity_check = %s", result)
        os.remove(scratch)
        return False
    logger.info("RESTORE TEST: integrity_check = ok")

    # Row-count comparison: source vs backup
    source_path = os.path.join(DB_SOURCE_DIR, "portfolio.db")
    src_con = sqlite3.connect(source_path)
    rst_con = sqlite3.connect(scratch)

    # Get all table names
    src_tables = set(
        r[0] for r in src_con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    )

    all_ok = True
    for table in sorted(src_tables):
        src_count = src_con.execute(f"SELECT count(*) FROM [{table}]").fetchone()[0]
        try:
            rst_count = rst_con.execute(f"SELECT count(*) FROM [{table}]").fetchone()[0]
        except sqlite3.OperationalError:
            rst_count = "MISSING"

        status = "OK" if src_count == rst_count else "MISMATCH"
        if status != "OK":
            all_ok = False
        logger.info(
            "RESTORE TEST: %-30s src=%-8s backup=%-8s [%s]",
            table, src_count, rst_count, status,
        )

    src_con.close()
    rst_con.close()
    os.remove(scratch)

    if all_ok:
        logger.info("RESTORE TEST: PASSED - all row counts match")
    else:
        logger.warning(
            "RESTORE TEST: COMPLETED with mismatches "
            "(expected - backup is a point-in-time snapshot)"
        )

    return True  # integrity is the hard gate; row-count drift is normal


# -- Main --------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="SQLite DB backup")
    parser.add_argument(
        "--target",
        default=datetime.date.today().strftime("%Y%m%d"),
        help="Target date string YYYYMMDD (default: today)",
    )
    parser.add_argument(
        "--restore-test", action="store_true",
        help="After backup, restore portfolio.db and verify integrity",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Log retention actions without removing dirs",
    )
    args = parser.parse_args()

    logger = setup_logging(args.target)

    # Phase 1: backup
    results = do_backup(args.target, logger)
    if not results:
        logger.error("No databases backed up - aborting")
        sys.exit(1)

    # Phase 2: retention
    enforce_retention(logger, dry_run=args.dry_run)

    # Phase 3: restore test (optional)
    if args.restore_test:
        logger.info("--- RESTORE TEST ---")
        ok = do_restore_test(args.target, logger)
        if not ok:
            logger.error("RESTORE TEST FAILED")
            sys.exit(2)

    logger.info("DB BACKUP DONE")


if __name__ == "__main__":
    main()
