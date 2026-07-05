@echo off
REM Double-click this to bootstrap the venv (first run) and launch the GUI.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [setup] Creating virtual environment...
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r "mobile_e2e\requirements.txt"
)

echo [run] Launching Mobile E2E GUI...
".venv\Scripts\python.exe" -m mobile_e2e.gui
