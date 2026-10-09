#!/usr/bin/env bash
# Qdrant nightly snapshot backup (Replaces broken NFS rsync — Rem OPS-3 / t_b03fe213)
# Crash-consistent via the Qdrant snapshot API. Snapshots land root-owned in
# /qdrant/snapshots (bind to /var/lib/qdrant/snapshots), so we copy them out
# with `docker cp` (daemon runs as root) and clean up inside the container.
set -uo pipefail
QDRANT_URL="http://localhost:6333"
COLLECTION="trading_signals"
CONTAINER="qdrant"
CONT_SNAP_DIR="/qdrant/snapshots/$COLLECTION"
NAS_DIR="/mnt/qnap/vectorstore/qdrant-snapshots"
LOG="/mnt/qnap/timeseries/logs/qdrant_backup.log"
KEEP=7
ts() { date -Iseconds; }

mkdir -p "$NAS_DIR"
http=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$QDRANT_URL/collections/$COLLECTION/snapshots")
if [ "$http" != "200" ]; then
  echo "$(ts) ERROR: snapshot API returned HTTP $http for $COLLECTION" >> "$LOG"
  exit 1
fi
# Newest snapshot: names embed creation time (YYYY-MM-DD-HH-MM-SS), so lexicographic
# max is the newest. List inside the container to avoid host perms on root-owned files.
name=$(docker exec "$CONTAINER" sh -c "ls -1 '$CONT_SNAP_DIR' 2>/dev/null | grep -E '\.snapshot$' | sort | tail -1")
if [ -z "$name" ]; then
  echo "$(ts) ERROR: no snapshot found in $CONT_SNAP_DIR" >> "$LOG"
  exit 1
fi
dest="$NAS_DIR/$name"
if ! docker cp "$CONTAINER:$CONT_SNAP_DIR/$name" "$dest" 2>/dev/null; then
  echo "$(ts) ERROR: docker cp of $name failed" >> "$LOG"
  exit 1
fi
# Integrity: compare container-side size to the copy.
csize=$(docker exec "$CONTAINER" stat -c %s "$CONT_SNAP_DIR/$name" 2>/dev/null)
nsize=$(stat -c %s "$dest" 2>/dev/null)
if [ -z "$csize" ] || [ "$csize" != "$nsize" ]; then
  echo "$(ts) ERROR: size mismatch container=$csize nas=$nsize" >> "$LOG"
  rm -f "$dest"
  exit 1
fi
# Prune: keep newest $KEEP on the NAS.
ls -1t "$NAS_DIR"/*.snapshot 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
# Clean up the source inside the container (root-owned; container runs as root).
docker exec "$CONTAINER" rm -f "$CONT_SNAP_DIR/$name" "$CONT_SNAP_DIR/$name.checksum" 2>/dev/null || true
echo "$(ts) OK: $name ($csize bytes) -> $NAS_DIR" >> "$LOG"
