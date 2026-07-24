#!/usr/bin/env bash
# EZEXECUTION FUTURES (MNQ/MES) launcher. macOS/Linux.
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then python3 -m venv .venv; fi
source .venv/bin/activate
pip install -q -r requirements.txt
( sleep 3; (open http://127.0.0.1:8010 2>/dev/null || xdg-open http://127.0.0.1:8010 2>/dev/null) ) &
echo "EZEXECUTION FUTURES running at http://127.0.0.1:8010 (separate from options on 8000)"
python -m uvicorn futures_app:app --host 127.0.0.1 --port 8010
