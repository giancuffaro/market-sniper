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
if "%FOUND%"=="1" (
    echo   Done - everything is shut down.
) else (
    echo   Nothing was running.
)
echo (Tip: the red X inside the app does this too.)
timeout /t 4 >nul
