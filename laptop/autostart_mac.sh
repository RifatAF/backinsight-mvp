#!/usr/bin/env bash
# Автозапуск на macOS: бот стартует при входе в систему и перезапускается при падении.
# Удалить: launchctl unload ~/Library/LaunchAgents/com.backinsight.abtest.plist && rm ~/Library/LaunchAgents/com.backinsight.abtest.plist
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(pwd)"; PL="$HOME/Library/LaunchAgents/com.backinsight.abtest.plist"
mkdir -p "$HOME/Library/LaunchAgents" abtest/logs
cat > "$PL" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.backinsight.abtest</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/caffeinate</string><string>-i</string>
    <string>$DIR/.venv/bin/python</string><string>$DIR/abtest.py</string><string>loop</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$DIR/abtest/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$DIR/abtest/logs/launchd.err.log</string>
</dict></plist>
PLIST
launchctl unload "$PL" 2>/dev/null || true
launchctl load "$PL"
echo "Запущено. Проверка: tail -f abtest/logs/abtest.log"
