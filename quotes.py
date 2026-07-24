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
