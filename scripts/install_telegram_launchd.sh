#!/usr/bin/env zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_DIR="$HOME/.fundlist_bot_runtime"
LABEL="com.fundlist.telegrambot"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"
OUT_LOG="$RUN_DIR/.context/telegram_stdout.log"

mkdir -p "$PLIST_DIR" "$REPO_DIR/.context"
mkdir -p "$RUN_DIR"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete --exclude '.git/' "$REPO_DIR/" "$RUN_DIR/"
else
  rm -rf "$RUN_DIR"
  mkdir -p "$RUN_DIR"
  cp -R "$REPO_DIR/." "$RUN_DIR/"
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

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/$LABEL"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo "installed: $PLIST_PATH"
launchctl print "gui/$(id -u)/$LABEL" | sed -n '1,40p'
