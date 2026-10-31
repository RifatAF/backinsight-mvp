#!/usr/bin/env bash
# Ручной запуск в окне терминала (Ctrl+C для остановки). На macOS не даёт ноутбуку уснуть, пока работает.
cd "$(dirname "$0")/.."
if command -v caffeinate >/dev/null; then exec caffeinate -i .venv/bin/python abtest.py loop; fi
exec .venv/bin/python abtest.py loop
