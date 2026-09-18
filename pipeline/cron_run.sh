#!/bin/bash
# Cron wrapper for trading-ai pipeline
# Prevents overlapping runs using per-job lockfiles.
#
# Lock model (job-scoped, 2026-09-18 — fixes lock starvation t_edb6fbca):
#   Each invocation type gets its OWN lock so the 5-minute pipeline run
#   can no longer starve the dedicated --verify / --discover-rules jobs
#   (they used to lose the shared-lock spawn race: "already running, skipping").
#   - pipeline (default, no flag):  /tmp/trading-ai-pipeline.lock
#   - --verify:                     /tmp/trading-ai-verify.lock
#   - --discover-rules:             /tmp/trading-ai-discover-rules.lock
# A job never blocks on another job's lock.
#
# Stale detection + retry-on-collision:
#   If the lock file's PID is dead, the lock is stale -> remove and proceed.
#   If the lock file's PID is alive (job genuinely running), retry with
#   exponential backoff (5s, 10s, 20s = max 3 retries, ~35s total), then
#   skip gracefully with a log line.

LOGFILE=/mnt/qnap/timeseries/logs/cron.log
PIPELINE_DIR=/home/trading/trading-ai
VENV=$PIPELINE_DIR/pipeline/.venv

log() { echo "$(date -u) $1" >> "$LOGFILE"; }

acquire_job_lock() {
    # $1 = lockfile path; returns 0 acquired, 1 skip
    local LOCKFILE="$1"
    local attempt=0
    local delay=5
    while :; do
        if [ -f "$LOCKFILE" ]; then
            local PID
            PID=$(cat "$LOCKFILE" 2>/dev/null)
            if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
                # Live lock: retry with exponential backoff
                attempt=$((attempt + 1))
                if [ "$attempt" -ge 3 ]; then
                    log "$(basename "$LOCKFILE" .lock) already running (PID $PID) after 3 retries, skipping"
                    return 1
                fi
                log "$(basename "$LOCKFILE" .lock) busy (PID $PID), retry $attempt in ${delay}s"
                sleep "$delay"
                delay=$((delay * 2))
                continue
            else
                log "Stale $(basename "$LOCKFILE" .lock) found (PID ${PID:-none}), removing"
                rm -f "$LOCKFILE"
            fi
        fi
        # Acquire
        if (set -o noclobber; echo $$ > "$LOCKFILE") 2>/dev/null; then
            trap "rm -f $LOCKFILE" EXIT
            return 0
        else
            # Lost the noclobber race; loop will retry
            attempt=$((attempt + 1))
            if [ "$attempt" -ge 3 ]; then
                log "$(basename "$LOCKFILE" .lock) race after 3 retries, skipping"
                return 1
            fi
            sleep "$delay"
            delay=$((delay * 2))
        fi
    done
}

# Resolve invocation type
JOB="pipeline"
case "$1" in
    --verify)          JOB="verify" ;;
    --discover-rules)  JOB="discover-rules" ;;
esac
JOBLOCK="/tmp/trading-ai-$JOB.lock"

# --- Pipeline body (default run) ---
if [ "$JOB" = "pipeline" ]; then
    if ! acquire_job_lock "$JOBLOCK"; then exit 0; fi
    log "── Cron run starting (pipeline) ──"
    cd "$PIPELINE_DIR" || exit 1
    source "$VENV/bin/activate"
    python3 pipeline/run.py >> "$LOGFILE" 2>&1
    log "── Cron run complete (pipeline) ──"
    exit 0
fi

# --- Daily rule discovery ---
if [ "$JOB" = "discover-rules" ]; then
    if ! acquire_job_lock "$JOBLOCK"; then exit 0; fi
    log "── Rule discovery starting ──"
    cd "$PIPELINE_DIR" || exit 1
    source "$VENV/bin/activate"
    python3 pipeline/rules/discover_rules.py >> "$LOGFILE" 2>&1
    log "── Rule discovery complete ──"
    exit 0
fi

# --- Prediction verification ---
if [ "$JOB" = "verify" ]; then
    if ! acquire_job_lock "$JOBLOCK"; then exit 0; fi
    log "── Verification starting ──"
    cd "$PIPELINE_DIR" || exit 1
    source "$VENV/bin/activate"
    python3 pipeline/paper_trading/verify.py >> "$LOGFILE" 2>&1
    log "── Verification complete ──"
    exit 0
fi
