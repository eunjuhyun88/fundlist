#!/usr/bin/env zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="$REPO_DIR/.context/cron.log"
SNAP_DIR="$REPO_DIR/.context/snapshots"
KEEP_SNAPSHOTS=40

mkdir -p "$SNAP_DIR"

echo "[$(/bin/date '+%Y-%m-%d %H:%M:%S %Z')] auto-context start" >> "$LOG_FILE"

/usr/bin/python3 "$REPO_DIR/scripts/context_ctl.py" save \
  --label auto \
  --summary "- 자동 주기 저장 (3시간 주기)" >> "$LOG_FILE" 2>&1

/usr/bin/python3 "$REPO_DIR/scripts/context_ctl.py" compact >> "$LOG_FILE" 2>&1

# Keep only latest N snapshots to prevent unbounded growth.
if [ -d "$SNAP_DIR" ]; then
  count=$(ls -1 "$SNAP_DIR"/*.md 2>/dev/null | wc -l | tr -d ' ')
  if [ "$count" -gt "$KEEP_SNAPSHOTS" ]; then
    i=0
    for file in $(ls -1t "$SNAP_DIR"/*.md); do
      i=$((i + 1))
      if [ "$i" -gt "$KEEP_SNAPSHOTS" ]; then
        rm -f "$file"
      fi
    done
  fi
fi

echo "[$(/bin/date '+%Y-%m-%d %H:%M:%S %Z')] auto-context done" >> "$LOG_FILE"

