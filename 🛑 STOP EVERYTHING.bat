@echo off
title STOP EVERYTHING - Market Sniper
cd /d "%~dp0"
echo Shutting down BOTH apps (options 8000 + futures 8010)...
set FOUND=0
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
    set FOUND=1
)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8010 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
    set FOUND=1
)
rem  Push anything outstanding BEFORE killing auto-sync, so shutting down can
rem  never be the reason a change failed to reach GitHub.
echo Flushing any unsaved work to GitHub...
if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe auto_sync.py --once
)
taskkill /F /FI "WINDOWTITLE eq SNIPER-AUTOSYNC*" >nul 2>&1

if "%FOUND%"=="1" (
    echo   Done - everything is shut down.
) else (
    echo   Nothing was running.
)
echo (Tip: the red X inside the app does this too.)
timeout /t 4 >nul
