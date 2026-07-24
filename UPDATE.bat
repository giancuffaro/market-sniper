@echo off
title UPDATE - Market Sniper
cd /d "%~dp0"
echo Pulling the latest version from GitHub...
echo.
git stash >nul 2>&1
git pull origin main
echo.
echo ==============================================================
echo   Update complete. Launch START-MARKET-SNIPER or START-FUTURES.
echo ==============================================================
echo You can close this window.
timeout /t 6 >nul
