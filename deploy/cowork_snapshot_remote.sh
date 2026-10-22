#!/bin/bash
# VPS-side of the weekly Cowork snapshot (see cowork_snapshot_to_vps.cmd). Reads a tar stream on STDIN.
# Usage: cowork_snapshot_remote.sh [EXPECTED_FILES]   (the Windows side counts the files it packed; a mismatch = not published)
# Safe by construction: free-space check BEFORE extracting; extract into <dir>.part and rename only on success;
# stale *.part (any date) removed at start; rotation (keep the 2 newest) only after a successful publish,
# so a failed transfer never removes an older snapshot.
# Env (for tests): BACKUP_ROOT (default /opt/plutustoys_backup), MIN_FREE_GB (default 8), KEEP (default 2).
set -u
ROOT="${BACKUP_ROOT:-/opt/plutustoys_backup}"
MIN_FREE_GB="${MIN_FREE_GB:-8}"
KEEP="${KEEP:-2}"
EXPECTED="${1:-}"
case "$ROOT" in ""|"/"|"/opt"|"/root"|"/home") echo "SNAPSHOT_FAILED: refusing unsafe BACKUP_ROOT='$ROOT'"; exit 2;; esac
DEST="$ROOT/cowork_$(date +%F)"

mkdir -p "$ROOT" && chmod 700 "$ROOT" || { echo "SNAPSHOT_FAILED: cannot create $ROOT"; exit 2; }
rm -rf "$ROOT"/cowork_*.part          # leftovers of a killed run (any date) - dead weight on the same disk as orders.db
avail=$(df --output=avail -BG "$ROOT" | tail -1 | tr -dc 0-9)
if [ -z "$avail" ] || [ "$avail" -lt "$MIN_FREE_GB" ]; then
  echo "SNAPSHOT_FAILED: free space ${avail:-?} GB < required $MIN_FREE_GB GB - nothing extracted, older snapshots untouched"
  exit 3
fi
mkdir "$DEST.part" && chmod 700 "$DEST.part" || { echo "SNAPSHOT_FAILED: cannot create $DEST.part"; exit 2; }
if ! tar -xf - -C "$DEST.part"; then
  echo "SNAPSHOT_FAILED: tar extract error - partial data removed, older snapshots untouched"
  rm -rf "$DEST.part"
  exit 4
fi
files=$(find "$DEST.part" -type f | wc -l)
if [ -n "$EXPECTED" ] && [ "$files" -ne "$EXPECTED" ]; then
  echo "SNAPSHOT_FAILED: extracted $files files but the sender packed $EXPECTED - truncated/incomplete stream, not published, older snapshots untouched"
  rm -rf "$DEST.part"
  exit 6
fi
if [ "$files" -lt 100 ]; then
  echo "SNAPSHOT_FAILED: only $files files extracted (suspicious, expected thousands) - not published, older snapshots untouched"
  rm -rf "$DEST.part"
  exit 5
fi
# publish: keep a same-day predecessor until the new one is in place
if [ -d "$DEST" ]; then mv "$DEST" "$DEST.old" || { echo "SNAPSHOT_FAILED: cannot move previous $DEST"; rm -rf "$DEST.part"; exit 7; }; fi
if ! mv "$DEST.part" "$DEST"; then
  echo "SNAPSHOT_FAILED: cannot publish $DEST"
  [ -d "$DEST.old" ] && mv "$DEST.old" "$DEST"
  rm -rf "$DEST.part"
  exit 7
fi
rm -rf "$DEST.old"
ls -1dt "$ROOT"/cowork_* 2>/dev/null | grep -v '\.part$' | tail -n +"$((KEEP + 1))" | xargs -r -d '\n' rm -rf
echo "SNAPSHOT_DONE: $DEST ($files files)"
du -sh "$ROOT"/cowork_* 2>/dev/null
df -h "$ROOT" | tail -1
exit 0
