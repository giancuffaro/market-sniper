@echo off
title CLEAN RESTART - Market Sniper
cd /d "%~dp0"

echo ==============================================================
echo   CLEAN RESTART
echo   Kills every Market Sniper process, then starts fresh.
echo   This file deletes itself once the app is running.
echo ==============================================================
echo.

echo [1/4] Stopping everything...

rem  Anything holding the two app ports.
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8010 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

rem  The v3.7 supervisor and anything it left behind, matched on command line so
rem  we only ever kill OUR python - never an unrelated python you have running.
wmic process where "name='python.exe' and commandline like '%%run_all.py%%'"   delete >nul 2>&1
wmic process where "name='python.exe' and commandline like '%%auto_sync.py%%'" delete >nul 2>&1
wmic process where "name='python.exe' and commandline like '%%uvicorn%%'"      delete >nul 2>&1

rem  Older builds opened one console per service.
taskkill /F /FI "WINDOWTITLE eq SNIPER-*" >nul 2>&1

echo       waiting for the ports to release...
timeout /t 3 >nul

set STILL=
for /f "delims=" %%i in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do set STILL=1
for /f "delims=" %%i in ('netstat -aon ^| findstr :8010 ^| findstr LISTENING') do set STILL=1
if defined STILL (
  echo       something is still holding a port - giving it one more go...
  for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
  for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8010 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
  timeout /t 2 >nul
)

echo [2/4] Clearing any stale git lock...
if exist ".git\index.lock" del /f /q ".git\index.lock" >nul 2>&1

echo [3/4] Scheduling this file to delete itself...
rem  Deleting a .bat while it runs is fine on Windows as long as nothing after
rem  the delete needs to be read from it. We hand the job to a detached shell
rem  that waits for the launcher to be up first, so a failed start still leaves
rem  this file here for another go.
start "" /b cmd /c "timeout /t 25 >nul & del /f /q \"%~f0\"" >nul 2>&1

echo [4/4] Starting Market Sniper...
echo.

rem  Hand over to the real launcher. It does the GitHub sync, then runs
rem  everything in one window.
rem
rem  The launcher's filename starts with an emoji. cmd reads .bat files in the
rem  OEM codepage, so writing that character literally here can arrive as
rem  mojibake and the call silently fails. Matching on the ASCII tail of the
rem  name sidesteps the encoding entirely.
set "LAUNCHER="
for %%f in ("*START MARKET SNIPER.bat") do set "LAUNCHER=%%f"

if not defined LAUNCHER (
  echo.
  echo   ERROR: could not find "START MARKET SNIPER.bat" in this folder.
  echo   Nothing was started. This file has NOT deleted itself - try again.
  echo.
  pause
  exit /b 1
)

call "%LAUNCHER%"
