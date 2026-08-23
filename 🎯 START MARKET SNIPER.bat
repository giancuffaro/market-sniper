@echo off
title MARKET SNIPER
cd /d "%~dp0"

rem  A crashed git leaves this behind and every later git command fails.
if exist ".git\index.lock" del /f /q ".git\index.lock" >nul 2>&1

rem  One-time leftovers. Harmless once they are gone.
if exist "_probe_delete.txt"  del /f /q "_probe_delete.txt"  >nul 2>&1
if exist "_probe_dir"         rd  /s /q "_probe_dir"         >nul 2>&1
if exist ".git\_probe"        del /f /q ".git\_probe"        >nul 2>&1
if exist ".git\_p2"           del /f /q ".git\_p2"           >nul 2>&1

echo [1/6] Preparing dependencies...
if not exist ".venv" ( python -m venv .venv )
call .venv\Scripts\activate
pip install -q -r requirements.txt
set ALLOW_LIVE=1

echo [2/6] Saving your local changes to GitHub FIRST...
rem  This must happen BEFORE the update below. The update mirrors GitHub onto
rem  this folder, so anything sitting here uncommitted would be destroyed by it.
rem  Committing and pushing first means the mirror has nothing left to destroy.
.venv\Scripts\python.exe auto_sync.py --once

echo [3/6] Updating from GitHub...
git fetch origin main

rem  Only mirror GitHub when nothing of ours is still unpushed. If step 2 could
rem  not reach GitHub (offline, sign-in expired), we skip the update entirely
rem  rather than trade your work for a clean sync.
set UNPUSHED=
for /f "delims=" %%i in ('git log --oneline origin/main..HEAD 2^>nul') do set UNPUSHED=1
set DIRTY=
for /f "delims=" %%i in ('git status --porcelain 2^>nul') do set DIRTY=1

if defined UNPUSHED goto :protect
if defined DIRTY   goto :protect

git reset --hard origin/main
goto :updated

:protect
echo.
echo       ################################################################
echo       #  Your folder has work that is not on GitHub yet.
echo       #  Skipping the update so nothing gets overwritten.
echo       #  The app still starts, using your local files.
echo       #  Check logs\auto-sync.log to see why the push did not land.
echo       ################################################################
echo.

:updated
echo [4/6] Unblocking files...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%~dp0' -Recurse | Unblock-File" >nul 2>&1

echo [5/6] Stopping any old copies (ports 8000 + 8010)...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8010 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq SNIPER-AUTOSYNC*" >nul 2>&1

echo [6/6] Starting auto-sync + BOTH apps (options 8000 + futures 8010)...
start "SNIPER-AUTOSYNC" /min cmd /c ".venv\Scripts\python.exe auto_sync.py"
start "SNIPER-OPTIONS"  /min cmd /c ".venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000"
start "SNIPER-FUTURES"  /min cmd /c ".venv\Scripts\python.exe -m uvicorn futures_app:app --host 127.0.0.1 --port 8010"
timeout /t 4 >nul
start "" http://127.0.0.1:8000
echo ==============================================================
echo   Options: http://127.0.0.1:8000    Futures: http://127.0.0.1:8010
echo   Switch between them with the buttons inside the app.
echo   The red X button in either app shuts BOTH down.
echo.
echo   Auto-sync is running. Every change to this folder is committed
echo   and pushed on its own - no git, no UPDATE.bat, no push.
echo   It refuses to push code that does not compile, and never
echo   commits my-settings.json. Log: logs\auto-sync.log
echo ==============================================================
timeout /t 8 >nul
