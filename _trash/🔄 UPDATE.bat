@echo off
title UPDATE - Market Sniper
cd /d "%~dp0"
echo Updating to the latest version from GitHub...
echo.
rem  Exact mirror (fetch + hard reset) instead of pull, so it always lands and
rem  never jams on a stash/merge. my-settings.json, data\ and logs are
rem  gitignored, so your saved setup is never touched.
git fetch origin main
git reset --hard origin/main
echo.
echo Update complete - closing in 5 seconds...
timeout /t 5 >nul
exit
