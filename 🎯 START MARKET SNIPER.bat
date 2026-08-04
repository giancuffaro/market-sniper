@echo off
title MARKET SNIPER
cd /d "%~dp0"

echo [0/4] Updating from GitHub (exact mirror - always lands)...
rem  fetch + hard reset makes this folder EXACTLY match GitHub, so an update can
rem  never get stuck on a leftover stash or a merge conflict the way git pull
rem  could. Your saved setup is safe: my-settings.json, data\ and logs are all
rem  gitignored, so git never touches them.
git fetch origin main
git reset --hard origin/main

echo [1/4] Unblocking files...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%~dp0' -Recurse | Unblock-File" >nul 2>&1

echo [2/4] Stopping any old copies (ports 8000 + 8010)...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8010 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

echo [3/4] Preparing dependencies...
if not exist ".venv" ( python -m venv .venv )
call .venv\Scripts\activate
pip install -q -r requirements.txt
set ALLOW_LIVE=1

echo [4/4] Starting BOTH apps (options 8000 + futures 8010)...
start "SNIPER-OPTIONS" /min cmd /c ".venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000"
start "SNIPER-FUTURES" /min cmd /c ".venv\Scripts\python.exe -m uvicorn futures_app:app --host 127.0.0.1 --port 8010"
timeout /t 4 >nul
start "" http://127.0.0.1:8000
echo ==============================================================
echo   Options: http://127.0.0.1:8000    Futures: http://127.0.0.1:8010
echo   Switch between them with the buttons inside the app.
echo   The red X button in either app shuts BOTH down.
echo ==============================================================
timeout /t 8 >nul
