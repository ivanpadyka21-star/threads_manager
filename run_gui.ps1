# Bootstraps the virtual environment (creating it on first run) and launches
# the GUI. Run from anywhere:  powershell -ExecutionPolicy Bypass -File run_gui.ps1
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

# Activate for this shell (so the venv "starts" if you keep the window open)...
& (Join-Path $venv "Scripts\Activate.ps1")

Write-Host "[run] Launching Mobile E2E GUI..." -ForegroundColor Green
& $py -m mobile_e2e.gui
