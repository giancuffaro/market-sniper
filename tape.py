"""
TAPE SPEED / MARKET VELOCITY — shared by the options app (8000) and the
futures app (8010).

WHAT THIS IS
    A measure of how fast the market is moving RIGHT NOW compared to how fast
    it has been moving over the last half hour. It answers the question you
    actually ask when you watch the tape: "is this thing waking up?"

WHAT THIS IS NOT
    It is NOT trades-per-second. Real tape speed needs tick-by-tick prints,
    and this app's only data source is Yahoo's 1-minute bars — the same feed
    quotes.py and futures_client.py already use. You cannot recover individual
    prints from a 1-minute bar. So this measures BAR velocity: how much volume
    and range are being printed per minute versus the recent norm.

    In practice that tracks tape speed closely enough to be useful, because a
    tape that speeds up prints more volume and wider bars. But the number is
    honest about what it is, and the UI labels it VELOCITY, not TAPE SPEED.

    To upgrade to true tape speed later, replace _bars() with a tick feed and
    count prints per second. Nothing else in this module has to change.

HOW THE SCORE WORKS
    RECENT   = last 5 complete 1-minute bars
    BASELINE = the 30 bars before that (~half an hour)

    volume ratio = mean recent volume / mean baseline volume
    range  ratio = mean recent (high-low) / mean baseline (high-low)

    Both ratios sit at 1.0 when the market is behaving exactly like it has been.
    They blend 60/40 in favour of volume — volume leads range, a burst of
    contracts usually prints before the candle stretches.

    score = 50 x blended ratio, clamped 0-100.
    So 50 = normal, 100 = twice as fast as the last half hour, 0 = dead.

    Comparing against a TRAILING window (rather than a fixed number) means the
    score self-corrects for the natural U-shape of the session — the open is
    always busy, and this won't scream "violent" for the first ten minutes
    just because it's the open.
"""

import json
import time
import urllib.request

_UA = {"User-Agent": "Mozilla/5.0 (MARKET SNIPER)"}

RECENT_BARS = 5          # the "right now" window
BASELINE_BARS = 30       # what we compare it against
MIN_BARS = 12            # below this there isn't enough session to judge

VOL_WEIGHT = 0.60        # volume leads range, so it carries more of the score
RANGE_WEIGHT = 0.40

# score -> state. Tuned so "normal" is a wide band and you only see FAST or
# VIOLENT when something has genuinely changed.
CALM_BELOW = 30
FAST_ABOVE = 68
VIOLENT_ABOVE = 88

_CACHE = {}              # ysym -> {"data": {...}, "ts": epoch}
_TTL = 10.0              # seconds; the bars themselves only update once a minute


def _bars(ysym):
    """Last session of 1-minute OHLCV bars for a Yahoo symbol.

    Returns a list of dicts: {t, o, h, l, c, v}. Incomplete bars (Yahoo pads
    the tail of the array with nulls) are dropped, so the newest entry is
    always a bar that actually closed.
    """
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.request.quote(ysym)}?range=1d&interval=1m")
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=6) as r:
        res = json.load(r)["chart"]["result"][0]

    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    O, H, L, C, V = (q.get("open") or [], q.get("high") or [],
                     q.get("low") or [], q.get("close") or [], q.get("volume") or [])

    bars = []
    for i, t in enumerate(ts):
        if i >= len(C) or i >= len(H) or i >= len(L):
            continue
        if C[i] is None or H[i] is None or L[i] is None:
            continue          # unfilled bar — skip, don't fake it
        bars.append({
            "t": int(t),
            "o": float(O[i]) if i < len(O) and O[i] is not None else float(C[i]),
            "h": float(H[i]),
            "l": float(L[i]),
            "c": float(C[i]),
            "v": float(V[i]) if i < len(V) and V[i] is not None else 0.0,
        })

    # Yahoo publishes the CURRENT minute with a price but zero volume until it
    # closes. Left in, that half-formed bar sits at the end of the recent window
    # and drags every reading down — it made acceleration read -100% on every
    # symbol, every time. Trim trailing zero-volume bars so the newest bar we
    # measure is always a minute that actually finished trading.
    while bars and bars[-1]["v"] <= 0:
        bars.pop()
    return bars


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _state(score):
    if score >= VIOLENT_ABOVE:
        return "violent"
    if score >= FAST_ABOVE:
        return "fast"
    if score < CALM_BELOW:
        return "calm"
    return "normal"


def compute(bars):
    """Pure function: bars in, velocity reading out. No network, no clock.

    Kept separate from the fetch so it can be unit-tested with canned bars and
    reused as-is when a real tick feed replaces _bars().
    """
    if len(bars) < MIN_BARS:
        return {"ok": False, "reason": "not enough bars yet this session"}

    recent = bars[-RECENT_BARS:]
    baseline = bars[-(RECENT_BARS + BASELINE_BARS):-RECENT_BARS]
    if not baseline:
        return {"ok": False, "reason": "not enough bars yet this session"}

    vol_recent = _mean([b["v"] for b in recent])
    vol_base = _mean([b["v"] for b in baseline])
    rng_recent = _mean([b["h"] - b["l"] for b in recent])
    rng_base = _mean([b["h"] - b["l"] for b in baseline])

    # Nothing traded in either window — a closed or halted market. Report that
    # honestly as dead rather than as "normal", which a neutral 1.0 ratio would
    # otherwise produce and which would read as though the tape were fine.
    if vol_recent <= 0 and vol_base <= 0:
        return {"ok": True, "score": 0.0, "state": "calm", "direction": "flat",
                "vol_ratio": 0.0, "range_ratio": 0.0, "accel_pct": 0.0,
                "vol_per_min": 0, "range_per_min": 0.0,
                "bars_used": len(recent) + len(baseline),
                "last_bar_ts": recent[-1]["t"], "note": "no volume — market closed or halted"}

    # A zero baseline with live recent volume means the prior window was dead
    # (pre-market, holiday open). Treat the ratio as neutral rather than
    # dividing by zero and reporting infinity.
    vol_ratio = (vol_recent / vol_base) if vol_base > 0 else 1.0
    rng_ratio = (rng_recent / rng_base) if rng_base > 0 else 1.0

    blended = VOL_WEIGHT * vol_ratio + RANGE_WEIGHT * rng_ratio
    score = max(0.0, min(100.0, 50.0 * blended))

    # Acceleration: is the newest bar faster than the rest of the recent window?
    # Positive means still building, negative means the burst is fading.
    last_v = recent[-1]["v"]
    accel = ((last_v / vol_recent) - 1.0) * 100.0 if vol_recent > 0 else 0.0

    # Which way the recent window actually went.
    # "Flat" needs to win ties: an unchanged window is not a down window.
    drift = recent[-1]["c"] - recent[0]["o"]
    if drift == 0 or (rng_recent > 0 and abs(drift) < rng_recent * 0.5):
        direction = "flat"
    else:
        direction = "up" if drift > 0 else "down"

    return {
        "ok": True,
        "score": round(score, 1),
        "state": _state(score),
        "direction": direction,
        "vol_ratio": round(vol_ratio, 2),
        "range_ratio": round(rng_ratio, 2),
        "accel_pct": round(accel, 1),
        "vol_per_min": int(vol_recent),
        "range_per_min": round(rng_recent, 2),
        "bars_used": len(recent) + len(baseline),
        "last_bar_ts": recent[-1]["t"],
    }


def velocity(ysym):
    """Cached velocity reading for a Yahoo symbol. Never raises.

    On a fetch failure it serves the last good reading marked stale, so a
    momentary network blip can't blank the strip or crash a poll loop.
    """
    now = time.time()
    c = _CACHE.get(ysym)
    if c and now - c["ts"] < _TTL:
        return c["data"]

    try:
        out = compute(_bars(ysym))
        out["live"] = True
    except Exception as e:                                   # noqa: BLE001
        if c:
            out = dict(c["data"])
            out["live"] = False
            out["stale"] = True
        else:
            out = {"ok": False, "live": False,
                   "reason": "no data: %s" % str(e)[:80]}

    _CACHE[ysym] = {"data": out, "ts": now}
    return out


def label(reading):
    """One short human string for the UI chip."""
    if not reading or not reading.get("ok"):
        return "VELOCITY —"
    arrow = {"up": "↑", "down": "↓", "flat": "→"}.get(
        reading.get("direction"), "")
    return "%s %s %d" % (reading["state"].upper(), arrow, round(reading["score"]))
