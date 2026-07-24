@echo off
title STOP - Futures (MNQ/MES)
cd /d "%~dp0"
echo Stopping Futures app (port 8010)...
set FOUND=0
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8010 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
    set FOUND=1
)
if "%FOUND%"=="1" (
    echo   Stopped. The futures app is fully shut down.
) else (
    echo   Nothing was running on port 8010 - already stopped.
)
echo.
echo You can close this window.
timeout /t 4 >nul
