"""
MARKET SNIPER FUTURES — session engine.
COMPLETELY SEPARATE from the options app. Nothing here imports the options code.

  MNQ (Micro Nasdaq): tick 0.25 = $0.50  ->  $2 per point
  MES (Micro S&P):    tick 0.25 = $1.25  ->  $5 per point

Three real broker routes, all live (v3.6 removed PAPER and Tradovate):

  WEBULL  — WebullFuturesSession: Webull production OpenAPI, REAL MONEY,
            gated behind ALLOW_LIVE=1.
  NINJA   — NinjaTraderSession: order instruction files to NinjaTrader 8.
            Was called "LIVE" before v3.6; the old name still resolves.
  TOPSTEP — TopstepSession: TopstepX / ProjectX REST.
"""

import os
import json
import time
import math
import uuid
import random
import datetime as dt
import urllib.request
import urllib.error

import user_config as uc
import config

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
    "round_enabled": False, "round_step": 50.0,
}

ROUND_STEPS = (10.0, 25.0, 50.0, 100.0, 250.0)


def round_target(price, side, step):
    """The round-number price an armed entry waits for.

    LONG  -> the level at or BELOW price (buy the pullback)
    SHORT -> the level at or ABOVE price (sell the rally)
    Ex: LONG MNQ at 28716 with step 50 -> 28700.  SHORT at 28716 -> 28750.
    """
    step = float(step)
    if step <= 0:
        raise OrderRejected("round-number step must be greater than 0")
    q = price / step
    lvl = math.floor(q) * step if side == "LONG" else math.ceil(q) * step
    return round(lvl, 2)


def to_tick(sym, price):
    """Snap a price to the instrument's tick so the broker won't reject it."""
    t = FUT[sym]["tick"]
    return round(round(float(price) / t) * t, 4)

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


def _restore_strategies(saved):
    """Your saved strategies, or the built-ins on a first run.

    If a newer version of the app ships a built-in you don't have saved yet,
    it gets added (switched OFF) instead of being lost.
    """
    kept = [c for c in (_coerce_fstrategy(s) for s in (saved or [])) if c]
    if not kept:
        return default_futures_strategies()
    have = {s["id"] for s in kept}
    for d in default_futures_strategies():
        if d["id"] not in have:
            kept.append(d)
    return kept


# ---- Uploaded historical data (NinjaTrader CSV export) ------------------
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_META_PATH = os.path.join(_DATA_DIR, "meta.json")
_CSV_CACHE = {}   # symbol -> {"mtime":.., "bars":[...]}

_DT_FORMATS = ("%Y%m%d %H%M%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
               "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y%m%d")

def _parse_dt(s):
    s = s.strip()
    for f in _DT_FORMATS:
        try:
            return dt.datetime.strptime(s, f)
        except ValueError:
            continue
    return None

def _parse_csv_bars(path):
    """Flexible parser for NinjaTrader / generic OHLCV exports.
    Accepts ';' , ',' or tab delimiters; datetime as one field or date+time."""
    bars = []
    with open(path, "r", errors="ignore") as fh:
        for ln, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            low = line.lower()
            if ln == 0 and any(h in low for h in ("open", "close", "date", "time")) \
                    and not any(ch.isdigit() for ch in line.split((";" if ";" in line else ","))[0][:4] or ""):
                continue  # header row
            delim = ";" if ";" in line else ("," if "," in line else ("\t" if "\t" in line else None))
            if not delim:
                continue
            parts = [p.strip() for p in line.split(delim)]
            if len(parts) < 5:
                continue
            dtobj = _parse_dt(parts[0]); idx = 1
            if dtobj is None and len(parts) >= 2:
                dtobj = _parse_dt(parts[0] + " " + parts[1]); idx = 2
            if dtobj is None:
                continue
            nums = parts[idx:]
            try:
                o, h, l, c = float(nums[0]), float(nums[1]), float(nums[2]), float(nums[3])
                v = float(nums[4]) if len(nums) > 4 else 0.0
            except (ValueError, IndexError):
                continue
            bars.append({"t": int(dtobj.replace(tzinfo=dt.timezone.utc).timestamp()),
                         "o": o, "h": h, "l": l, "c": c, "v": v})
    bars.sort(key=lambda b: b["t"])
    return bars

def csv_bars(symbol):
    path = os.path.join(_DATA_DIR, f"{symbol}.csv")
    if not os.path.exists(path):
        return None
    mt = os.path.getmtime(path)
    c = _CSV_CACHE.get(symbol)
    if c and c["mtime"] == mt:
        return c["bars"]
    bars = _parse_csv_bars(path)
    _CSV_CACHE[symbol] = {"mtime": mt, "bars": bars}
    return bars

def _load_meta():
    try:
        with open(_META_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {}

def data_status():
    meta = _load_meta()
    out = {}
    for sym in FUT:
        if os.path.exists(os.path.join(_DATA_DIR, f"{sym}.csv")):
            out[sym] = meta.get(sym) or {"uploaded_at": None, "bars": None}
        else:
            out[sym] = None
    return out

def save_uploaded(symbol, raw):
    if symbol not in FUT:
        raise OrderRejected("unknown symbol")
    os.makedirs(_DATA_DIR, exist_ok=True)
    path = os.path.join(_DATA_DIR, f"{symbol}.csv")
    with open(path, "wb") as fh:
        fh.write(raw)
    _CSV_CACHE.pop(symbol, None)
    bars = _parse_csv_bars(path)
    if len(bars) < 30:
        try:
            os.remove(path)
        except OSError:
            pass
        raise OrderRejected("couldn't read enough bars — expected a NinjaTrader OHLCV "
                            "export (datetime, open, high, low, close, volume)")
    meta = _load_meta()
    meta[symbol] = {"uploaded_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "bars": len(bars),
                    "from": dt.datetime.utcfromtimestamp(bars[0]["t"]).strftime("%Y-%m-%d"),
                    "to": dt.datetime.utcfromtimestamp(bars[-1]["t"]).strftime("%Y-%m-%d")}
    with open(_META_PATH, "w") as fh:
        json.dump(meta, fh)
    _CSV_CACHE[symbol] = {"mtime": os.path.getmtime(path), "bars": bars}
    return {"symbol": symbol, **meta[symbol]}


# ---- Backtesting (free Yahoo data) --------------------------------------
def _ohlcv(sym, interval, rng):
    res = _chart(sym, interval, rng)
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    O, H, L, C, V = (q.get("open") or [], q.get("high") or [], q.get("low") or [],
                     q.get("close") or [], q.get("volume") or [])
    bars = []
    for i, t in enumerate(ts):
        if i >= len(C) or C[i] is None or H[i] is None or L[i] is None:
            continue
        bars.append({"t": int(t),
                     "o": float(O[i]) if i < len(O) and O[i] is not None else float(C[i]),
                     "h": float(H[i]), "l": float(L[i]), "c": float(C[i]),
                     "v": float(V[i]) if i < len(V) and V[i] is not None else 0.0})
    return bars


def _ema_series(vals, period):
    k = 2.0 / (period + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _sim_ema(bars, fast, slow):
    """Always-in MA system: reverse on the opposite cross. Returns pnl per trade in points."""
    closes = [b["c"] for b in bars]
    if len(closes) < slow + 2:
        return []
    ef, es = _ema_series(closes, fast), _ema_series(closes, slow)
    trades, pos, entry = [], None, 0.0
    for i in range(slow + 1, len(bars)):
        up = ef[i - 1] <= es[i - 1] and ef[i] > es[i]
        dn = ef[i - 1] >= es[i - 1] and ef[i] < es[i]
        if pos is None:
            if up: pos, entry = "LONG", closes[i]
            elif dn: pos, entry = "SHORT", closes[i]
        elif pos == "LONG" and dn:
            trades.append(closes[i] - entry); pos, entry = "SHORT", closes[i]
        elif pos == "SHORT" and up:
            trades.append(entry - closes[i]); pos, entry = "LONG", closes[i]
    return trades


def _sim_vwap(bars, band):
    """Session-VWAP pullback: enter on pullback to VWAP, exit when close crosses back."""
    trades, pos, entry = [], None, 0.0
    day = None; num = den = 0.0; vwap = None
    for b in bars:
        d = dt.datetime.utcfromtimestamp(b["t"]).date()
        if d != day:
            if pos is not None:
                trades.append((b["c"] - entry) if pos == "LONG" else (entry - b["c"]))
                pos = None
            day = d; num = den = 0.0
        vol = b["v"] or 1.0
        tp = (b["h"] + b["l"] + b["c"]) / 3.0
        num += tp * vol; den += vol; vwap = num / den
        if pos is None:
            if b["c"] > vwap and b["l"] <= vwap + band: pos, entry = "LONG", b["c"]
            elif b["c"] < vwap and b["h"] >= vwap - band: pos, entry = "SHORT", b["c"]
        elif pos == "LONG" and b["c"] < vwap:
            trades.append(b["c"] - entry); pos = None
        elif pos == "SHORT" and b["c"] > vwap:
            trades.append(entry - b["c"]); pos = None
    return trades


# What a round turn really costs you. A backtest that ignores these will show
# a green number on a strategy that bleeds money in real life — the edge on a
# scalping system is often smaller than the cost of trading it.
DEFAULT_COMMISSION = 1.24     # $ per round turn, per contract (Webull micros, approx)
DEFAULT_SLIPPAGE_TICKS = 1.0  # ticks given up on entry+exit combined


def _bt_stats(trades, sym, commission=None, slippage_ticks=None):
    """`trades` is a list of gross point moves. Commission and slippage are
    charged per round turn, then the whole picture is recomputed on the
    after-cost numbers — that is the only P&L that would have hit your account."""
    pv = FUT[sym]["point_value"]
    tick = FUT[sym]["tick"]
    comm = DEFAULT_COMMISSION if commission is None else max(0.0, float(commission))
    slip_t = DEFAULT_SLIPPAGE_TICKS if slippage_ticks is None else max(0.0, float(slippage_ticks))
    cost_per_trade = comm + slip_t * tick * pv           # dollars, per round turn
    gross_net = round(sum(trades) * pv, 2)
    cost_total = round(cost_per_trade * len(trades), 2)
    # charge the cost against every trade before anything is counted
    trades = [p - cost_per_trade / pv for p in trades]
    wins = [p for p in trades if p > 0]
    losses = [p for p in trades if p < 0]
    gp, gl = sum(wins) * pv, abs(sum(losses)) * pv
    eq = peak = mdd = 0.0
    for p in trades:
        eq += p * pv; peak = max(peak, eq); mdd = min(mdd, eq - peak)
    n = len(trades)
    return {
        "trades": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n * 100, 1) if n else 0.0,
        "net": round(sum(trades) * pv, 2),
        "gross_profit": round(gp, 2), "gross_loss": round(gl, 2),
        "avg_win": round(sum(wins) / len(wins) * pv, 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses) * pv, 2) if losses else 0.0,
        "largest_win": round(max(wins) * pv, 2) if wins else 0.0,
        "largest_loss": round(min(losses) * pv, 2) if losses else 0.0,
        "profit_factor": round(gp / gl, 2) if gl > 0 else None,
        "max_drawdown": round(mdd, 2), "point_value": pv,
        "gross_net": gross_net, "cost_total": cost_total,
        "cost_per_trade": round(cost_per_trade, 2),
        "commission": round(comm, 2), "slippage_ticks": slip_t,
        "slippage_dollars": round(slip_t * tick * pv, 2),
    }


# duration -> (Yahoo range, interval). Finest interval that free data allows.
_BT_MAP = {"1mo": ("1mo", "5m"), "3mo": ("3mo", "60m"), "6mo": ("6mo", "60m"),
           "1y": ("1y", "60m"), "2y": ("2y", "60m"), "5y": ("5y", "1d")}

def backtest(strategy, duration, commission=None, slippage_ticks=None):
    sym = strategy.get("symbol", "MNQ")
    if sym not in FUT:
        sym = "MNQ"
    trig = strategy.get("trigger", {})
    stype = trig.get("type")
    note = ""
    months = {"1mo": 1, "3mo": 3, "6mo": 6, "1y": 12, "2y": 24, "5y": 60}.get(duration, 6)
    uploaded = csv_bars(sym)
    if uploaded and len(uploaded) >= 30:
        # Use the trader's own NinjaTrader export, sliced to the chosen window.
        source, interval = "Your NinjaTrader data", "your bars"
        cutoff = uploaded[-1]["t"] - months * 30 * 86400
        bars = [b for b in uploaded if b["t"] >= cutoff]
        if len(bars) < 30:
            bars = uploaded
        # Say so plainly rather than quietly testing a shorter window than asked.
        span_days = (uploaded[-1]["t"] - uploaded[0]["t"]) / 86400.0
        if span_days < months * 30 * 0.9:
            note = ("Your file only covers about %d days, so that's the whole test — "
                    "export a longer range in NinjaTrader for a real %s look."
                    % (round(span_days), duration))
    else:
        source = "Yahoo (free)"
        if stype == "vwap_pullback":
            rng, interval = "1mo", "5m"      # VWAP needs intraday; free 5m caps ~60 days
            if duration != "1mo":
                note = "VWAP needs intraday bars — free data limits this to ~1 month at 5-min. Upload NinjaTrader data for the full range."
        else:
            rng, interval = _BT_MAP.get(duration, ("6mo", "60m"))
        try:
            bars = _ohlcv(sym, interval, rng)
        except Exception as e:
            return {"error": f"could not load free data ({type(e).__name__})"}
    if len(bars) < 30:
        return {"error": "not enough historical data for this range"}
    if stype == "vwap_pullback":
        trades = _sim_vwap(bars, float(trig.get("band", 2.0)))
    else:
        trades = _sim_ema(bars, int(trig.get("fast", 9)), int(trig.get("slow", 21)))
    out = _bt_stats(trades, sym, commission, slippage_ticks)
    out.update({"symbol": sym, "interval": interval, "duration": duration, "source": source,
                "from": dt.datetime.utcfromtimestamp(bars[0]["t"]).strftime("%Y-%m-%d"),
                "to": dt.datetime.utcfromtimestamp(bars[-1]["t"]).strftime("%Y-%m-%d"),
                "bars": len(bars), "note": note, "name": strategy.get("name", "")})
    return out


class OrderRejected(Exception):
    pass


class BaseFuturesSession:
    def __init__(self, mode):
        self.mode = mode
        self.account_id = None
        self.buying_power = 0.0
        self.position = None
        self.day_realized = 0.0          # NET of fees — the number that matters
        self.day_fees = 0.0              # commission paid today, shown separately
        self.blotter = []
        # Start from whatever you had switched on last time (my-settings.json),
        # not from the factory defaults.
        self.settings = dict(DEFAULT_SETTINGS)
        self.settings.update({k: v for k, v in uc.load("futures_settings", {}).items()
                              if k in DEFAULT_SETTINGS})
        self.last_event = None
        self.strategies = _restore_strategies(uc.load("futures_strategies", None))
        self._fired = set()   # (strategy_id, bar_ts) already entered on that bar
        self.armed = None     # pending round-number entry

    # ---- ROUND-NUMBER ENTRY (resting LIMIT at the level) --------------------
    def arm(self, symbol, side, qty):
        """Rest a LIMIT order on the nearest round level.

        LONG  -> buy limit at the level BELOW price   (never pays more)
        SHORT -> sell limit at the level ABOVE price  (never sells for less)
        Because the limit sits at or better than the market it can only fill at
        that price or better — no slippage. On live routes the order is working
        at the broker.
        """
        qty = int(qty)
        self._guard_open(qty)
        if symbol not in FUT:
            raise OrderRejected("unknown symbol")
        px = get_price(symbol)["price"]
        step = float(self.settings.get("round_step") or 50.0)
        target = to_tick(symbol, round_target(px, side, step))
        order_id = None
        if hasattr(self, "place_limit"):          # live routes rest it at the broker
            order_id = self.place_limit(symbol, side, qty, target)
        self.armed = {"symbol": symbol, "side": side, "qty": qty,
                      "target": target, "step": step, "price_at_arm": round(px, 2),
                      "working": order_id is not None, "order_id": order_id}
        return dict(self.armed)

    def disarm(self):
        a = self.armed
        if a and a.get("order_id") and hasattr(self, "cancel_limit"):
            try:
                self.cancel_limit(a["order_id"])
                self.last_event = "Working limit at %g CANCELLED at the broker." % a["target"]
            except OrderRejected as e:
                # Never silently drop it — a live order may still be out there.
                self.armed = None
                raise OrderRejected(
                    "Couldn't cancel the working limit (%s). The order may STILL be live at your "
                    "broker — cancel it there before trading again." % e)
        self.armed = None
        return {"armed": None}

    def _maybe_trigger_entry(self):
        a = self.armed
        if not a or self.position is not None:
            return
        px = get_price(a["symbol"])["price"]
        reached = (px <= a["target"]) if a["side"] == "LONG" else (px >= a["target"])
        if not reached:
            return
        self.armed = None                      # clear first so we never double-fire
        try:
            # Live: the resting limit already filled at the broker — just record it.
            # Paper: simulate the fill now, at the limit price.
            if not a.get("working"):
                self.place(a["symbol"], a["side"], a["qty"], limit=a["target"])
            else:
                self._adopt_filled(a)
            if self.position:
                self.position["entry_round"] = a["target"]
                self.position["entry"] = a["target"]   # a limit fills AT its price or better
                self._update_trail()
            self.last_event = (f"LIMIT FILLED — {a['symbol']} {a['side']} {a['qty']} "
                               f"at {a['target']:g}")
        except OrderRejected as e:
            self.last_event = f"armed entry blocked: {e}"

    def _adopt_filled(self, a):
        """Record the position for a limit that was already working at the broker."""
        self.position = {"symbol": a["symbol"], "side": a["side"], "qty": a["qty"],
                         "entry": a["target"], "mark": get_price(a["symbol"])["price"],
                         "opened_at": dt.datetime.now().strftime("%H:%M")}
        self._update_trail()

    def update_strategies(self, strategies):
        if not isinstance(strategies, list):
            raise OrderRejected("strategies must be a list")
        self.strategies = [c for c in (_coerce_fstrategy(s) for s in strategies) if c]
        uc.save("futures_strategies", self.strategies)   # remembered for next launch
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
        if self.position is not None or self.armed is not None:
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
        for k in ("tp_enabled", "sl_enabled", "trail_enabled", "round_enabled"):
            if k in new:
                s[k] = bool(new[k])
        if "round_step" in new:
            try:
                v = float(new["round_step"])
                if v > 0:
                    s["round_step"] = v
            except (TypeError, ValueError):
                pass
        for k in ("tp_points", "sl_points", "trail_points"):
            if k in new:
                try:
                    v = float(new[k])
                    if v > 0:
                        s[k] = v
                except (TypeError, ValueError):
                    pass
        uc.save("futures_settings", s)      # remembered for next launch
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

    def _log_trade(self, p, exit_price, pnl, reason="CLOSE"):
        """Write a finished futures trade to the daily log. Never raises."""
        try:
            import trade_log, datetime as _dt
            now = _dt.datetime.now()
            trade_log.record({
                "date": now.strftime("%Y-%m-%d"),
                "time_in": p.get("opened_at") or "",
                "time_out": now.strftime("%H:%M:%S"),
                "app": "FUTURES",
                "broker": getattr(self, "mode", ""),
                "account": getattr(self, "account_id", ""),
                "symbol": p.get("symbol"),
                "side": p.get("side"),
                "strike": "",
                "expiry": "",
                "qty": p.get("qty"),
                "entry": p.get("entry"),
                "exit": exit_price,
                "pnl": pnl,
                "pnl_pct": "",
                "exit_reason": reason,
                "held_secs": "",
                "note": "",
            })
        except Exception as e:                               # noqa: BLE001
            print("[trade_log] futures not recorded: %s" % str(e)[:120], flush=True)

    def forget_position(self):
        """Escape hatch for a position the app thinks you hold but the broker
        does not - typically because you closed it by hand in the platform.

        Sends NOTHING to any broker. It only clears this app's own idea of the
        trade. That matters more here than it looks: TP, SL and the trailing
        stop are evaluated every second, and firing one against a position you
        have already closed sends a CLOSE for contracts you do not hold - which
        does not flatten anything, it OPENS a new position the other way.

        Only use it once you have confirmed in the platform that you are flat.
        """
        p, self.position = self.position, None
        self.armed = None
        if p:
            self.last_event = (
                "Cleared %s %s x%s from the screen. NO order was sent - confirm "
                "in your platform that you are actually flat."
                % (p.get("side"), p.get("symbol"), p.get("qty")))
        return {"cleared": bool(p), "position": None}

    def _maybe_auto_close(self):
        hit = self._bracket_hit()
        # Remember WHY, so the trade log says TP / SL / TRAIL instead of CLOSE.
        self._exit_reason = hit or "CLOSE"
        if not hit:
            return
        try:
            result = self.close()
            pnl = result.get("pnl", 0.0)   # NET of the round-turn commission
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
        self._maybe_trigger_entry()   # fire an armed round-number entry if price got there
        self._eval_strategies()       # fire any enabled strategy on an EMA cross
        ev, self.last_event = self.last_event, None
        return {"mode": self.mode, "account_id": self.account_id,
                "buying_power": round(self.buying_power, 2),
                "position": self.position, "armed": self.armed,
                "day_realized": round(self.day_realized, 2),
                "day_fees": round(self.day_fees, 2),
                "blotter": self.blotter[-20:], "settings": self.settings,
                "strategies": self.strategies, "event": ev}


class NinjaTraderSession(BaseFuturesSession):
    """Routes real orders to NinjaTrader 8 via its ATI Order Instruction Files.
    One-way (fire-and-forget): it drops oif*.txt into the 'incoming' folder and
    NinjaTrader executes them. Position/P&L here are the app's own estimate from
    the live price feed — always confirm fills in NinjaTrader itself."""

    def __init__(self, mode="LIVE"):
        super().__init__(mode)
        self.account = "Sim101"
        self.folder = None
        self._oif_n = 0

    def connect(self, app_key, app_secret, account=None, incoming_folder=None):
        self.account = (account or "Sim101").strip() or "Sim101"
        folder = (incoming_folder or "").strip() or \
            os.path.expanduser("~/Documents/NinjaTrader 8/incoming")
        if not os.path.isdir(folder):
            raise OrderRejected(
                "Couldn't find NinjaTrader's 'incoming' folder at:\n" + folder +
                "\n\nMake sure NinjaTrader 8 is running, then either create that folder or "
                "type the exact path (it's inside your Documents\\NinjaTrader 8 folder).")
        self.folder = folder
        self.account_id = "NT:" + self.account
        self.buying_power = 0.0
        return self.state()

    def _write_oif(self, text):
        self._oif_n += 1
        name = "oif_ms_%d_%d.txt" % (int(time.time() * 1000), self._oif_n)
        with open(os.path.join(self.folder, name), "w") as fh:
            fh.write(text.strip() + "\n")

    def place(self, symbol, side, qty, limit=None):
        self._guard_open(qty)
        if symbol not in FUT:
            raise OrderRejected("unknown symbol")
        action = "BUY" if side == "LONG" else "SELL"
        if limit:
            px = to_tick(symbol, limit)
            self._write_oif("PLACE;%s;%s;%s;%d;LIMIT;%s;;DAY" % (
                self.account, symbol, action, int(qty), _fmt_px(px)))
            kind = "LIMIT %s" % _fmt_px(px)
        else:
            px = get_price(symbol)["price"]
            self._write_oif("PLACE;%s;%s;%s;%d;MARKET;;;DAY" % (self.account, symbol, action, int(qty)))
            kind = "MARKET"
        self.position = {"symbol": symbol, "side": side, "qty": int(qty),
                         "entry": px, "mark": get_price(symbol)["price"],
                         "opened_at": dt.datetime.now().strftime("%H:%M")}
        self._update_trail()
        self.last_event = "SENT to NinjaTrader (%s): %s %d %s %s" % (
            self.account, action, int(qty), symbol, kind)
        return self.position

    # --- working limit for round-number entry ---
    def place_limit(self, symbol, side, qty, price):
        self._guard_open(qty)
        if symbol not in FUT:
            raise OrderRejected("unknown symbol")
        action = "BUY" if side == "LONG" else "SELL"
        oid = "MS%d" % int(time.time() * 1000)
        px = _fmt_px(to_tick(symbol, price))
        # PLACE;ACCOUNT;INSTRUMENT;ACTION;QTY;TYPE;LIMIT;STOP;TIF;OCO;ORDER ID
        self._write_oif("PLACE;%s;%s;%s;%d;LIMIT;%s;;DAY;;%s" % (
            self.account, symbol, action, int(qty), px, oid))
        self.last_event = "NinjaTrader (%s): working %s LIMIT %d %s @ %s" % (
            self.account, action, int(qty), symbol, px)
        return oid

    def cancel_limit(self, order_id):
        self._write_oif("CANCEL;%s;;;;;;;;;;;" % order_id)
        return True

    def refresh_mark(self):
        p = self.position
        self._eval_strategies()
        p = self.position
        if not p:
            return None
        p["mark"] = get_price(p["symbol"])["price"]
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
        self._write_oif("CLOSEPOSITION;%s;%s;;;;;;;;;;;" % (self.account, p["symbol"]))
        pv = FUT[p["symbol"]]["point_value"]
        pnl = round(self._points_pnl() * pv * p["qty"], 2)
        self.day_realized += pnl
        self._log_trade(p, locals().get("px") or p.get("mark"), pnl,
                        getattr(self, "_exit_reason", "CLOSE"))
        self.buying_power += pnl
        self.blotter.append({"time": p["opened_at"],
                             "desc": "%s %s x%d (NT)" % (p["symbol"], p["side"], p["qty"]),
                             "move": "%.2f -> %.2f" % (p["entry"], p["mark"]), "pnl": pnl})
        self.position = None
        self.last_event = "SENT to NinjaTrader: CLOSE %s" % p["symbol"]
        return {"closed": True, "pnl": pnl}


_MONTH_CODE = {3: "H", 6: "M", 9: "U", 12: "Z"}

def _tv_front_symbol(sym):
    """Nearest quarterly contract, e.g. MNQM6 (June 2026).

    Named _tv_* for historical reasons (it arrived with the Tradovate route,
    removed in v3.6). Topstep uses the identical convention, so it stays."""
    today = dt.date.today()
    y, m = today.year, today.month
    for qm in (3, 6, 9, 12):
        if qm > m or (qm == m and today.day < 12):   # roll ~mid expiry month
            return "%s%s%d" % (sym, _MONTH_CODE[qm], y % 10)
    return "%s%s%d" % (sym, _MONTH_CODE[3], (y + 1) % 10)

def _fmt_px(v):
    """Trim trailing zeros so 28700.0 goes out as 28700, 23150.25 stays 23150.25."""
    return ("%.4f" % float(v)).rstrip("0").rstrip(".")


_TS_BASE = "https://api.topstepx.com"

def _ts_req(path, token=None, body=None):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(_TS_BASE + path, data=json.dumps(body or {}).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


class TopstepSession(BaseFuturesSession):
    """Real order routing to a Topstep (TopstepX / ProjectX) account."""

    def __init__(self, mode="TOPSTEP"):
        super().__init__(mode)
        self.token = None; self.acct = None
        self._contracts = {}

    def connect(self, username, apikey, account_hint=""):
        try:
            res = _ts_req("/api/Auth/loginKey",
                          body={"userName": (username or "").strip(), "apiKey": (apikey or "").strip()})
        except urllib.error.HTTPError as e:
            raise OrderRejected("Topstep login failed (%s). Check your TopstepX username and API key." % e.code)
        except Exception as e:
            raise OrderRejected("Couldn't reach Topstep: " + str(e)[:120])
        if not res.get("success") or not res.get("token"):
            raise OrderRejected("Topstep login rejected — wrong username/API key, or the $29-mo "
                                "(50%% off code: topstep) ProjectX API subscription isn't active. "
                                "(code %s)" % res.get("errorCode"))
        self.token = res["token"]
        try:
            ares = _ts_req("/api/Account/search", token=self.token, body={"onlyActiveAccounts": True})
        except Exception:
            ares = {}
        accts = ares.get("accounts") if isinstance(ares, dict) else ares
        if not accts:
            raise OrderRejected("Logged in, but no active Topstep accounts found.")
        hint = (account_hint or "").strip().lower()
        self.acct = next((a for a in accts if hint and hint in str(a.get("name", "")).lower()), accts[0])
        self.account_id = "TS:" + str(self.acct.get("name") or self.acct.get("id"))
        try:
            self.buying_power = float(self.acct.get("balance") or 0)
        except (TypeError, ValueError):
            self.buying_power = 0.0
        return self.state()

    # ---- Broker truth -----------------------------------------------------
    # The app's idea of your position is its own bookkeeping. Close a trade by
    # hand in TopstepX and this app never hears about it - it keeps managing a
    # trade that no longer exists, and a TP/SL firing then sends a CLOSE for
    # contracts you do not hold, which OPENS a position the other way.
    # ProjectX can just tell us, so ask it.
    RECONCILE_EVERY = 10.0            # seconds; the API is not free

    def broker_positions(self):
        """Open positions ACCORDING TO TOPSTEP. None means 'could not ask'.

        None and [] mean very different things here: [] is 'the broker says you
        are flat', None is 'the question failed'. Never treat a failed question
        as a flat account."""
        if not (self.token and self.acct):
            return None
        try:
            r = _ts_req("/api/Position/searchOpen", token=self.token,
                        body={"accountId": self.acct.get("id")})
        except Exception:
            return None
        if not isinstance(r, dict) or not r.get("success", True):
            return None
        pos = r.get("positions")
        return pos if isinstance(pos, list) else None

    def reconcile(self, force=False):
        """Drop our position if the broker says we are flat. Sends no orders."""
        now = time.time()
        if not force and now - getattr(self, "_last_reconcile", 0) < self.RECONCILE_EVERY:
            return None
        self._last_reconcile = now
        if not self.position:
            return None
        live = self.broker_positions()
        if live is None:
            return None                       # could not ask - change nothing
        if len(live) > 0:
            return None                       # broker agrees we hold something
        p = self.position
        self.position = None
        self.armed = None
        self.last_event = (
            "Topstep says you are FLAT, so the app cleared its %s %s x%s. "
            "You closed it outside the app. No order was sent."
            % (p.get("side"), p.get("symbol"), p.get("qty")))
        return {"cleared": True}

    def _contract_for(self, sym):
        if sym in self._contracts:
            return self._contracts[sym]
        cid = None
        try:
            res = _ts_req("/api/Contract/search", token=self.token,
                          body={"searchText": sym, "live": False})
            rows = res.get("contracts") or []
            for c in rows:
                cid_c = str(c.get("id", ""))
                if (".%s." % sym) in cid_c or str(c.get("name", "")).startswith(sym):
                    cid = cid_c
                    break
        except Exception:
            pass
        if not cid:  # fallback: build front-quarter id like CON.F.US.MNQ.U26
            t = _tv_front_symbol(sym)          # e.g. MNQU6
            code, yr = t[-2], "%02d" % (dt.date.today().year % 100)
            cid = "CON.F.US.%s.%s%s" % (sym, code, yr)
        self._contracts[sym] = cid
        return cid

    def _order(self, symbol, side_num, qty, limit=None):
        # ProjectX order types: 1 = Limit, 2 = Market
        body = {"accountId": self.acct.get("id"), "contractId": self._contract_for(symbol),
                "type": 2, "side": side_num, "size": int(qty)}
        if limit is not None:
            body["type"] = 1
            body["limitPrice"] = to_tick(symbol, limit)
        try:
            res = _ts_req("/api/Order/place", token=self.token, body=body)
        except urllib.error.HTTPError as e:
            raise OrderRejected("Topstep order rejected (%s)." % e.code)
        except Exception as e:
            raise OrderRejected("Topstep order failed: " + str(e)[:120])
        if not res.get("success", True):
            raise OrderRejected("Topstep: order refused (code %s) — check the account's rules/limits."
                                % res.get("errorCode"))
        return body["contractId"], res.get("orderId")

    def place(self, symbol, side, qty, limit=None):
        self._guard_open(qty)
        if symbol not in FUT:
            raise OrderRejected("unknown symbol")
        contract, _ = self._order(symbol, 0 if side == "LONG" else 1, qty, limit)  # 0=Buy 1=Sell
        px = float(limit) if limit else get_price(symbol)["price"]
        self.position = {"symbol": symbol, "side": side, "qty": int(qty), "entry": px,
                         "mark": get_price(symbol)["price"],
                         "opened_at": dt.datetime.now().strftime("%H:%M"), "contract": contract}
        self._update_trail()
        self.last_event = "Topstep: %s %d %s %s" % (
            "BUY" if side == "LONG" else "SELL", int(qty), contract,
            ("LIMIT %s" % _fmt_px(px)) if limit else "MARKET")
        return self.position

    # --- working limit for round-number entry ---
    def place_limit(self, symbol, side, qty, price):
        self._guard_open(qty)
        if symbol not in FUT:
            raise OrderRejected("unknown symbol")
        contract, oid = self._order(symbol, 0 if side == "LONG" else 1, qty, price)
        if not oid:
            raise OrderRejected("Topstep accepted the limit but returned no order id — "
                                "check the order in your Topstep platform before continuing.")
        self.last_event = "Topstep: working %s LIMIT %d %s @ %s" % (
            "BUY" if side == "LONG" else "SELL", int(qty), contract, _fmt_px(price))
        return str(oid)

    def cancel_limit(self, order_id):
        try:
            _ts_req("/api/Order/cancel", token=self.token,
                    body={"accountId": self.acct.get("id"), "orderId": int(order_id)})
        except Exception as e:
            raise OrderRejected("Topstep cancel failed: " + str(e)[:120])
        return True

    def refresh_mark(self):
        self._eval_strategies()
        p = self.position
        if not p:
            return None
        p["mark"] = get_price(p["symbol"])["price"]
        pv = FUT[p["symbol"]]["point_value"]
        p["pnl"] = round(self._points_pnl() * pv * p["qty"], 2)
        p["points"] = round(self._points_pnl(), 2)
        self._update_trail()
        # Ask the broker BEFORE any bracket can fire. If you closed by hand,
        # the position below is fiction and firing a stop against it would
        # open a brand new trade the other way.
        if self.reconcile() is not None:
            return None                     # we just cleared it; nothing to manage
        self._maybe_auto_close()
        return self.position

    def close(self):
        if not self.position:
            raise OrderRejected("no open position to close")
        p = self.position
        self._order(p["symbol"], 1 if p["side"] == "LONG" else 0, p["qty"])
        pv = FUT[p["symbol"]]["point_value"]
        pnl = round(self._points_pnl() * pv * p["qty"], 2)
        self.day_realized += pnl; self.buying_power += pnl
        self._log_trade(p, locals().get("px") or p.get("mark"), pnl,
                        getattr(self, "_exit_reason", "CLOSE"))
        self.blotter.append({"time": p["opened_at"],
                             "desc": "%s %s x%d (TS)" % (p["symbol"], p["side"], p["qty"]),
                             "move": "%.2f -> %.2f" % (p["entry"], p["mark"]), "pnl": pnl})
        self.position = None
        self.last_event = "Topstep: CLOSE " + p["symbol"]
        return {"closed": True, "pnl": pnl}


class WebullFuturesSession(BaseFuturesSession):
    """REAL Webull futures — places real futures orders through Webull's own
    OpenAPI against production (api.webull.com). REAL MONEY.

    v3.6 removed the PAPER/sandbox mode, so this is now live-only and the
    ALLOW_LIVE=1 gate (set only by the launcher) applies on every order with no
    path around it — exactly like the options live side.

    Order EXECUTION is real. Position and P&L shown here are the app's estimate
    from the live price feed — same honest caveat the NinjaTrader route carries —
    until fill read-back is wired; always confirm fills in Webull. Commission on
    close is the same $1.24 estimate the backtest uses."""

    # App root -> the product code Webull's instrument lookup wants.
    PRODUCT = {"MNQ": "MNQ", "MES": "MES"}

    def __init__(self, mode="LIVE"):
        super().__init__(mode)
        self.account_id = None
        self.trade = None
        self.data = None
        self._contract = {}     # "MNQ" -> resolved front-month symbol e.g. "MNQZ5"

    @property
    def _is_live(self):
        return True          # sandbox removed in v3.6 — this route is always real

    def _require_live_env(self):
        """Real-money safety gate — needs ALLOW_LIVE=1, set by the launcher.
        Since the sandbox is gone there is no mode that skips this."""
        if config.REQUIRE_LIVE_ENV_OK and os.environ.get("ALLOW_LIVE") != "1":
            raise OrderRejected("LIVE blocked: start the app with '🎯 START MARKET "
                                "SNIPER' (it sets ALLOW_LIVE=1) to arm real-money futures.")

    def _find(self, obj, *names):
        """Webull's field names drift, so look for any of them anywhere."""
        if isinstance(obj, dict):
            for n in names:
                if n in obj and obj[n] not in (None, ""):
                    return obj[n]
            for v in obj.values():
                r = self._find(v, *names)
                if r is not None:
                    return r
        elif isinstance(obj, list):
            for v in obj:
                r = self._find(v, *names)
                if r is not None:
                    return r
        return None

    def connect(self, app_key, app_secret, account=None, incoming_folder=None):
        try:
            from webull.core.client import ApiClient
            from webull.trade.trade_client import TradeClient
            from webull.data.data_client import DataClient
        except Exception as e:                              # noqa: BLE001
            raise OrderRejected("Webull SDK not installed — run INSTALL.bat, then "
                                "relaunch. (%s)" % str(e)[:120])
        self._require_live_env()                # real-money gate, no way around it
        if not app_key or not app_secret:
            raise OrderRejected("Needs your LIVE Webull api key + secret in the "
                                "boxes above.")
        api = ApiClient(app_key.strip(), app_secret.strip(), config.REGION)
        api.add_endpoint(config.REGION, config.LIVE_TRADE_ENDPOINT)   # production
        self._api = api
        self.trade = TradeClient(api)
        self.data = DataClient(api)
        res = self.trade.account_v2.get_account_list()
        if getattr(res, "status_code", None) != 200:
            raise OrderRejected(
                "LIVE connect failed: HTTP %s. Check the api key + secret are your "
                "production Webull keys. (Nothing was touched.)"
                % getattr(res, "status_code", "?"))
        data = res.json()
        accounts = (data if isinstance(data, list)
                    else (data.get("data") or data.get("accounts")
                          or data.get("account_list") or []))
        if isinstance(accounts, dict):
            accounts = [accounts]
        suffixes = [str(s).upper() for s in getattr(config, "FUTURES_ACCOUNT_SUFFIXES", [])]
        # Webull marks a futures book with account_class="FUTURES" (its
        # account_type is just "MARGIN"), so we must read account_class/label,
        # not account_type. Verified against the live account payload.
        acct = None
        first_id = None
        for a in accounts:
            aid = self._find(a, "account_id", "accountId", "secAccountId",
                             "sec_account_id", "id")
            if aid and first_id is None:
                first_id = aid
            marker = " ".join(str(self._find(a, f) or "") for f in (
                "account_class", "accountClass", "account_type", "accountType",
                "account_category", "account_label", "accountLabel")).upper()
            if aid and ("FUTURE" in marker
                        or any(str(aid).upper().endswith(sfx) for sfx in suffixes)):
                acct = aid
                break
        if acct is None:
            # No clearly-futures account. This route is real money only, so we
            # NEVER guess — refuse rather than risk an order on the wrong book.
            raise OrderRejected(
                "Connected but couldn't identify your FUTURES account among %d "
                "books. Add the last characters of your futures account id to "
                "FUTURES_ACCOUNT_SUFFIXES in config.py, then reconnect."
                % len(accounts))
        self.account_id = str(acct)
        self.buying_power = self._read_balance(self.account_id)
        self.last_event = ("Connected to Webull LIVE (REAL MONEY), account %s"
                           % self.account_id)
        return self.state()

    def _read_balance(self, aid):
        """Best-effort buying power. Verified against the live payload: it lives
        at account_currency_assets[].buying_power. Never fatal — 0.0 if unread."""
        try:
            res = self.trade.account_v2.get_account_balance(aid)
            if getattr(res, "status_code", None) != 200:
                return 0.0
            bp = self._find(res.json(), "buying_power", "buyingPower",
                            "net_liquidation_value", "total_net_liquidation_value",
                            "cash_balance", "total_cash_balance")
            return float(bp) if bp not in (None, "") else 0.0
        except Exception:                                   # noqa: BLE001
            return 0.0

    def _instrument_dicts(self, obj, out):
        """Collect every dict that looks like a futures contract (has a symbol
        and a contract_month/min_tick) from an arbitrarily-nested response."""
        if isinstance(obj, dict):
            if obj.get("symbol") and (obj.get("contract_month") or obj.get("min_tick")
                                      or obj.get("instrument_id")):
                out.append(obj)
            for v in obj.values():
                self._instrument_dicts(v, out)
        elif isinstance(obj, list):
            for v in obj:
                self._instrument_dicts(v, out)
        return out

    def _local_frontmonth(self, root):
        """Compute the front-month symbol locally as a fallback when the API
        lookup is unavailable. MNQ/MES are QUARTERLY (Mar/Jun/Sep/Dec ->
        H/M/U/Z), rolling a few days before the 3rd-Friday expiry. Verified:
        Aug 2026 -> MNQU6, matching Webull's own front month."""
        MCODE = {3: "H", 6: "M", 9: "U", 12: "Z"}

        def third_friday(y, m):
            d = dt.date(y, m, 1)
            return d + dt.timedelta(days=(4 - d.weekday()) % 7 + 14)

        today = dt.date.today()
        y, m = today.year, today.month
        for _ in range(12):
            if m in MCODE and third_friday(y, m) - dt.timedelta(days=5) >= today:
                return "%s%s%d" % (self.PRODUCT.get(root, root), MCODE[m], y % 10)
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return None

    def _resolve(self, root):
        """MNQ -> the current front-month contract symbol (e.g. MNQU6): the
        tradable contract with the nearest expiry. Uses Webull's instrument
        lookup, and falls back to a local calc if that host is unavailable."""
        if root in self._contract:
            return self._contract[root]
        code = self.PRODUCT.get(root, root)
        sym = None
        try:
            res = self.data.instrument.get_futures_instrument_by_code(
                code, "US_FUTURES", "MONTHLY")
            if getattr(res, "status_code", None) == 200:
                contracts = self._instrument_dicts(res.json(), [])
                tradable = [c for c in contracts
                            if str(c.get("status", "OC")).upper() in ("OC", "")] or contracts
                tradable.sort(key=lambda c: str(c.get("contract_month")
                                                or c.get("settlement_date") or "99999999"))
                if tradable:
                    sym = tradable[0].get("symbol")
        except Exception:                                   # noqa: BLE001
            sym = None
        if not sym:                       # instrument lookup unavailable
            sym = self._local_frontmonth(root)
        if not sym:
            raise OrderRejected("couldn't determine the front-month %s contract." % root)
        self._contract[root] = str(sym)
        return self._contract[root]

    def _order(self, contract, side, qty, order_type, limit=None):
        o = {"combo_type": "NORMAL", "client_order_id": uuid.uuid4().hex,
             "symbol": contract, "instrument_type": "FUTURES", "market": "US",
             "order_type": order_type, "quantity": str(int(qty)), "side": side,
             "time_in_force": "DAY", "entrust_type": "QTY"}
        if limit is not None:
            o["limit_price"] = str(limit)
        return o

    def place(self, symbol, side, qty, limit=None):
        self._guard_open(qty)
        if symbol not in FUT:
            raise OrderRejected("unknown symbol")
        contract = self._resolve(symbol)
        o = self._order(contract, "BUY" if side == "LONG" else "SELL", qty,
                        "LIMIT" if limit is not None else "MARKET", limit)
        res = self.trade.order_v3.place_order(self.account_id, [o])
        if getattr(res, "status_code", None) != 200:
            raise OrderRejected("Webull rejected the order: HTTP %s %s"
                                % (getattr(res, "status_code", "?"),
                                   str(getattr(res, "text", ""))[:150]))
        px = get_price(symbol)["price"]
        entry = float(limit) if limit is not None else px
        self.position = {"symbol": symbol, "side": side, "qty": int(qty),
                         "entry": round(entry, 2), "mark": px, "contract": contract,
                         "client_order_id": o["client_order_id"],
                         "opened_at": dt.datetime.now().strftime("%H:%M")}
        self._update_trail()
        self.last_event = "SENT to Webull %s: %s %d %s (%s)" % (
            "LIVE", side, int(qty), symbol, contract)
        return self.position

    def refresh_mark(self):
        p = self.position
        if not p:
            return None
        real = get_price(p["symbol"])
        if real["live"]:
            p["mark"] = real["price"]
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
        contract = p.get("contract") or self._resolve(p["symbol"])
        o = self._order(contract, "SELL" if p["side"] == "LONG" else "BUY",
                        p["qty"], "MARKET")
        res = self.trade.order_v3.place_order(self.account_id, [o])
        if getattr(res, "status_code", None) != 200:
            raise OrderRejected("Webull rejected the close: HTTP %s. If "
                                "it's still open, close it in Webull."
                                % getattr(res, "status_code", "?"))
        pv = FUT[p["symbol"]]["point_value"]
        gross = round(self._points_pnl() * pv * p["qty"], 2)
        fees = round(DEFAULT_COMMISSION * p["qty"], 2)   # estimate; confirm in Webull
        net = round(gross - fees, 2)
        self.day_realized += net
        self.day_fees = round(self.day_fees + fees, 2)
        self.blotter.append({"time": p["opened_at"],
                             "desc": f"{p['symbol']} {p['side']} x{p['qty']}",
                             "move": f"{p['entry']:.2f} -> {p['mark']:.2f}",
                             "gross": gross, "fees": fees, "pnl": net})
        self.position = None
        return {"closed": True, "pnl": net, "gross": gross, "fees": fees}


# v3.6 removed PAPER (Webull sandbox) and Tradovate. Three real routes remain.
# "LIVE" used to mean NinjaTrader while "WEBULL" meant Webull-live, which read
# backwards every time. NINJA is the name now; LIVE still maps to it so a saved
# pref from an older version logs in instead of erroring.
MODES = ("WEBULL", "NINJA", "TOPSTEP")
_MODE_ALIASES = {"LIVE": "NINJA"}        # pre-v3.6 saved prefs


def normalize_mode(mode):
    """Canonical mode name, or None if it is not one we still support."""
    m = str(mode or "").upper()
    m = _MODE_ALIASES.get(m, m)
    return m if m in MODES else None


def make_session(mode):
    m = normalize_mode(mode)
    if m == "WEBULL":
        return WebullFuturesSession("LIVE")     # Webull production, ALLOW_LIVE-gated
    if m == "TOPSTEP":
        return TopstepSession("TOPSTEP")
    if m == "NINJA":
        return NinjaTraderSession("NINJA")
    raise OrderRejected(
        "unknown mode %r — this build routes to WEBULL, NINJA or TOPSTEP. "
        "PAPER and Tradovate were removed in v3.6." % (mode,))
