@echo off
title PUSH v3.6 - Market Sniper
cd /d "%~dp0"
chcp 65001 >nul

echo ==============================================================
echo   PUSHING v3.6 TO GITHUB
echo.
echo   This MUST run before you next launch the app.
echo   START MARKET SNIPER does 'git reset --hard origin/main',
echo   which WIPES any change that is not on GitHub yet.
echo ==============================================================
echo.

rem  A stale lock got left behind and could not be cleared automatically.
if exist ".git\index.lock" del /f /q ".git\index.lock"
if exist ".git\_probe"     del /f /q ".git\_probe"
if exist ".git\_p2"        del /f /q ".git\_p2"

echo [1/8] tape.py - market velocity module
git add tape.py
git commit -q -m "Velocity: new tape.py - how fast the market is moving vs the last half hour" -m "Volume rate plus range expansion over the last 5 one-minute bars against the previous 30, blended 60/40, scored 0-100 where 50 means moving exactly as fast as it has been. Trailing baseline so it self-corrects for the session's natural U-shape instead of screaming at the open." -m "Honest about what it is: BAR velocity, not trades-per-second. The only feed either app has is Yahoo 1-minute bars, and you cannot recover individual prints from those. Swap _bars() for a tick feed to upgrade; compute() is pure and would not change." -m "Fixed during testing: Yahoo publishes the current minute with a price but zero volume until it closes. Left in, that half-formed bar pinned acceleration at -100 on every symbol and made TSLA read 0.03x volume. Trailing zero-volume bars are now trimmed."

echo [2/8] velocity endpoints + UI strips
git add main.py futures_app.py
git commit -q -m "Velocity: /api/tape on both apps" -m "Read-only and broker-free - same public bar feed as the price chips, so it works before you connect and cannot affect an order."

echo [3/8] entry preview - backend
git add webull_client.py
git commit -q -m "Options: preview where an armed entry actually fires, before you press" -m "Extracted the trigger math out of arm() into entry_target(), and added preview_entry() on top of it. Both now read the same function, so the number on screen cannot drift away from the number that fires." -m "Rounding behaviour is UNCHANGED - still nearest whole dollar. Showing the real number first is the point; whether it should round directionally (calls floor, puts ceil) is a live-money decision to make after watching it for a session." -m "Also: futures accounts are now filtered out of the options account picker rather than labelled FUTURES. The by-ID guard is kept, so a futures-only key still gets an explanation instead of an empty list."

echo [4/8] preview endpoint
git add main.py
git commit -q -m "Options: /api/preview - read-only entry preview endpoint"

echo [5/8] remove PAPER and Tradovate
git add config.py futures_client.py
git commit -q -m "LIVE-ONLY: remove Webull sandbox from both apps and Tradovate from futures" -m "ALLOW_LIVE=1 (launcher-set) is now the single gate on every order either app can send. No session class can skip it." -m "Kept _tv_front_symbol and _fmt_px despite the Tradovate-derived names - Topstep and NinjaTrader both call them, and deleting them would have broken Topstep silently." -m "Futures modes are now WEBULL / NINJA / TOPSTEP. 'LIVE' used to mean NinjaTrader while 'WEBULL' meant Webull-live, which read backwards. normalize_mode() maps the old 'LIVE' onto NINJA so pre-v3.6 saved prefs still log in."

echo [6/8] UI for all of the above
git add index.html futures_index.html
git commit -q -m "UI: velocity strip, entry preview line, LIVE-only screens" -m "Removed the PAPER tick box and sandbox copy from options, and the PAPER and Tradovate panels from futures. Verified no JS references a DOM id that no longer exists."

echo [7/8] docs + version bump
git add README.md PROJECT-STATUS.md TUTORIAL.html .gitignore PLAN-v3.6.md
git commit -q -m "Docs: v3.6 - live-only build, velocity, entry preview" -m "Also ignore rotated webull_data_sdk logs."

echo.
echo [8/8] Pushing to GitHub...
git push origin main
if errorlevel 1 goto failed

echo.
echo ==============================================================
echo   DONE - v3.6 is on GitHub.
echo   Safe to launch now. START MARKET SNIPER will pull it.
echo ==============================================================
echo.
git --no-pager log --oneline -7
echo.
pause
exit /b 0

:failed
echo.
echo ==============================================================
echo   PUSH FAILED - your work is COMMITTED but NOT on GitHub.
echo.
echo   DO NOT launch the app yet. A launch would reset the folder
echo   to GitHub and throw away everything above.
echo.
echo   Most likely cause: GitHub wants you to sign in.
echo   Fix it, then just run this file again - the commits are
echo   already made, so it will go straight to the push.
echo ==============================================================
echo.
pause
exit /b 1
