@echo off
rem boke test runner (ASCII-only on purpose: Chinese in .bat breaks under GBK codepage)
title boke tests
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [X] .venv\Scripts\python.exe not found - create the main venv first
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pytest tests\ -q

echo.
echo tests finished - press any key to close
pause >nul
