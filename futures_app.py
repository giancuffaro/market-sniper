"""
MARKET SNIPER FUTURES backend — FastAPI, port 8010.
COMPLETELY SEPARATE from the options app (which runs on port 8000).
Run via START-FUTURES.bat, or:  python -m uvicorn futures_app:app --port 8010
"""

import os
import pathlib
import time
import traceback
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel


def _fail(where, e):
    """Never let a real broker error hide behind a blank 500 — log the full
    traceback to the terminal and hand the screen a readable reason."""
    traceback.print_exc()
    return JSONResponse({"ok": False, "rejected": True,
                         "reason": "%s: %s" % (type(e).__name__, str(e)[:400]),
                         "where": where})

import futures_client as fc
import user_config as uc
try:
    import tape
except Exception:
    tape = None

app = FastAPI(title="MARKET SNIPER FUTURES")

# v3.8: one live session PER BROKER, not one session total. Log into Topstep,
# NinjaTrader and Webull once each and flip between them without logging out.
# ACTIVE only decides which one the trade buttons act on — every connected
# session stays alive.
SESSIONS = {}                 # "TOPSTEP" | "NINJA" | "WEBULL"  ->  session
ACTIVE = {"mode": None}

# Kept so old code paths that referenced SESSION["s"] fail loudly rather than
# silently trading the wrong account.
SESSION = {"s": None}
HERE = pathlib.Path(__file__).parent
FUT_VERSION = "1.0"


class ConnectReq(BaseModel):
    app_key: str = ""
    app_secret: str = ""
    mode: str = "WEBULL"
    account: str = ""
    incoming_folder: str = ""
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
    """The broker the buttons currently act on."""
    s = SESSIONS.get(ACTIVE["mode"])
    if s is None:
        raise HTTPException(400, "not connected")
    return s


def _summary(mode, s):
    pos = getattr(s, "position", None)
    return {"mode": mode,
            "account_id": getattr(s, "account_id", None),
            "active": mode == ACTIVE["mode"],
            "has_position": pos is not None,
            "symbol": (pos or {}).get("symbol"),
            "side": (pos or {}).get("side"),
            "pnl": (pos or {}).get("pnl"),
            # Points, so the broker tabs can say what is held without a cash
            # figure. pnl stays in the payload for the trade log, unshown.
            "points": (pos or {}).get("points"),
            "day_points": (s._day_points() if hasattr(s, "_day_points") else None),
            "day_realized": round(getattr(s, "day_realized", 0.0), 2)}


def _refresh_all():
    """Refresh EVERY connected broker, not just the visible one.

    refresh_mark() is what evaluates TP, SL and the trailing stop. If it only
    ran for the active session, switching tabs would quietly stop managing a
    live position on the broker you switched away from — the trade would sit
    there with no brackets and nobody watching. So all of them tick."""
    for mode, s in list(SESSIONS.items()):
        try:
            if hasattr(s, "refresh_mark"):
                s.refresh_mark()
        except Exception as e:                               # noqa: BLE001
            print("[state] %s refresh failed: %s" % (mode, str(e)[:150]), flush=True)


@app.get("/", response_class=HTMLResponse)
def index():
    return (HERE / "futures_index.html").read_text(encoding="utf-8")

@app.get("/api/health")
def health():
    return {"ok": True, "version": FUT_VERSION, "app": "futures"}

@app.get("/api/prices")
def prices():
    return {s: fc.get_price(s) for s in fc.FUT}

# ONE START AT A TIME. Ported from the options app, where three impatient
# clicks filled a three-worker pool with abandoned tasks and every click after
# that queued invisibly and timed out. The futures connect talks to
# NinjaTrader, TopstepX or Webull, any of which can be slow to answer.
_CONNECTING = {"until": 0.0}
CONNECT_COOLDOWN_S = 15


@app.post("/api/connect")
def connect(req: ConnectReq):
    # normalize_mode also maps the pre-v3.6 name "LIVE" onto NINJA, so a saved
    # pref from an older build still logs in instead of erroring.
    mode = fc.normalize_mode(req.mode)
    if mode is None:
        raise HTTPException(400, "mode must be WEBULL, NINJA or TOPSTEP "
                                 "(PAPER and Tradovate were removed in v3.6)")
    now = time.time()
    if now < _CONNECTING["until"]:
        raise HTTPException(400,
            "Still finishing the last START — give it %d more second(s). "
            "Pressing it again queues another attempt behind this one, which "
            "is what makes it look stuck."
            % max(1, int(_CONNECTING["until"] - now)))
    _CONNECTING["until"] = now + CONNECT_COOLDOWN_S
    cooldown = 0.0

    s = fc.make_session(mode)
    try:
        if mode == "TOPSTEP":
            state = s.connect(req.ts_user, req.ts_key, req.ts_acct)
        elif mode == "NINJA":
            state = s.connect(req.app_key.strip(), req.app_secret.strip(),
                              req.account.strip(), req.incoming_folder.strip())
        else:   # WEBULL — production key + secret
            state = s.connect(req.app_key.strip(), req.app_secret.strip())
    except fc.OrderRejected as e:
        raise HTTPException(400, str(e))
    except Exception as e:                                   # noqa: BLE001
        # Say what happened instead of falling through as a 500. A 500 comes
        # back as plain text, the page cannot parse it, and it gets reported
        # as "could not reach the app" while the app is running fine - the
        # exact confusion that cost half an hour on the options side.
        raise HTTPException(400, "start failed: %s: %s"
                                 % (type(e).__name__, str(e)[:200]))
    finally:
        _CONNECTING["until"] = cooldown
    SESSIONS[mode] = s
    ACTIVE["mode"] = mode
    state = dict(state or {})
    state["sessions"] = [_summary(m, x) for m, x in SESSIONS.items()]
    state["active_mode"] = mode
    return state

@app.get("/api/state")
def state():
    s = _sess()
    _refresh_all()                      # brackets tick on EVERY logged-in broker
    st = dict(s.state() or {})
    st["sessions"] = [_summary(m, x) for m, x in SESSIONS.items()]
    st["active_mode"] = ACTIVE["mode"]
    return st


@app.get("/api/sessions")
def sessions():
    """Which brokers are logged in, and which one the buttons are pointed at."""
    _refresh_all()
    return {"active": ACTIVE["mode"],
            "sessions": [_summary(m, x) for m, x in SESSIONS.items()]}


class SwitchReq(BaseModel):
    mode: str


@app.post("/api/switch")
def switch(req: SwitchReq):
    """Point the buttons at an already-connected broker. No re-login."""
    mode = fc.normalize_mode(req.mode)
    if mode is None:
        raise HTTPException(400, "mode must be WEBULL, NINJA or TOPSTEP")
    if mode not in SESSIONS:
        raise HTTPException(400, "%s is not logged in yet — connect it once first." % mode)
    ACTIVE["mode"] = mode
    s = SESSIONS[mode]
    _refresh_all()
    st = dict(s.state() or {})
    st["sessions"] = [_summary(m, x) for m, x in SESSIONS.items()]
    st["active_mode"] = mode
    return st

@app.post("/api/order/place")
def place(req: OrderReq):
    if req.side not in ("LONG", "SHORT"):
        raise HTTPException(400, "side must be LONG or SHORT")
    try:
        pos = _sess().place(req.symbol, req.side, int(req.qty))
        return {"ok": True, "position": pos}
    except fc.OrderRejected as e:
        return JSONResponse({"ok": False, "rejected": True, "reason": str(e)})
    except Exception as e:                                   # noqa: BLE001
        return _fail("place", e)

@app.post("/api/order/arm")
def arm(req: OrderReq):
    if req.side not in ("LONG", "SHORT"):
        raise HTTPException(400, "side must be LONG or SHORT")
    try:
        return {"ok": True, "armed": _sess().arm(req.symbol, req.side, int(req.qty))}
    except fc.OrderRejected as e:
        return JSONResponse({"ok": False, "rejected": True, "reason": str(e)})
    except Exception as e:                                   # noqa: BLE001
        return _fail("arm", e)

@app.post("/api/order/disarm")
def disarm():
    try:
        return {"ok": True, **_sess().disarm()}
    except fc.OrderRejected as e:
        return JSONResponse({"ok": False, "rejected": True, "reason": str(e)})
    except Exception as e:                                   # noqa: BLE001
        return _fail("disarm", e)

@app.post("/api/position/forget")
def forget_position():
    """Clear a position the broker does not actually have. Sends NOTHING."""
    try:
        return {"ok": True, **_sess().forget_position()}
    except Exception as e:                                   # noqa: BLE001
        return _fail("forget", e)


@app.post("/api/order/close")
def close():
    try:
        return {"ok": True, **_sess().close()}
    except fc.OrderRejected as e:
        return JSONResponse({"ok": False, "rejected": True, "reason": str(e)})
    except Exception as e:                                   # noqa: BLE001
        return _fail("close", e)

# ---- Remembered setup (survives restarts AND updates) ----------------------
# Plain identifiers are always remembered. API keys/secrets are remembered too
# unless you untick "Remember my login on this PC".
#
# v3.7 fix: wb_key/wb_sec were in NO list at all, so the Webull futures key was
# never saved and had to be retyped every single time. remember_login also
# defaulted to FALSE when absent, which actively DELETED secrets on every save
# for anyone who had never seen the checkbox — and the checkbox only appeared
# in TOPSTEP mode, so most people never did.
PREF_KEYS = ("theme", "mode", "symbol", "qty", "bt_commission", "bt_slippage",
             "nt_account", "nt_folder", "ts_user", "ts_acct", "show_secrets")
SECRET_KEYS = ("ts_key", "wb_key", "wb_sec")
REMEMBER_BY_DEFAULT = True


@app.get("/api/prefs")
def get_prefs():
    """Everything the app should remember, in one call — read before you connect
    so the setup screen and CONFIGURATION open already filled in."""
    p = dict(uc.load("futures_prefs", {}))
    p.setdefault("remember_login", REMEMBER_BY_DEFAULT)
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

    # Absent means "never chosen", which must NOT read as "no". Defaulting this
    # to False is what silently wiped saved keys on every write.
    if p.get("remember_login", REMEMBER_BY_DEFAULT):
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


@app.get("/api/tape")
def tape_speed(symbol: str = "MNQ"):
    """Market velocity — how fast this is moving vs the last half hour.

    Read-only and broker-free: same public bar feed as the price chips, so it
    works before you connect and can't affect an order."""
    if tape is None:
        return {"symbol": symbol, "ok": False, "reason": "tape module unavailable"}
    if symbol not in fc.FUT:
        raise HTTPException(400, "unknown symbol")
    return {"symbol": symbol, **tape.velocity(fc.FUT[symbol]["yahoo"])}

@app.get("/api/strategies")
def get_strategies():
    return {"strategies": _sess().strategies}

@app.post("/api/strategies")
def set_strategies(req: dict):
    try:
        return {"ok": True, "strategies": _sess().update_strategies(req.get("strategies", []))}
    except fc.OrderRejected as e:
        raise HTTPException(400, str(e))

class DisconnectReq(BaseModel):
    mode: Optional[str] = None      # None = every broker


def _pull_working_limits(sessions):
    """Cancel any limit resting at a broker before we stop watching it.

    THIS IS THE HOLE IT CLOSES. An armed round-number entry is a REAL limit
    order sitting at the broker, but the ratchet that would protect the fill
    lives in this app and only runs while it is open. Leave a limit working
    over a weekend and it can fill at Sunday's 18:00 ET reopen with nothing
    managing it - a naked position until someone notices.

    Best-effort by design: a broker that will not take the cancel must not stop
    the app shutting down. What it must never do is fail silently, so every
    outcome is reported back.
    """
    pulled, failed = [], []
    for mode, sess in list(sessions.items()):
        a = getattr(sess, "armed", None)
        if not (a and a.get("order_id") and hasattr(sess, "cancel_limit")):
            continue
        try:
            sess.cancel_limit(a["order_id"])
            sess.armed = None
            pulled.append("%s %s %s @ %g" % (mode, a.get("side"), a.get("symbol"),
                                             a.get("target", 0)))
        except Exception as e:                               # noqa: BLE001
            failed.append("%s: %s" % (mode, str(e)[:80]))
    return pulled, failed


@app.post("/api/disconnect")
def disconnect(req: DisconnectReq = None):
    """Drop one broker, or all of them when no mode is given."""
    target = fc.normalize_mode(req.mode) if (req and req.mode) else None
    if target:
        s = SESSIONS.pop(target, None)
        if s is not None:
            _pull_working_limits({target: s})
        if ACTIVE["mode"] == target:
            ACTIVE["mode"] = next(iter(SESSIONS), None)
        return {"ok": True, "disconnected": target, "active": ACTIVE["mode"],
                "sessions": [_summary(m, x) for m, x in SESSIONS.items()]}
    pulled, failed = _pull_working_limits(SESSIONS)
    SESSIONS.clear()
    ACTIVE["mode"] = None
    return {"ok": True, "disconnected": "all", "active": None, "sessions": [],
            "cancelled": pulled, "cancel_failed": failed}

@app.get("/api/debug/positions")
def debug_positions():
    """What each broker session believes it holds, and the app's own view.

    The options equivalent is what found the wrong-contract price bug in two
    minutes after an hour of guessing. Read-only: it sends nothing.
    """
    out = []
    for mode, s in SESSIONS.items():
        row = {"mode": mode, "account_id": getattr(s, "account_id", None),
               "position": getattr(s, "position", None),
               "armed": getattr(s, "armed", None),
               "day_points": (s._day_points() if hasattr(s, "_day_points") else None),
               "last_event": (getattr(s, "last_event", "") or "")[:200]}
        if hasattr(s, "broker_positions"):
            try:
                row["broker_rows"] = s.broker_positions()
            except Exception as e:                           # noqa: BLE001
                row["broker_rows"] = "read failed: %s" % str(e)[:120]
        out.append(row)
    return {"active": ACTIVE.get("mode"), "sessions": out,
            "prices": {k: fc.get_price(k) for k in fc.FUT}}


@app.post("/api/shutdown")
def shutdown():
    import threading, time
    # Pull anything resting BEFORE the process goes away. See
    # _pull_working_limits: the ratchet dies with this app, the limit does not.
    pulled, failed = _pull_working_limits(SESSIONS)
    if pulled:
        print("[SHUTDOWN] cancelled working limits: %s" % ", ".join(pulled), flush=True)
    if failed:
        print("[SHUTDOWN] COULD NOT cancel: %s - check the broker by hand"
              % ", ".join(failed), flush=True)
    def _die():
        time.sleep(0.4)
        os._exit(0)
    threading.Thread(target=_die, daemon=True).start()
    return {"ok": True, "cancelled": pulled, "cancel_failed": failed}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8010)))
