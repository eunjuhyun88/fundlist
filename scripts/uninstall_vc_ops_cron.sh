#!/usr/bin/env zsh
set -euo pipefail

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

(crontab -l 2>/dev/null || true) | awk '
  !/fundlist-vc-ops-hourly/ && !/scripts\/vc_ops_cron\.sh/
' > "$TMP"

crontab "$TMP"

echo "removed vc-ops cron:"
crontab -l 2>/dev/null | sed -n '1,200p'
