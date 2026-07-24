"""
MARKET SNIPER FUTURES — session engine.
COMPLETELY SEPARATE from the options app. Nothing here imports the options code.

  MNQ (Micro Nasdaq): tick 0.25 = $0.50  ->  $2 per point
  MES (Micro S&P):    tick 0.25 = $1.25  ->  $5 per point

  PaperFuturesSession — simulated fills, REAL live prices (Yahoo NQ=F / ES=F proxies).
  LiveFuturesSession  — not wired yet (needs futures approval + CME data $228/mo).
"""

import json
import time
import math
import random
import datetime as dt
import urllib.request

FUT = {
    "MNQ": {"yahoo": "NQ=F", "tick": 0.25, "point_value": 2.0, "seed": 23150.0},
    "MES": {"yahoo": "ES=F", "tick": 0.25, "point_value": 5.0, "seed": 6360.0},
}

MAX_CONTRACTS = 10
DAILY_LOSS_LIMIT = 500.0

DEFAULT_SETTINGS = {
    "tp_enabled": False, "tp_points": 10.0,
    "sl_enabled": False, "sl_points": 5.0,
    "trail_enabled": False, "trail_points": 5.0,
}

_CACHE = {}
_UA = {"User-Agent": "Mozilla/5.0 (MARKET-SNIPER-FUT)"}


def get_price(sym):
    now = time.time()
    c = _CACHE.get(sym)
    if c and now - c["ts"] < 5:
        return c["v"]
    try:
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               f"{urllib.request.quote(FUT[sym]['yahoo'])}?range=1d&interval=1m")
        with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=6) as r:
            meta = json.load(r)["chart"]["result"][0]["meta"]
        price = float(meta["regularMarketPrice"])
        prev = float(meta.get("previousClose") or price)
        v = {"price": round(price, 2), "change": round(price - prev, 2),
             "change_pct": round((price - prev) / prev * 100, 2) if prev else 0.0, "live": True}
    except Exception:
        base = c["v"]["price"] if c else FUT[sym]["seed"]
        v = {"price": round(base + random.uniform(-1, 1), 2), "change": 0.0,
             "change_pct": 0.0, "live": False}
    _CACHE[sym] = {"ts": now, "v": v}
    return v


class OrderRejected(Exception):
    pass


class BaseFuturesSession:
    def __init__(self, mode):
        self.mode = mode
        self.account_id = None
        self.buying_power = 0.0
        self.position = None
        self.day_realized = 0.0
        self.blotter = []
        self.settings = dict(DEFAULT_SETTINGS)
        self.last_event = None

    def update_settings(self, new):
        s = self.settings
        for k in ("tp_enabled", "sl_enabled", "trail_enabled"):
            if k in new:
                s[k] = bool(new[k])
        for k in ("tp_points", "sl_points", "trail_points"):
            if k in new:
                try:
                    v = float(new[k])
                    if v > 0:
                        s[k] = v
                except (TypeError, ValueError):
                    pass
        return s

    def _points_pnl(self):
        p = self.position
        d = p["mark"] - p["entry"]
        return d if p["side"] == "LONG" else -d

    def _update_trail(self):
        p, s = self.position, self.settings
        if not (p and s["trail_enabled"]):
            return
        if p["side"] == "LONG":
            p["best"] = max(p.get("best", p["entry"]), p["mark"])
            p["trail_stop"] = round(p["best"] - s["trail_points"], 2)
        else:
            p["best"] = min(p.get("best", p["entry"]), p["mark"])
            p["trail_stop"] = round(p["best"] + s["trail_points"], 2)

    def _bracket_hit(self):
        p, s = self.position, self.settings
        if not p:
            return None
        pts = self._points_pnl()
        if s["tp_enabled"] and pts >= s["tp_points"]:
            return "TP"
        if s["sl_enabled"] and pts <= -s["sl_points"]:
            return "SL"
        if s["trail_enabled"] and p.get("trail_stop") is not None:
            if p["side"] == "LONG" and p["mark"] <= p["trail_stop"]:
                return "TRAIL"
            if p["side"] == "SHORT" and p["mark"] >= p["trail_stop"]:
                return "TRAIL"
        return None

    def _maybe_auto_close(self):
        hit = self._bracket_hit()
        if not hit:
            return
        try:
            pnl = round(self._points_pnl() * FUT[self.position["symbol"]]["point_value"]
                        * self.position["qty"], 2)
            self.close()
            if self.blotter:
                self.blotter[-1]["desc"] += f"  [{hit}]"
            label = {"TP": "TAKE PROFIT", "SL": "STOP LOSS", "TRAIL": "TRAILING STOP"}[hit]
            sign = "+" if pnl >= 0 else "−"
            self.last_event = f"{label} HIT — position closed {sign}${abs(pnl):.2f}"
        except OrderRejected as e:
            self.last_event = f"{hit} hit but close blocked: {e}"

    def _guard_open(self, qty):
        if qty > MAX_CONTRACTS:
            raise OrderRejected(f"quantity {qty} exceeds MAX_CONTRACTS ({MAX_CONTRACTS})")
        if self.day_realized <= -abs(DAILY_LOSS_LIMIT):
            raise OrderRejected(f"daily loss limit hit (${DAILY_LOSS_LIMIT:.0f}) — trading blocked")
        if self.position is not None:
            raise OrderRejected("a position is already open — close it first")

    def state(self):
        ev, self.last_event = self.last_event, None
        return {"mode": self.mode, "account_id": self.account_id,
                "buying_power": round(self.buying_power, 2),
                "position": self.position, "day_realized": round(self.day_realized, 2),
                "blotter": self.blotter[-20:], "settings": self.settings, "event": ev}


class PaperFuturesSession(BaseFuturesSession):
    def connect(self, app_key, app_secret):
        self.account_id = "FUT-PAPER"
        self.buying_power = 5000.00
        return self.state()

    def place(self, symbol, side, qty):
        self._guard_open(qty)
        px = get_price(symbol)["price"]
        self.position = {"symbol": symbol, "side": side, "qty": qty,
                         "entry": px, "mark": px,
                         "opened_at": dt.datetime.now().strftime("%H:%M")}
        self._update_trail()
        return self.position

    def refresh_mark(self):
        p = self.position
        if not p:
            return None
        real = get_price(p["symbol"])
        if real["live"]:
            p["mark"] = real["price"]
        else:
            p["mark"] = round(p["mark"] + random.choice([-1, -1, 0, 1, 1]) * FUT[p["symbol"]]["tick"], 2)
        pv = FUT[p["symbol"]]["point_value"]
        p["pnl"] = round(self._points_pnl() * pv * p["qty"], 2)
        p["points"] = round(self._points_pnl(), 2)
        self._update_trail()
        self._maybe_auto_close()
        return self.position

    def close(self):
        if not self.position:
            raise OrderRejected("no open position to close")
        p = self.position
        pv = FUT[p["symbol"]]["point_value"]
        pnl = round(self._points_pnl() * pv * p["qty"], 2)
        self.day_realized += pnl
        self.buying_power += pnl
        self.blotter.append({"time": p["opened_at"],
                             "desc": f"{p['symbol']} {p['side']} x{p['qty']}",
                             "move": f"{p['entry']:.2f} -> {p['mark']:.2f}", "pnl": pnl})
        self.position = None
        return {"closed": True, "pnl": pnl}


class LiveFuturesSession(BaseFuturesSession):
    def connect(self, app_key, app_secret):
        raise OrderRejected(
            "FUTURES LIVE is not wired yet — it needs: futures approval on your "
            "Webull account, the CME OpenAPI data subscription ($228/mo), and "
            "the futures order format confirmed. PAPER futures is fully working "
            "meanwhile — hit PAPER + START. Ask Claude to wire LIVE when you "
            "have futures approval + CME data.")


def make_session(mode):
    return PaperFuturesSession(mode) if mode == "PAPER" else LiveFuturesSession(mode)
