#!/usr/bin/env bash
# MARKET SNIPER launcher (macOS/Linux)
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then python3 -m venv .venv; fi
source .venv/bin/activate
pip install -q -r requirements.txt
export ALLOW_LIVE=1
( sleep 3; (open http://127.0.0.1:8000 2>/dev/null || xdg-open http://127.0.0.1:8000 2>/dev/null) ) &
echo "MARKET SNIPER at http://127.0.0.1:8000 — LIVE/PAPER chosen inside the app."
python -m uvicorn main:app --host 127.0.0.1 --port 8000
