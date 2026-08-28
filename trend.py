"""MARKET SNIPER — TREND MODULE.

up / down / chop, per symbol, from three independent signals that must agree.

WHY THIS EXISTS ALONGSIDE THE OLD PANEL
The multi-timeframe strip in quotes.py shows eleven boxes, and every one of them
is the same signal: is EMA9 above EMA21 at the last bar. That is a LEVEL
comparison, so a market that has already rolled over still reads UP for as long
as the fast line sits above the slow one. The eleven columns are also four
fetches regrouped, so they are not independent opinions - they mostly restate
each other. Measured live on 2026-08-26 at 12:42 it returned 6 UP, 3 DOWN and
2 FLAT on the same symbol, with 20m FLAT wedged between 15m DOWN and 30m UP.
Eleven answers and no answer.

So this asks three DIFFERENT questions and reports whether they agree:

  SLOPE      is the 21-EMA actually rising, and is price on the right side of
             it? Slope, not level - that is the part the old panel missed.
  STRUCTURE  higher highs AND higher lows over the last 15-20 bars, which is
             what a trend is rather than what an average says about one.
  VOLUME     is the volume arriving on up-bars or on down-bars? Direction with
             no volume behind it is drift.

Agreement is the output. Three votes one way is a trend worth trading with;
a split is chop, and chop is the reading that keeps you out of the trades this
is meant to keep you out of.

THRESHOLDS ARE SCALED, NOT FIXED. The old panel used a flat 0.05% dead-band on
the 1-minute and the weekly alike - over-sensitive at the top, never triggered
at the bottom. Here the slope is measured in units of the symbol's own recent
bar range, so one threshold means the same thing on QQQ, on TSLA and on any
timeframe.
"""

import json
import time
import urllib.request

_UA = {"User-Agent": "Mozilla/5.0 (MARKET SNIPER)"}

# QQQ leads, and the Mag Seven are what moves it. Divergence inside the basket
# is itself a signal: QQQ up while five of seven are down is a thin rally.
BASKET = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"]

EMA_PERIOD = 21
STRUCTURE_BARS = 20          # window for higher-highs / higher-lows
SWING = 3                    # bars either side that define a pivot
VOLUME_BARS = 20

# Slope, in units of the average bar range per bar. 0.05 means the EMA must
# climb at least 5% of a typical bar's range each bar to count as rising -
# small enough to catch a real drift, large enough to ignore noise.
SLOPE_MIN = 0.05
SLOPE_BARS = 5               # measure the slope over this many bars

# Up-volume must be this share of the total to confirm. 0.55 rather than 0.5:
# an even split is not confirmation of anything.
VOL_CONFIRM = 0.55

TF = {"1m": ("1d", "1m"), "5m": ("5d", "5m"), "15m": ("1mo", "15m")}

_CACHE = {}
_TTL = 15.0                  # G asked for a 15-second poll; this matches it


def _bars(ysym, tf="1m"):
    rng, interval = TF.get(tf, TF["1m"])
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.request.quote(ysym)}?range={rng}&interval={interval}")
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=8) as r:
        res = json.load(r)["chart"]["result"][0]
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    O, H, L, C, V = (q.get("open") or [], q.get("high") or [], q.get("low") or [],
                     q.get("close") or [], q.get("volume") or [])
    out = []
    for i, t in enumerate(ts):
        if i >= len(C) or C[i] is None or H[i] is None or L[i] is None:
            continue
        out.append({"t": int(t),
                    "o": float(O[i]) if i < len(O) and O[i] is not None else float(C[i]),
                    "h": float(H[i]), "l": float(L[i]), "c": float(C[i]),
                    "v": float(V[i]) if i < len(V) and V[i] is not None else 0.0})
    # The forming bar has a price but no volume yet; it drags every reading.
    while out and out[-1]["v"] <= 0:
        out.pop()
    return out


def _ema_series(vals, period):
    """The WHOLE EMA series, not just its last value.

    The old panel computed one number, which is why it could only ever compare
    levels. Slope needs the line, so the line is what gets returned.
    """
    if not vals:
        return []
    k = 2.0 / (period + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _avg_range(bars, n=20):
    w = bars[-n:] if len(bars) > n else bars
    if not w:
        return 0.0
    return sum(b["h"] - b["l"] for b in w) / len(w)


def slope_signal(bars):
    """Is the 21-EMA rising or falling, and is price on the right side of it?

    Two conditions, both required. A rising EMA with price underneath it is a
    trend already losing its grip, and taking either one alone is how you end
    up long into a rollover.
    """
    if len(bars) < EMA_PERIOD + SLOPE_BARS + 1:
        return {"vote": 0, "reason": "not enough bars"}
    closes = [b["c"] for b in bars]
    ema = _ema_series(closes, EMA_PERIOD)
    rng = _avg_range(bars)
    if rng <= 0:
        return {"vote": 0, "reason": "no range"}
    # Per-bar rise, expressed in typical bar ranges. Scaling by the symbol's own
    # range is what lets ONE threshold work on QQQ, TSLA and every timeframe.
    per_bar = (ema[-1] - ema[-1 - SLOPE_BARS]) / SLOPE_BARS
    norm = per_bar / rng
    above = closes[-1] > ema[-1]
    vote = 0
    if norm >= SLOPE_MIN and above:
        vote = 1
    elif norm <= -SLOPE_MIN and not above:
        vote = -1
    return {"vote": vote, "slope": round(norm, 3),
            "price_vs_ema": "above" if above else "below",
            "ema": round(ema[-1], 2)}


def _pivots(bars):
    """Swing highs and lows: a bar higher (or lower) than SWING bars each side."""
    highs, lows = [], []
    for i in range(SWING, len(bars) - SWING):
        h = bars[i]["h"]
        l = bars[i]["l"]
        if all(h >= bars[j]["h"] for j in range(i - SWING, i + SWING + 1)):
            highs.append((i, h))
        if all(l <= bars[j]["l"] for j in range(i - SWING, i + SWING + 1)):
            lows.append((i, l))
    return highs, lows


def structure_signal(bars):
    """Higher highs AND higher lows, or lower highs AND lower lows.

    Both halves are required. Higher highs with lower lows is a widening range,
    not an uptrend, and it is exactly the shape that traps a breakout buyer.
    """
    w = bars[-STRUCTURE_BARS:] if len(bars) > STRUCTURE_BARS else bars
    if len(w) < SWING * 2 + 3:
        return {"vote": 0, "reason": "not enough bars"}
    highs, lows = _pivots(w)
    if len(highs) < 2 or len(lows) < 2:
        return {"vote": 0, "reason": "no clear swings"}
    hh = highs[-1][1] > highs[-2][1]
    hl = lows[-1][1] > lows[-2][1]
    lh = highs[-1][1] < highs[-2][1]
    ll = lows[-1][1] < lows[-2][1]
    vote = 1 if (hh and hl) else (-1 if (lh and ll) else 0)
    return {"vote": vote, "higher_high": hh, "higher_low": hl,
            "swings": [len(highs), len(lows)]}


def volume_signal(bars):
    """Is volume arriving on up-bars or down-bars?

    Volume is capped at 12x the window median first. Yahoo's feed intermittently
    publishes a cumulative figure in a single bar - measured at 300x its
    neighbours on QQQ - and one such bar would otherwise decide this vote on
    its own, whichever way that bar happened to close.
    """
    w = bars[-VOLUME_BARS:] if len(bars) > VOLUME_BARS else bars
    if len(w) < 5:
        return {"vote": 0, "reason": "not enough bars"}
    vols = sorted(b["v"] for b in w if b["v"] > 0)
    if not vols:
        return {"vote": 0, "reason": "no volume"}
    ceiling = vols[len(vols) // 2] * 12.0
    up = sum(min(b["v"], ceiling) for b in w if b["c"] > b["o"])
    down = sum(min(b["v"], ceiling) for b in w if b["c"] < b["o"])
    total = up + down
    if total <= 0:
        return {"vote": 0, "reason": "no directional volume"}
    share = up / total
    vote = 1 if share >= VOL_CONFIRM else (-1 if (1 - share) >= VOL_CONFIRM else 0)
    return {"vote": vote, "up_share": round(share * 100, 1)}


def direction(bars):
    """Combine the three votes into up / down / chop."""
    s, st, v = slope_signal(bars), structure_signal(bars), volume_signal(bars)
    votes = [s["vote"], st["vote"], v["vote"]]
    score = sum(votes)
    agree = sum(1 for x in votes if x and x == (1 if score > 0 else -1))
    # Two of three, and nothing pulling the other way. A 2-1 split is a market
    # arguing with itself, which is chop by any useful definition.
    if score >= 2 and -1 not in votes:
        state = "up"
    elif score <= -2 and 1 not in votes:
        state = "down"
    else:
        state = "chop"
    return {"ok": True, "state": state, "score": score, "agree": agree,
            "slope": s, "structure": st, "volume": v,
            "bars": len(bars)}


def for_symbol(ysym, tf="1m", force=False):
    key = (ysym, tf)
    now = time.time()
    c = _CACHE.get(key)
    if c and not force and now - c["ts"] < _TTL:
        return c["data"]
    try:
        d = direction(_bars(ysym, tf))
    except Exception as e:                                   # noqa: BLE001
        d = {"ok": False, "reason": str(e)[:120]}
    _CACHE[key] = {"data": d, "ts": now}
    return d


def basket(lead="QQQ", tf="1m", names=None):
    """The lead symbol plus the Mag Seven, and how much of the basket agrees.

    Divergence is the point: QQQ green while five of seven are red is a rally
    carried by one or two names, which is a different trade from a broad one.
    """
    names = names or BASKET
    lead_d = for_symbol(lead, tf)
    members = {}
    for n in names:
        members[n] = for_symbol(n, tf)
    ups = sum(1 for d in members.values() if d.get("state") == "up")
    downs = sum(1 for d in members.values() if d.get("state") == "down")
    chops = len(members) - ups - downs
    lead_state = lead_d.get("state")
    confirmed = None
    if lead_state == "up":
        confirmed = ups > downs
    elif lead_state == "down":
        confirmed = downs > ups
    return {"ok": True, "tf": tf, "lead": lead, "lead_trend": lead_d,
            "members": members, "up": ups, "down": downs, "chop": chops,
            "breadth": round((ups - downs) / float(len(members)), 2),
            "confirmed": confirmed}


def label(d):
    if not d or not d.get("ok"):
        return "—"
    return "%s (%d/3)" % (d["state"].upper(), d.get("agree", 0))


# =========================================================================
# MARKET BREADTH — AN APPROXIMATION, AND LABELLED AS ONE
#
# True advancers-minus-decliners is an exchange-published figure. It is NOT
# available here: Yahoo returns 404 for ^ADD, ^ADVN, ^DECN, ^TICK and ^TRIN,
# and the Webull OpenAPI SDK is a TRADING api - it exposes accounts, orders and
# instrument snapshots, with no market-statistics endpoint. Checked, not
# assumed.
#
# So breadth is computed from the eleven S&P sector ETFs. That is a proxy for
# "how much of the market is participating", not a share count, and it is
# reported under a name that says so. Eleven requests, ~2 seconds, cached.
#
# Why sectors rather than a basket of big names: the Mag Seven ARE the index,
# so counting them tells you almost the same thing twice. Sectors weight the
# parts of the market those names are not in, which is the whole question
# breadth is asked to answer.
# =========================================================================

SECTORS = ["XLK", "XLF", "XLV", "XLE", "XLY", "XLP",
           "XLI", "XLB", "XLU", "XLRE", "XLC"]

_BREADTH_CACHE = {"data": None, "ts": 0.0}
_BREADTH_TTL = 60.0


def _day_change(ysym):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.request.quote(ysym)}?range=1d&interval=1d")
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=8) as r:
        meta = json.load(r)["chart"]["result"][0]["meta"]
    px = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not px or not prev:
        return None
    return (px / prev - 1.0) * 100.0


def market_breadth(force=False):
    """Sector participation, standing in for advancers minus decliners."""
    now = time.time()
    if _BREADTH_CACHE["data"] and not force and now - _BREADTH_CACHE["ts"] < _BREADTH_TTL:
        return _BREADTH_CACHE["data"]
    moves = {}
    for t in SECTORS:
        try:
            c = _day_change(t)
            if c is not None:
                moves[t] = round(c, 2)
        except Exception:
            continue
    if not moves:
        out = {"ok": False, "reason": "no sector data"}
        _BREADTH_CACHE.update(data=out, ts=now)
        return out
    up = sum(1 for v in moves.values() if v > 0)
    down = sum(1 for v in moves.values() if v < 0)
    n = len(moves)
    ratio = (up - down) / float(n)
    state = "broad-up" if ratio >= 0.45 else ("broad-down" if ratio <= -0.45 else "mixed")
    out = {"ok": True, "state": state, "advancing": up, "declining": down,
           "flat": n - up - down, "sectors": n, "net": up - down,
           "ratio": round(ratio, 2), "moves": moves,
           "basis": "11 S&P sector ETFs — a participation proxy, NOT exchange "
                    "advance/decline. No free ADD feed exists; ^ADD, ^ADVN and "
                    "^TICK all 404 and the Webull SDK has no market-stats call."}
    _BREADTH_CACHE.update(data=out, ts=now)
    return out


def breadth_label(b):
    if not b or not b.get("ok"):
        return "—"
    return "BREADTH %d up / %d down (%s)" % (b["advancing"], b["declining"], b["state"])
