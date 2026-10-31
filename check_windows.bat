@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -Command "Get-ScheduledTask -TaskName backinsight-abtest | Select-Object TaskName,State | Format-Table -AutoSize"
.venv\Scripts\python abtest.py health
echo.
.venv\Scripts\python abtest.py report
echo.
echo Последние строки лога:
powershell -NoProfile -Command "Get-Content abtest\logs\abtest.log -Tail 15 -Encoding UTF8"
pause
