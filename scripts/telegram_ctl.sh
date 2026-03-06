#!/usr/bin/env zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONTEXT_DIR="$REPO_DIR/.context"
PID_FILE="$CONTEXT_DIR/telegram_bot.pid"
OUT_LOG="$CONTEXT_DIR/telegram_stdout.log"
RUNNER="$REPO_DIR/scripts/run_telegram_bot.sh"

mkdir -p "$CONTEXT_DIR"

find_bot_pids() {
  local pids
  {
    if [[ -f "$PID_FILE" ]]; then
      pids="$(cat "$PID_FILE" 2>/dev/null || true)"
      if [[ -n "$pids" ]]; then
        for pid in ${(f)pids}; do
          if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
          fi
        done
      fi
    fi
    ps -Ao pid=,command= | awk '
      /run_telegram_bot\.sh/ && $0 !~ /telegram_ctl\.sh/ {print $1}
      /telegram_bot\.py/ {print $1}
    '
  } | awk '/^[0-9]+$/' | sort -u
}

is_running() {
  [[ -n "$(find_bot_pids | head -n 1)" ]]
}

store_pids() {
  local pids
  pids="$(find_bot_pids)"
  if [[ -n "$pids" ]]; then
    print -r -- "$pids" > "$PID_FILE"
  else
    rm -f "$PID_FILE"
  fi
}

detect_pid() {
  find_bot_pids | head -n 1
}

start_bot() {
  local pid
  if is_running; then
    store_pids
    echo "telegram bot already running pids=$(tr '\n' ',' < "$PID_FILE" | sed 's/,$//')"
    return 0
  fi
  nohup "$RUNNER" >>"$OUT_LOG" 2>&1 &
  pid="$!"
  echo "$pid" > "$PID_FILE"
  sleep 1
  if is_running; then
    store_pids
    echo "telegram bot started pids=$(tr '\n' ',' < "$PID_FILE" | sed 's/,$//')"
    return 0
  fi
  echo "telegram bot failed to start; recent logs:"
  tail -n 40 "$OUT_LOG" || true
  return 1
}

stop_bot() {
  local pids pid
  if ! is_running; then
    rm -f "$PID_FILE"
    echo "telegram bot is not running"
    return 0
  fi
  pids="$(find_bot_pids)"
  for pid in ${(f)pids}; do
    kill "$pid" 2>/dev/null || true
  done
  sleep 1
  pids="$(find_bot_pids)"
  for pid in ${(f)pids}; do
    kill -9 "$pid" 2>/dev/null || true
  done
  rm -f "$PID_FILE"
  echo "telegram bot stopped"
}

status_bot() {
  local pids
  if is_running; then
    store_pids
    pids="$(tr '\n' ',' < "$PID_FILE" | sed 's/,$//')"
    echo "telegram bot running pids=$pids"
  else
    echo "telegram bot stopped"
  fi
}

usage() {
  cat <<'USAGE'
Usage:
  ./scripts/telegram_ctl.sh start
  ./scripts/telegram_ctl.sh stop
  ./scripts/telegram_ctl.sh restart
  ./scripts/telegram_ctl.sh status
  ./scripts/telegram_ctl.sh logs [N]
USAGE
}

cmd="${1:-status}"
case "$cmd" in
  start)
    start_bot
    ;;
  stop)
    stop_bot
    ;;
  restart)
    stop_bot
    start_bot
    ;;
  status)
    status_bot
    ;;
  logs)
    tail -n "${2:-80}" "$OUT_LOG" || true
    ;;
  *)
    usage
    exit 2
    ;;
esac
