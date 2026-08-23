@echo off
title MARKET SNIPER - one-time install
cd /d "%~dp0"
echo ============================================================
echo   MARKET SNIPER - one-time install (Webull SDK + deps)
echo ============================================================
echo [1/5] Telling Windows these files are safe...
rem  Anything arriving in a downloaded ZIP carries "Mark of the Web" and Windows
rem  refuses to run it. This clears that flag on every file here, including the
rem  .bat and .vbs launchers. (Cloning with git avoids the flag entirely.)
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%~dp0' -Recurse | Unblock-File" >nul 2>&1
echo [2/5] Building the private Python environment...
if not exist ".venv" ( python -m venv .venv )
call .venv\Scripts\activate
python -m pip install --upgrade pip

echo [3/5] Installing app dependencies...
pip install -q -r requirements.txt
if errorlevel 1 goto coredeps_failed

echo [4/5] Installing the Webull SDK...
python -m pip install --upgrade webull-openapi-python-sdk

echo [5/5] Installing the tray icon (optional)...
rem  Optional on purpose. If this fails the app still runs perfectly - you just
rem  get a console window instead of a tray icon, so it must not stop install.
pip install -q pystray pillow
if errorlevel 1 (
  echo       Tray icon deps did not install. Not a problem - the app runs fine,
  echo       you just cannot use "START HIDDEN". Everything else works.
) else (
  echo       Tray icon ready - you can use "START HIDDEN (tray only).vbs".
)

echo.
echo Verifying...
python -c "from webull.core.client import ApiClient; from webull.trade.trade_client import TradeClient; from webull.data.data_client import DataClient; print('VERIFIED - Webull SDK importable')"
if errorlevel 1 goto sdk_failed

python -c "import fastapi, uvicorn, pydantic; print('VERIFIED - app dependencies importable')"
if errorlevel 1 goto coredeps_failed

python -c "import pystray, PIL; print('VERIFIED - tray icon available')" 2>nul || echo "      (tray icon not available - optional, app still works)"

echo.
echo ==========================================================
echo  SUCCESS - you are ready to trade.
echo.
echo  Start it with EITHER:
echo    START MARKET SNIPER.bat      - normal, shows a console
echo    START HIDDEN (tray only).vbs - no window, tray icon only
echo.
echo  First time Windows may say "Windows protected your PC".
echo  That is SmartScreen, not a problem with the app: click
echo  "More info" then "Run anyway". It only asks once.
echo ==========================================================
pause
exit /b 0

:coredeps_failed
echo.
echo ==========================================================
echo  PROBLEM - core dependencies did not install.
echo  The app cannot run without these. Check your internet
echo  connection, then run this installer again.
echo  Still stuck? Run CHECK-SETUP.bat and share the output.
echo ==========================================================
pause
exit /b 1

:sdk_failed
echo.
echo ==========================================================
echo  PROBLEM - the Webull SDK did not install.
echo  Run CHECK-SETUP.bat and share the output with Claude.
echo ==========================================================
pause
exit /b 1
