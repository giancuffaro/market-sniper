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
try:
    import tape
except Exception:
    tape = None
# Dwell time. Optional like the rest: a missing analytics module must never be
# the reason the trading app will not start.
try:
    import levels
except Exception:
    levels = None
try:
    import gauges
except Exception:
    gauges = None
try:
    import trend as trendmod
except Exception:
    trendmod = None

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
    # `paper` was removed in v3.6 (no more Webull sandbox). It is still accepted
    # and ignored so a stale browser tab or saved profile can't 422 on connect.
    paper: bool = False

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
    # Retired: entry and ratchet are one setting (my_enabled). Still accepted
    # so an old cached page cannot 422 - update_settings folds it in.
    ratchet_enabled: Optional[bool] = None
    ratchet_step_pct: Optional[float] = None


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


@app.get("/api/tape")
def tape_speed(symbol: str = "QQQ"):
    """Market velocity — how fast this is moving vs the last half hour.

    Read-only and broker-free: it reads the same public bar feed as the price
    chips, so it works before you connect and can't affect an order."""
    if tape is None:
        return {"symbol": symbol, "ok": False, "reason": "tape module unavailable"}
    if symbol not in config.SYMBOLS:
        raise HTTPException(400, f"{symbol} isn't one of the tradable symbols.")
    ysym = quotes.YSYM.get(symbol, symbol) if quotes else symbol
    return {"symbol": symbol, **tape.velocity(ysym)}


@app.get("/api/dwell")
def dwell_time(symbol: str = "QQQ"):
    """Minutes since price traded the whole dollar above, and the one below.

    Both stale means price is pinned. Read-only and broker-free, and it reuses
    the bars tape.py already caches, so it costs no extra request.

    It also reports whether dwell and velocity agree. They answer different
    questions - "is price going anywhere" versus "is the tape busy" - and can
    legitimately differ, but pinned-and-violent is a contradiction worth
    seeing rather than quietly averaging away."""
    if levels is None or tape is None:
        return {"symbol": symbol, "ok": False, "reason": "module unavailable"}
    if symbol not in config.SYMBOLS:
        raise HTTPException(400, f"{symbol} isn't one of the tradable symbols.")
    ysym = quotes.YSYM.get(symbol, symbol) if quotes else symbol
    price = None
    try:
        price = quotes.get_price(symbol)["price"]
    except Exception:
        pass                       # dwell() falls back to the last bar's close
    try:
        d = levels.dwell(tape._bars(ysym), price=price)
    except Exception as e:                                   # noqa: BLE001
        return {"symbol": symbol, "ok": False, "reason": str(e)[:120]}
    out = {"symbol": symbol, **d, "label": levels.label(d)}
    try:
        out["agreement"] = levels.agreement(d, tape.velocity(ysym))
    except Exception:
        pass                       # the cross-check is a bonus, never a blocker
    return out


@app.get("/api/volume")
def volume_gauge(symbol: str = "QQQ"):
    """Today's volume ranked against ~500 sessions, corrected for time of day.

    The correction is the whole point: comparing volume-so-far against whole
    past days makes every morning read as dead. Today is projected to a full
    session first, using a profile learned from real 5-minute bars, and only
    then ranked."""
    if gauges is None:
        return {"symbol": symbol, "ok": False, "reason": "gauges unavailable"}
    if symbol not in config.SYMBOLS:
        raise HTTPException(400, f"{symbol} isn't one of the tradable symbols.")
    ysym = quotes.YSYM.get(symbol, symbol) if quotes else symbol
    v = gauges.volume(ysym)
    return {"symbol": symbol, **v, "label": gauges.label(v)}


@app.get("/api/volatility")
def volatility_gauge(symbol: str = "QQQ"):
    """Realized and implied volatility, kept separate on purpose.

    Realized is broker-free and ranked against two years of this symbol's own
    history. Implied needs a live option chain: Webull is the only source that
    can price one - Yahoo's options endpoint answers 401, and the NinjaTrader
    link is a one-way file drop. Not connected means implied says so rather
    than guessing."""
    if gauges is None:
        return {"symbol": symbol, "ok": False, "reason": "gauges unavailable"}
    if symbol not in config.SYMBOLS:
        raise HTTPException(400, f"{symbol} isn't one of the tradable symbols.")
    ysym = quotes.YSYM.get(symbol, symbol) if quotes else symbol
    opt = None
    e = SESSION.get("s")
    if e is not None:
        try:
            opt = e.atm_option_for_vol(symbol)
        except Exception:
            opt = None                 # a chain hiccup must not kill realized
    v = gauges.volatility(ysym, option=opt)
    # A broker's own implied vol beats one inverted from a mid price.
    if opt and opt.get("iv_native") and v.get("implied", {}).get("ok"):
        v["implied"]["iv_pct"] = round(opt["iv_native"], 1)
        v["implied"]["source"] = "webull"
        rv = (v.get("realized") or {}).get("rv_pct")
        if rv:
            r = round(opt["iv_native"] / rv, 2)
            v["implied"]["vs_realized"] = r
            v["implied"]["state"] = ("rich" if r >= gauges.IV_RICH_RATIO else
                                     "cheap" if r <= gauges.IV_CHEAP_RATIO else "fair")
    elif v.get("implied", {}).get("ok"):
        v["implied"]["source"] = "inverted from the Webull mid"
    if opt:
        v["greeks"] = {k: opt.get(k) for k in ("delta", "theta", "gamma", "vega")}
    return {"symbol": symbol, **v, "label": gauges.vol_label(v)}


@app.get("/api/direction")
def direction(symbol: str = "QQQ", tf: str = "1m"):
    """up / down / chop from three signals that have to agree.

    Deliberately NOT a replacement for /api/trend yet - both are live so they
    can be compared side by side before the old one goes anywhere.

    The old panel asks one question (is EMA9 over EMA21) eleven times. This
    asks three different ones - is the 21-EMA sloping and is price the right
    side of it, are highs and lows both stepping the same way, and is volume
    arriving on up-bars - and reports how many agree."""
    if trendmod is None:
        return {"symbol": symbol, "ok": False, "reason": "trend module unavailable"}
    ysym = quotes.YSYM.get(symbol, symbol) if quotes else symbol
    d = trendmod.for_symbol(ysym, tf)
    return {"symbol": symbol, "tf": tf, **d, "label": trendmod.label(d)}


@app.get("/api/breadth")
def breadth(lead: str = "QQQ", tf: str = "1m"):
    """The lead symbol against the Mag Seven.

    QQQ green while five of seven are red is a rally carried by one or two
    names. That is a different trade from a broad one, and the old panel had
    no way to tell you which you were in."""
    if trendmod is None:
        return {"ok": False, "reason": "trend module unavailable"}
    ysym = quotes.YSYM.get(lead, lead) if quotes else lead
    return trendmod.basket(ysym, tf)


@app.get("/api/preview")
def preview(symbol: str = "QQQ", side: str = "CALLS"):
    """Where an armed entry would trigger, and which strike you'd end up with.

    Read-only — it computes, it does not arm. Nothing reaches Webull."""
    if symbol not in config.SYMBOLS:
        raise HTTPException(400, f"{symbol} isn't one of the tradable symbols.")
    if side not in ("CALLS", "PUTS"):
        raise HTTPException(400, "side must be CALLS or PUTS")
    try:
        return _sess().preview_entry(symbol, side)
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "reason": str(e)[:140]}


@app.post("/api/connect")
def connect(req: ConnectReq):
    # LIVE only since v3.6 — every order this app sends is real money.
    s = wb.make_session("LIVE")
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
# v3.7: saved key profiles used to live ONLY in browser localStorage, so
# clearing the browser, switching browser, or a new machine lost every account.
# They are written to my-settings.json now — same disk file the futures app
# uses, gitignored, never leaves this computer.
OPT_PREF_KEYS = ("theme", "mode", "symbol", "qty", "autolock",
                 "show_secrets", "active_profile")
OPT_SECRET_KEYS = ("profiles",)          # [{name, k, s}, ...]
REMEMBER_BY_DEFAULT = True


@app.get("/api/prefs")
def get_prefs():
    """Everything the app should remember, in one call — read before you connect
    so the screen and MY CONFIG open already filled in."""
    saved = uc.load("options_settings", {})
    settings = dict(config.DEFAULT_SETTINGS)
    settings.update({k: v for k, v in saved.items() if k in config.DEFAULT_SETTINGS})
    prefs = dict(uc.load("options_prefs", {}))
    prefs.setdefault("remember_login", REMEMBER_BY_DEFAULT)
    return {"prefs": prefs,
            "settings": settings,
            "strategies": wb._restore_strategies(uc.load("options_strategies", None)),
            "saved_to": uc.where()}


@app.post("/api/prefs")
def set_prefs(req: dict):
    p = dict(uc.load("options_prefs", {}))
    for k in OPT_PREF_KEYS:
        if k in req and req[k] is not None:
            p[k] = req[k]
    if "remember_login" in req:
        p["remember_login"] = bool(req["remember_login"])

    # Absent means "never chosen" -> remember. Only an explicit false is a no.
    if p.get("remember_login", REMEMBER_BY_DEFAULT):
        for k in OPT_SECRET_KEYS:
            if k in req and req[k] is not None:
                p[k] = req[k]
    else:
        for k in OPT_SECRET_KEYS:
            p.pop(k, None)
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

@app.get("/api/tradelog")
def tradelog(days: int = 7):
    """Recent per-day totals, straight from the trade log."""
    try:
        import trade_log
        return {"ok": True, "path": trade_log.XLSX_PATH,
                "days": trade_log.summary(days)}
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "reason": str(e)[:140]}


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
