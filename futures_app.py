"""
MARKET SNIPER FUTURES backend — FastAPI, port 8010.
COMPLETELY SEPARATE from the options app (which runs on port 8000).
Run via START-FUTURES.bat, or:  python -m uvicorn futures_app:app --port 8010
"""

import os
import pathlib
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import futures_client as fc
import user_config as uc

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
    tv_user: str = ""
    tv_pass: str = ""
    tv_key: str = ""
    tv_sec: str = ""
    tv_env: str = "demo"
    ts_user: str = ""
    ts_key: str = ""
    ts_acct: str = ""

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
    round_enabled: Optional[bool] = None
    round_step: Optional[float] = None


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
    if req.mode not in ("PAPER", "LIVE", "TRADOVATE", "TOPSTEP"):
        raise HTTPException(400, "mode must be PAPER, LIVE, TRADOVATE or TOPSTEP")
    s = fc.make_session(req.mode)
    try:
        if req.mode == "TRADOVATE":
            state = s.connect(req.tv_user, req.tv_pass, req.tv_key, req.tv_sec, req.tv_env)
        elif req.mode == "TOPSTEP":
            state = s.connect(req.ts_user, req.ts_key, req.ts_acct)
        elif req.mode == "LIVE":
            state = s.connect(req.app_key.strip(), req.app_secret.strip(),
                              req.account.strip(), req.incoming_folder.strip())
        else:
            state = s.connect(req.app_key.strip(), req.app_secret.strip())
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

@app.post("/api/order/arm")
def arm(req: OrderReq):
    if req.side not in ("LONG", "SHORT"):
        raise HTTPException(400, "side must be LONG or SHORT")
    try:
        return {"ok": True, "armed": _sess().arm(req.symbol, req.side, int(req.qty))}
    except fc.OrderRejected as e:
        return JSONResponse({"ok": False, "rejected": True, "reason": str(e)})

@app.post("/api/order/disarm")
def disarm():
    try:
        return {"ok": True, **_sess().disarm()}
    except fc.OrderRejected as e:
        return JSONResponse({"ok": False, "rejected": True, "reason": str(e)})

@app.post("/api/order/close")
def close():
    try:
        return {"ok": True, **_sess().close()}
    except fc.OrderRejected as e:
        return JSONResponse({"ok": False, "rejected": True, "reason": str(e)})

# ---- Remembered setup (survives restarts AND updates) ----------------------
# Plain identifiers are always remembered. Passwords / API secrets are ONLY
# written to disk if you tick "Remember my login on this PC".
PREF_KEYS = ("theme", "mode", "symbol", "qty", "bt_commission", "bt_slippage",
             "nt_account", "nt_folder", "tv_user", "tv_env", "ts_user", "ts_acct")
SECRET_KEYS = ("tv_pass", "tv_key", "tv_sec", "ts_key")


@app.get("/api/prefs")
def get_prefs():
    """Everything the app should remember, in one call — read before you connect
    so the setup screen and CONFIGURATION open already filled in."""
    p = uc.load("futures_prefs", {})
    saved = uc.load("futures_settings", {})
    settings = dict(fc.DEFAULT_SETTINGS)
    settings.update({k: v for k, v in saved.items() if k in fc.DEFAULT_SETTINGS})
    return {"prefs": p,
            "settings": settings,
            "strategies": fc._restore_strategies(uc.load("futures_strategies", None)),
            "saved_to": uc.where()}


@app.post("/api/prefs")
def set_prefs(req: dict):
    p = dict(uc.load("futures_prefs", {}))
    for k in PREF_KEYS:
        if k in req and req[k] is not None:
            p[k] = req[k]
    if "remember_login" in req:
        p["remember_login"] = bool(req["remember_login"])
    if p.get("remember_login"):
        for k in SECRET_KEYS:
            if k in req and req[k] is not None:
                p[k] = req[k]
    else:
        for k in SECRET_KEYS:      # unticking it wipes anything already stored
            p.pop(k, None)
    uc.save("futures_prefs", p)
    return {"ok": True, "prefs": p}


@app.get("/api/settings")
def get_settings():
    return _sess().settings

@app.post("/api/settings")
def set_settings(req: SettingsReq):
    return _sess().update_settings({k: v for k, v in req.model_dump().items() if v is not None})

class BacktestReq(BaseModel):
    strategy: dict
    duration: str = "6mo"
    commission: Optional[float] = None      # $ per round turn, per contract
    slippage_ticks: Optional[float] = None  # ticks lost on entry + exit

@app.post("/api/backtest")
def backtest(req: BacktestReq):
    return fc.backtest(req.strategy, req.duration, req.commission, req.slippage_ticks)

@app.get("/api/data_status")
def data_status():
    return fc.data_status()

class UploadReq(BaseModel):
    symbol: str
    content_b64: str = ""

MAX_UPLOAD_MB = 200

@app.post("/api/upload_data")
def upload_data(req: UploadReq):
    import base64
    try:
        raw = base64.b64decode(req.content_b64)
    except Exception:
        raise HTTPException(400, "Couldn't read that file — make sure it's the CSV you exported.")
    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400,
            f"That file is {len(raw)/1048576:.0f} MB — too big to load in one go "
            f"(limit {MAX_UPLOAD_MB} MB). Re-export a shorter date range in NinjaTrader, "
            f"or a coarser bar size like 5-minute instead of 1-minute.")
    try:
        return {"ok": True, **fc.save_uploaded(req.symbol, raw)}
    except fc.OrderRejected as e:
        raise HTTPException(400, str(e))

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
