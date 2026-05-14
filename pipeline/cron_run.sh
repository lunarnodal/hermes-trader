#!/bin/bash
# Cron wrapper for trading-ai pipeline
# Prevents overlapping runs using a lockfile

LOCKFILE=/tmp/trading-ai-pipeline.lock
LOGFILE=/mnt/qnap/timeseries/logs/cron.log
PIPELINE_DIR=/home/trading/trading-ai
VENV=$PIPELINE_DIR/pipeline/.venv

# Check for existing run
if [ -f "$LOCKFILE" ]; then
    PID=$(cat "$LOCKFILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "$(date -u) Pipeline already running (PID $PID), skipping" >> "$LOGFILE"
        exit 0
    else
        echo "$(date -u) Stale lockfile found, removing" >> "$LOGFILE"
        rm -f "$LOCKFILE"
    fi
fi

# Write lockfile
echo $$ > "$LOCKFILE"
trap "rm -f $LOCKFILE" EXIT

echo "$(date -u) ── Cron run starting ──" >> "$LOGFILE"

cd "$PIPELINE_DIR"
source "$VENV/bin/activate"
python3 pipeline/run.py >> "$LOGFILE" 2>&1

echo "$(date -u) ── Cron run complete ──" >> "$LOGFILE"

# Daily rule discovery (passed via --discover-rules flag)
if [[ "$1" == "--discover-rules" ]]; then
    echo "$(date -u) ── Rule discovery starting ──" >> "$LOGFILE"
    cd "$PIPELINE_DIR"
    source "$VENV/bin/activate"
    python3 pipeline/rules/discover_rules.py >> "$LOGFILE" 2>&1
    echo "$(date -u) ── Rule discovery complete ──" >> "$LOGFILE"
    exit 0
fi

# Prediction verification (passed via --verify flag)
if [[ "$1" == "--verify" ]]; then
    echo "$(date -u) ── Verification starting ──" >> "$LOGFILE"
    cd "$PIPELINE_DIR"
    source "$VENV/bin/activate"
    python3 pipeline/paper_trading/verify.py >> "$LOGFILE" 2>&1
    echo "$(date -u) ── Verification complete ──" >> "$LOGFILE"
    exit 0
fi
