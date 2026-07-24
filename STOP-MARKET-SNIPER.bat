@echo off
title STOP - Market Sniper (options)
cd /d "%~dp0"
echo Stopping Market Sniper (options app, port 8000)...
set FOUND=0
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
    set FOUND=1
)
if "%FOUND%"=="1" (
    echo   Stopped. The options app is fully shut down.
) else (
    echo   Nothing was running on port 8000 - already stopped.
)
echo.
echo You can close this window.
timeout /t 4 >nul
