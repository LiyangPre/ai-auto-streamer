@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The project Python environment is missing.
    echo Create .venv and install requirements.txt as described in START_HERE.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -X utf8 scripts\start_studio.py
if errorlevel 1 pause
