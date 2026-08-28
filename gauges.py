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

# Exchange clock. -4 is EDT and is only a FALLBACK: every Yahoo response
# carries meta.gmtoffset for the exchange, which already accounts for daylight
# saving. Hardcoding -4 works from March to November and is silently an hour
# out for the rest of the year - and an hour is a fifth of a trading session,
# which is enough to move a "how much of the day is done" figure by 15 points.
ET = dt.timezone(dt.timedelta(hours=-4))
_TZ_CACHE = {}


def _tz_for(ysym, meta=None):
    """Exchange timezone from the feed itself, cached. Falls back to EDT."""
    if meta and meta.get("gmtoffset") is not None:
        try:
            tz = dt.timezone(dt.timedelta(seconds=int(meta["gmtoffset"])))
            _TZ_CACHE[ysym] = tz
            return tz
        except Exception:
            pass
    return _TZ_CACHE.get(ysym, ET)
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


# Yahoo's INTRADAY volume is not trustworthy as a sum. Measured 2026-08-26
# 12:55 ET, QQQ's 5-minute series contained six bars at 26-47x the median bar
# (11:40 alone was 28,423,894 against a 606,308 median) and the day's bars
# summed to 277,170,149 - against a median QQQ session of 43,645,800. The feed
# intermittently publishes a cumulative figure in a single bar.
#
# Two consequences, handled separately:
#   - TODAY'S TOTAL comes from the DAILY bar instead of a sum of intraday bars.
#     It agreed with the artifact-cleaned sum to within 1% (19.45M vs 19.32M),
#     and it is the same series the history is ranked against, so it is a like
#     for like comparison rather than two different feeds.
#   - THE PROFILE only needs the SHAPE of a day, so its bars are cleaned by
#     clipping anything above OUTLIER_X times that session's median.
OUTLIER_X = 12.0


def _clean(vals):
    """Clip feed artifacts to OUTLIER_X x the median of the same session."""
    good = sorted(v for v in vals if v and v > 0)
    if not good:
        return list(vals)
    med = good[len(good) // 2]
    ceiling = med * OUTLIER_X
    return [min(v, ceiling) for v in vals]


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
    tz = _tz_for(ysym, res.get("meta"))
    today = dt.datetime.now(dt.timezone.utc).astimezone(tz).date()
    out = []
    for i, t in enumerate(ts):
        v = vols[i] if i < len(vols) else None
        if v is None or v <= 0:
            continue
        d = dt.datetime.fromtimestamp(t, dt.timezone.utc).astimezone(tz).date()
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
    tz = _tz_for(ysym, res.get("meta"))

    # Group into sessions, keyed by date, as {minute: volume}.
    days = {}
    for i, t in enumerate(ts):
        v = vols[i] if i < len(vols) else None
        if v is None or v <= 0:
            continue
        loc = dt.datetime.fromtimestamp(t, dt.timezone.utc).astimezone(tz)
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
        mins = sorted(day)
        cleaned = _clean([day[m] for m in mins])
        total = sum(cleaned)
        if total <= 0:
            continue
        run = 0.0
        for m, v in zip(mins, cleaned):
            run += v
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
        res = _chart(ysym, "1d", "1d")["chart"]["result"][0]
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "reason": str(e)[:140]}

    # One number, from the daily series - NOT a sum of intraday bars. See the
    # note by OUTLIER_X: summing them overstated QQQ by 14x this afternoon.
    meta = res.get("meta") or {}
    traded = float(meta.get("regularMarketVolume") or 0)
    if traded <= 0:
        vv = ((res.get("indicators", {}).get("quote") or [{}])[0].get("volume") or [])
        traded = float(vv[0]) if vv and vv[0] else 0.0

    # How far into the session we are, from the clock rather than from bars.
    tz = _tz_for(ysym, meta)
    nowet = dt.datetime.fromtimestamp(now_epoch or time.time(),
                                      dt.timezone.utc).astimezone(tz)
    last_minute = nowet.hour * 60 + nowet.minute
    if last_minute >= SESSION_CLOSE_MIN:
        last_minute = SESSION_CLOSE_MIN - 1
    if last_minute < SESSION_OPEN_MIN:
        last_minute = None

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


# =========================================================================
# VOLATILITY — TWO SEPARATE GAUGES
#
# Realized and implied answer different questions and are deliberately never
# blended into one number:
#
#   REALIZED  what the stock has ACTUALLY been doing. Free, broker-free, and
#             ranked as a percentile against two years of its own history, so
#             "high" means high for this symbol rather than high in the
#             abstract.
#   IMPLIED   what the option market is CHARGING for the future. It has to come
#             from a live chain - Yahoo's options endpoint returns 401 without
#             a crumb, so there is no free source - which means it is only
#             available once connected, and says so plainly when it is not.
#
# The gap between them is the number worth watching. Implied well above
# realized means you are paying up for movement that has not been happening.
# Every threshold below is a plain constant, meant to be argued with.
# =========================================================================

import math

RV_WINDOW_DAYS = 20          # trading days in the realized-vol window
RV_HISTORY_DAYS = 500        # sessions to rank that window against
TRADING_DAYS = 252

# Tweakable. These are percentile bands on the symbol's OWN history, not
# absolute vol numbers, so they travel between symbols without re-tuning.
RV_LOW_PCTL = 25.0
RV_HIGH_PCTL = 75.0

# Implied-vs-realized. Above RICH, options are expensive relative to how the
# stock has actually moved; below CHEAP, they are underpricing it.
IV_RICH_RATIO = 1.25
IV_CHEAP_RATIO = 0.90

_RV_CACHE = {}
_RV_TTL = 900.0


def _closes_daily(ysym, force=False):
    now = time.time()
    c = _RV_CACHE.get(ysym)
    if c and not force and now - c["ts"] < _RV_TTL:
        return c["closes"]
    res = _chart(ysym, "2y", "1d")["chart"]["result"][0]
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    closes = [float(x) for x in (q.get("close") or []) if x is not None and x > 0]
    _RV_CACHE[ysym] = {"closes": closes, "ts": now}
    return closes


def _annualised_vol(closes):
    """Close-to-close annualised volatility, in percent."""
    if len(closes) < 3:
        return None
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS) * 100.0


def realized(ysym, force=False):
    """Realized vol now, and where it sits in this symbol's own two years."""
    try:
        closes = _closes_daily(ysym, force=force)
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "reason": str(e)[:140]}
    if len(closes) < RV_WINDOW_DAYS + 30:
        return {"ok": False, "reason": "not enough history"}

    current = _annualised_vol(closes[-(RV_WINDOW_DAYS + 1):])
    if current is None:
        return {"ok": False, "reason": "could not compute"}

    # The same measure, rolled back through history, so today is compared
    # against the identical calculation rather than against a different one.
    hist = []
    start = max(RV_WINDOW_DAYS + 1, len(closes) - RV_HISTORY_DAYS)
    for end in range(start, len(closes)):
        v = _annualised_vol(closes[end - RV_WINDOW_DAYS - 1:end])
        if v is not None:
            hist.append(v)
    pctl = percentile_of(current, hist)
    state = ("low" if pctl is not None and pctl < RV_LOW_PCTL else
             "high" if pctl is not None and pctl > RV_HIGH_PCTL else "normal")
    return {"ok": True, "rv_pct": round(current, 2), "percentile": pctl,
            "state": state, "window_days": RV_WINDOW_DAYS, "samples": len(hist)}


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot, strike, t_years, vol, is_call, rate=0.0):
    """Black-Scholes, no dividends. Rate is 0 by default: over the hours a
    0DTE contract lives, carry is far smaller than the bid/ask spread."""
    if t_years <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        intrinsic = (spot - strike) if is_call else (strike - spot)
        return max(0.0, intrinsic)
    sd = vol * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * t_years) / sd
    d2 = d1 - sd
    disc = math.exp(-rate * t_years)
    if is_call:
        return spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
    return strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def implied_vol(price, spot, strike, t_years, is_call, rate=0.0):
    """Invert Black-Scholes by bisection.

    Bisection rather than Newton on purpose: vega collapses toward zero for a
    0DTE contract that is any distance from the money, and Newton divides by
    it. Bisection cannot diverge - it just narrows, or reports that it could
    not bracket the price, which is the honest answer for a contract whose
    quote is below intrinsic.
    """
    if price is None or price <= 0 or t_years <= 0:
        return None
    intrinsic = max(0.0, (spot - strike) if is_call else (strike - spot))
    if price < intrinsic - 0.01:
        return None                    # quote below intrinsic: not invertible
    lo, hi = 0.0001, 5.0               # 0.01% to 500% annualised
    if bs_price(spot, strike, t_years, hi, is_call, rate) < price:
        return None                    # even 500% vol cannot reach this price
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if bs_price(spot, strike, t_years, mid, is_call, rate) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0 * 100.0


def hours_to_expiry(now_epoch=None, close_hour=16, tz=None):
    """Hours until today's close. Never returns 0 - a zero would make every
    implied vol infinite; the last minutes are floored at one minute."""
    tz = tz or ET
    now = dt.datetime.fromtimestamp(now_epoch or time.time(),
                                    dt.timezone.utc).astimezone(tz)
    close = now.replace(hour=close_hour, minute=0, second=0, microsecond=0)
    secs = (close - now).total_seconds()
    return max(60.0, secs) / 3600.0


def vix(force=False):
    """^VIX — a free, broker-free implied-vol reading for the market.

    Found while looking for a breadth feed: every advance/decline ticker 404s
    on Yahoo, but ^VIX serves fine. It is NOT this symbol's implied vol - it is
    30-day S&P implied vol - so it is reported under its own name and never
    substituted for the per-contract IV that comes off the Webull chain.
    """
    now = time.time()
    c = _RV_CACHE.get("^VIX_now")
    if c and not force and now - c["ts"] < 60.0:
        return c["v"]
    try:
        meta = _chart("^VIX", "1d", "5m")["chart"]["result"][0]["meta"]
        v = {"ok": True, "level": round(float(meta.get("regularMarketPrice")), 2),
             "prev": round(float(meta.get("chartPreviousClose") or 0), 2)}
        v["change"] = round(v["level"] - v["prev"], 2) if v["prev"] else None
    except Exception as e:                                   # noqa: BLE001
        v = {"ok": False, "reason": str(e)[:120]}
    _RV_CACHE["^VIX_now"] = {"v": v, "ts": now}
    return v


def volatility(ysym, option=None, now_epoch=None, force=False):
    """Both gauges. `option` is an optional live ATM quote from the broker:
    {"spot":..,"strike":..,"price":..,"is_call":..} - without it, implied is
    reported as unavailable rather than guessed."""
    out = {"ok": True, "realized": realized(ysym, force=force), "implied": None,
           # Market-wide implied vol, always available. Separate from the
           # per-contract IV below because they are different measurements.
           "vix": vix(force=force)}
    if not option:
        out["implied"] = {"ok": False,
                          "reason": "needs a live option chain — connect to see it"}
        return out
    try:
        hrs = hours_to_expiry(now_epoch)
        t = hrs / 24.0 / 365.0
        iv = implied_vol(float(option["price"]), float(option["spot"]),
                         float(option["strike"]), t, bool(option.get("is_call", True)))
    except Exception as e:                                   # noqa: BLE001
        out["implied"] = {"ok": False, "reason": str(e)[:120]}
        return out
    if iv is None:
        out["implied"] = {"ok": False, "reason": "could not invert this quote"}
        return out
    rv = (out["realized"] or {}).get("rv_pct")
    ratio = round(iv / rv, 2) if rv else None
    state = "unknown"
    if ratio is not None:
        state = ("rich" if ratio >= IV_RICH_RATIO else
                 "cheap" if ratio <= IV_CHEAP_RATIO else "fair")
    out["implied"] = {"ok": True, "iv_pct": round(iv, 1), "hours_left": round(hrs, 2),
                      "vs_realized": ratio, "state": state}
    return out


def vol_label(v):
    if not v or not v.get("ok"):
        return "—"
    r = v.get("realized") or {}
    i = v.get("implied") or {}
    left = ("RV %.0f%% (%.0fth)" % (r["rv_pct"], r["percentile"])
            if r.get("ok") and r.get("percentile") is not None else "RV —")
    right = ("IV %.0f%% %s" % (i["iv_pct"], i["state"].upper())
             if i.get("ok") else "IV —")
    return left + "  ·  " + right
