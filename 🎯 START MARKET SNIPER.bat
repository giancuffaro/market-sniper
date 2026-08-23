@echo off
title MARKET SNIPER
cd /d "%~dp0"

rem  A crashed git leaves lock files behind and every later git command fails.
rem  It is not just index.lock: HEAD.lock, config.lock and refs\heads\*.lock all
rem  do it. Sweeping only index.lock is why "cannot lock ref HEAD" could stick.
if exist ".git\index.lock"        del /f /q ".git\index.lock"        >nul 2>&1
if exist ".git\HEAD.lock"         del /f /q ".git\HEAD.lock"         >nul 2>&1
if exist ".git\config.lock"       del /f /q ".git\config.lock"       >nul 2>&1
if exist ".git\packed-refs.lock"  del /f /q ".git\packed-refs.lock"  >nul 2>&1
if exist ".git\ORIG_HEAD.lock"    del /f /q ".git\ORIG_HEAD.lock"    >nul 2>&1
del /f /q /s ".git\refs\*.lock" >nul 2>&1

rem  One-time leftovers. Harmless once they are gone.
if exist "_probe_delete.txt"  del /f /q "_probe_delete.txt"  >nul 2>&1
if exist "_probe_dir"         rd  /s /q "_probe_dir"         >nul 2>&1
if exist ".git\_probe"        del /f /q ".git\_probe"        >nul 2>&1
if exist ".git\_p2"           del /f /q ".git\_p2"           >nul 2>&1

echo [1/6] Preparing dependencies...
if not exist ".venv" ( python -m venv .venv )
call .venv\Scripts\activate

rem  Always call pip through the venv's OWN python. Relying on `activate` to put
rem  the venv first on PATH is not dependable - on this machine bare `pip` was
rem  resolving to the system Python 3.14 install, so packages went there while
rem  the app kept running from .venv and never saw them.
set "VPY=%~dp0.venv\Scripts\python.exe"
"%VPY%" -m pip install -q -r requirements.txt

rem  Tray icon deps: installed ONCE, and only when actually missing. Kept out of
rem  requirements.txt so this file, which runs every launch, does not re-check
rem  them every time.
"%VPY%" -c "import pystray, PIL" >nul 2>&1
if errorlevel 1 (
  echo       First run: installing the tray icon, one moment...
  "%VPY%" -m pip install -q pystray pillow
  "%VPY%" -c "import pystray, PIL" >nul 2>&1
  if errorlevel 1 (
    echo       Tray icon unavailable - app still works fine without it.
  ) else (
    echo       Tray icon installed.
  )
)

set ALLOW_LIVE=1

echo [2/6] Saving your local changes to GitHub FIRST...
rem  This must happen BEFORE the update below. The update mirrors GitHub onto
rem  this folder, so anything sitting here uncommitted would be destroyed by it.
rem  Committing and pushing first means the mirror has nothing left to destroy.
"%VPY%" auto_sync.py --once

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

echo [6/6] Starting everything in THIS window...
echo ==============================================================
echo   Options: http://127.0.0.1:8000    Futures: http://127.0.0.1:8010
echo   Switch between them with the buttons inside the app.
echo   The red X button in either app shuts BOTH down.
echo.
echo   Auto-sync is running. Every change to this folder is committed
echo   and pushed on its own - no git, no UPDATE.bat, no push.
echo   It refuses to push code that does not compile, and never
echo   commits my-settings.json. Log: logs\auto-sync.log
echo.
echo   THIS is the only window now. Closing it stops everything.
echo ==============================================================

rem  Open the browser after a beat, without blocking the console below.
start "" /b cmd /c "timeout /t 5 >nul & start "" http://127.0.0.1:8000"

rem  Runs in the foreground on purpose: its output IS this window, and closing
rem  this window takes the servers with it. No orphaned background consoles.
"%VPY%" run_all.py
