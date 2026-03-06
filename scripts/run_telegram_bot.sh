#!/usr/bin/env zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_DIR"

ENV_FILE="$REPO_DIR/.context/telegram.env"
if [[ -f "$ENV_FILE" ]]; then
  eval "$(/usr/bin/python3 "$REPO_DIR/scripts/load_env_exports.py" "$ENV_FILE")"
fi

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  echo "TELEGRAM_BOT_TOKEN is required (export env or set $ENV_FILE)" >&2
  exit 2
fi

PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
RUN_FOREVER="${TELEGRAM_RUN_FOREVER:-1}"
RUNNER_LOG="$REPO_DIR/.context/telegram_runner.log"

if [[ "$RUN_FOREVER" == "0" ]]; then
  exec "$PYTHON_BIN" "$REPO_DIR/scripts/telegram_bot.py"
fi

mkdir -p "$REPO_DIR/.context"
trap 'exit 0' INT TERM

while true; do
  set +e
  "$PYTHON_BIN" "$REPO_DIR/scripts/telegram_bot.py"
  code="$?"
  set -e
  printf '[%s] telegram_bot exited code=%s; restart in 3s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$code" >> "$RUNNER_LOG"
  sleep 3
done
