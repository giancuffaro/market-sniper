@echo off
title UPDATE - Market Sniper
cd /d "%~dp0"
echo Pulling the latest version from GitHub...
echo.
git stash >nul 2>&1
git pull origin main
echo.
echo Update complete - closing in 5 seconds...
timeout /t 5 >nul
exit
