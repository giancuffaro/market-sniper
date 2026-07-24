@echo off
title MARKET SNIPER
cd /d "%~dp0"

echo [1/3] Unblocking files...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%~dp0' -Recurse | Unblock-File" >nul 2>&1

echo [2/3] Stopping any old copy on port 8000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

echo [3/3] Starting...
if not exist ".venv" ( python -m venv .venv )
call .venv\Scripts\activate
pip install -q -r requirements.txt
set ALLOW_LIVE=1
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep 3; Start-Process 'http://127.0.0.1:8000'"
echo ==============================================================
echo   MARKET SNIPER running.
echo   Dashboard: http://127.0.0.1:8000  -  close window to stop.
echo   LIVE and PAPER are both chosen inside the app.
echo ==============================================================
python -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
