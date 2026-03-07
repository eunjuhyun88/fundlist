#!/usr/bin/env zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_DIR="$HOME/.fundlist_bot_runtime"
LABEL="com.fundlist.telegrambot"
DOMAIN="gui/$(id -u)"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"
OUT_LOG="$RUN_DIR/.context/telegram_stdout.log"

mkdir -p "$PLIST_DIR" "$REPO_DIR/.context"
mkdir -p "$RUN_DIR"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude '.git/' \
    --exclude '.context/' \
    --exclude 'data/' \
    "$REPO_DIR/" "$RUN_DIR/"
else
  find "$RUN_DIR" -mindepth 1 -maxdepth 1 \
    ! -name '.context' \
    ! -name 'data' \
    -exec rm -rf {} +
  find "$REPO_DIR" -mindepth 1 -maxdepth 1 \
    ! -name '.git' \
    ! -name '.context' \
    ! -name 'data' \
    -exec cp -R {} "$RUN_DIR/" \;
fi

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>$RUN_DIR/scripts/run_telegram_bot.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$RUN_DIR</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$OUT_LOG</string>
  <key>StandardErrorPath</key>
  <string>$OUT_LOG</string>
</dict>
</plist>
EOF

launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
for _ in {1..10}; do
  if ! launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

launchctl bootstrap "$DOMAIN" "$PLIST_PATH"
launchctl enable "$DOMAIN/$LABEL"
launchctl kickstart -k "$DOMAIN/$LABEL"

echo "installed: $PLIST_PATH"
launchctl print "$DOMAIN/$LABEL" | sed -n '1,40p'
