@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo == 1/2 Установка Python-окружения и проверка данных
powershell -NoProfile -ExecutionPolicy Bypass -File laptop\setup.ps1 %*
if errorlevel 1 (
  echo Установка не удалась, см. сообщения выше.
  pause
  exit /b 1
)
echo.
echo == 2/2 Автозапуск (Windows попросит права администратора)
powershell -NoProfile -ExecutionPolicy Bypass -File laptop\autostart_windows.ps1
echo.
echo Проверка: check_windows.bat
pause
