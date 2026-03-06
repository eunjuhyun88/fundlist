#!/usr/bin/env zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$REPO_DIR/.context"
LOG_FILE="$LOG_DIR/vc_ops.log"
ENV_FILE="$REPO_DIR/.context/telegram.env"
CONFIG_DIR="$REPO_DIR/config"
DIGEST_MODE="${1:-${VC_OPS_PUSH_MODE:-morning}}"

mkdir -p "$LOG_DIR"
cd "$REPO_DIR"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

/usr/bin/python3 "$REPO_DIR/fundlist.py" ops-sync \
  --alert-days "${VC_OPS_ALERT_DAYS:-14}" \
  --output "$REPO_DIR/data/reports/vc_ops_report.md" \
  >> "$LOG_FILE" 2>&1

PROGRAMS_RAW="${VC_OPS_PROGRAMS:-}"
PROGRAM_WATCHLIST_FILE="${VC_OPS_PROGRAM_FILE:-$CONFIG_DIR/program_watchlist.txt}"
if [[ -z "$PROGRAMS_RAW" && -f "$PROGRAM_WATCHLIST_FILE" ]]; then
  PROGRAMS_RAW="$(grep -v '^[[:space:]]*#' "$PROGRAM_WATCHLIST_FILE" | sed '/^[[:space:]]*$/d' | paste -sd, -)"
fi
PROGRAMS_RAW="${PROGRAMS_RAW:-alliance dao}"
PROGRAM_REPORT_DIR="$REPO_DIR/data/reports/program_reports"
mkdir -p "$PROGRAM_REPORT_DIR"

IFS=',' read -rA programs <<< "$PROGRAMS_RAW"
for program in "${programs[@]}"; do
  p="$(echo "$program" | xargs)"
  if [[ -z "$p" ]]; then
    continue
  fi
  slug="$(echo "$p" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/_/g; s/^_+//; s/_+$//')"
  if [[ -z "$slug" ]]; then
    slug=program
  fi
  /usr/bin/python3 "$REPO_DIR/fundlist.py" ops-program-report \
    --skip-import \
    --program "$p" \
    --alert-days "${VC_OPS_ALERT_DAYS:-21}" \
    --output "$PROGRAM_REPORT_DIR/${slug}_submission_report.md" \
    >> "$LOG_FILE" 2>&1
done

if [[ "${VC_SUBMISSION_SCAN:-1}" != "0" ]]; then
  SUBMISSION_ARGS=(
    --max-sites "${VC_SUBMISSION_MAX_SITES:-120}"
    --max-pages-per-site "${VC_SUBMISSION_MAX_PAGES:-6}"
    --max-results-per-query "${VC_SUBMISSION_MAX_RESULTS_PER_QUERY:-10}"
    --http-timeout "${VC_SUBMISSION_HTTP_TIMEOUT:-8}"
    --min-score "${VC_SUBMISSION_MIN_SCORE:-4}"
    --event-limit "${VC_SUBMISSION_EVENT_LIMIT:-20}"
    --output "$REPO_DIR/data/reports/submission_targets_report.md"
  )
  if [[ "${VC_SUBMISSION_JSON_OUTPUT:-1}" != "0" ]]; then
    SUBMISSION_ARGS+=(--json-output "$REPO_DIR/data/reports/submission_targets.json")
  fi
  QUERY_FILE="${VC_SUBMISSION_QUERY_FILE:-$CONFIG_DIR/submission_queries.txt}"
  if [[ -f "$QUERY_FILE" ]]; then
    SUBMISSION_ARGS+=(--query-file "$QUERY_FILE")
  fi
  if [[ "${VC_SUBMISSION_USE_FUNDRAISE_SEEDS:-1}" != "1" ]]; then
    SUBMISSION_ARGS+=(--no-fundraise-seeds)
  else
    SUBMISSION_ARGS+=(--fundraise-seed-limit "${VC_SUBMISSION_FUNDRAISE_SEED_LIMIT:-300}")
  fi
  /usr/bin/python3 "$REPO_DIR/fundlist.py" submission-scan "${SUBMISSION_ARGS[@]}" >> "$LOG_FILE" 2>&1 || true
fi



if [[ "${VC_OPS_PUSH_TELEGRAM:-1}" != "0" ]]; then
  /usr/bin/python3 "$REPO_DIR/scripts/push_telegram_reports.py" \
    --mode "$DIGEST_MODE" \
    >> "$LOG_FILE" 2>&1 || true
fi
