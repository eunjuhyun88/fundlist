#!/usr/bin/env zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MARKER="# fundlist-vc-ops-hourly"
SCHEDULE="${VC_OPS_CRON_SCHEDULE:-0 * * * *}"
ENTRY="$SCHEDULE /bin/zsh \"$REPO_DIR/scripts/vc_ops_cron.sh\""

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

(crontab -l 2>/dev/null || true) | awk '
  !/fundlist-vc-ops-hourly/ && !/scripts\/vc_ops_cron\.sh/
' > "$TMP"

{
  echo "$MARKER"
  echo "$ENTRY"
} >> "$TMP"

crontab "$TMP"

echo "installed vc-ops cron:"
crontab -l | sed -n '1,200p'
