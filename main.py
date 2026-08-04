"""MARKET SNIPER backend — FastAPI. v3.0"""

import os
import pathlib
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import config
import user_config as uc
import webull_client as wb
try:
    import quotes
except Exception:
    quotes = None

_EXEC = ThreadPoolExecutor(max_workers=3)
CONNECT_TIMEOUT_S = 25

app = FastAPI(title="MARKET SNIPER")
SESSION = {"s": None}
MIRROR = {"s": None, "name": None}
HERE = pathlib.Path(__file__).parent


class ConnectReq(BaseModel):
    app_key: str = ""
    app_secret: str = ""
    account_id: Optional[str] = None
    paper: bool = False        # True -> Webull SANDBOX (can't touch real money)

class MirrorReq(BaseModel):
    app_key: str = ""
    app_secret: str = ""
    name: str = ""

class QuoteReq(BaseModel):
    symbol: str
    side: str

class OrderReq(BaseModel):
    symbol: str
    side: str
    qty: int

class ArmReq(BaseModel):
    symbol: str
    side: str
    qty: int

class SettingsReq(BaseModel):
    strike_mode: Optional[str] = None
    tp_enabled: Optional[bool] = None
    tp_value: Optional[float] = None
    tp_unit: Optional[str] = None
    sl_enabled: Optional[bool] = None
    sl_value: Optional[float] = None
    sl_unit: Optional[str] = None
    my_enabled: Optional[bool] = None


def _sess():
    s = SESSION["s"]
    if s is None:
        raise HTTPException(400, "not connected")
    return s


@app.get("/", response_class=HTMLResponse)
def index():
    return (HERE / "index.html").read_text(encoding="utf-8")

@app.get("/api/health")
def health():
    return {"ok": True, "version": getattr(config, "APP_VERSION", "old"),
            "sdk_available": wb.SDK_AVAILABLE, "sdk_hint": getattr(wb, "SDK_HINT", None)}

@app.get("/api/prices")
def prices():
    if quotes is None:
        return {}
    try:
        return quotes.get_all()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=200)

@app.get("/api/trend")
def trend(symbol: str = "QQQ"):
    """Multi-timeframe trend read-out for the strip under the price chips."""
    if quotes is None:
        return {"symbol": symbol, "trend": {}}
    if symbol not in config.SYMBOLS:
        raise HTTPException(400, f"{symbol} isn't one of the tradable symbols.")
    try:
        return {"symbol": symbol, "trend": quotes.trend(symbol)}
    except Exception as e:
        return {"symbol": symbol, "trend": {}, "error": str(e)[:120]}


@app.post("/api/connect")
def connect(req: ConnectReq):
    # PAPER routes to Webull's sandbox (no real money possible); otherwise LIVE.
    s = wb.make_session("PAPER" if req.paper else "LIVE")
    try:
        fut = _EXEC.submit(s.connect, req.app_key.strip(), req.app_secret.strip(),
                           req.account_id)
        state = fut.result(timeout=CONNECT_TIMEOUT_S)
    except FutTimeout:
        raise HTTPException(400, f"Webull didn't respond within {CONNECT_TIMEOUT_S}s — retry.")
    except wb.ChooseAccounts as e:
        return {"choose_accounts": [
            {"id": str(a), "type": t, "bp": bp} for a, t, bp in e.accounts]}
    except wb.OrderRejected as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        if "x-signature" in low or "unauthorized" in low or "401" in low or "signature" in low:
            raise HTTPException(400, "Keys rejected — your API key & secret don't match "
                                     "(or they're mistyped). Re-copy BOTH from Webull. If they're "
                                     "correct, check your PC clock is set to sync automatically.")
        raise HTTPException(400, f"connect failed: {type(e).__name__}: {msg[:200]}")
    SESSION["s"] = s
    return state

@app.post("/api/mirror/connect")
def mirror_connect(req: MirrorReq):
    _sess()
    m = wb.make_session()
    try:
        fut = _EXEC.submit(m.connect, req.app_key.strip(), req.app_secret.strip(), None)
        fut.result(timeout=CONNECT_TIMEOUT_S)
    except FutTimeout:
        raise HTTPException(400, "mirror account: Webull didn't respond — retry")
    except wb.ChooseAccounts:
        raise HTTPException(400, "mirror key sees multiple accounts — set "
                                 "PREFERRED_ACCOUNT_TYPE in config.py for the mirror side")
    except wb.OrderRejected as e:
        raise HTTPException(400, f"mirror account: {e}")
    except Exception as e:
        raise HTTPException(400, f"mirror connect failed: {str(e)[:150]}")
    MIRROR["s"], MIRROR["name"] = m, (req.name or "mirror")
    return {"ok": True, "name": MIRROR["name"],
            "buying_power": m.buying_power, "account_type": m.account_type}

@app.post("/api/mirror/disable")
def mirror_disable():
    MIRROR["s"], MIRROR["name"] = None, None
    return {"ok": True}

def _mirror_info():
    m = MIRROR["s"]
    if not m:
        return None
    return {"name": MIRROR["name"], "buying_power": round(m.buying_power, 2),
            "account_type": m.account_type, "has_position": m.position is not None}

@app.post("/api/quote")
def quote(req: QuoteReq):
    try:
        return _sess().quote(req.symbol, req.side)
    except Exception as e:
        raise HTTPException(400, wb.friendly_error(e))

@app.get("/api/state")
def state():
    s = _sess()
    if hasattr(s, "refresh_mark"):
        s.refresh_mark()
    m = MIRROR["s"]
    if m is not None and hasattr(m, "refresh_mark"):
        m.refresh_mark()
    st = s.state()
    st["mirror"] = _mirror_info()
    return st

def _reject(e):
    """Every rejection the screen sees is written in plain English first."""
    reason = wb.friendly_error(e)
    return JSONResponse({"ok": False, "rejected": True, "reason": reason,
                         "phantom": wb.is_phantom_position(reason),
                         "raw": f"{type(e).__name__}: {str(e)[:300]}"})


@app.post("/api/order/place")
def place(req: OrderReq):
    s = _sess()
    if s.position is not None:
        return _reject(wb.OrderRejected(
            "You're already in a trade. Close it first — while a position is open the "
            "only thing this app will send is a CLOSE order."))
    try:
        pos = s.place(req.symbol, req.side, int(req.qty))
    except Exception as e:
        return _reject(e)
    out = {"ok": True, "position": pos}
    m = MIRROR["s"]
    if m is not None:
        try:
            m.place(req.symbol, req.side, int(req.qty))
            out["mirror_ok"] = True
        except Exception as e:
            out["mirror_ok"] = False
            out["mirror_reason"] = str(e)[:180]
    return out

@app.post("/api/order/arm")
def arm(req: ArmReq):
    s = _sess()
    if s.position is not None:
        return _reject(wb.OrderRejected(
            "You're already in a trade. Close it first — while a position is open the "
            "only thing this app will send is a CLOSE order."))
    try:
        armed = s.arm(req.symbol, req.side, int(req.qty))
    except Exception as e:
        return _reject(e)
    return {"ok": True, "armed": armed, **armed}

@app.post("/api/order/disarm")
def disarm():
    return {"ok": True, **_sess().disarm()}

@app.post("/api/order/close")
def close():
    try:
        res = _sess().close()
    except Exception as e:
        return _reject(e)
    out = {"ok": True, **res}
    m = MIRROR["s"]
    if m is not None and m.position is not None:
        try:
            m.close()
            out["mirror_ok"] = True
        except Exception as e:
            out["mirror_ok"] = False
            out["mirror_reason"] = str(e)[:180]
    return out

# ---- Remembered setup (survives restarts AND updates) ----------------------
# Kept in my-settings.json next to the app so it isn't lost when the browser
# forgets, and isn't lost when UPDATE.bat pulls a new version.
OPT_PREF_KEYS = ("theme", "mode", "symbol", "qty", "autolock")


@app.get("/api/prefs")
def get_prefs():
    """Everything the app should remember, in one call — read before you connect
    so the screen and MY CONFIG open already filled in."""
    saved = uc.load("options_settings", {})
    settings = dict(config.DEFAULT_SETTINGS)
    settings.update({k: v for k, v in saved.items() if k in config.DEFAULT_SETTINGS})
    return {"prefs": uc.load("options_prefs", {}),
            "settings": settings,
            "strategies": wb._restore_strategies(uc.load("options_strategies", None)),
            "saved_to": uc.where()}


@app.post("/api/prefs")
def set_prefs(req: dict):
    p = dict(uc.load("options_prefs", {}))
    for k in OPT_PREF_KEYS:
        if k in req and req[k] is not None:
            p[k] = req[k]
    uc.save("options_prefs", p)
    return {"ok": True, "prefs": p}


@app.get("/api/settings")
def get_settings():
    return _sess().settings

@app.post("/api/settings")
def set_settings(req: SettingsReq):
    s = _sess()
    new = {k: v for k, v in req.model_dump().items() if v is not None}
    out = s.update_settings(new)
    if MIRROR["s"] is not None:
        MIRROR["s"].update_settings(new)
    return out

@app.get("/api/strategies")
def get_strategies():
    return {"strategies": _sess().strategies}

@app.post("/api/strategies")
def set_strategies(req: dict):
    s = _sess()
    try:
        strategies = s.update_strategies(req.get("strategies", []))
    except wb.OrderRejected as e:
        raise HTTPException(400, str(e))
    if MIRROR["s"] is not None:
        try:
            MIRROR["s"].update_strategies(req.get("strategies", []))
        except Exception:
            pass
    return {"ok": True, "strategies": strategies}

@app.post("/api/position/forget")
def forget_position():
    """Clear a position the broker doesn't actually have. Sends NOTHING to Webull."""
    return {"ok": True, **_sess().forget_position()}


@app.post("/api/disconnect")
def disconnect():
    SESSION["s"] = None
    MIRROR["s"], MIRROR["name"] = None, None
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
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8000)))
