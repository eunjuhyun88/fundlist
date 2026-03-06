#!/usr/bin/env zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MARKER_MORNING="# fundlist-vc-ops-morning"
MARKER_EVENING="# fundlist-vc-ops-evening"
SCHEDULE_MORNING="${VC_OPS_CRON_SCHEDULE_MORNING:-0 9 * * *}"
SCHEDULE_EVENING="${VC_OPS_CRON_SCHEDULE_EVENING:-0 18 * * *}"
ENTRY_MORNING="$SCHEDULE_MORNING /bin/zsh \"$REPO_DIR/scripts/vc_ops_cron.sh\" morning"
ENTRY_EVENING="$SCHEDULE_EVENING /bin/zsh \"$REPO_DIR/scripts/vc_ops_cron.sh\" evening"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

(crontab -l 2>/dev/null || true) | awk '
  !/fundlist-vc-ops-morning/ &&
  !/fundlist-vc-ops-evening/ &&
  !/fundlist-vc-ops-hourly/ &&
  !/scripts\/vc_ops_cron\.sh/
' > "$TMP"

{
  echo "$MARKER_MORNING"
  echo "$ENTRY_MORNING"
  echo "$MARKER_EVENING"
  echo "$ENTRY_EVENING"
} >> "$TMP"

crontab "$TMP"

echo "installed vc-ops cron:"
crontab -l | sed -n '1,200p'
