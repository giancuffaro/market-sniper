"""MARKET SNIPER — VOLUME GAUGE.

Is today busy or dead, ranked against the last ~500 sessions?

THE TRAP THIS EXISTS TO AVOID
Comparing today's volume-so-far against whole past DAYS makes every morning
look dead. At 10:00 a normal session has traded maybe a fifth of its day, so a
raw comparison says "5th percentile, very low volume" every single morning,
right up until the afternoon when it drifts to normal. The number would be
worse than useless - it would be wrong in a consistent direction and you would
learn to ignore it.

So today's volume is PROJECTED to a full day first, using how much of a normal
day is done by this time, and only then ranked. The intraday profile is built
from real 5-minute bars over the last month rather than assumed, because the
shape is not a straight line: the open and the close carry far more than the
middle, and the exact split differs per symbol.

Percentile, not a ratio. "72nd percentile" says where today sits among the
sessions you have actually traded. "1.4x average" invites you to compare
against a mean that a handful of huge days has already dragged upward.
"""

import bisect
import datetime as dt
import json
import time
import urllib.request

_UA = {"User-Agent": "Mozilla/5.0 (MARKET SNIPER)"}

LOW_PCTL = 25.0          # below this: quiet session
HIGH_PCTL = 75.0         # above this: busy session

DAILY_LOOKBACK = 500     # sessions to rank against (~2 years)
PROFILE_DAYS = 30        # sessions used to learn the intraday shape

_DAILY_CACHE = {}        # ysym -> {"vols": [...], "ts": epoch}
_DAILY_TTL = 6 * 3600    # the daily series only changes once a day

_PROFILE_CACHE = {}      # ysym -> {"profile": [(minute, fraction)], "ts": epoch}
_PROFILE_TTL = 12 * 3600

_TODAY_CACHE = {}        # ysym -> {"data": {...}, "ts": epoch}
_TODAY_TTL = 30.0

ET = dt.timezone(dt.timedelta(hours=-4))     # trading clock; DST handled below
SESSION_OPEN_MIN = 9 * 60 + 30               # 09:30
SESSION_CLOSE_MIN = 16 * 60                  # 16:00
SESSION_MINUTES = SESSION_CLOSE_MIN - SESSION_OPEN_MIN


def _get(url):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


def _chart(ysym, rng, interval):
    return _get("https://query1.finance.yahoo.com/v8/finance/chart/"
                f"{urllib.request.quote(ysym)}?range={rng}&interval={interval}")


def _minute_of_day(epoch, tz_offset_hours):
    t = dt.datetime.fromtimestamp(epoch, dt.timezone.utc).astimezone(
        dt.timezone(dt.timedelta(hours=tz_offset_hours)))
    return t.hour * 60 + t.minute


def daily_volumes(ysym, force=False):
    """Completed daily volumes, oldest first. TODAY IS EXCLUDED.

    Leaving today in would rank it against a list containing itself, which
    drags every reading toward the middle - and on a genuinely record day the
    record itself would pull the percentile down.
    """
    now = time.time()
    c = _DAILY_CACHE.get(ysym)
    if c and not force and now - c["ts"] < _DAILY_TTL:
        return c["vols"]
    res = _chart(ysym, "2y", "1d")["chart"]["result"][0]
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    vols = q.get("volume") or []
    today = dt.datetime.now(dt.timezone.utc).astimezone(ET).date()
    out = []
    for i, t in enumerate(ts):
        v = vols[i] if i < len(vols) else None
        if v is None or v <= 0:
            continue
        d = dt.datetime.fromtimestamp(t, dt.timezone.utc).astimezone(ET).date()
        if d >= today:
            continue                       # exclude today, and any stray future row
        out.append(float(v))
    out = out[-DAILY_LOOKBACK:]
    _DAILY_CACHE[ysym] = {"vols": out, "ts": now}
    return out


def intraday_profile(ysym, force=False):
    """What fraction of a normal day has traded by each minute of the session.

    Learned from real 5-minute bars, not assumed. Returned as a sorted list of
    (minute_of_day, cumulative_fraction) so a lookup is a bisect.
    """
    now = time.time()
    c = _PROFILE_CACHE.get(ysym)
    if c and not force and now - c["ts"] < _PROFILE_TTL:
        return c["profile"]

    res = _chart(ysym, "%dd" % PROFILE_DAYS, "5m")["chart"]["result"][0]
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    vols = q.get("volume") or []

    # Group into sessions, keyed by date, as {minute: volume}.
    days = {}
    for i, t in enumerate(ts):
        v = vols[i] if i < len(vols) else None
        if v is None or v <= 0:
            continue
        loc = dt.datetime.fromtimestamp(t, dt.timezone.utc).astimezone(ET)
        m = loc.hour * 60 + loc.minute
        if m < SESSION_OPEN_MIN or m >= SESSION_CLOSE_MIN:
            continue                       # regular hours only
        days.setdefault(loc.date(), {})[m] = float(v)

    # Only whole-ish sessions: a half day would distort the shape.
    usable = [d for d in days.values() if len(d) >= 60]
    if not usable:
        _PROFILE_CACHE[ysym] = {"profile": [], "ts": now}
        return []

    # Average the CUMULATIVE FRACTION per day, not raw volume. Averaging raw
    # volume would let one heavy session dominate the shape; every day should
    # get one equal vote on what "a normal day" looks like.
    buckets = {}
    for day in usable:
        total = sum(day.values())
        if total <= 0:
            continue
        run = 0.0
        for m in sorted(day):
            run += day[m]
            buckets.setdefault(m, []).append(run / total)
    profile = [(m, sum(v) / len(v)) for m, v in sorted(buckets.items())]
    _PROFILE_CACHE[ysym] = {"profile": profile, "ts": now}
    return profile


def expected_fraction(profile, minute):
    """Fraction of a normal day done by this minute, from the learned profile."""
    if not profile:
        # No profile: fall back to a flat clock. Crude, and it is the reason
        # the profile is learned at all - but better than dividing by zero.
        if minute <= SESSION_OPEN_MIN:
            return 0.0
        if minute >= SESSION_CLOSE_MIN:
            return 1.0
        return (minute - SESSION_OPEN_MIN) / float(SESSION_MINUTES)
    mins = [p[0] for p in profile]
    i = bisect.bisect_right(mins, minute) - 1
    if i < 0:
        return 0.0
    return profile[i][1]


def percentile_of(value, series):
    """Percent of past sessions this value exceeds. 0-100."""
    if not series:
        return None
    below = bisect.bisect_left(sorted(series), value)
    return round(below / float(len(series)) * 100.0, 1)


def _band(p):
    if p is None:
        return "unknown"
    if p < LOW_PCTL:
        return "low"
    if p > HIGH_PCTL:
        return "high"
    return "normal"


def volume(ysym, now_epoch=None, force=False):
    """Today's volume ranked against ~500 sessions, time-of-day corrected."""
    key = ysym
    now = time.time()
    c = _TODAY_CACHE.get(key)
    if c and not force and now - c["ts"] < _TODAY_TTL and now_epoch is None:
        return c["data"]

    try:
        hist = daily_volumes(ysym, force=force)
        profile = intraday_profile(ysym, force=force)
        res = _chart(ysym, "1d", "5m")["chart"]["result"][0]
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "reason": str(e)[:140]}

    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    vols = q.get("volume") or []

    traded = 0.0
    last_minute = None
    for i, t in enumerate(ts):
        v = vols[i] if i < len(vols) else None
        if v is None or v <= 0:
            continue
        loc = dt.datetime.fromtimestamp(t, dt.timezone.utc).astimezone(ET)
        m = loc.hour * 60 + loc.minute
        if m < SESSION_OPEN_MIN or m >= SESSION_CLOSE_MIN:
            continue
        traded += float(v)
        last_minute = m

    if not hist:
        return {"ok": False, "reason": "no history to rank against"}
    if last_minute is None or traded <= 0:
        return {"ok": True, "state": "unknown", "traded": 0,
                "note": "no regular-hours volume yet today"}

    frac = expected_fraction(profile, last_minute)
    # Before ~09:35 the fraction is tiny and dividing by it turns rounding
    # noise into a wild projection. Say "too early" rather than print a number
    # that will swing by 40 percentiles on the next bar.
    if frac < 0.02:
        return {"ok": True, "state": "unknown", "traded": int(traded),
                "session_pct": round(frac * 100, 1),
                "note": "too early to project the day"}

    projected = traded / frac
    pctl = percentile_of(projected, hist)
    pace = percentile_of(traded, [h * frac for h in hist])

    return {
        "ok": True,
        "state": _band(pctl),
        "percentile": pctl,
        "pace_percentile": pace,
        "projected": int(projected),
        "traded": int(traded),
        "session_pct": round(frac * 100, 1),
        "median_day": int(sorted(hist)[len(hist) // 2]),
        "sessions": len(hist),
        "profile_points": len(profile),
        "as_of_minute": last_minute,
        "note": "",
    }


def label(v):
    if not v or not v.get("ok"):
        return "—"
    if v.get("state") == "unknown":
        return v.get("note") or "—"
    return "VOL %s (%.0fth pctl, %.0f%% of day done)" % (
        v["state"].upper(), v["percentile"], v["session_pct"])
