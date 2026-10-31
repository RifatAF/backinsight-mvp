# Установка на Windows. PowerShell из папки репозитория:  powershell -ExecutionPolicy Bypass -File laptop\setup.ps1
param([int]$Capital = 10000, [string]$Coins = "BTC,ETH", [string]$Exchange = "bybit")
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Write-Host "Нужен Python 3.10+ с python.org (галочка Add to PATH)"; exit 1 }
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install -q --upgrade pip
.\.venv\Scripts\python -m pip install -q -r requirements.txt
Write-Host "== проверка доступа к данным"
.\.venv\Scripts\python abtest.py doctor --exchange $Exchange
if (-not (Test-Path abtest\state.json)) { .\.venv\Scripts\python abtest.py init --capital $Capital --coins $Coins --exchange $Exchange }
Write-Host "`nГотово. Автозапуск: powershell -ExecutionPolicy Bypass -File laptop\autostart_windows.ps1"
