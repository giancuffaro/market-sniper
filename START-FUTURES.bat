@echo off
title FUTURES (MNQ/MES)
cd /d "%~dp0"
echo Unblocking any newly copied files...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%~dp0' -Recurse | Unblock-File" >nul 2>&1
echo Stopping any old copy on port 8010...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8010 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
if not exist ".venv" ( python -m venv .venv )
call .venv\Scripts\activate
pip install -q -r requirements.txt
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep 3; Start-Process 'http://127.0.0.1:8010'"
echo FUTURES app (MNQ/MES, paper) at http://127.0.0.1:8010 - runs alongside the options app.
python -m uvicorn futures_app:app --host 127.0.0.1 --port 8010
pause
