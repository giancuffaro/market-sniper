"""
MARKET SNIPER FUTURES backend — FastAPI, port 8010.
COMPLETELY SEPARATE from the options app (which runs on port 8000).
Run via START-FUTURES.bat, or:  python -m uvicorn futures_app:app --port 8010
"""

import os
import pathlib
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import futures_client as fc

# CSV upload needs python-multipart. If it's missing, the app must still boot —
# so we only register the multipart upload route when it's available.
try:
    import multipart  # noqa: F401  (python-multipart)
    HAS_MULTIPART = True
except Exception:
    try:
        import python_multipart  # noqa: F401
        HAS_MULTIPART = True
    except Exception:
        HAS_MULTIPART = False

app = FastAPI(title="MARKET SNIPER FUTURES")
SESSION = {"s": None}
HERE = pathlib.Path(__file__).parent
FUT_VERSION = "1.0"


class ConnectReq(BaseModel):
    app_key: str = ""
    app_secret: str = ""
    mode: str = "PAPER"
    account: str = ""
    incoming_folder: str = ""

class OrderReq(BaseModel):
    symbol: str
    side: str      # LONG | SHORT
    qty: int

class SettingsReq(BaseModel):
    tp_enabled: Optional[bool] = None
    tp_points: Optional[float] = None
    sl_enabled: Optional[bool] = None
    sl_points: Optional[float] = None
    trail_enabled: Optional[bool] = None
    trail_points: Optional[float] = None


def _sess():
    s = SESSION["s"]
    if s is None:
        raise HTTPException(400, "not connected")
    return s


@app.get("/", response_class=HTMLResponse)
def index():
    return (HERE / "futures_index.html").read_text(encoding="utf-8")

@app.get("/api/health")
def health():
    return {"ok": True, "version": FUT_VERSION, "app": "futures"}

@app.get("/api/prices")
def prices():
    return {s: fc.get_price(s) for s in fc.FUT}

@app.post("/api/connect")
def connect(req: ConnectReq):
    if req.mode not in ("PAPER", "LIVE"):
        raise HTTPException(400, "mode must be PAPER or LIVE")
    s = fc.make_session(req.mode)
    try:
        state = s.connect(req.app_key.strip(), req.app_secret.strip(),
                          req.account.strip(), req.incoming_folder.strip())
    except fc.OrderRejected as e:
        raise HTTPException(400, str(e))
    SESSION["s"] = s
    return state

@app.get("/api/state")
def state():
    s = _sess()
    s.refresh_mark()
    return s.state()

@app.post("/api/order/place")
def place(req: OrderReq):
    if req.side not in ("LONG", "SHORT"):
        raise HTTPException(400, "side must be LONG or SHORT")
    try:
        pos = _sess().place(req.symbol, req.side, int(req.qty))
        return {"ok": True, "position": pos}
    except fc.OrderRejected as e:
        return JSONResponse({"ok": False, "rejected": True, "reason": str(e)})

@app.post("/api/order/close")
def close():
    try:
        return {"ok": True, **_sess().close()}
    except fc.OrderRejected as e:
        return JSONResponse({"ok": False, "rejected": True, "reason": str(e)})

@app.get("/api/settings")
def get_settings():
    return _sess().settings

@app.post("/api/settings")
def set_settings(req: SettingsReq):
    return _sess().update_settings({k: v for k, v in req.model_dump().items() if v is not None})

class BacktestReq(BaseModel):
    strategy: dict
    duration: str = "6mo"

@app.post("/api/backtest")
def backtest(req: BacktestReq):
    return fc.backtest(req.strategy, req.duration)

@app.get("/api/data_status")
def data_status():
    return fc.data_status()

if HAS_MULTIPART:
    @app.post("/api/upload_data")
    async def upload_data(symbol: str = Form(...), file: UploadFile = File(...)):
        raw = await file.read()
        try:
            return {"ok": True, **fc.save_uploaded(symbol, raw)}
        except fc.OrderRejected as e:
            raise HTTPException(400, str(e))
else:
    @app.post("/api/upload_data")
    async def upload_data_unavailable():
        raise HTTPException(400, "CSV upload needs the python-multipart package. "
                                 "Close the app and run:  pip install python-multipart")

@app.get("/api/trend")
def trend(symbol: str = "MNQ"):
    if symbol not in fc.FUT:
        raise HTTPException(400, "unknown symbol")
    return {"symbol": symbol, "trend": fc.trend(symbol)}

@app.get("/api/strategies")
def get_strategies():
    return {"strategies": _sess().strategies}

@app.post("/api/strategies")
def set_strategies(req: dict):
    try:
        return {"ok": True, "strategies": _sess().update_strategies(req.get("strategies", []))}
    except fc.OrderRejected as e:
        raise HTTPException(400, str(e))

@app.post("/api/disconnect")
def disconnect():
    SESSION["s"] = None
    return {"ok": True}

@app.post("/api/shutdown")
def shutdown():
    import threading, time
    def _die():
        time.sleep(0.4)
        os._exit(0)
    threading.Thread(target=_die, daemon=True).start()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8010)))
