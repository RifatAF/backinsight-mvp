# Автозапуск на Windows. Запускается из install_windows.bat или так (сам попросит права администратора):
#   powershell -ExecutionPolicy Bypass -File laptop\autostart_windows.ps1
# Что делает:
#   - задача планировщика backinsight-abtest от вашего пользователя, работает БЕЗ входа в систему:
#     старт при включении компьютера, при входе, после пробуждения (сторож каждые 10 минут), перезапуск при сбое;
#   - будит ноутбук из сна в 00:03 UTC, чтобы дневной цикл прошёл по живому стакану;
#   - на питании от сети отключает сон и действие при закрытии крышки (флаг -NoPower, чтобы не трогать).
# Удалить: Unregister-ScheduledTask -TaskName backinsight-abtest -Confirm:$false
param([switch]$NoPower, [string]$Exe = "", [string]$WorkDir = "", [switch]$Quiet)

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    $a = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    if ($NoPower) { $a += " -NoPower" }
    if ($Exe) { $a += " -Exe `"$Exe`" -WorkDir `"$WorkDir`"" }
    if ($Quiet) { $a += " -Quiet" }
    Start-Process powershell -Verb RunAs -ArgumentList $a -Wait
    exit
}

if ($Exe) {                                             # запуск из backinsight.exe
    $dir = $WorkDir
    $action = New-ScheduledTaskAction -Execute $Exe -Argument "loop" -WorkingDirectory $dir
} else {
    $dir = Split-Path $PSScriptRoot -Parent
    $py = Join-Path $dir ".venv\Scripts\pythonw.exe"
    if (-not (Test-Path $py)) { Write-Host "Сначала установка: laptop\setup.ps1"; Read-Host "Enter"; exit 1 }
    $action = New-ScheduledTaskAction -Execute $py -Argument "`"$dir\abtest.py`" loop" -WorkingDirectory $dir
}
$name = "backinsight-abtest"
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name


$boot = New-ScheduledTaskTrigger -AtStartup
$boot.Delay = "PT1M"                                   # подождать сеть после включения
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$watch = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 10)
$wakeAt = [DateTime]::SpecifyKind([DateTime]::UtcNow.Date.AddMinutes(3), [DateTimeKind]::Utc).ToLocalTime()
$wake = New-ScheduledTaskTrigger -Daily -At $wakeAt     # 00:03 UTC в местном времени

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -WakeToRun -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero)

# S4U: запуск без входа в систему и без хранения пароля. Если не вышло, только при входе.
try {
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
    Register-ScheduledTask -TaskName $name -Action $action -Trigger @($boot, $logon, $watch, $wake) `
      -Settings $settings -Principal $principal -Force -ErrorAction Stop | Out-Null
    Write-Host "Задача создана: запуск при включении компьютера, даже без входа в систему."
} catch {
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $name -Action $action -Trigger @($logon, $watch, $wake) `
      -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "Задача создана в режиме «при входе в систему» ($($_.Exception.Message))."
    Write-Host "Чтобы стартовало без входа, включите автовход: netplwiz, снять галочку «Требовать ввод имени пользователя и пароля»."
}

if (-not $NoPower) {
    powercfg /change standby-timeout-ac 0     | Out-Null   # не засыпать от сети
    powercfg /change hibernate-timeout-ac 0   | Out-Null
    powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0 | Out-Null   # крышка от сети: ничего не делать
    powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1 2>$null | Out-Null   # разрешить таймеры пробуждения
    powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1 2>$null | Out-Null
    powercfg /setactive SCHEME_CURRENT | Out-Null
    Write-Host "Питание: от сети ноутбук не засыпает, закрытая крышка не усыпляет."
}

Start-ScheduledTask -TaskName $name
Start-Sleep 5
$info = Get-ScheduledTask -TaskName $name
Write-Host "Состояние задачи: $($info.State)"
Write-Host "Лог: $dir\abtest\logs\abtest.log"
if (-not $Quiet) { Read-Host "Готово. Enter, чтобы закрыть" } else { Start-Sleep 3 }
