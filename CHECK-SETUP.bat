@echo off
title MARKET SNIPER - Setup Check
cd /d "%~dp0"
echo ================= MARKET SNIPER SETUP CHECK =================
python --version 2>nul || echo PROBLEM: Python not found - install from python.org (tick "Add to PATH")
if not exist ".venv" ( echo PROBLEM: no .venv - run INSTALL.bat first & goto end )
call .venv\Scripts\activate
python --version
echo.
echo --- Webull SDK ---
python -c "from webull.core.client import ApiClient; from webull.trade.trade_client import TradeClient; from webull.data.data_client import DataClient; print('OK: SDK importable')" 2>nul || echo MISSING: run INSTALL.bat
echo.
echo --- App dependencies ---
python -c "import fastapi, uvicorn, pydantic; print('OK: fastapi / uvicorn / pydantic')" 2>nul || echo MISSING: run INSTALL.bat
echo.
echo --- Code version ---
python -c "import config; print('config version:', config.APP_VERSION)" 2>nul || echo PROBLEM: config.py missing or old
echo.
echo --- Port 8000 in use? ---
netstat -aon | findstr :8000 | findstr LISTENING && echo (a server is running - the launcher restarts it automatically) || echo not running
echo ============================================================
:end
pause
