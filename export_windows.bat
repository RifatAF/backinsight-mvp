@echo off
chcp 65001 >nul
cd /d "%~dp0"
.venv\Scripts\python abtest.py export
explorer abtest\exports
pause
