# Установка на Windows. Обычно запускается из install_windows.bat.
#   powershell -ExecutionPolicy Bypass -File laptop\setup.ps1 [-Capital 100] [-Coins BTC,ETH] [-Exchange bybit]
param([double]$Capital = 100, [string]$Coins = "BTC,ETH", [string]$Exchange = "bybit")
Set-Location (Split-Path $PSScriptRoot -Parent)

function Find-Python {
    foreach ($c in "py -3", "python") {
        $exe, $rest = $c.Split(" ")
        try {
            $v = & $exe @rest -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
            if ($v -eq "True") { return $c }
        } catch {}
    }
    return $null
}

$pyc = Find-Python
if (-not $pyc -and (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "== Python не найден, ставлю Python 3.12 через winget"
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    $pyc = Find-Python
}
if (-not $pyc) {
    Write-Host "Нужен Python 3.10+ с python.org (при установке галочка Add python.exe to PATH), потом запустите снова."
    exit 1
}
$exe, $rest = $pyc.Split(" ")

if (-not (Test-Path .venv)) { & $exe @rest -m venv .venv }
.\.venv\Scripts\python -m pip install -q --upgrade pip
.\.venv\Scripts\python -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "Не удалось поставить зависимости, проверьте интернет."; exit 1 }

Write-Host "== Проверка доступа к данным"
.\.venv\Scripts\python abtest.py doctor --exchange $Exchange
if (-not (Test-Path abtest\state.json)) {
    .\.venv\Scripts\python abtest.py init --capital $Capital --coins $Coins --exchange $Exchange
}
Write-Host "`nУстановка готова."
