# Автозапуск на Windows: задача планировщика при входе в систему, перезапуск при сбое, без окна.
# Удалить: Unregister-ScheduledTask -TaskName backinsight-abtest -Confirm:$false
$dir = Split-Path $PSScriptRoot -Parent
$py = Join-Path $dir ".venv\Scripts\pythonw.exe"
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$dir\abtest.py`" loop" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "backinsight-abtest" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "backinsight-abtest"
Write-Host "Запущено. Лог: $dir\abtest\logs\abtest.log"
