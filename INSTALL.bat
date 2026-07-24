@echo off
title MARKET SNIPER - one-time install
cd /d "%~dp0"
echo ============================================================
echo   MARKET SNIPER - one-time install (Webull SDK + deps)
echo ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%~dp0' -Recurse | Unblock-File" >nul 2>&1
if not exist ".venv" ( python -m venv .venv )
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -q -r requirements.txt
echo Installing the Webull SDK...
python -m pip install --upgrade webull-openapi-python-sdk
echo Verifying...
python -c "from webull.core.client import ApiClient; from webull.trade.trade_client import TradeClient; from webull.data.data_client import DataClient; print('VERIFIED - everything importable')"
if errorlevel 1 (
  echo PROBLEM - run CHECK-SETUP.bat and share the output with Claude.
) else (
  echo ==========================================================
  echo  SUCCESS. Next: double-click START-MARKET-SNIPER.bat
  echo ==========================================================
)
pause
