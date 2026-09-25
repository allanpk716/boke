@echo off
rem boke one-click launcher (ASCII-only on purpose: Chinese in .bat breaks under GBK codepage)
title boke dub pipeline
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [X] .venv\Scripts\python.exe not found - create the main venv first
    pause
    exit /b 1
)

rem ---- already running? just open browser ----
netstat -ano | findstr ":8765" >nul 2>&1
if %errorlevel%==0 (
    echo [i] already running on :8765 - opening browser
    start http://localhost:8765
    exit /b 0
)

echo [i] starting web server at http://localhost:8765
echo [i] keep this window open, Ctrl+C to stop
echo [i] browser opens in 3s / library.html = import videos / review.html = voiceprint review

start "" /b cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:8765"

".venv\Scripts\python.exe" "work\tools\web_server.py"

echo.
echo [!] server exited. If you did not stop it manually, the error is above.
pause
