"""MARKET SNIPER — DWELL TIME.

How long has it been since price actually traded through the whole dollar
above, and the whole dollar below?

Both stale means price is pinned between two levels and going nowhere. That is
the read this exists for: it is a different question from "is the tape busy",
which tape.py answers. Volume can be heavy while price sits in a 40-cent band
all afternoon — busy and stuck at the same time. Dwell time catches the stuck
part, velocity catches the busy part, and disagreement between them is
information rather than a bug.

WHOLE DOLLARS ONLY. The entry grid in webull_client uses X2.50 and X7.50 half
levels as well, deliberately — those are entry triggers. This is a different
measurement: the dollar is where price pauses and where the option strikes sit,
and adding halves would halve every dwell reading for no gain.

No network here. Bars come from tape.py, which is already fetching and caching
them once every 10 seconds, so this costs nothing extra.
"""

import math
import time

import tape

# Bars are 1-minute, so this is 3 hours. Long enough that a genuinely pinned
# market reports a big number rather than silently capping early.
MAX_LOOKBACK_BARS = 180

# Above this many minutes without a touch, a level counts as stale. 20 minutes
# is roughly two of G's option holds - if neither level has been touched in
# that time, nothing is going anywhere.
STALE_MINUTES = 20.0


def _touched(bar, level):
    """Did this bar trade through the level? Highs and lows, not closes.

    Closes would miss a level that price wicked through and rejected, which is
    exactly the touch that matters most - it is the one that proves the level
    is live rather than theoretical.
    """
    return bar["l"] <= level <= bar["h"]


def bracketing_levels(price):
    """The whole dollars immediately below and above price.

    Price sitting exactly ON a dollar is the awkward case. It is reported as
    the level below, so the pair is always (at-or-below, strictly-above) and
    the two are never the same number.
    """
    price = float(price)
    below = math.floor(price)
    above = below + 1.0
    return float(below), float(above)


def dwell(bars, price=None, now=None):
    """Minutes since price last traded the dollar below, and the dollar above.

    Returns None for a level that was never touched inside the lookback, which
    is different from 0 and must stay different: 0 means "touched this minute",
    None means "not in three hours".
    """
    if not bars:
        return {"ok": False, "reason": "no bars"}

    now = now or time.time()
    if price is None:
        price = bars[-1]["c"]
    below, above = bracketing_levels(price)
    window = bars[-MAX_LOOKBACK_BARS:]

    def minutes_since(level):
        # Walk backwards: the FIRST touch found is the most recent one.
        for b in reversed(window):
            if _touched(b, level):
                return max(0.0, round((now - b["t"]) / 60.0, 1))
        return None

    m_below = minutes_since(below)
    m_above = minutes_since(above)

    def is_stale(m):
        return m is None or m >= STALE_MINUTES

    both_stale = is_stale(m_below) and is_stale(m_above)

    # A band price cannot escape is only meaningful if we can also say how wide
    # it is. Half a dollar on QQQ is very different from half a dollar on TSLA.
    span = above - below
    pinned_pct = round(span / float(price) * 100.0, 3) if price else None

    return {
        "ok": True,
        "price": round(float(price), 2),
        "below": below,
        "above": above,
        "mins_below": m_below,
        "mins_above": m_above,
        "below_stale": is_stale(m_below),
        "above_stale": is_stale(m_above),
        "pinned": both_stale,
        "band_pct": pinned_pct,
        "lookback_bars": len(window),
        "stale_minutes": STALE_MINUTES,
    }


def label(d):
    """One line for the screen."""
    if not d or not d.get("ok"):
        return "—"
    # ">Nm" must quote the bars we ACTUALLY had, not the cap we would have
    # liked. Early in a session there are only a few, and claiming ">180m" off
    # 40 bars of data is a number the app cannot stand behind.
    horizon = d.get("lookback_bars") or MAX_LOOKBACK_BARS

    def fmt(m):
        if m is None:
            return ">%dm" % horizon
        if m < 1:
            return "now"
        return "%dm" % round(m)
    s = "%.0f %s  ·  %.0f %s" % (d["above"], fmt(d["mins_above"]),
                                 d["below"], fmt(d["mins_below"]))
    return ("PINNED  " + s) if d.get("pinned") else s


def for_symbol(symbol, price=None):
    """Dwell reading straight from tape.py's cached bars. Never raises."""
    try:
        bars = tape._bars(tape.YSYM.get(symbol, symbol)
                          if hasattr(tape, "YSYM") else symbol)
        return dwell(bars, price=price)
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "reason": str(e)[:120]}


def agreement(d, vel):
    """Do dwell time and tape velocity tell the same story?

    They measure different things and CAN legitimately differ - heavy volume
    inside a tight band is real. But "pinned" plus "violent" is a contradiction
    worth surfacing, because one of the two is then wrong and G should not act
    on either until he knows which.
    """
    if not (d and d.get("ok") and vel and vel.get("ok")):
        return {"ok": False}
    pinned = bool(d.get("pinned"))
    fast = vel.get("state") in ("fast", "violent")
    quiet = vel.get("state") == "calm"
    if pinned and fast:
        return {"ok": True, "agree": False,
                "note": "price is pinned between levels but the tape reads "
                        "%s — heavy trade inside a tight band, or one of the "
                        "two readings is wrong" % vel.get("state")}
    if pinned and quiet:
        return {"ok": True, "agree": True, "note": "pinned and quiet — nothing doing"}
    if not pinned and quiet:
        return {"ok": True, "agree": True,
                "note": "moving between levels, but the tape is thin"}
    return {"ok": True, "agree": True, "note": ""}
