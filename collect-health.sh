#!/usr/bin/env bash
# collect-health.sh — read-only evidence bundle for a trading platform health review.
#
#   sudo ./collect-health.sh
#
# Overrides (env): APP_USER APP_DIR DB_PATH DAYS INCLUDE_DB INCLUDE_SRC
#                  QDRANT_URL QDRANT_API_KEY LLM_PORTS ALPACA_BASE UNIT_RE
#
# Read-only against the platform: no service restarts, no writes outside /tmp,
# SQLite opened with mode=ro and copied via the online backup API.
# Secrets are redacted from configs, unit files, crontabs, and logs. The source
# tarball excludes .env/json/yaml; review repo/secret-scan.txt before uploading.
set -uo pipefail

APP_USER="${APP_USER:-trading}"
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
DAYS="${DAYS:-14}"
INCLUDE_DB="${INCLUDE_DB:-1}"
INCLUDE_SRC="${INCLUDE_SRC:-1}"
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
LLM_PORTS="${LLM_PORTS:-8080 8081 8083}"
ALPACA_BASE="${ALPACA_BASE:-https://paper-api.alpaca.markets}"
UNIT_RE="${UNIT_RE:-trad|hermes|llama|vllm|qdrant|dash|flask|theta|alpaca|ingest|brief|critic|predict}"
NAME="healthcheck-$(hostname -s)-$(date +%Y%m%d-%H%M)"
OUT="/tmp/$NAME"
PRUNE=( -path '*/.git' -o -path '*/venv*' -o -path '*/.venv*' -o -path '*/__pycache__'
        -o -path '*/node_modules' -o -path '*/site-packages' -o -path '*/.cache' )

[[ $EUID -eq 0 ]] || { echo "Run with sudo (journals, crontabs, other users' files)."; exit 1; }
[[ -n "$APP_HOME" && -d "$APP_HOME" ]] || { echo "User $APP_USER not found."; exit 1; }
mkdir -p "$OUT"/{system,services,llm,qdrant,repo,db,logs,reports,broker}
LOG="$OUT/collector.log"
note() { echo "[$(date +%T)] $*" | tee -a "$LOG"; }

redact() {
  sed -E \
    -e 's/([A-Za-z0-9_]*(KEY|SECRET|TOKEN|PASSWORD|PASSWD|WEBHOOK|AUTH|CREDENTIAL)[A-Za-z0-9_]*["'\'']?[[:space:]]*[=:][[:space:]]*).*/\1<REDACTED>/I' \
    -e 's#(discord(app)?\.com/api/webhooks/)[^[:space:]"'\'']+#\1<REDACTED>#g' \
    -e 's/(Bearer[[:space:]]+)[A-Za-z0-9._~+\/=-]+/\1<REDACTED>/g' \
    -e 's#(://[^:/@[:space:]]+:)[^@[:space:]]+@#\1<REDACTED>@#g'
}
cap() { local f="$1"; shift; { printf '$ %s\n' "$*"; bash -c "$*"; } >"$OUT/$f" 2>&1 || echo "WARN $f" >>"$LOG"; }

# ---------- discovery ----------
DB_PATH="${DB_PATH:-$(find "$APP_HOME" \( "${PRUNE[@]}" \) -prune -o -type f -name 'paper_trading.db' -print 2>/dev/null | head -1)}"
if [[ -z "${APP_DIR:-}" ]]; then
  base="${DB_PATH:+$(dirname "$DB_PATH")}"; base="${base:-$APP_HOME}"
  APP_DIR="$(git -c safe.directory='*' -C "$base" rev-parse --show-toplevel 2>/dev/null || echo "$base")"
fi
PY="$(for v in "$APP_DIR"/{venv,.venv,env} "$APP_HOME"/{venv,.venv}; do [[ -x "$v/bin/python" ]] && { echo "$v/bin/python"; break; }; done)"
PY="${PY:-python3}"
note "APP_HOME=$APP_HOME APP_DIR=$APP_DIR DB_PATH=${DB_PATH:-<not found>} PY=$PY DAYS=$DAYS"

# ---------- system ----------
note "system"
cap system/host.txt        "hostnamectl; uname -a; uptime; cat /etc/os-release"
cap system/time.txt        "timedatectl; chronyc tracking 2>/dev/null || timedatectl timesync-status 2>/dev/null"
cap system/disk.txt        "df -hT -x tmpfs -x devtmpfs; echo; lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT; echo; du -sh '$APP_DIR' '$APP_HOME' 2>/dev/null"
cap system/memory.txt      "free -h; echo; swapon --show; echo; vmstat 1 3"
cap system/gpu.txt         "nvidia-smi; echo; nvidia-smi --query-gpu=index,name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,pcie.link.gen.current,pcie.link.width.current,clocks_throttle_reasons.active --format=csv; echo; nvidia-smi topo -m; echo; nvidia-smi nvlink -s"
cap system/processes.txt   "ps -eo pid,user,%cpu,%mem,rss,etime,cmd --sort=-%mem | head -50"
cap system/listening.txt   "ss -tlnp"
cap system/kernel-errs.txt "dmesg -T --level=err,warn 2>/dev/null | tail -300; echo; journalctl -k -p warning --since -${DAYS}d --no-pager | grep -Ei 'oom|xid|nvrm|i/o error|ext4|xfs|nvme|mce' | tail -300"
cap system/failed.txt      "systemctl --failed --no-pager"

# ---------- services ----------
note "services"
cap services/units.txt      "systemctl list-units --all --no-pager --plain | grep -Ei '$UNIT_RE'"
cap services/unit-files.txt "systemctl list-unit-files --no-pager | grep -Ei '$UNIT_RE'"
cap services/timers.txt     "systemctl list-timers --all --no-pager"
cap services/user-units.txt "systemctl --user -M '$APP_USER@' list-units --all --no-pager 2>&1; systemctl --user -M '$APP_USER@' list-timers --all --no-pager 2>&1"
{
  for u in $(getent passwd | awk -F: '$3==0 || $3>=1000 {print $1}'); do
    c="$(crontab -l -u "$u" 2>/dev/null)" && printf '== %s\n%s\n\n' "$u" "$c"
  done
  echo "== /etc/cron.d"; grep -H . /etc/cron.d/* 2>/dev/null
} | redact > "$OUT/services/crontabs.txt"

mapfile -t UNITS < <( { systemctl list-units --all --no-legend --plain; systemctl list-unit-files --no-legend; } \
                      | awk '{print $1}' | grep -Ei "$UNIT_RE" | sort -u)
for u in "${UNITS[@]}"; do
  d="$OUT/services/units/$u"; mkdir -p "$d"
  systemctl cat "$u" 2>&1 | redact > "$d/unit.txt"
  systemctl show "$u" -p ActiveState,SubState,Result,NRestarts,ExecMainStartTimestamp,ExecMainStatus,MemoryCurrent,LastTriggerUSec > "$d/state.txt" 2>&1
  journalctl -u "$u" --since "-${DAYS}d" --no-pager -o short-iso 2>&1 | redact > "$d/journal.full"
  grep -Ei 'error|exception|traceback|critical|fail|timeout|refused|killed|oom' "$d/journal.full" | tail -n 3000 > "$d/journal.errors.txt"
  tail -n 3000 "$d/journal.full" > "$d/journal.tail.txt"
  rm -f "$d/journal.full"
done

if command -v docker >/dev/null; then
  cap services/docker.txt "docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'; echo; docker stats --no-stream"
  for c in $(docker ps -a --format '{{.Names}}' | grep -Ei "$UNIT_RE"); do
    docker logs --since "${DAYS}d" --tail 3000 "$c" 2>&1 | redact > "$OUT/services/docker-$c.log"
  done
fi

# ---------- inference endpoints ----------
note "llm endpoints"
for p in $LLM_PORTS; do
  {
    for path in /health /v1/models /props /slots; do
      echo "== :$p$path"; curl -sS -m 10 "http://localhost:$p$path"; echo
    done
    echo "== /metrics (head)"; curl -sS -m 10 "http://localhost:$p/metrics" | head -n 100; echo
    echo "== timing"; curl -sS -m 30 -o /dev/null -w 'connect=%{time_connect}s total=%{time_total}s http=%{http_code}\n' "http://localhost:$p/v1/models"
  } > "$OUT/llm/port-$p.txt" 2>&1
done

# ---------- qdrant ----------
note "qdrant"
QH=(); [[ -n "${QDRANT_API_KEY:-}" ]] && QH=(-H "api-key: $QDRANT_API_KEY")
{
  echo "== /"; curl -sS -m 10 "${QH[@]}" "$QDRANT_URL/"; echo
  echo "== /cluster"; curl -sS -m 10 "${QH[@]}" "$QDRANT_URL/cluster"; echo
  curl -sS -m 10 "${QH[@]}" "$QDRANT_URL/collections" \
    | python3 -c 'import sys,json; [print(c["name"]) for c in json.load(sys.stdin)["result"]["collections"]]' 2>/dev/null \
    | while read -r c; do echo "== $c"; curl -sS -m 10 "${QH[@]}" "$QDRANT_URL/collections/$c"; echo; done
} > "$OUT/qdrant/collections.txt" 2>&1

# ---------- repo / code ----------
note "repo"
G=(git -c safe.directory='*' -C "$APP_DIR")
if "${G[@]}" rev-parse >/dev/null 2>&1; then
  "${G[@]}" status --porcelain=v1 -b                                  > "$OUT/repo/git-status.txt" 2>&1
  "${G[@]}" branch -vv; "${G[@]}" stash list                         >> "$OUT/repo/git-status.txt" 2>&1
  "${G[@]}" remote -v | redact                                       >> "$OUT/repo/git-status.txt" 2>&1
  "${G[@]}" log -200 --date=iso --pretty='%h %ad %an %s'              > "$OUT/repo/git-log.txt" 2>&1
  "${G[@]}" log --since="45 days ago" --stat --date=iso               > "$OUT/repo/git-log-45d-stat.txt" 2>&1
  "${G[@]}" diff HEAD | redact                                        > "$OUT/repo/git-uncommitted.diff" 2>&1
else
  note "APP_DIR is not a git repo"
fi
find "$APP_DIR" \( "${PRUNE[@]}" \) -prune -o -type f -printf '%TY-%Tm-%Td %TH:%TM %10s %P\n' 2>/dev/null \
  | sort -k4 > "$OUT/repo/file-inventory.txt"
{ echo "== $PY"; "$PY" -V; "$PY" -m pip freeze; } > "$OUT/repo/python-env.txt" 2>&1

# configs, redacted, path-preserving
( cd "$APP_DIR" && find . \( "${PRUNE[@]}" \) -prune -o -type f -size -512k \( -name '.env*' -o -name '*.env' \
    -o -name '*.yaml' -o -name '*.yml' -o -name '*.toml' -o -name '*.ini' -o -name '*.cfg' -o -name '*.conf' \
    -o -name '*.json' -o -name 'SOUL*' \) -print 2>/dev/null ) \
  | while read -r rel; do
      mkdir -p "$OUT/repo/config/$(dirname "$rel")"
      redact < "$APP_DIR/$rel" > "$OUT/repo/config/$rel"
    done

# source tarball (no env/json/yaml/db/logs/models)
if [[ "$INCLUDE_SRC" == 1 ]]; then
  ( cd "$APP_DIR" && find . \( "${PRUNE[@]}" \) -prune -o -type f -size -2M \( -name '*.py' -o -name '*.html' \
      -o -name '*.js' -o -name '*.css' -o -name '*.j2' -o -name '*.jinja*' -o -name '*.sql' -o -name '*.sh' \
      -o -name '*.md' -o -name '*.service' -o -name '*.timer' -o -name 'requirements*.txt' -o -name 'Makefile' \
      -o -name 'Dockerfile*' \) -print0 ) \
    | tar -C "$APP_DIR" --null -T - -czf "$OUT/repo/source.tar.gz" 2>>"$LOG"
fi

# static checks
EXD=(--exclude-dir=.git --exclude-dir=venv --exclude-dir=.venv --exclude-dir=__pycache__ --exclude-dir=node_modules --exclude-dir=site-packages)
grep -rnIE "${EXD[@]}" "(api[_-]?key|secret|token|passw(or)?d|webhook)[\"']?[[:space:]]*[=:][[:space:]]*[\"'][A-Za-z0-9/_+=.:-]{12,}" "$APP_DIR" 2>/dev/null \
  | sed -E 's/([=:][[:space:]]*["'\'']).*/\1<value redacted>/' > "$OUT/repo/secret-scan.txt"
grep -rnIE "${EXD[@]}" --include='*.py' 'alpaca|tradeapi|ALPACA_|APCA_|paper-api|TradingClient|HistoricalDataClient|OptionHistorical' "$APP_DIR" \
  > "$OUT/repo/broker-coupling.txt" 2>/dev/null
grep -rnIiE "${EXD[@]}" 'hackathon|PA3Y2DOOQXZW' "$APP_DIR" "$APP_HOME" /etc/systemd /etc/cron.d 2>/dev/null \
  | grep -v "^$OUT" | redact > "$OUT/repo/hackathon-remnants.txt"
grep -rnIE "${EXD[@]}" --include='*.py' 'or True\b|and False\b|if (True|False|0|1):|except:[[:space:]]*$|except Exception:[[:space:]]*pass|#[[:space:]]*(TODO|FIXME|XXX|HACK)' "$APP_DIR" \
  > "$OUT/repo/code-smells.txt" 2>/dev/null
"$PY" - "$APP_DIR" > "$OUT/repo/syntax-check.txt" 2>&1 <<'PYEOF'
import os, sys
root = sys.argv[1]; bad = 0; n = 0
skip = {".git", "venv", ".venv", "__pycache__", "node_modules", "site-packages"}
for d, dirs, files in os.walk(root):
    dirs[:] = [x for x in dirs if x not in skip and not x.startswith("venv")]
    for f in files:
        if f.endswith(".py"):
            p = os.path.join(d, f); n += 1
            try: compile(open(p, encoding="utf-8", errors="replace").read(), p, "exec")
            except SyntaxError as e: bad += 1; print(f"SYNTAX {p}:{e.lineno}: {e.msg}")
print(f"checked={n} syntax_errors={bad}")
PYEOF

# ---------- database ----------
if [[ -n "$DB_PATH" && -f "$DB_PATH" ]]; then
  note "database"
  ls -la "$(dirname "$DB_PATH")"/*.db* > "$OUT/db/files.txt" 2>&1
  "$PY" - "$DB_PATH" "$OUT/db" "$INCLUDE_DB" > "$OUT/db/collector.txt" 2>&1 <<'PYEOF'
import re, sqlite3, sys
db, out, do_backup = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
q = lambda sql: con.execute(sql).fetchall()
w = open(f"{out}/summary.txt", "w")
w.write(f"db: {db}\n")
for p in ("journal_mode", "page_size", "page_count", "freelist_count", "user_version"):
    w.write(f"{p}: {q(f'PRAGMA {p}')[0][0]}\n")
w.write(f"quick_check: {q('PRAGMA quick_check')[0][0]}\n")
w.write(f"foreign_key_violations: {len(q('PRAGMA foreign_key_check'))}\n\n")
with open(f"{out}/schema.sql", "w") as s:
    s.write(";\n\n".join(r[0] for r in q("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name")) + ";\n")
tables = [r[0] for r in q("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
for t in tables:
    cols = [r[1] for r in q(f'PRAGMA table_info("{t}")')]
    n = q(f'SELECT COUNT(*) FROM "{t}"')[0][0]
    w.write(f"== {t}: {n} rows\n")
    for c in cols:
        lc = c.lower()
        if re.search(r"(time|date|_at$|^ts$)", lc):
            mn, mx = q(f'SELECT MIN("{c}"), MAX("{c}") FROM "{t}"')[0]
            w.write(f"   {c}: min={mn} max={mx}\n")
        if re.search(r"(account|acct|strategy|mode)", lc):
            for v, k in q(f'SELECT "{c}", COUNT(*) FROM "{t}" GROUP BY 1 ORDER BY 2 DESC LIMIT 25'):
                w.write(f"   {c}={v!r}: {k}\n")
        if n and q(f'SELECT COUNT(*) FROM "{t}" WHERE "{c}" IS NOT NULL')[0][0] == 0:
            w.write(f"   {c}: ALL NULL\n")
w.close()
if do_backup:
    dst = sqlite3.connect(f"{out}/{db.rsplit('/', 1)[-1]}")
    con.backup(dst); dst.close()
print("ok")
PYEOF
  [[ -f "$OUT/db/$(basename "$DB_PATH")" ]] && gzip -9 "$OUT/db/$(basename "$DB_PATH")"
else
  note "paper_trading.db not found; set DB_PATH"
fi

# ---------- application logs ----------
note "logs"
{ find "$APP_DIR" "$APP_HOME" \( "${PRUNE[@]}" \) -prune -o -type f \( -name '*.log' -o -name '*.log.[0-9]*' -o -name '*.jsonl' \) -mtime -"$DAYS" -print 2>/dev/null
  find /var/log -type f -mtime -"$DAYS" 2>/dev/null | grep -Ei "$UNIT_RE"
} | sort -u | while read -r f; do
  case "$f" in *.gz) continue;; esac
  dest="$OUT/logs/$(echo "${f#/}" | sed 's#/#__#g')"
  tail -n 5000 "$f" | redact > "$dest"
  printf '%8d errors  %10s bytes  %s\n' "$(grep -ciE 'error|exception|traceback|critical' "$f")" "$(stat -c %s "$f")" "$f" >> "$OUT/logs/_index.txt"
done
cat "$OUT"/logs/*.log* "$OUT"/services/units/*/journal.errors.txt 2>/dev/null \
  | grep -iE 'error|exception|critical|traceback' \
  | sed -E 's/^[0-9T:., +-]+//; s/0x[0-9a-f]+/HEX/g; s/[0-9]+(\.[0-9]+)?/N/g' \
  | sort | uniq -c | sort -rn | head -150 > "$OUT/logs/_error-signatures.txt"

# ---------- reports ----------
note "reports"
find "$APP_DIR" "$APP_HOME" \( "${PRUNE[@]}" \) -prune -o -type f -size -5M -mtime -60 \
     \( -ipath '*report*' -o -ipath '*post_mortem*' -o -ipath '*postmortem*' -o -ipath '*briefing*' -o -ipath '*calibration*' \) \
     \( -name '*.md' -o -name '*.html' -o -name '*.json' -o -name '*.txt' -o -name '*.csv' -o -name '*.pdf' \) -print 2>/dev/null \
  | sort -u | while read -r f; do cp -p "$f" "$OUT/reports/$(echo "${f#/}" | sed 's#/#__#g')"; done

# ---------- broker state (Alpaca paper, read-only GETs) ----------
note "broker"
(
  ENVF="$(find "$APP_DIR" -maxdepth 2 -name '.env' -print -quit)"
  set -a; [[ -n "$ENVF" ]] && . "$ENVF" 2>/dev/null; set +a
  K="${ALPACA_API_KEY:-${APCA_API_KEY_ID:-}}"
  S="${ALPACA_SECRET_KEY:-${ALPACA_API_SECRET:-${APCA_API_SECRET_KEY:-}}}"
  [[ -n "$K" && -n "$S" ]] || { echo "Alpaca creds not found (checked ${ENVF:-no .env})"; exit 0; }
  AFTER="$(date -u -d '-90 days' +%Y-%m-%dT%H:%M:%SZ)"
  for ep in "account" "account/configurations" "positions" \
            "orders?status=all&limit=500&direction=desc&nested=true&after=$AFTER" \
            "account/activities?after=$AFTER&page_size=100&direction=desc" \
            "account/portfolio/history?period=3M&timeframe=1D"; do
    fn="$(echo "${ep%%\?*}" | tr '/' '_')"
    curl -sS -m 30 -H "APCA-API-KEY-ID: $K" -H "APCA-API-SECRET-KEY: $S" "$ALPACA_BASE/v2/$ep" > "$OUT/broker/$fn.json"
  done
  echo "broker ok"
) >> "$LOG" 2>&1

# ---------- package ----------
find "$OUT" -type f -empty -delete
tar -C /tmp -czf "/tmp/$NAME.tar.gz" "$NAME"
[[ -n "${SUDO_USER:-}" ]] && chown "$SUDO_USER" "/tmp/$NAME.tar.gz"
SZ=$(du -m "/tmp/$NAME.tar.gz" | cut -f1)
note "bundle: /tmp/$NAME.tar.gz (${SZ} MB)"
(( SZ > 30 )) && note "Over ~30 MB: re-run with INCLUDE_DB=0 (upload the DB separately) or a smaller DAYS."
note "Before uploading: review $OUT/repo/secret-scan.txt and spot-check $OUT/repo/config/."
