"""Tiered ratchet — his ask, 9/2/26.

WHY TIERS EXIST
---------------
A percentage is not the same amount of noise at every premium. The bid on a
$0.40 contract moves in $0.01 ticks, so ONE tick is 2.5% — a "breakeven" stop
on a cheap lotto sits inside the spread and gets scratched by a quote flicker,
not by the market. That same 5% on a $4.00 contract is $0.20, several ticks
away, and perfectly safe to use.

So the rule stops being one number and becomes three, picked off what he
actually PAID for the contract:

    UNDER $1.00   arms +25%, first lock +10%, then +15% a rung   (loose)
    $1.00-$1.99   arms +15%, first lock  0% (BE), then +10%      (middle)
    $2.00 AND UP  arms +10%, first lock  +5%, then  +5%          (tight)

Cheap contracts lock LATER but lock MORE per rung. Expensive contracts lock
early and often, because their noise floor is small enough to allow it.

TWO SAFETY FLOORS
-----------------
1. TICK FLOOR (widens the rung) — a rung must be worth at least 4 ticks of the
   contract's own tick size ($0.01 under $3.00, $0.05 at or above). A 5% rung
   on a $3.00 contract is $0.15 = 3 ticks, too tight, so the step is widened
   until it clears 4 ticks.
2. SPREAD FLOOR (lowers the stop) — the new stop must sit at least one full
   bid/ask spread under the live bid. If the spread is $0.20 and the ratchet
   wants the stop $0.08 under the bid, that stop lives INSIDE the noise and
   gets hit by the quote, not by the trade.

Neither floor ever moves a stop DOWN from where it already sits. They only
refuse to raise it somewhere unsafe; the trade then keeps its old stop until
it earns the room for a better one.
"""

# premium ceiling -> (arm_pct, first_lock_pct, step_pct)
TIERS = (
    (1.00, (25.0, 10.0, 15.0)),      # premium  <  $1.00
    (2.00, (15.0,  0.0, 10.0)),      # $1.00 <= premium < $2.00
    (None, (10.0,  5.0,  5.0)),      # premium >= $2.00
)

MIN_RUNG_TICKS = 4.0                 # floor 1
MIN_STOP_TICKS = 2.0                 # floor 2 hard minimum


def tick_size(price):
    """US option tick: a penny under $3.00, a nickel at $3.00 and above."""
    try:
        return 0.05 if float(price) >= 3.0 else 0.01
    except (TypeError, ValueError):
        return 0.01


def ratchet_plan(fill_price):
    """(arm_pct, first_lock_pct, step_pct) for what he paid, tick floor applied.

    fill_price is the REAL fill, not the caller's posted price — the same
    number every other stop in this program is measured from.
    """
    try:
        f = float(fill_price or 0)
    except (TypeError, ValueError):
        f = 0.0
    plan = TIERS[-1][1]
    for ceiling, p in TIERS:
        if ceiling is None or f < ceiling:
            plan = p
            break
    arm, first, step = plan
    if f > 0:
        # FLOOR 1: widen the rung until it is worth at least 4 ticks.
        min_step = (tick_size(f) * MIN_RUNG_TICKS) / f * 100.0
        if step < min_step:
            step = round(min_step, 2)
    return arm, first, step


def ratchet_locked_pct(gain_pct, fill_price):
    """How much profit the stop should be locking right now, as a percent above
    the fill — or None while the trade hasn't reached its first rung.

    Rung 0 is first_lock. Every further step_pct of gain adds another step_pct
    of locked profit. No ceiling: a runner keeps climbing forever.

        $0.50 fill : +25% -> lock +10 | +40% -> +25 | +55% -> +40 ...
        $1.50 fill : +15% -> lock  BE | +25% -> +10 | +35% -> +20 ...
        $3.00 fill : +10% -> lock  +7 | +17% -> +13 | +23% -> +20 ...
                     (tick floor widened 5% to ~6.7% on a nickel-tick name)
    """
    if gain_pct is None:
        return None
    arm, first, step = ratchet_plan(fill_price)
    if step <= 0:
        return None
    if float(gain_pct) < arm - 1e-9:
        return None
    k = int((float(gain_pct) - arm + 1e-9) // step)
    return first + step * k


def ratchet_stop_price(fill_price, locked_pct, bid=None, ask=None,
                       current_stop=None, direction=1):
    """The dollar price the resting stop should move to, spread floor applied.

    Returns None when the move isn't safe or isn't an improvement — the caller
    then leaves the existing stop alone and spends no API call on it.

    direction is +1 for a long (the normal case: every options BUY) and -1 for
    a short. A short's protective stop lives ABOVE the entry and walks DOWN as
    the trade profits — the exact mirror of a long — so "never loosen" flips
    to "never raise" and the spread floor pushes the stop UP off the ask
    instead of down off the bid.

    bid/ask are the live market. Pass whatever you have; with no quote the
    spread floor simply doesn't apply.
    """
    try:
        fill = float(fill_price or 0)
    except (TypeError, ValueError):
        return None
    if fill <= 0 or locked_pct is None:
        return None

    dirn = -1 if int(direction or 1) < 0 else 1
    want = fill * (1.0 + dirn * float(locked_pct) / 100.0)
    tick = tick_size(want)

    try:
        b = float(bid) if bid else None
        a = float(ask) if ask else None
    except (TypeError, ValueError):
        b = a = None

    # FLOOR 2 — never inside the spread.
    if dirn == 1:
        # LONG: you exit by SELLING into the bid, so the stop sits under it.
        if b and b > 0:
            spread = (a - b) if (a and a > b) else 0.0
            room = max(spread, tick * MIN_STOP_TICKS)
            ceiling = b - room
            if want > ceiling:
                want = ceiling
    else:
        # SHORT: you exit by BUYING at the ask, so the stop sits above it.
        ref = a if (a and a > 0) else b
        if ref and ref > 0:
            spread = (a - b) if (a and b and a > b) else 0.0
            room = max(spread, tick * MIN_STOP_TICKS)
            floor_px = ref + room
            if want < floor_px:
                want = floor_px

    want = round(max(0.01, want), 2)

    # Never loosen. For a long that means never lower; for a short, never
    # raise — both are "never give back ground the trade already earned".
    if current_stop is not None:
        try:
            cur = float(current_stop)
            if dirn == 1 and want <= cur + 1e-9:
                return None
            if dirn == -1 and want >= cur - 1e-9:
                return None
        except (TypeError, ValueError):
            pass
    return want


# =====================================================================
# FUTURES — points, not percent
# =====================================================================
# A percentage is meaningless on a future. MNQ trades near 24,000; "10%" is
# 2,400 points, which is not a stop, it is a different trade. Futures move on
# POINTS and his whole futures book is already written in them: entries snap
# to the 25-point grid, the default bracket is 25 risk / 50 reward, and MES
# gets its own 10-point stop.
#
# So the futures ratchet uses the ONE number that trade already has — its own
# stop width — as both the arm and the rung:
#
#     arm  = one stop-width of profit  -> lock BREAKEVEN
#     then = every further stop-width  -> lock another stop-width
#
# MNQ on the standard 25-pt stop: +25 locks BE, +50 locks +25, +75 locks +50.
# MES on his 10-pt stop:          +10 locks BE, +20 locks +10, +30 locks +20.
#
# It reads the same as the options ratchet and needs no new numbers from him.

FUT_DEFAULT_STOP_PTS = 25.0
FUT_STOP_PTS_BY_SYMBOL = {"MES": 10.0, "ES": 10.0}


def futures_stop_points(symbol, their_stop=None, entry=None):
    """How many points this trade risks — the rung size for its ratchet.

    The caller's OWN stop wins when they posted one (his standing rule:
    "theirs first, mine as fallback"), so a room that risks 12 points
    ratchets in 12s and a room that posts nothing uses the house 25.
    """
    if their_stop is not None and entry is not None:
        try:
            d = abs(float(entry) - float(their_stop))
            if d > 0:
                return d
        except (TypeError, ValueError):
            pass
    return FUT_STOP_PTS_BY_SYMBOL.get(str(symbol or "").upper()[:3],
                                      FUT_DEFAULT_STOP_PTS)


def futures_locked_points(gain_points, stop_pts):
    """Points of profit the stop should be locking, or None before the first
    rung. Mirrors ratchet_locked_pct, in points instead of percent."""
    if gain_points is None or not stop_pts or stop_pts <= 0:
        return None
    g = float(gain_points)
    if g < stop_pts - 1e-9:
        return None
    k = int((g - stop_pts + 1e-9) // stop_pts)
    return stop_pts * k


def futures_stop_price(entry, locked_points, direction=1, current_stop=None,
                       tick=0.25):
    """Where the futures stop belongs, in price. None if it isn't an
    improvement. direction +1 long, -1 short. Index futures tick 0.25."""
    try:
        e = float(entry)
    except (TypeError, ValueError):
        return None
    if locked_points is None:
        return None
    dirn = -1 if int(direction or 1) < 0 else 1
    want = e + dirn * float(locked_points)
    if tick and tick > 0:
        want = round(round(want / tick) * tick, 4)
    if current_stop is not None:
        try:
            cur = float(current_stop)
            if dirn == 1 and want <= cur + 1e-9:
                return None
            if dirn == -1 and want >= cur - 1e-9:
                return None
        except (TypeError, ValueError):
            pass
    return want


# ---------------------------------------------------------------- anti-clip
ANTI_CLIP_K = 0.40      # the stop keeps at least this share of the gain as room


def anti_clip(locked_pct, gain_pct, k=ANTI_CLIP_K):
    """THE ANTI-CLIP RULE (9/2, from the 520-trade / 4,000-resample study):
    the stop may never sit closer than k of the gain already made, i.e.
    locked <= (1 - k) * gain. Does nothing on a normal trade (with 5% rungs
    it only binds above +12.5%); past that it keeps the stop a proportional
    distance back so a runner is never strangled. k=0.40 and 0.60 tied in
    the study; 0.40 gives back less on a reversal. Not a tuned optimum."""
    if locked_pct is None or gain_pct is None:
        return locked_pct
    cap = (1.0 - float(k)) * float(gain_pct)
    return min(float(locked_pct), round(cap, 2))
