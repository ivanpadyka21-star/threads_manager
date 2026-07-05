# Bootstraps the virtual environment (creating it on first run) and starts the
# web app. Run from anywhere:  powershell -ExecutionPolicy Bypass -File run_web.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $root ".venv"
$py = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "[setup] Creating virtual environment..." -ForegroundColor Cyan
    python -m venv $venv
    & $py -m pip install --upgrade pip
    & $py -m pip install -r (Join-Path $root "mobile_e2e\requirements.txt")
}

& (Join-Path $venv "Scripts\Activate.ps1")

Write-Host "[run] Starting web app at http://127.0.0.1:5000 ..." -ForegroundColor Green
& $py -m mobile_e2e.web
