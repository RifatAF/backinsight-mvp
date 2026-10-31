#!/usr/bin/env bash
# Установка на macOS / Linux. Запуск из папки репозитория:  bash laptop/setup.sh
# Параметры (необязательно): CAPITAL=10000 COINS=BTC,ETH EXCHANGE=bybit bash laptop/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
CAPITAL="${CAPITAL:-10000}"; COINS="${COINS:-BTC,ETH}"; EXCHANGE="${EXCHANGE:-bybit}"
command -v python3 >/dev/null || { echo "Нужен Python 3.10+: https://www.python.org/downloads/"; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3, 10), "нужен Python 3.10+"'
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
echo "== проверка доступа к данным"
.venv/bin/python abtest.py doctor --exchange "$EXCHANGE" || true
if [ ! -f abtest/state.json ]; then
  .venv/bin/python abtest.py init --capital "$CAPITAL" --coins "$COINS" --exchange "$EXCHANGE"
else
  echo "счета уже созданы (abtest/state.json), пропускаю init"
fi
echo
echo "Готово. Дальше:"
echo "  автозапуск macOS:  bash laptop/autostart_mac.sh"
echo "  автозапуск Linux:  bash laptop/autostart_linux.sh"
echo "  или вручную:       bash laptop/start.sh"
