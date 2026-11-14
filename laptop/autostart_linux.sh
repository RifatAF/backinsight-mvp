#!/usr/bin/env bash
# Автозапуск на Linux (systemd --user): старт при включении, перезапуск при падении.
# Удалить: systemctl --user disable --now abtest && rm ~/.config/systemd/user/abtest.service
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(pwd)"; mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/abtest.service" <<UNIT
[Unit]
Description=backinsight A/B test (virtual money)
After=network-online.target
[Service]
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python $DIR/abtest.py loop
Restart=always
RestartSec=60
[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now abtest
loginctl enable-linger "$USER" 2>/dev/null || true
echo "Запущено. Проверка: systemctl --user status abtest; tail -f abtest/logs/abtest.log"
