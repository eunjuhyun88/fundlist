#!/usr/bin/env zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

echo "[1/7] collect (openclaw,sec,coingecko)"
/usr/bin/python3 fundlist.py collect --sources openclaw,sec,coingecko

echo "[2/7] list check"
rows="$(/usr/bin/python3 fundlist.py list --limit 1)"
if [ -z "$rows" ] || [ "$rows" = "(no rows)" ]; then
  echo "ERROR: no rows found after collect" >&2
  exit 1
fi
echo "$rows"

echo "[3/7] context save"
/usr/bin/python3 scripts/context_ctl.py save --label check --summary "- runbook check executed"

echo "[4/7] context compact"
/usr/bin/python3 scripts/context_ctl.py compact

echo "[5/7] context restore check"
/usr/bin/python3 scripts/context_ctl.py restore --mode compact --path-only

echo "[6/7] fundraising pipeline check"
/usr/bin/python3 fundlist.py fundraise-run \
  --output "$REPO_DIR/data/reports/fundraising_report_check.md"

echo "[7/7] vc ops sync check"
/usr/bin/python3 fundlist.py ops-sync --skip-import \
  --output "$REPO_DIR/data/reports/vc_ops_report_check.md"

echo "OK: runbook check passed"
