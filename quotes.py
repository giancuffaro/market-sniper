"""
Live price feed for SPY / QQQ / TSLA.

Uses the public Yahoo Finance chart endpoint (keyless) via the standard
library only — no extra dependencies. Results are cached for a few seconds
so rapid polling doesn't hammer the endpoint. If a fetch fails (offline,
rate-limited, weekend), it falls back to the last known / seed value so the
app never breaks.

Note: TSLA replaced SPX here. In LIVE trading, wire Webull's own market-data
SDK for exchange-consistent option pricing.
"""

import json
import time
import urllib.request

# Webull symbol -> Yahoo symbol
YSYM = {"SPY": "SPY", "QQQ": "QQQ", "TSLA": "TSLA"}

# seed fallbacks (only used if the very first fetch fails)
_SEED = {"SPY": 626.40, "QQQ": 500.13, "TSLA": 330.00}

_CACHE = {}            # sym -> {"price":..,"prev":..,"ts":..}
_TTL = 5.0             # seconds
_UA = {"User-Agent": "Mozilla/5.0 (EZEXECUTION)"}


def _fetch(ysym):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.request.quote(ysym)}?range=1d&interval=1m")
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=6) as r:
        data = json.load(r)
    meta = data["chart"]["result"][0]["meta"]
    price = meta.get("regularMarketPrice")
    prev = meta.get("previousClose") or meta.get("chartPreviousClose") or price
    return float(price), float(prev)


def get_price(symbol):
    """Return dict: {price, prev, change, change_pct, live(bool)}."""
    now = time.time()
    c = _CACHE.get(symbol)
    if c and now - c["ts"] < _TTL:
        price, prev, live = c["price"], c["prev"], c["live"]
    else:
        try:
            price, prev = _fetch(YSYM[symbol])
            live = True
        except Exception:
            if c:
                price, prev, live = c["price"], c["prev"], False
            else:
                price, prev, live = _SEED[symbol], _SEED[symbol], False
        _CACHE[symbol] = {"price": price, "prev": prev, "ts": now, "live": live}
    change = round(price - prev, 2)
    pct = round((change / prev) * 100, 2) if prev else 0.0
    return {"price": round(price, 2), "prev": round(prev, 2),
            "change": change, "change_pct": pct, "live": live}


def get_all():
    return {s: get_price(s) for s in YSYM}


# ---- TREND ANALYZER ------------------------------------------------------
# The same read-out the futures app has, for SPY / QQQ / TSLA.
#
# For each timeframe it compares a fast 9-period average of price against a
# slower 21-period one. Fast above slow = buyers in control = UP. Fast below =
# DOWN. Too close to call = FLAT. Nothing here places a trade; it's a glance at
# whether the short timeframes agree with the long ones before you click.

_TREND_CACHE = {}     # sym -> {"data":..,"ts":..}
_TREND_TTL = 20.0


def _ema(vals, period):
    k = 2.0 / (period + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def _closes(ysym, interval, rng):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.request.quote(ysym)}?range={rng}&interval={interval}")
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=6) as r:
        res = json.load(r)["chart"]["result"][0]
    c = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    return [float(x) for x in c if x is not None]


def _trend_from_closes(closes, fast=9, slow=21):
    if len(closes) < slow + 1:
        return "—"
    f, s = _ema(closes, fast), _ema(closes, slow)
    thr = closes[-1] * 0.0005          # ~0.05% dead-band, so noise reads FLAT
    if f - s > thr:
        return "UP"
    if f - s < -thr:
        return "DOWN"
    return "FLAT"


# Four series are fetched; the rest are built by grouping those bars, so a full
# refresh is four requests instead of eleven.
_TREND_BASES = {
    "b1m":  ("1m", "1d"),
    "b5m":  ("5m", "5d"),
    "b60m": ("60m", "1mo"),
    "b1d":  ("1d", "1y"),
}
# (label, which series, how many bars to glue together). 10m = two 5-min bars.
_TREND_TFS = [
    ("1m", "b1m", 1),
    ("5m", "b5m", 1), ("10m", "b5m", 2), ("15m", "b5m", 3),
    ("20m", "b5m", 4), ("30m", "b5m", 6),
    ("1h", "b60m", 1), ("2h", "b60m", 2), ("4h", "b60m", 4),
    ("1d", "b1d", 1), ("1w", "b1d", 5),
]


def trend(symbol):
    """{'1m':'UP','5m':'DOWN',...}. A dash means that timeframe couldn't be
    read — the app carries on, it just shows nothing for that box."""
    now = time.time()
    c = _TREND_CACHE.get(symbol)
    if c and now - c["ts"] < _TREND_TTL:
        return c["data"]
    ysym = YSYM.get(symbol, symbol)
    bases = {}
    for key, (interval, rng) in _TREND_BASES.items():
        try:
            bases[key] = _closes(ysym, interval, rng)
        except Exception:
            bases[key] = []
    out = {}
    for tf, bkey, group in _TREND_TFS:
        closes = bases.get(bkey) or []
        if group > 1 and closes:
            closes = [closes[i] for i in range(len(closes) - 1, -1, -group)][::-1]
        out[tf] = _trend_from_closes(closes) if closes else "—"
    _TREND_CACHE[symbol] = {"data": out, "ts": now}
    return out


# ---- Opening Range (for ORB strategies) ---------------------------------
_ORB_CACHE = {}     # (sym,minutes) -> {"data":..,"ts":..}
_ORB_TTL = 20.0

def _fetch_bars(ysym):
    """Return (session_open_epoch, [(ts, high, low), ...]) of 1-min bars."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.request.quote(ysym)}?range=1d&interval=1m")
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=6) as r:
        data = json.load(r)
    res = data["chart"]["result"][0]
    meta = res["meta"]
    try:
        open_epoch = int(meta["currentTradingPeriod"]["regular"]["start"])
    except Exception:
        open_epoch = None
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    highs, lows = q.get("high") or [], q.get("low") or []
    bars = []
    for i, t in enumerate(ts):
        h = highs[i] if i < len(highs) else None
        lo = lows[i] if i < len(lows) else None
        if h is not None and lo is not None:
            bars.append((int(t), float(h), float(lo)))
    return open_epoch, bars


def opening_range(symbol, minutes=15):
    """High/low of the first `minutes` after the regular open.
    Returns {high, low, complete, open_epoch} or {} if unavailable.
    `complete` is True once the opening-range window has fully elapsed."""
    key = (symbol, minutes)
    now = time.time()
    c = _ORB_CACHE.get(key)
    if c and now - c["ts"] < _ORB_TTL:
        return c["data"]
    out = {}
    try:
        open_epoch, bars = _fetch_bars(YSYM[symbol])
        if open_epoch and bars:
            end = open_epoch + minutes * 60
            win = [(h, lo) for (t, h, lo) in bars if open_epoch <= t < end]
            if win:
                out = {"high": round(max(h for h, _ in win), 2),
                       "low": round(min(lo for _, lo in win), 2),
                       "complete": now >= end, "open_epoch": open_epoch}
    except Exception:
        out = {}
    _ORB_CACHE[key] = {"data": out, "ts": now}
    return out
