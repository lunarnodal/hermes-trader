#!/usr/bin/env bash
# db_backup.sh - Cron wrapper for nightly SQLite DB backups
# Runs as the trading user via cron.
#
# Cron entry (America/New_York):
#   30 23 * * * /home/trading/trading-ai/ops/db_backup.sh
#               ^ runs at 11:30 PM ET nightly
set -euo pipefail

export DB_SOURCE_DIR=/home/trading/trading-ai/data
export DB_BACKUP_ROOT=/mnt/qnap/timeseries/db-backups
export LOG_FILE=/mnt/qnap/timeseries/logs/db_backup.log

exec python3 "${0%.*}.py" "$@"
