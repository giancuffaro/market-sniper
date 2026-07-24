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


def _closes(sym, count=80):
    """Recent 1-minute closes as [(ts, close), ...] (oldest→newest)."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.request.quote(FUT[sym]['yahoo'])}?range=1d&interval=1m")
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=6) as r:
        res = json.load(r)["chart"]["result"][0]
    ts = res.get("timestamp") or []
    closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    out = [(int(ts[i]), float(closes[i])) for i in range(min(len(ts), len(closes)))
           if closes[i] is not None]
    return out[-count:]


def _ema(vals, period):
    k = 2.0 / (period + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def _chart(sym, interval, rng):
    """Raw Yahoo chart result dict for a symbol/interval/range."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.request.quote(FUT[sym]['yahoo'])}?range={rng}&interval={interval}")
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=6) as r:
        return json.load(r)["chart"]["result"][0]


def _interval_closes(sym, interval, rng):
    res = _chart(sym, interval, rng)
    c = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    return [float(x) for x in c if x is not None]


def session_vwap(sym):
    """Session VWAP from 1-min bars, plus the latest bar (ts,high,low,close)."""
    try:
        res = _chart(sym, "1m", "1d")
    except Exception:
        return {}
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    H, L, C, V = q.get("high") or [], q.get("low") or [], q.get("close") or [], q.get("volume") or []
    try:
        open_epoch = int(res["meta"]["currentTradingPeriod"]["regular"]["start"])
    except Exception:
        open_epoch = ts[0] if ts else 0
    num = den = 0.0
    last = None
    for i, t in enumerate(ts):
        if i >= len(V) or H[i] is None or L[i] is None or C[i] is None or V[i] is None:
            continue
        if t < open_epoch:
            continue
        tp = (H[i] + L[i] + C[i]) / 3.0
        num += tp * V[i]; den += V[i]
        last = (int(t), float(H[i]), float(L[i]), float(C[i]))
    if den <= 0 or not last:
        return {}
    return {"vwap": round(num / den, 2), "bar": last}


def _trend_from_closes(closes, fast=9, slow=21):
    if len(closes) < slow + 1:
        return "—"
    f, s = _ema(closes, fast), _ema(closes, slow)
    thr = closes[-1] * 0.0005    # ~0.05% dead-band = FLAT
    if f - s > thr:
        return "UP"
    if f - s < -thr:
        return "DOWN"
    return "FLAT"


# Base series fetched once each; higher timeframes are resampled from them.
_TREND_BASES = {
    "b1m":  ("1m", "1d"),
    "b5m":  ("5m", "5d"),
    "b60m": ("60m", "1mo"),
    "b1d":  ("1d", "1y"),
}
# (timeframe key, base key, group size). e.g. 3m = three 1-min bars.
_TREND_TFS = [
    ("1m", "b1m", 1),
    ("5m", "b5m", 1), ("10m", "b5m", 2), ("15m", "b5m", 3),
    ("20m", "b5m", 4), ("30m", "b5m", 6),
    ("1h", "b60m", 1), ("2h", "b60m", 2), ("4h", "b60m", 4),
    ("1d", "b1d", 1), ("1w", "b1d", 5),
]

def trend(sym):
    bases = {}
    for key, (interval, rng) in _TREND_BASES.items():
        try:
            bases[key] = _interval_closes(sym, interval, rng)
        except Exception:
            bases[key] = []
    out = {}
    for tf, bkey, group in _TREND_TFS:
        closes = bases.get(bkey) or []
        if group > 1 and closes:
            closes = [closes[i] for i in range(len(closes) - 1, -1, -group)][::-1]
        out[tf] = _trend_from_closes(closes) if closes else "—"
    return out


def default_futures_strategies():
    """One popular, followable built-in: 9/21 EMA crossover (trend)."""
    return [
        {"id": "ema921", "name": "9/21 EMA Crossover", "builtin": True, "enabled": False,
         "symbol": "MNQ", "qty": 1,
         "desc": ("Classic trend strategy. On the 1-minute chart, when the 9 EMA crosses "
                  "ABOVE the 21 EMA it goes LONG; when the 9 EMA crosses BELOW the 21 EMA "
                  "it goes SHORT. Manage the trade with the Take-Profit / Stop / Trailing "
                  "stop you set in Configuration."),
         "trigger": {"type": "ema_cross", "fast": 9, "slow": 21}},
        {"id": "vwap_pb", "name": "VWAP Pullback", "builtin": True, "enabled": False,
         "symbol": "MNQ", "qty": 1,
         "desc": ("Trend + value entry. When price is ABOVE the session VWAP (uptrend) and "
                  "pulls back to touch VWAP but closes back above it, goes LONG. When price is "
                  "BELOW VWAP (downtrend) and rallies to touch it but closes back below, goes "
                  "SHORT. VWAP is the volume-weighted average price from the session open."),
         "trigger": {"type": "vwap_pullback", "band": 2.0}},
    ]


def _coerce_fstrategy(st):
    if not isinstance(st, dict):
        return None
    trig = st.get("trigger") or {}
    ttype = trig.get("type") if trig.get("type") in ("ema_cross", "vwap_pullback") else "ema_cross"
    base = {
        "id": str(st.get("id") or "s")[:40],
        "name": str(st.get("name") or "Strategy")[:60],
        "desc": str(st.get("desc") or "")[:400],
        "enabled": bool(st.get("enabled")),
        "builtin": bool(st.get("builtin")),
        "symbol": st.get("symbol") if st.get("symbol") in FUT else "MNQ",
        "qty": max(1, min(int(st.get("qty") or 1), MAX_CONTRACTS)),
    }
    if ttype == "vwap_pullback":
        band = float(trig.get("band") or 2.0)
        base["trigger"] = {"type": "vwap_pullback", "band": max(0.25, min(band, 50.0))}
    else:
        fast = max(2, min(int(trig.get("fast") or 9), 50))
        slow = max(fast + 1, min(int(trig.get("slow") or 21), 100))
        base["trigger"] = {"type": "ema_cross", "fast": fast, "slow": slow}
    return base


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
        self.strategies = default_futures_strategies()
        self._fired = set()   # (strategy_id, bar_ts) already entered on that bar

    def update_strategies(self, strategies):
        if not isinstance(strategies, list):
            raise OrderRejected("strategies must be a list")
        self.strategies = [c for c in (_coerce_fstrategy(s) for s in strategies) if c]
        return self.strategies

    def _strategy_side(self, st, sym):
        """Return ('LONG'/'SHORT', bar_ts) if the strategy fires, else (None, bar_ts)."""
        trig = st.get("trigger", {})
        if trig.get("type") == "vwap_pullback":
            data = session_vwap(sym)
            if not data:
                return (None, None)
            vwap = data["vwap"]; ts, hi, lo, cl = data["bar"]
            band = float(trig.get("band", 2.0))
            if cl > vwap and lo <= vwap + band:      # uptrend, pulled back to VWAP, held above
                return ("LONG", ts)
            if cl < vwap and hi >= vwap - band:      # downtrend, rallied to VWAP, held below
                return ("SHORT", ts)
            return (None, ts)
        fast, slow = int(trig.get("fast", 9)), int(trig.get("slow", 21))
        try:
            bars = _closes(sym, slow * 3 + 5)
        except Exception:
            return (None, None)
        if len(bars) < slow + 5:
            return (None, None)
        closes = [c for _, c in bars]
        bar_ts = bars[-1][0]
        f_cur, s_cur = _ema(closes, fast), _ema(closes, slow)
        f_prev, s_prev = _ema(closes[:-1], fast), _ema(closes[:-1], slow)
        if f_prev <= s_prev and f_cur > s_cur:
            return ("LONG", bar_ts)
        if f_prev >= s_prev and f_cur < s_cur:
            return ("SHORT", bar_ts)
        return (None, bar_ts)

    def _eval_strategies(self):
        if self.position is not None:
            return
        for st in self.strategies:
            if not st.get("enabled"):
                continue
            sym = st.get("symbol") or "MNQ"
            if sym not in FUT:
                continue
            side, bar_ts = self._strategy_side(st, sym)
            if not side:
                continue
            if (st.get("id"), bar_ts) in self._fired:
                continue
            self._fired.add((st.get("id"), bar_ts))   # one entry per bar per strategy
            try:
                self.place(sym, side, int(st.get("qty", 1)))
                reason = ("VWAP pullback" if st.get("trigger", {}).get("type") == "vwap_pullback"
                          else "EMA cross")
                self.last_event = f"STRATEGY «{st.get('name')}» — {side} {sym} ({reason})"
            except OrderRejected as e:
                self.last_event = f"strategy «{st.get('name')}» blocked: {e}"
            return

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
        self._eval_strategies()   # fire any enabled strategy on an EMA cross
        ev, self.last_event = self.last_event, None
        return {"mode": self.mode, "account_id": self.account_id,
                "buying_power": round(self.buying_power, 2),
                "position": self.position, "day_realized": round(self.day_realized, 2),
                "blotter": self.blotter[-20:], "settings": self.settings,
                "strategies": self.strategies, "event": ev}


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
