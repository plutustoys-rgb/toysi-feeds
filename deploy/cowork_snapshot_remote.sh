#!/bin/bash
# VPS-side of the weekly Cowork snapshot (see cowork_snapshot_to_vps.cmd). Reads a tar stream on STDIN.
# Safe by construction: free-space check BEFORE extracting; extract into <dir>.part and rename only on success;
# rotation (keep the 2 newest snapshots) runs only after a successful extract, so a failed transfer never removes an older snapshot.
# Env (for tests): BACKUP_ROOT (default /opt/plutustoys_backup), MIN_FREE_GB (default 8), KEEP (default 2).
set -u
ROOT="${BACKUP_ROOT:-/opt/plutustoys_backup}"
MIN_FREE_GB="${MIN_FREE_GB:-8}"
KEEP="${KEEP:-2}"
DEST="$ROOT/cowork_$(date +%F)"

mkdir -p "$ROOT" && chmod 700 "$ROOT" || { echo "SNAPSHOT_FAILED: cannot create $ROOT"; exit 2; }
avail=$(df --output=avail -BG "$ROOT" | tail -1 | tr -dc 0-9)
if [ -z "$avail" ] || [ "$avail" -lt "$MIN_FREE_GB" ]; then
  echo "SNAPSHOT_FAILED: free space ${avail:-?} GB < required $MIN_FREE_GB GB - nothing extracted, older snapshots untouched"
  exit 3
fi
rm -rf "$DEST.part" && mkdir "$DEST.part" && chmod 700 "$DEST.part" || { echo "SNAPSHOT_FAILED: cannot create $DEST.part"; exit 2; }
if ! tar -xf - -C "$DEST.part"; then
  echo "SNAPSHOT_FAILED: tar extract error - partial data removed, older snapshots untouched"
  rm -rf "$DEST.part"
  exit 4
fi
files=$(find "$DEST.part" -type f | wc -l)
if [ "$files" -lt 100 ]; then
  echo "SNAPSHOT_FAILED: only $files files extracted (suspicious, expected thousands) - not published, older snapshots untouched"
  rm -rf "$DEST.part"
  exit 5
fi
rm -rf "$DEST" && mv "$DEST.part" "$DEST"
ls -1dt "$ROOT"/cowork_* 2>/dev/null | grep -v '\.part$' | tail -n +"$((KEEP + 1))" | xargs -r rm -rf
echo "SNAPSHOT_DONE: $DEST ($files files)"
du -sh "$ROOT"/cowork_* 2>/dev/null
df -h "$ROOT" | tail -1
