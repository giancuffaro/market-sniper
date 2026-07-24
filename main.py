"""MARKET SNIPER backend — FastAPI. v3.0"""

import os
import pathlib
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import config
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
    mode: str = "PAPER"
    account_id: Optional[str] = None

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

@app.post("/api/connect")
def connect(req: ConnectReq):
    if req.mode not in ("PAPER", "LIVE"):
        raise HTTPException(400, "mode must be PAPER or LIVE")
    s = wb.make_session(req.mode)
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
        raise HTTPException(400, f"connect failed: {type(e).__name__}: {str(e)[:200]}")
    SESSION["s"] = s
    return state

@app.post("/api/mirror/connect")
def mirror_connect(req: MirrorReq):
    main = _sess()
    if main.mode != "LIVE":
        raise HTTPException(400, "mirror trading only works in LIVE mode")
    m = wb.make_session("LIVE")
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
    except wb.OrderRejected as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"quote failed: {type(e).__name__}: {str(e)[:200]}")

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

@app.post("/api/order/place")
def place(req: OrderReq):
    try:
        pos = _sess().place(req.symbol, req.side, int(req.qty))
    except wb.OrderRejected as e:
        return JSONResponse({"ok": False, "rejected": True, "reason": str(e)})
    except Exception as e:
        return JSONResponse({"ok": False, "rejected": True,
                             "reason": f"{type(e).__name__}: {str(e)[:200]}"})
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
    try:
        armed = _sess().arm(req.symbol, req.side, int(req.qty))
    except wb.OrderRejected as e:
        return JSONResponse({"ok": False, "rejected": True, "reason": str(e)})
    except Exception as e:
        return JSONResponse({"ok": False, "rejected": True,
                             "reason": f"{type(e).__name__}: {str(e)[:200]}"})
    return {"ok": True, "armed": armed, **armed}

@app.post("/api/order/disarm")
def disarm():
    return {"ok": True, **_sess().disarm()}

@app.post("/api/order/close")
def close():
    try:
        res = _sess().close()
    except wb.OrderRejected as e:
        return JSONResponse({"ok": False, "rejected": True, "reason": str(e)})
    except Exception as e:
        return JSONResponse({"ok": False, "rejected": True,
                             "reason": f"{type(e).__name__}: {str(e)[:200]}"})
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
