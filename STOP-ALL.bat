@echo off
title STOP ALL - Market Sniper
cd /d "%~dp0"
echo Shutting down EVERYTHING (options + futures)...
echo.
set FOUND=0
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
    set FOUND=1
)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8010 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
    set FOUND=1
)
if "%FOUND%"=="1" (
    echo   Done - both apps are fully shut down. No PC restart needed.
) else (
    echo   Nothing was running - already fully stopped.
)
echo.
echo You can close this window.
timeout /t 4 >nul
