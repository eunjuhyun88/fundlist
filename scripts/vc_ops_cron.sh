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
  eval "$(/usr/bin/python3 "$REPO_DIR/scripts/load_env_exports.py" "$ENV_FILE")"
fi

OPS_SYNC_ARGS=(
  --alert-days "${VC_OPS_ALERT_DAYS:-14}"
  --output "$REPO_DIR/data/reports/vc_ops_report.md"
)
if [[ -n "${VC_FUNDRAISE_FILES:-}" ]]; then
  OPS_SYNC_ARGS+=(--files "$VC_FUNDRAISE_FILES")
fi

/usr/bin/python3 "$REPO_DIR/fundlist.py" ops-sync "${OPS_SYNC_ARGS[@]}" >> "$LOG_FILE" 2>&1

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
  FULL_SWEEP="${VC_SUBMISSION_FULL_SWEEP:-0}"
  DEFAULT_MAX_SITES="120"
  DEFAULT_FUNDRAISE_SEED_LIMIT="300"
  DEFAULT_REPORT_LIMIT="120"
  DEFAULT_MAX_RESULTS="10"
  if [[ "$FULL_SWEEP" == "1" ]]; then
    DEFAULT_MAX_SITES="500"
    DEFAULT_FUNDRAISE_SEED_LIMIT="5000"
    DEFAULT_REPORT_LIMIT="500"
    DEFAULT_MAX_RESULTS="0"
  fi
  SUBMISSION_ARGS=(
    --max-sites "${VC_SUBMISSION_MAX_SITES:-$DEFAULT_MAX_SITES}"
    --max-pages-per-site "${VC_SUBMISSION_MAX_PAGES:-6}"
    --max-results-per-query "${VC_SUBMISSION_MAX_RESULTS_PER_QUERY:-$DEFAULT_MAX_RESULTS}"
    --http-timeout "${VC_SUBMISSION_HTTP_TIMEOUT:-8}"
    --min-score "${VC_SUBMISSION_MIN_SCORE:-4}"
    --event-limit "${VC_SUBMISSION_EVENT_LIMIT:-20}"
    --report-limit "${VC_SUBMISSION_REPORT_LIMIT:-$DEFAULT_REPORT_LIMIT}"
    --output "$REPO_DIR/data/reports/submission_targets_report.md"
  )
  if [[ "${VC_SUBMISSION_JSON_OUTPUT:-1}" != "0" ]]; then
    SUBMISSION_ARGS+=(--json-output "$REPO_DIR/data/reports/submission_targets.json")
  fi
  if [[ "$FULL_SWEEP" == "1" || "${VC_SUBMISSION_SKIP_SEARCH:-0}" == "1" ]]; then
    SUBMISSION_ARGS+=(--skip-search)
  fi
  if [[ -n "${VC_SUBMISSION_SEED_URLS:-}" ]]; then
    SUBMISSION_ARGS+=(--seed-urls "$VC_SUBMISSION_SEED_URLS")
  fi
  QUERY_FILE="${VC_SUBMISSION_QUERY_FILE:-$CONFIG_DIR/submission_queries.txt}"
  if [[ -f "$QUERY_FILE" ]]; then
    SUBMISSION_ARGS+=(--query-file "$QUERY_FILE")
  fi
  if [[ "${VC_SUBMISSION_USE_FUNDRAISE_SEEDS:-1}" != "1" ]]; then
    SUBMISSION_ARGS+=(--no-fundraise-seeds)
  else
    SUBMISSION_ARGS+=(--fundraise-seed-limit "${VC_SUBMISSION_FUNDRAISE_SEED_LIMIT:-$DEFAULT_FUNDRAISE_SEED_LIMIT}")
  fi
  /usr/bin/python3 "$REPO_DIR/fundlist.py" submission-scan "${SUBMISSION_ARGS[@]}" >> "$LOG_FILE" 2>&1 || true
fi

if [[ "${VC_SUBMISSION_REVIEW_RETRY:-1}" != "0" ]]; then
  REVIEW_LIMIT="${VC_SUBMISSION_REVIEW_LIMIT:-30}"
  REVIEW_ARGS=(
    --review-targets-only
    --skip-search
    --no-fundraise-seeds
    --review-target-limit "$REVIEW_LIMIT"
    --max-sites "$REVIEW_LIMIT"
    --max-pages-per-site "${VC_SUBMISSION_MAX_PAGES:-6}"
    --http-timeout "${VC_SUBMISSION_HTTP_TIMEOUT:-8}"
    --min-score "${VC_SUBMISSION_MIN_SCORE:-4}"
    --event-limit "${VC_SUBMISSION_EVENT_LIMIT:-20}"
    --report-limit "${VC_SUBMISSION_REPORT_LIMIT:-120}"
    --output "$REPO_DIR/data/reports/submission_targets_report.md"
  )
  if [[ "${VC_SUBMISSION_JSON_OUTPUT:-1}" != "0" ]]; then
    REVIEW_ARGS+=(--json-output "$REPO_DIR/data/reports/submission_targets.json")
  fi
  /usr/bin/python3 "$REPO_DIR/fundlist.py" submission-scan "${REVIEW_ARGS[@]}" >> "$LOG_FILE" 2>&1 || true
fi

if [[ "${VC_SUBMISSION_FALLBACK:-1}" != "0" ]]; then
  FALLBACK_ARGS=(
    --limit "${VC_SUBMISSION_FAILURE_LIMIT:-20}"
    --output "$REPO_DIR/data/reports/submission_fallback_report.md"
    --json-output "$REPO_DIR/data/reports/submission_fallback.json"
    --refresh-submission-report "$REPO_DIR/data/reports/submission_targets_report.md"
    --refresh-submission-json "$REPO_DIR/data/reports/submission_targets.json"
  )
  /usr/bin/python3 "$REPO_DIR/fundlist.py" submission-fallback "${FALLBACK_ARGS[@]}" >> "$LOG_FILE" 2>&1 || true
fi



if [[ "${VC_OPS_PUSH_TELEGRAM:-1}" != "0" ]]; then
  /usr/bin/python3 "$REPO_DIR/scripts/push_telegram_reports.py" \
    --mode "$DIGEST_MODE" \
    >> "$LOG_FILE" 2>&1 || true
fi
