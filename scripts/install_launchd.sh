#!/usr/bin/env bash
# Installs (or reinstalls) the launchd job that runs the engine daily at 10:00 (or the hour given as $1).
# The engine itself only searches if >= min_days_between_runs have passed since the last run.
# If the Mac was asleep at that time, launchd runs it on wake.
set -euo pipefail

LABEL="com.aleix.linkedin-engine"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/linkedin-engine.log"
HOUR="${1:-10}"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$UV</string><string>run</string><string>--project</string><string>$PROJECT_DIR</string>
    <string>engine</string><string>run</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $PLIST (daily at $HOUR:00). Log: $LOG"
echo "Run now:   launchctl kickstart gui/$(id -u)/$LABEL"
echo "Uninstall: launchctl bootout gui/$(id -u)/$LABEL && rm $PLIST"
