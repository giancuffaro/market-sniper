"""MARKET SNIPER — Webull session wrapper. v3.1
(Symbols: SPY/QQQ daily-0DTE, TSLA weekly via nearest-Friday expiry.)"""

import os, time, uuid, math, random, threading, functools
import datetime as dt
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

import config
import user_config as uc
try:
    import quotes
except Exception:
    quotes = None

SDK_AVAILABLE = False
SDK_HINT = None
try:
    from webull.core.client import ApiClient
    from webull.trade.trade_client import TradeClient
    SDK_AVAILABLE = True
except Exception as _e:
    SDK_HINT = f"import failed: {str(_e)[:80]}"
    try:
        import webullsdkcore  # noqa: F401
        SDK_HINT = "an OLDER Webull SDK is installed — restart with START MARKET SNIPER (it reinstalls the SDK)"
    except Exception:
        pass

DataClient = None
try:
    from webull.data.data_client import DataClient
except Exception:
    DataClient = None


# NYSE holidays — extend yearly. Weekends are handled separately.
_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}

def _is_trading_day(d):
    return d.weekday() < 5 and d.isoformat() not in _HOLIDAYS

def _next_trading_day(d):
    for _ in range(12):
        if _is_trading_day(d):
            return d
        d += dt.timedelta(days=1)
    return d

def _today_expiry():
    return _next_trading_day(dt.date.today()).isoformat()

# SPY/QQQ have daily (0DTE) expirations; other symbols (e.g. TSLA) are weekly.
# Never return a weekend/holiday — those OCC symbols don't exist (INVALID_SYMBOL).
DAILY_EXPIRY = ("SPY", "QQQ")
def _expiry_for(symbol):
    today = dt.date.today()
    if symbol in DAILY_EXPIRY:
        return _next_trading_day(today).isoformat()
    days = (4 - today.weekday()) % 7          # 4 = Friday
    fri = today + dt.timedelta(days=days)
    while not _is_trading_day(fri):           # holiday Friday -> Thursday expiry
        fri -= dt.timedelta(days=1)
    return fri.isoformat()

def occ_symbol(symbol, expiration, option_type, strike):
    d = expiration.replace("-", "")[2:]
    cp = "C" if option_type == "CALL" else "P"
    return f"{symbol}{d}{cp}{int(round(strike * 1000)):08d}"

def first_otm_strike(spot, side, step):
    if side == "CALLS":
        return math.floor(spot / step) * step + step
    return math.ceil(spot / step) * step - step

def parse_strike_mode(mode):
    """'ITM3' -> ('ITM', 3), 'ATM1' -> ('ATM', 1).

    Anything unreadable falls back to ('ITM', 1) - erring toward a contract with
    intrinsic value rather than a lottery ticket.
    """
    m = str(mode or "ITM1").upper().strip()
    if m.startswith("ATM"):
        kind = "ATM"
    elif m.startswith("OTM"):
        kind = "OTM"
    else:
        kind = "ITM"
    try:
        depth = int(m[3:] or 1)
    except ValueError:
        depth = 1
    return kind, max(1, min(depth, 20))     # 20 strikes is already absurd


def pick_strike(spot, side, step, mode="OTM1"):
    """The strike an order will actually use, `depth` strikes in or out.

    IN the money means the strike the underlying has already passed: BELOW spot
    for calls, ABOVE spot for puts. Out of the money is the mirror. Depth counts
    STRIKES, not dollars, so it respects each symbol's strike_step — 3 deep is
    $3 on QQQ (step 1.0) but $7.50 on TSLA (step 2.5).

    Worked example, QQQ at 724 with ITM3:
        CALLS -> ceil(724) - 3  = 721   (below spot, already in the money)
        PUTS  -> floor(724) + 3 = 727   (above spot, already in the money)
    """
    kind, depth = parse_strike_mode(mode)

    if kind == "ATM":
        # Nearest strike to spot, but NEVER out of the money. If the nearest
        # lands OTM we step one in, so an ATM pick always carries some intrinsic
        # value instead of being pure time premium that expires worthless.
        near = round(spot / step) * step
        if side == "CALLS" and near > spot:
            near -= step                       # above spot = OTM call -> step in
        elif side == "PUTS" and near < spot:
            near += step                       # below spot = OTM put  -> step in
        return round(near, 4)

    if kind == "ITM":
        if side == "CALLS":
            return math.ceil(spot / step) * step - depth * step
        return math.floor(spot / step) * step + depth * step
    if side == "CALLS":
        return math.floor(spot / step) * step + depth * step
    return math.ceil(spot / step) * step - depth * step

def next_whole(spot, side):
    return math.floor(spot) + 1 if side == "CALLS" else math.ceil(spot) - 1


def default_strategies():
    """Built-in example strategies. Conditions that auto-execute when met."""
    return [
        {"id": "orb15", "name": "ORB 15-min", "builtin": True, "enabled": False,
         "symbol": "QQQ", "qty": 1,
         "desc": ("Opening Range Breakout. Marks the HIGH and LOW of the first 15 "
                  "minutes after the 9:30 ET open. If price breaks ABOVE that high it "
                  "buys CALLS; if it breaks BELOW the low it buys PUTS. One entry per day."),
         "trigger": {"type": "orb", "minutes": 15},
         "tp_unit": "whole", "tp_value": 1, "sl_unit": "pct", "sl_value": 20},
        {"id": "lvlbreak", "name": "Level Break (example)", "builtin": True, "enabled": False,
         "symbol": "QQQ", "qty": 1,
         "desc": ("Watches one price level you choose. Cross UP through it → CALLS; "
                  "cross DOWN through it → PUTS."),
         "trigger": {"type": "cross", "level": 500.0, "dir": "up"},
         "tp_unit": "whole", "tp_value": 1, "sl_unit": "pct", "sl_value": 20},
    ]


def _coerce_strategy(st):
    """Sanitize a strategy dict coming from the client."""
    if not isinstance(st, dict):
        return None
    trig = st.get("trigger") or {}
    ttype = trig.get("type") if trig.get("type") in ("orb", "cross") else "orb"
    out = {
        "id": str(st.get("id") or "s")[:40],
        "name": str(st.get("name") or "Strategy")[:60],
        "desc": str(st.get("desc") or "")[:400],
        "enabled": bool(st.get("enabled")),
        "builtin": bool(st.get("builtin")),
        "symbol": st.get("symbol") if st.get("symbol") in config.SYMBOLS else "QQQ",
        "qty": max(1, min(int(st.get("qty") or 1), config.MAX_CONTRACTS)),
        "tp_unit": st.get("tp_unit") if st.get("tp_unit") in ("whole", "cents", "usd") else "whole",
        "tp_value": float(st.get("tp_value") or 1),
        "sl_unit": st.get("sl_unit") if st.get("sl_unit") in ("pct", "cents") else "pct",
        "sl_value": float(st.get("sl_value") or 20),
    }
    if ttype == "orb":
        out["trigger"] = {"type": "orb", "minutes": max(1, min(int(trig.get("minutes") or 15), 60))}
    else:
        out["trigger"] = {"type": "cross", "level": float(trig.get("level") or 0),
                          "dir": "down" if trig.get("dir") == "down" else "up"}
    return out


def _restore_strategies(saved):
    """Your saved strategies, or the built-ins on a first run.

    If a newer version of the app ships a built-in you don't have saved yet,
    it gets added (switched OFF) instead of being lost.
    """
    kept = [c for c in (_coerce_strategy(s) for s in (saved or [])) if c]
    if not kept:
        return default_strategies()
    have = {s["id"] for s in kept}
    for d in default_strategies():
        if d["id"] not in have:
            kept.append(d)
    return kept


def buy_limit(ask):
    return round(ask + max(config.MARKETABLE_BUFFER_MIN, ask * config.MARKETABLE_BUFFER_PCT), 2)

def sell_limit(bid):
    return max(0.01, round(bid - max(config.MARKETABLE_BUFFER_MIN, bid * config.MARKETABLE_BUFFER_PCT), 2))


class OrderRejected(Exception):
    pass


class ChooseAccounts(Exception):
    def __init__(self, accounts):
        self.accounts = accounts
        super().__init__("choose an account")


def _find_key(obj, *names):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in names:
                return v
        for v in obj.values():
            r = _find_key(v, *names)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = _find_key(item, *names)
            if r is not None:
                return r
    return None

def _now_et():
    return dt.datetime.now(ET) if ET else dt.datetime.now()

def check_market_hours(action="OPEN"):
    now = _now_et()
    if now.weekday() >= 5:
        raise OrderRejected("trade is not able to execute — market closed (weekend)")
    t = now.time()
    open_t, cutoff, close_t = dt.time(*config.MARKET_OPEN), dt.time(*config.ENTRY_CUTOFF), dt.time(*config.MARKET_CLOSE_HARD)
    if action == "OPEN":
        if t < open_t or t > cutoff:
            raise OrderRejected("trade is not able to execute due to non trading hours — "
                                "new trades allowed 9:30 AM to 3:40 PM ET only")
    else:
        if t < open_t or t > close_t:
            raise OrderRejected("close order not able to execute due to non trading hours — "
                                "market hours are 9:30 AM to 4:00 PM ET")


def _is_futures_account(aid, raw_account):
    said = str(aid).upper()
    for suf in getattr(config, "FUTURES_ACCOUNT_SUFFIXES", []):
        if said.endswith(str(suf).upper()):
            return True
    return "FUTURE" in str(raw_account).upper()


# =========================================================================
# THE RATE BUDGET — one app key, 300 requests / 60 s, THREE processes
#
# Market Sniper is not alone on this key. The discord-sniper bridge and the
# Fill Announcer share it. On 2026-09-02 the announcer alone produced 76,991
# rate-limit errors in one night and every other process 429'd with it -
# including the bot's stops, which is the part that costs money.
#
# Market Sniper had NO pacing at all. At one /api/state per second, with a
# position open and a mirror account attached, it was issuing roughly 2-3
# calls a second - 120-180 a minute against a 300 budget shared three ways.
#
# So: every call through this gate, spaced, and a hard stop after a 429.
# =========================================================================

MIN_CALL_INTERVAL = 0.20      # seconds between ANY two Webull calls
BACKOFF_AFTER_429 = 20.0      # dead time once the broker says slow down


class _Budget:
    """Serialises and paces every Webull request this process makes.

    A lock, not just a timestamp: /api/state, the mirror session and the
    strategy engine all call in from different threads, and two threads
    reading the same "last call" moment would both decide they were clear
    to go.
    """

    def __init__(self, min_interval=MIN_CALL_INTERVAL):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0
        self._blocked_until = 0.0
        self.calls = 0
        self.paced = 0.0          # total seconds spent waiting
        self.rate_limits = 0

    def pace(self):
        with self._lock:
            now = time.time()
            wait = 0.0
            if now < self._blocked_until:
                wait = self._blocked_until - now
            gap = self._last + self.min_interval - (now + wait)
            if gap > 0:
                wait += gap
            if wait > 0:
                time.sleep(wait)
                self.paced += wait
            self._last = time.time()
            self.calls += 1

    def note_rate_limit(self):
        with self._lock:
            self.rate_limits += 1
            self._blocked_until = time.time() + BACKOFF_AFTER_429
        print("[BUDGET ] Webull said rate limit - holding all calls for %.0fs "
              "(total %d)" % (BACKOFF_AFTER_429, self.rate_limits), flush=True)

    def stats(self):
        return {"calls": self.calls, "rate_limits": self.rate_limits,
                "paced_seconds": round(self.paced, 1),
                "blocked_for": max(0.0, round(self._blocked_until - time.time(), 1))}


BUDGET = _Budget()

_RATE_WORDS = ("RATE_LIMIT", "TOO_MANY_REQUEST", "RATELIMIT", "429")


def paced(fn, *args, **kwargs):
    """Run one Webull call through the budget.

    Any exception is re-raised untouched - the callers already turn these
    into plain English. The only thing added is noticing a 429 so the whole
    process backs off instead of hammering a door that is already shut.
    """
    BUDGET.pace()
    try:
        return fn(*args, **kwargs)
    except Exception as e:                                   # noqa: BLE001
        if any(w in str(e).upper() for w in _RATE_WORDS):
            BUDGET.note_rate_limit()
        raise



class OptionData:
    def __init__(self, api_client):
        self._api = api_client
        self._dc = None

    def _client(self):
        if self._dc is None:
            if DataClient is None:
                raise OrderRejected("webull.data.DataClient not importable — restart with START MARKET SNIPER.")
            self._dc = DataClient(self._api)
        return self._dc

    def _fns(self):
        dc = self._client()
        holders = [("data_client", dc)]
        for attr in dir(dc):
            if attr.startswith("_"):
                continue
            if "option" in attr.lower() or "market" in attr.lower():
                try:
                    holders.append((attr, getattr(dc, attr)))
                except Exception:
                    pass
        found = []
        for hname, h in holders:
            for m in dir(h):
                if m.startswith("_"):
                    continue
                low = m.lower()
                if "option" in low and ("snapshot" in low or "quote" in low):
                    fn = getattr(h, m, None)
                    if callable(fn):
                        found.append((f"{hname}.{m}", fn))
        if not found:
            for hname, h in holders:
                if "option" in hname.lower():
                    for m in dir(h):
                        if not m.startswith("_") and "snapshot" in m.lower():
                            fn = getattr(h, m, None)
                            if callable(fn):
                                found.append((f"{hname}.{m}", fn))
        return found, holders

    def _result(self, res):
        code = getattr(res, "status_code", 200)
        if code == 403:
            raise OrderRejected(
                "Webull returned 403 for market data — the $4.99/mo OPRA OpenAPI "
                "subscription is missing/inactive — add it at developer.webull.com.")
        if code != 200:
            try:
                body = str(res.json())[:150]
            except Exception:
                body = ""
            raise RuntimeError(f"HTTP {code} {body}")
        return res.json() if hasattr(res, "json") else res

    def snapshot_row(self, occ):
        fns, holders = self._fns()
        if not fns:
            inventory = "; ".join(
                f"{n}: [{', '.join(a for a in dir(h) if not a.startswith('_'))[:140]}]"
                for n, h in holders[:6])
            raise OrderRejected(
                "couldn't find the option-snapshot method — paste this to Claude → " + inventory)
        errors = []
        arg_shapes = ((occ,), ([occ],), (occ, "US_OPTION"), ([occ], "US_OPTION"))
        kw_shapes = ({"symbols": occ}, {"symbols": [occ]},
                     {"symbols": occ, "category": "US_OPTION"},
                     {"symbols": [occ], "category": "US_OPTION"})
        for name, fn in fns:
            for args in arg_shapes:
                try:
                    body = self._result(fn(*args))
                    return body[0] if isinstance(body, list) and body else body
                except OrderRejected:
                    raise
                except TypeError:
                    continue
                except Exception as e:
                    errors.append(f"{name}: {str(e)[:150]}")
            for kw in kw_shapes:
                try:
                    body = self._result(fn(**kw))
                    return body[0] if isinstance(body, list) and body else body
                except OrderRejected:
                    raise
                except TypeError:
                    continue
                except Exception as e:
                    errors.append(f"{name}(kw): {str(e)[:150]}")
        joined = " | ".join(errors[:3])
        if "INVALID_SYMBOL" in joined or "Invalid Symbol" in joined:
            raise OrderRejected(
                "No option contract found for that expiration. Usually this means the "
                "market is closed (weekend/holiday) so there's no same-day (0DTE) contract "
                "yet — try during market hours on a trading day.")
        raise OrderRejected("Couldn't load the option quote from Webull right now. Try again in a "
                            "moment; if it keeps failing, the market may be closed. (" + joined[:150] + ")")

    def ask_bid_mark(self, occ):
        row = self.snapshot_row(occ)
        def f(*names):
            v = _find_key(row, *names)
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        return f("ask", "ask_price", "askPrice"), f("bid", "bid_price", "bidPrice"), \
               f("price", "close"), row


class BaseSession:
    def __init__(self, mode="LIVE"):
        self.mode = mode
        self.account_id = None
        self.account_type = None
        self.buying_power = 0.0
        self.position = None
        self.active_mode = {"mode": "manual", "strategy": None, "strategy_name": None}
        # Today's running total and trade list. Loaded from disk so closing the
        # app at lunch and reopening it doesn't wipe the morning's numbers.
        self.day_realized = 0.0
        self.blotter = []
        self._day = None
        self._load_day()
        # Start from whatever you had switched on last time (my-settings.json),
        # not from the factory defaults.
        self.settings = dict(config.DEFAULT_SETTINGS)
        _saved = uc.load("options_settings", {}) or {}
        self.settings.update({k: v for k, v in _saved.items()
                              if k in config.DEFAULT_SETTINGS})
        # A file written before entry and ratchet became one setting. The filter
        # above drops the retired key silently, which would quietly switch the
        # feature off for anyone upgrading mid-position, so fold it in first.
        if "my_enabled" not in _saved and "ratchet_enabled" in _saved:
            self.settings["my_enabled"] = bool(_saved["ratchet_enabled"])
        self.last_event = None
        self.armed = None   # ABSOLUTE ENTRY pending round-number entry
        self.strategies = _restore_strategies(uc.load("options_strategies", None))
        self._fired = set()   # (strategy_id, date) that already entered today
        # ONE order at a time. The screen refreshes every second, and that refresh
        # is what fires an automatic take-profit / stop-loss close. Without this
        # lock, that automatic close and your finger on the CLOSE button can both
        # send a SELL for the same contracts — the second one arrives at Webull
        # with nothing left to sell, and comes back as the baffling
        # "covered call / not enough shares" rejection.
        self._order_lock = threading.RLock()

    def update_settings(self, new):
        s = self.settings
        if "strike_mode" in new:
            # Accept any ITM/OTM depth (ITM1..ITM20, OTM1..OTM20). Rebuilt from
            # the parsed pair so a typo can't reach the order path as-is.
            kind, depth = parse_strike_mode(new["strike_mode"])
            s["strike_mode"] = "%s%d" % (kind, 1 if kind == "ATM" else depth)
        if "tp_enabled" in new: s["tp_enabled"] = bool(new["tp_enabled"])
        if "sl_enabled" in new: s["sl_enabled"] = bool(new["sl_enabled"])
        if "my_enabled" in new:
            s["my_enabled"] = bool(new["my_enabled"])
            # Turning ABSOLUTE ENTRY on switches every strategy off, and vice
            # versa. One armed thing at a time, always.
            self._enforce_single_mode("entry" if s["my_enabled"] else None)
        # "ratchet_enabled" is no longer a setting - entry and ratchet are ONE
        # thing, stored once as my_enabled. Older saved files and any caller
        # still sending the old key are folded in here rather than rejected,
        # and the key is never written back, so it dies out on first save.
        if "ratchet_enabled" in new and "my_enabled" not in new:
            s["my_enabled"] = bool(new["ratchet_enabled"])
            self._enforce_single_mode("entry" if s["my_enabled"] else None)
        s.pop("ratchet_enabled", None)
        if "ratchet_step_pct" in new:
            try:
                s["ratchet_step_pct"] = max(1.0, min(float(new["ratchet_step_pct"]), 100.0))
            except (TypeError, ValueError):
                pass
        if "tp_unit" in new and new["tp_unit"] in ("cents", "usd", "whole"):
            s["tp_unit"] = new["tp_unit"]
        if "sl_unit" in new and new["sl_unit"] in ("cents", "usd", "price", "pct"):
            s["sl_unit"] = new["sl_unit"]
        for k in ("tp_value", "sl_value"):
            if k in new:
                try:
                    v = float(new[k])
                    if v > 0:
                        s[k] = v
                except (TypeError, ValueError):
                    pass
        uc.save("options_settings", s)      # remembered for next launch
        return s

    @staticmethod
    def ratchet_levels(peak_pct, step):
        """Where the stop sits, given the best percentage this trade has seen.

        The stop always rests exactly one step BELOW the highest rung reached,
        and rungs are whole multiples of `step`:

            best seen      rung      stop        next target
            entry (0%)       0%      -10%           +10%
            +10%           +10%        0%  (BE)     +20%
            +20%           +20%      +10%           +30%
            +30%           +30%      +20%           +40%

        Deriving it from the high-water mark (rather than nudging a stop each
        time a level is crossed) means a gap straight from +5% to +25% still
        lands the stop on +10% - the +20% rung was cleared, so it counts.
        """
        step = float(step) if step else 10.0
        if step <= 0:
            step = 10.0
        # Nudge before flooring. (3.30-3.00)/3.00*100 is 9.999999999999993, not
        # 10, so an exact +10% touch would floor to rung 0 and leave the stop at
        # -10% instead of moving to breakeven. A trade landing exactly on a rung
        # is the normal case, not an edge case.
        peak = max(0.0, float(peak_pct))
        rung = math.floor(peak / step + 1e-9) * step
        return {"rung": round(rung, 4),
                "stop_pct": round(rung - step, 4),
                "next_pct": round(rung + step, 4)}

    def _update_ratchet(self):
        """Track the best % this trade has seen and where the stop now sits."""
        p = self.position
        # p["ratchet_on"], not settings: a trade keeps the terms it opened with.
        if not (p and p.get("ratchet_on", self.settings.get("my_enabled"))):
            return
        if not p.get("entry"):
            return
        pct = (p["mark"] - p["entry"]) / p["entry"] * 100.0
        # High-water mark. It only ever goes UP, which is what makes the stop
        # a ratchet rather than something that can loosen again.
        p["peak_pct"] = max(p.get("peak_pct", 0.0), pct)
        lv = self.ratchet_levels(p["peak_pct"],
                                 p.get("ratchet_step",
                                       self.settings.get("ratchet_step_pct", 10.0)))
        p["ratchet"] = {**lv, "pct": round(pct, 2),
                        "stop_price": round(p["entry"] * (1 + lv["stop_pct"] / 100.0), 4),
                        "next_price": round(p["entry"] * (1 + lv["next_pct"] / 100.0), 4)}

    def _bracket_hit(self):
        p, s = self.position, self.settings
        if not p or p.get("mark") is None:
            return None

        # RATCHET owns the exit when it is on. It deliberately replaces TP/SL:
        # under the ratchet, +10% is no longer somewhere you sell, it is where
        # the stop moves to breakeven. Leaving the old take-profit live would
        # close the trade at the exact moment the ratchet is trying to let it
        # run, so the two cannot both be active.
        if p.get("ratchet_on", s.get("my_enabled")) and p.get("entry"):
            self._update_ratchet()
            r = p.get("ratchet") or {}
            if r and r["pct"] <= r["stop_pct"]:
                # Name the exit for the trade log: breakeven, a locked-in gain,
                # or the original stop.
                sp = r["stop_pct"]
                p["exit_reason"] = ("RATCHET-BE" if abs(sp) < 1e-9
                                    else ("RATCHET+%g%%" % sp if sp > 0 else "SL"))
                return "SL" if sp < 0 else "TP"
            return None
        move_cents = (p["mark"] - p["entry"]) * 100.0
        pnl_usd = (p["mark"] - p["entry"]) * 100.0 * p["qty"]
        spot = p.get("spot")
        is_call = (p["side"] == "CALLS")
        if s["tp_enabled"]:
            u, t = s["tp_unit"], s["tp_value"]
            if u == "cents" and move_cents >= t: return "TP"
            if u == "usd" and pnl_usd >= t: return "TP"
            if u == "whole" and spot is not None and p.get("tp_spot") is not None:
                if (is_call and spot >= p["tp_spot"]) or (not is_call and spot <= p["tp_spot"]):
                    return "TP"
        if s["sl_enabled"]:
            u, l = s["sl_unit"], s["sl_value"]
            if u == "cents" and move_cents <= -l: return "SL"
            if u == "usd" and pnl_usd <= -l: return "SL"
            if u == "pct" and p["entry"] > 0:
                if (p["mark"] - p["entry"]) / p["entry"] * 100.0 <= -l: return "SL"
            if u == "price" and spot is not None:
                if (is_call and spot <= l) or (not is_call and spot >= l):
                    return "SL"
        return None

    def _maybe_auto_close(self):
        # Re-check INSIDE the lock. If you pressed CLOSE a split second earlier,
        # the position is already gone by the time we get in here and we must not
        # send a second SELL.
        with self._order_lock:
            if not self.position:
                return
            hit = self._bracket_hit()
            if not hit:
                return
            return self._do_auto_close(hit)

    def _do_auto_close(self, hit):
        try:
            # Stamp the reason on the position BEFORE closing, so the trade log
            # records TP / SL rather than a generic manual CLOSE.
            #
            # Do NOT overwrite a reason that is already there. The ratchet sets
            # the precise one - RATCHET-BE, RATCHET+20%, SL - and this used to
            # clobber it with the coarse TP/SL code on the way out. The log then
            # said "TP" for every ratchet exit, so the one question the log
            # exists to answer, "did the ratchet fire and at which rung", could
            # not be answered from it.
            if self.position and not self.position.get("exit_reason"):
                self.position["exit_reason"] = hit
            res = self.close()
            pnl = float(res.get("pnl") or 0.0)
            if self.blotter:
                # so you can see at a glance which exits were automatic
                self.blotter[-1]["desc"] += f"  [{hit}]"
                self._save_day()
            sign = "+" if pnl >= 0 else "−"
            self.last_event = (f"{'TAKE PROFIT' if hit=='TP' else 'STOP LOSS'} HIT — "
                               f"position closed {sign}${abs(pnl):.2f}")
        except OrderRejected as e:
            self.last_event = f"{hit} hit but close blocked: {e}"

    # ---- ABSOLUTE ENTRY: round-number armed entry -------------------------------
    def _underlying(self, symbol):
        """Best available price for the stock itself (not the option)."""
        try:
            import quotes
            return float(quotes.get_price(symbol)["price"])
        except Exception:
            return None

    @staticmethod
    def entry_levels_near(spot, span=3):
        """Every ABSOLUTE ENTRY level within a few dollars of `spot`.

        The grid is every WHOLE dollar, plus the half-dollar levels that land on
        X2.50 and X7.50 - so 707.00, 707.50, 708.00 ... 712.00, 712.50, 713.00,
        but NOT 708.50 or 709.50.
        """
        base = int(math.floor(spot))
        out = []
        for d in range(base - span, base + span + 1):
            out.append(float(d))
            if d % 10 in (2, 7):            # ...2.50 and ...7.50 only
                out.append(d + 0.5)
        return sorted(out)

    @staticmethod
    def entry_target(spot, side="CALLS"):
        """The underlying level an ABSOLUTE ENTRY fires at. DIRECTIONAL.

        CALLS wait for the nearest level at or BELOW spot - you buy the dip to
        support. PUTS wait for the nearest level at or ABOVE - you buy the push
        into resistance. Whole dollars plus the .50 levels on the 2s and 7s.

        It has to be directional, because _maybe_trigger_entry asks
        "spot <= target" for calls and "spot >= target" for puts. Return the
        NEAREST level instead and at spot 709.80 the call target is 710.00,
        which is already >= spot - so it would fire the instant you armed it,
        having waited for nothing.

        Single source of truth. arm() uses it to set the real trigger and
        preview_entry() uses it to show that trigger beforehand, so the number
        on screen can never drift from the number that fires.
        """
        return LiveSession.entry_window(spot, side)[0]

    @staticmethod
    def entry_window(spot, side="CALLS"):
        """(pullback_level, breakout_level) for this side.

        A pullback-only trigger cannot fire in a trend, and that is not a
        theoretical worry - measured live at 13:13 ET with QQQ at 715.90, the
        call trigger sat at 715.00 and 715 had not been touched ONCE all
        session. Armed calls would have waited all afternoon.

        So an armed entry now watches both sides of price:
            CALLS  fill on a fall to the level BELOW  (buy the dip)
                   or a push up through the level ABOVE (buy the break)
            PUTS   mirror it.
        Whichever comes first wins, and the level still sets the price - this
        adds a second place to fill, it does not turn the entry into a market
        order.
        """
        spot = float(spot)
        levels = LiveSession.entry_levels_near(spot, span=4)
        below = [lv for lv in levels if lv <= spot + 1e-9]
        above = [lv for lv in levels if lv >= spot - 1e-9]
        lo = below[-1] if below else levels[0]
        hi = above[0] if above else levels[-1]
        # The breakout level must be strictly beyond price, or "cross through
        # it" is already true and the entry fires the instant it is armed.
        if abs(hi - spot) < 1e-9:
            beyond = [lv for lv in levels if lv > spot + 1e-9]
            hi = beyond[0] if beyond else hi
        if abs(lo - spot) < 1e-9:
            under = [lv for lv in levels if lv < spot - 1e-9]
            lo = under[-1] if under else lo
        return (lo, hi) if str(side).upper() == "CALLS" else (hi, lo)

    def preview_entry(self, symbol, side):
        """What an armed entry WOULD do at the current price. Sends nothing.

        Answers the question you ask before pressing ARM: at what level does
        this actually trigger, how far away is that, and which strike do I end
        up holding when it does?
        """
        spot = self._underlying(symbol)
        if spot is None:
            return {"ok": False, "reason": "no underlying price available"}

        target = self.entry_target(spot, side)
        step = config.SYMBOLS.get(symbol, {}).get("strike_step", 1.0)
        mode = self.settings.get("strike_mode", "OTM1")

        # Strike is resolved AT THE TRIGGER LEVEL, not at the current price —
        # that's the contract you'd actually be filled on when it fires.
        strike = pick_strike(target, side, step, mode)

        distance = round(target - spot, 2)
        return {
            "ok": True, "symbol": symbol, "side": side,
            "spot": round(spot, 2),
            "target": target,
            "distance": distance,
            "direction": "above" if distance > 0 else ("below" if distance < 0 else "at"),
            "strike": strike,
            "strike_mode": mode,
            "option_type": "CALL" if side == "CALLS" else "PUT",
        }

    def arm(self, symbol, side, qty):
        """ABSOLUTE ENTRY: wait for the underlying to reach the nearest whole
        dollar, then buy.

        Entry only. The exit belongs to the RATCHET - this deliberately does
        NOT set a take-profit, because a +$1 TP would close the trade at the
        exact moment the ratchet is trying to let it run."""
        qty = int(qty)
        self._guard_open(qty)               # hours / max-contracts / no open pos
        spot = self._underlying(symbol)
        if spot is None:
            raise OrderRejected("no underlying price available to arm the entry")
        pull, brk = self.entry_window(spot, side)
        target = pull                            # the level price is walking toward
        s = self.settings
        s["my_enabled"] = True
        # The RATCHET owns the exit when it is on, so do not also arm a
        # take-profit that would close the trade at the exact moment the
        # ratchet is trying to let it run.
        if not s.get("my_enabled"):
            s["tp_enabled"] = True; s["tp_unit"] = "whole"
            s["sl_enabled"] = True; s["sl_unit"] = "pct"; s["sl_value"] = config.MY_CONFIG_SL_PCT
        self.armed = {"symbol": symbol, "side": side, "qty": qty,
                      "target": target, "breakout": brk,
                      "spot_at_arm": round(spot, 2)}
        return dict(self.armed)

    def disarm(self):
        self.armed = None
        return {"armed": None}

    def _maybe_trigger_entry(self):
        a = self.armed
        if not a or self.position is not None:
            return
        spot = self._underlying(a["symbol"])
        if spot is None:
            return
        # Either side fills it. The pullback level is behind price, the
        # breakout level in front, and whichever price reaches first wins.
        brk = a.get("breakout")
        if a["side"] == "CALLS":
            pulled = spot <= a["target"]
            broke = brk is not None and spot >= brk
        else:
            pulled = spot >= a["target"]
            broke = brk is not None and spot <= brk
        if not (pulled or broke):
            return
        hit_level = a["target"] if pulled else brk
        how = "pullback" if pulled else "breakout"
        self.armed = None                   # clear first so we never double-fire
        try:
            self.place(a["symbol"], a["side"], a["qty"])
            p = self.position
            if p:
                # Anchor TP to the ROUND NUMBER (+$1 up for calls, −$1 for puts),
                # not the noisy fill price — that's the whole point of MY CONFIG.
                p["entry_round"] = hit_level
                p["tp_spot"] = hit_level + 1.0 if a["side"] == "CALLS" else hit_level - 1.0
            # .2f, not .0f: half-levels are real targets and 707.50 printed as
            # "708" is a different price than the one that actually fired.
            self.last_event = (f"ENTRY TRIGGERED ({how}) — {a['symbol']} reached "
                               f"{hit_level:.2f}, bought {a['side']} at the ask")
        except OrderRejected as e:
            self.last_event = f"ABSOLUTE ENTRY blocked at {a['target']:.2f}: {e}"

    # ---- Strategy engine: conditions that auto-execute --------------------
    def _enforce_single_mode(self, prefer=None):
        """Exactly ONE thing may be armed: ABSOLUTE ENTRY, or one strategy.

        Enforced on the SERVER, not just the screen. Two auto-entries live at
        once means two positions out of one move, and a checkbox is the last
        thing that should be trusted to prevent that.

        prefer="entry"      -> keep ABSOLUTE ENTRY, switch every strategy off
        prefer="<strat id>" -> keep that strategy, switch ENTRY and the rest off
        prefer=None         -> resolve whatever is already on; entry wins a tie
        """
        strats = self.strategies or []
        on = [st for st in strats if st.get("enabled")]

        if prefer == "entry":
            for st in strats:
                st["enabled"] = False
            self.settings["my_enabled"] = True
        elif prefer:
            for st in strats:
                st["enabled"] = (st.get("id") == prefer)
            self.settings["my_enabled"] = False
        elif self.settings.get("my_enabled") and on:
            for st in strats:
                st["enabled"] = False
        elif len(on) > 1:
            keep = on[0].get("id")
            for st in strats:
                st["enabled"] = (st.get("id") == keep)

        active = next((st for st in strats if st.get("enabled")), None)
        self.active_mode = {
            "mode": "strategy" if active else
                    ("entry" if self.settings.get("my_enabled") else "manual"),
            "strategy": (active or {}).get("id"),
            "strategy_name": (active or {}).get("name")}
        return self.active_mode

    def update_strategies(self, strategies):
        if not isinstance(strategies, list):
            raise OrderRejected("strategies must be a list")
        cleaned = [c for c in (_coerce_strategy(s) for s in strategies) if c]
        # Only ONE strategy may be on. If the client sent several, the newly
        # enabled one wins and the rest are switched off.
        was_on = {st["id"] for st in (self.strategies or []) if st.get("enabled")}
        now_on = [st for st in cleaned if st.get("enabled")]
        newly = next((st for st in now_on if st["id"] not in was_on), None)
        self.strategies = cleaned
        if newly:
            self._enforce_single_mode(newly["id"])
        else:
            self._enforce_single_mode(None)
        uc.save("options_strategies", self.strategies)   # remembered for next launch
        uc.save("options_settings", self.settings)
        return self.strategies

    def _strategy_side(self, st, sym):
        """Return 'CALLS' / 'PUTS' if the strategy's condition is met, else None."""
        trig = st.get("trigger", {})
        price = self._underlying(sym)
        if price is None:
            return None
        if trig.get("type") == "orb":
            try:
                import quotes
                rng = quotes.opening_range(sym, int(trig.get("minutes", 15)))
            except Exception:
                rng = {}
            if not rng or not rng.get("complete"):
                return None
            if price > rng["high"]:
                return "CALLS"
            if price < rng["low"]:
                return "PUTS"
            return None
        if trig.get("type") == "cross":
            level = float(trig.get("level", 0))
            if trig.get("dir") == "down":
                return "PUTS" if price <= level else None
            return "CALLS" if price >= level else None
        return None

    def _eval_strategies(self):
        if self.position is not None or self.armed is not None:
            return
        today = dt.date.today().isoformat()
        for st in self.strategies:
            if not st.get("enabled"):
                continue
            sym = st.get("symbol") or "QQQ"
            if config.SYMBOLS.get(sym, {}).get("enabled") is not True:
                continue
            if (st.get("id"), today) in self._fired:
                continue
            side = self._strategy_side(st, sym)
            if not side:
                continue
            self._fired.add((st.get("id"), today))     # one entry per strategy per day
            s = self.settings
            s["tp_enabled"] = True; s["tp_unit"] = st.get("tp_unit", "whole")
            s["tp_value"] = float(st.get("tp_value", 1))
            s["sl_enabled"] = True; s["sl_unit"] = st.get("sl_unit", "pct")
            s["sl_value"] = float(st.get("sl_value", 20))
            try:
                self.place(sym, side, int(st.get("qty", 1)))
                self.last_event = (f"STRATEGY «{st.get('name')}» FIRED — "
                                   f"bought {side} {sym} at the ask")
            except OrderRejected as e:
                self.last_event = f"strategy «{st.get('name')}» blocked: {e}"
            return                                       # one position at a time

    def _decorate_position(self, q):
        p = self.position
        p["entry_spot"] = q["spot"]
        if self.settings["tp_unit"] == "whole":
            p["tp_spot"] = next_whole(q["spot"], p["side"])

    def _hours_enforced(self):
        return bool(config.ENFORCE_MARKET_HOURS)

    # ---- TODAY / DAY NET ---------------------------------------------------
    # Every closed trade lands here. It is written to my-settings.json as it
    # happens, so the running total survives closing the app, restarting the
    # PC, and UPDATE.bat. A new calendar day starts the count over at zero.

    def _load_day(self):
        today = dt.date.today().isoformat()
        saved = uc.load("options_day", {})
        if saved.get("date") == today:
            try:
                self.day_realized = float(saved.get("net", 0.0))
                self.blotter = [b for b in saved.get("blotter", []) if isinstance(b, dict)]
            except Exception:
                self.day_realized, self.blotter = 0.0, []
        else:
            self.day_realized, self.blotter = 0.0, []
        self._day = today

    def _save_day(self):
        uc.save("options_day", {"date": self._day,
                                "net": round(self.day_realized, 2),
                                "blotter": self.blotter[-50:]})

    def _roll_day(self):
        """Midnight rolls the tally over — yesterday's number shouldn't sit on
        today's screen."""
        today = dt.date.today().isoformat()
        if today != self._day:
            self._day, self.day_realized, self.blotter = today, 0.0, []
            self._save_day()

    def _record_close(self, p, exit_price, estimated=False):
        """The one place a finished trade gets written down. Nothing else
        touches DAY NET, so the number on screen and the list under it can
        never disagree."""
        pnl = round((float(exit_price) - p["entry"]) * 100 * p["qty"], 2)
        self.day_realized = round(self.day_realized + pnl, 2)
        tag = "C" if p["side"] == "CALLS" else "P"
        self.blotter.append({
            "time": p.get("opened_at") or _now_et().strftime("%H:%M"),
            "desc": f"{p['symbol']} {int(p['strike'])}{tag} x{p['qty']}"
                    + ("  ~est" if estimated else ""),
            "move": f"{p['entry']:.2f} -> {float(exit_price):.2f}",
            "pnl": pnl,
            # The screen shows PERCENT only - no dollar figures anywhere. The
            # dollar value is still recorded here and in the trade log, it is
            # simply never displayed.
            "pct": (round((float(exit_price) - p["entry"]) / p["entry"] * 100.0, 1)
                    if p.get("entry") else 0.0),
            "estimated": bool(estimated)})
        self._save_day()

        # Every finished trade funnels through here - manual CLOSE, TP, SL and
        # the auto-clear when Webull says you are already flat - so this is the
        # one place the daily log has to be written from.
        try:
            import trade_log
            now = _now_et()
            opened = p.get("opened_at") or now.strftime("%H:%M")
            trade_log.record({
                "date": now.strftime("%Y-%m-%d"),
                "time_in": opened,
                "time_out": now.strftime("%H:%M:%S"),
                "app": "OPTIONS",
                "broker": "WEBULL",
                "account": getattr(self, "account_id", ""),
                "symbol": p.get("symbol"),
                "side": p.get("side"),
                "strike": p.get("strike"),
                "expiry": p.get("expiration"),
                "qty": p.get("qty"),
                "entry": round(float(p.get("entry") or 0), 4),
                "exit": round(float(exit_price), 4),
                "pnl": pnl,
                "pnl_pct": (round((float(exit_price) - p["entry"]) / p["entry"] * 100, 2)
                            if p.get("entry") else ""),
                "exit_reason": p.get("exit_reason") or ("ESTIMATED" if estimated else "CLOSE"),
                # How good and how bad it ever got. Together with exit_reason
                # these answer whether the ratchet earned its keep on this trade.
                "best_pct": p.get("best_pct", ""),
                "worst_pct": p.get("worst_pct", ""),
                "best_price": p.get("best_price", ""),
                "worst_price": p.get("worst_price", ""),
                "gave_back_pct": (round(p["best_pct"] - ((float(exit_price) - p["entry"])
                                  / p["entry"] * 100.0), 2)
                                  if p.get("best_pct") and p.get("entry") else ""),
                "ratchet_stop_pct": (p.get("ratchet") or {}).get("stop_pct", ""),
                "ratchet_step": p.get("ratchet_step", ""),
                "strike_mode": self.settings.get("strike_mode", ""),
                "held_secs": (int(time.time() - p["opened_ts"])
                              if p.get("opened_ts") else ""),
                **(p.get("entry_ctx") or {}),
                "note": "price estimated until Webull confirms the fill" if estimated else "",
            })
        except Exception as e:                               # noqa: BLE001
            print("[trade_log] not recorded: %s" % str(e)[:120], flush=True)
        return pnl

    # ---- Broker truth ------------------------------------------------------
    # The app's position is its own bookkeeping. Close the trade by hand in the
    # Webull app and this app never hears about it - it keeps managing a trade
    # that no longer exists, and a TP/SL firing then sends a SELL for contracts
    # you do not hold, which does not flatten anything.
    # How often to ask Webull "what do I actually hold". The whole poll runs
    # once a second anyway, so this is the only thing deciding how long a
    # phantom position sits on screen. 3s costs one small API call and makes it
    # feel instant.
    RECONCILE_EVERY = 3.0

    def _position_fns(self):
        """Find the SDK's 'list my positions' call at runtime.

        The Webull SDK renames things between versions, which is why the option
        snapshot call is discovered rather than hardcoded. Same trick here: look
        for anything on the trade client that reads like a positions endpoint.
        """
        found = []
        holders = [("trade", self.trade)]
        for attr in dir(self.trade):
            if attr.startswith("_"):
                continue
            if any(w in attr.lower() for w in ("account", "position", "trade")):
                try:
                    holders.append((attr, getattr(self.trade, attr)))
                except Exception:
                    pass
        for hname, h in holders:
            for m in dir(h):
                if m.startswith("_"):
                    continue
                low = m.lower()
                if "position" in low and any(w in low for w in ("get", "list", "query", "search")):
                    fn = getattr(h, m, None)
                    if callable(fn):
                        found.append(("%s.%s" % (hname, m), fn))
        return found

    def broker_positions(self):
        """Open positions ACCORDING TO WEBULL. None means 'could not ask'.

        None and [] are deliberately different: [] is the broker saying you hold
        nothing, None is the question failing. A failed question must never be
        mistaken for a flat account, or one network blip would drop a live trade
        off the screen and stop managing it.
        """
        if not (self.trade and self.account_id):
            return None
        for name, fn in self._position_fns():
            for args in ((self.account_id,), ()):
                try:
                    res = fn(*args)
                except Exception:
                    continue
                try:
                    body = res.json() if hasattr(res, "json") else res
                except Exception:
                    continue
                if getattr(res, "status_code", 200) != 200:
                    continue
                rows = body
                if isinstance(body, dict):
                    for key in ("data", "positions", "items", "list", "result"):
                        if isinstance(body.get(key), list):
                            rows = body[key]
                            break
                if isinstance(rows, list):
                    self._pos_fn_name = name
                    return rows
        return None

    def reconcile(self, force=False):
        """Clear our position if Webull says we hold nothing. Sends no orders."""
        now = time.time()
        if not force and now - getattr(self, "_last_reconcile", 0) < self.RECONCILE_EVERY:
            return None
        self._last_reconcile = now
        if not self.position:
            return None
        rows = self.broker_positions()
        if rows is None:
            return None                       # could not ask - change nothing
        if len(rows) > 0:
            return None                       # broker still shows something open
        with self._order_lock:
            p, self.position = self.position, None
            self.armed = None
        if p:
            self.last_event = (
                "Webull says you hold nothing, so the app cleared its %s %s%s. "
                "You closed it outside the app. No order was sent."
                % (p["symbol"], int(p["strike"]) if float(p["strike"]).is_integer()
                   else p["strike"], "C" if p["side"] == "CALLS" else "P"))
        return {"cleared": bool(p)}

    def forget_position(self):
        """Escape hatch for a position the app thinks you have but the broker
        doesn't. Sends NOTHING to Webull — it only clears the app's own screen so
        you aren't stuck watching CLOSE fail forever. Only use it after you've
        confirmed in the Webull app that you really are flat."""
        with self._order_lock:
            p, self.position = self.position, None
            self.armed = None
        if p:
            self.last_event = (f"Cleared {p['symbol']} {int(p['strike'])}"
                               f"{'C' if p['side'] == 'CALLS' else 'P'} from the screen. "
                               f"No order was sent — check Webull to be sure you're flat.")
        return {"cleared": bool(p)}

    def _guard_open(self, qty):
        if self._hours_enforced():
            check_market_hours("OPEN")
        if qty > config.MAX_CONTRACTS:
            raise OrderRejected(f"quantity {qty} exceeds MAX_CONTRACTS ({config.MAX_CONTRACTS})")
        if self.day_realized <= -abs(config.DAILY_LOSS_LIMIT):
            raise OrderRejected(f"daily loss limit hit (${config.DAILY_LOSS_LIMIT:.0f}) — trading blocked")
        if self.position is not None:
            raise OrderRejected("a position is already open — close it first")

    def _guard_close(self):
        if self._hours_enforced():
            check_market_hours("CLOSE")

    def state(self):
        self._roll_day()              # a new day starts DAY NET back at zero
        self._maybe_trigger_entry()   # fire armed ABSOLUTE ENTRY if price reached
        self._eval_strategies()       # fire any enabled strategy whose condition is met
        ev, self.last_event = self.last_event, None
        return {"mode": self.mode, "account_id": self.account_id,
                "account_type": self.account_type,
                "buying_power": round(self.buying_power, 2),
                "position": self.position, "armed": self.armed,
                "day_realized": round(self.day_realized, 2),
                # Percent view of the day: every trade's return added up. With a
                # constant position size this is the number that matters, and it
                # is the only one the screen is allowed to show.
                "day_pct": round(sum(float(b.get("pct") or 0) for b in self.blotter), 1),
                "day_wins": sum(1 for b in self.blotter if float(b.get("pct") or 0) > 0),
                "day_losses": sum(1 for b in self.blotter if float(b.get("pct") or 0) < 0),
                "blotter": self.blotter[-20:], "settings": self.settings,
                "strategies": self.strategies, "event": ev,
                "active_mode": getattr(self, "active_mode", None)}


class LiveSession(BaseSession):
    def __init__(self, mode="LIVE"):
        super().__init__(mode)
        self.trade = None
        self._endpoint = config.LIVE_TRADE_ENDPOINT
        self._pending_close = None   # a close whose real fill price we're chasing

    def _require_live_env(self):
        """The real-money safety gate. Requires ALLOW_LIVE=1, set only by the
        launcher. Since v3.6 removed PAPER there is no session that skips this,
        so it is the single gate on every order the app can send."""
        if config.REQUIRE_LIVE_ENV_OK and os.environ.get("ALLOW_LIVE") != "1":
            raise OrderRejected("LIVE blocked: launch with START-MARKET-SNIPER (sets ALLOW_LIVE=1).")

    def _balance_for(self, aid):
        try:
            res = self.trade.account_v2.get_account_balance(aid)
            bal = res.json()
            bp = _find_key(bal, "buying_power", "buyingPower", "optionBuyingPower",
                           "option_buying_power", "day_buying_power", "cash_buying_power",
                           "available_amount", "availableAmount")
            return float(bp) if bp is not None else None
        except Exception:
            return None

    def connect(self, app_key, app_secret, account_id=None):
        if not SDK_AVAILABLE:
            detail = f" ({SDK_HINT})" if SDK_HINT else ""
            raise OrderRejected("Webull SDK not usable" + detail + " — restart with START MARKET SNIPER.")
        self._require_live_env()
        api_client = ApiClient(app_key, app_secret, config.REGION)
        api_client.add_endpoint(config.REGION, self._endpoint)
        self._api_client = api_client
        self._od = OptionData(api_client)
        self.trade = TradeClient(api_client)
        res = self.trade.account_v2.get_account_list()
        if getattr(res, "status_code", None) != 200:
            raise OrderRejected(f"account list failed: {getattr(res,'status_code','?')}")
        data = res.json()
        if isinstance(data, list):
            accounts = data
        elif isinstance(data, dict):
            accounts = (data.get("data") or data.get("accounts") or data.get("account_list") or [])
            if isinstance(accounts, dict):
                accounts = [accounts]
        else:
            accounts = []
        if not accounts:
            raise OrderRejected(f"connected, but no accounts in response: {str(data)[:150]}")

        def _acct_type(a):
            at = _find_key(a, "account_type", "accountType", "account_category",
                           "accountCategory", "register_type", "registerType",
                           "broker_account_type", "customer_type")
            return str(at).upper() if at else "UNKNOWN"

        parsed = []
        for a in accounts:
            aid = _find_key(a, "account_id", "accountId", "secAccountId",
                            "sec_account_id", "id")
            if aid:
                fut = _is_futures_account(aid, a)
                display = "FUTURES ⚠" if fut else _acct_type(a)
                parsed.append((aid, display, fut))
        if not parsed:
            raise OrderRejected(f"couldn't find any account id in: {str(accounts)[:150]}")

        # Futures accounts never appear in the options picker. This app trades
        # SPY/QQQ options, so a futures row here can only ever be a mis-click.
        # They stay in `parsed` on purpose: if one is asked for BY ID further
        # down, it still gets the proper explanation instead of "not found".
        tradable = [p for p in parsed if not p[2]]
        if not tradable:
            raise OrderRejected(
                "this API key only sees your FUTURES account — this app trades "
                "options (SPY/QQQ). For MNQ/MES use the futures app on "
                "http://127.0.0.1:8010.")

        if account_id:
            match = [p for p in parsed if str(p[0]) == str(account_id)]
            if not match:
                raise OrderRejected(f"account {account_id} not found for this key")
            chosen = match[0]
        else:
            pref = (config.PREFERRED_ACCOUNT_TYPE or "").upper()
            chosen = None
            if pref:
                for aid, at, fut in tradable:
                    if pref in at:
                        chosen = (aid, at, fut)
                        break
            if chosen is None:
                if len(tradable) == 1:
                    chosen = tradable[0]
                else:
                    enriched = [(aid, at, self._balance_for(aid)) for aid, at, _ in tradable]
                    raise ChooseAccounts(enriched)

        if chosen[2]:
            raise OrderRejected(
                "that's your FUTURES account — this app trades options (SPY/QQQ). "
                "For MNQ/MES use START-FUTURES.bat (http://127.0.0.1:8010). "
                "Futures LIVE still needs CME data + wiring; paper works today.")

        self.account_id, self.account_type = chosen[0], chosen[1]
        self._load_balance()
        return self.state()

    def _load_balance(self):
        bp = self._balance_for(self.account_id)
        if bp is not None:
            self.buying_power = bp

    def _order_query_fns(self):
        holders = [("trade", self.trade)]
        for attr in dir(self.trade):
            if attr.startswith("_"):
                continue
            if "order" in attr.lower():
                try:
                    holders.append((attr, getattr(self.trade, attr)))
                except Exception:
                    pass
        found = []
        for hname, h in holders:
            for m in dir(h):
                if m.startswith("_"):
                    continue
                low = m.lower()
                if "order" in low and any(k in low for k in ("detail", "open", "history", "list", "query")):
                    fn = getattr(h, m, None)
                    if callable(fn):
                        found.append((f"{hname}.{m}", fn))
        return found

    def _try_update_fill(self, p):
        coid = p.get("client_order_id")
        if not coid:
            p["fill_checked"] = 99
            return
        for name, fn in self._order_query_fns():
            for call in ((self.account_id, coid), (self.account_id,),
                         {"account_id": self.account_id, "client_order_id": coid},
                         {"account_id": self.account_id}):
                try:
                    res = fn(**call) if isinstance(call, dict) else fn(*call)
                    if getattr(res, "status_code", 200) != 200:
                        continue
                    body = res.json() if hasattr(res, "json") else res
                    if coid not in str(body):
                        continue
                    fp = _find_key(body, "filled_price", "avg_filled_price",
                                   "avgFilledPrice", "average_price", "averagePrice",
                                   "avg_price", "avgPrice", "filled_avg_price",
                                   "deal_price", "dealPrice", "trade_price")
                    try:
                        fp = float(fp)
                    except (TypeError, ValueError):
                        continue
                    if 0 < fp < 10000 and abs(fp - p["entry"]) > 0.0001:
                        old = p["entry"]
                        p["entry"] = round(fp, 3)
                        p["fill_checked"] = 99
                        self.last_event = (f"FILL CONFIRMED @ ${fp:.2f} "
                                           f"(estimate was ${old:.2f}) — P&L now exact.")
                        return
                    if 0 < fp < 10000:
                        p["fill_checked"] = 99
                        return
                except TypeError:
                    continue
                except Exception:
                    continue

    @staticmethod
    def contract_quality(spot, strike, option_type, ask, bid):
        """Is this contract worth owning, or is it scheduled to die?

        Everything here comes from spot / strike / bid / ask - no greeks feed,
        no implied-vol solve, nothing that can silently go stale. Returns the
        numbers plus a plain-English reason when it fails.
        """
        ask = float(ask or 0)
        bid = float(bid or 0)
        intrinsic = (max(0.0, spot - strike) if option_type == "CALL"
                     else max(0.0, strike - spot))
        extrinsic = max(0.0, ask - intrinsic)
        mid = (ask + bid) / 2.0 if (ask and bid) else ask
        spread_pct = ((ask - bid) / mid * 100.0) if (mid > 0 and bid > 0) else None
        extrinsic_pct = (extrinsic / ask * 100.0) if ask > 0 else 100.0

        bad = []
        # The one that actually matters. A fully out-of-the-money 0DTE has NO
        # real value - 100% of what you pay is scheduled to be gone by 4pm.
        if getattr(config, "CONTRACT_REQUIRE_INTRINSIC", True) and intrinsic <= 0:
            bad.append("this strike is OUT of the money — there is no real value in it, "
                       "100%% of the $%.2f is time premium that expires worthless" % ask)
        if ask < config.CONTRACT_MIN_PREMIUM:
            bad.append("premium $%.2f is under the $%.2f minimum — the spread alone "
                       "would eat this" % (ask, config.CONTRACT_MIN_PREMIUM))
        if spread_pct is not None and spread_pct > config.CONTRACT_MAX_SPREAD_PCT:
            bad.append("bid/ask spread is %.0f%% (max %.0f%%) — you would be down that "
                       "much the instant you fill" % (spread_pct, config.CONTRACT_MAX_SPREAD_PCT))
        # EXTRINSIC IS A WARNING, NOT A BLOCK.
        #
        # This used to refuse the order and it was wrong. Extrinsic percentage
        # is not a measure of a bad contract - it measures how close spot is to
        # the strike and how much time is left, and both of those are inherent
        # to 0DTE. Right at the open almost everything is 90%+ time value; an
        # ITM1 with price sitting a nickel from the strike is 95% by
        # arithmetic, not because it is junk. Blocking on it meant the app
        # refused to trade at exactly the hour there is most to trade.
        #
        # The thing that genuinely has no value is a FULLY out-of-the-money
        # contract, and intrinsic <= 0 above already blocks that outright.
        warn = []
        if extrinsic_pct > config.CONTRACT_MAX_EXTRINSIC_PCT:
            warn.append("%.0f%% of this premium is time value — only $%.2f of the "
                        "$%.2f is real. Normal near the money and early in the day; "
                        "it decays fastest in the last hour."
                        % (extrinsic_pct, intrinsic, ask))
        return {"ok": not bad,
                "warnings": warn,
                "intrinsic": round(intrinsic, 4),
                "extrinsic": round(extrinsic, 4),
                "extrinsic_pct": round(extrinsic_pct, 1),
                "spread_pct": round(spread_pct, 1) if spread_pct is not None else None,
                "premium": round(ask, 4),
                "reasons": bad}

    def entry_conditions(self, symbol, side, spot=None):
        """A snapshot of the market at the moment of entry. Never raises.

        TELEMETRY ONLY. Nothing here gates an order and nothing tunes itself on
        it. That is deliberate: with a handful of trades, any rule fitted to
        this data would be fitted to noise. Measure first, decide later, and
        only once there is enough of it to mean something.

        Every reading is wrapped separately - a trend fetch timing out must
        never be the reason a trade fails to record.
        """
        out = {}
        try:
            spot = spot if spot is not None else self._underlying(symbol)
        except Exception:
            spot = None
        ysym = symbol
        try:
            import quotes as _q
            ysym = _q.YSYM.get(symbol, symbol)
        except Exception:
            pass

        try:
            import trend as _t
            d = _t.for_symbol(ysym, "1m")
            if d.get("ok"):
                out["in_trend"] = d.get("state")
                out["in_trend_agree"] = d.get("agree")
                # Was this entry AGAINST the 1-minute trend? The single most
                # useful thing to be able to group the blotter by later.
                st = d.get("state")
                if st in ("up", "down"):
                    against = (side == "PUTS" and st == "up") or \
                              (side == "CALLS" and st == "down")
                    out["in_counter_trend"] = "yes" if against else "no"
                else:
                    out["in_counter_trend"] = "chop"
            b = _t.basket(ysym, "1m")
            if b.get("ok"):
                out["in_breadth"] = b.get("breadth")
        except Exception:
            pass

        try:
            import tape as _tp
            v = _tp.velocity(ysym)
            if v.get("ok"):
                out["in_velocity"] = v.get("state")
                out["in_vel_score"] = v.get("score")
        except Exception:
            pass

        try:
            import levels as _lv, tape as _tp2
            d = _lv.dwell(_tp2._bars(ysym), price=spot)
            if d.get("ok"):
                out["in_dwell_above"] = d.get("mins_above")
                out["in_dwell_below"] = d.get("mins_below")
                out["in_pinned"] = "yes" if d.get("pinned") else "no"
        except Exception:
            pass

        try:
            import gauges as _g
            vol = _g.volume(ysym)
            if vol.get("ok") and vol.get("percentile") is not None:
                out["in_vol_pctl"] = vol.get("percentile")
            rv = _g.realized(ysym)
            if rv.get("ok"):
                out["in_rv_pct"] = rv.get("rv_pct")
        except Exception:
            pass

        # THE PARKED RULE, measured but never enforced.
        # "Counter-trend only near a level ending in 0 or 5, and only on a
        # round clock time." It is untested, so it is logged as telemetry and
        # gates nothing - once there are enough trades either side of it, the
        # blotter can say whether it was ever worth obeying.
        try:
            if spot is not None:
                last_digit = int(abs(spot)) % 10
                out["in_round_level"] = "yes" if last_digit in (0, 5) else "no"
            now = _now_et()
            out["in_round_clock"] = "yes" if now.minute in (0, 30) else "no"
            out["in_parked_rule"] = (
                "would-allow" if (out.get("in_round_level") == "yes"
                                  and out.get("in_round_clock") == "yes")
                else "would-block")
        except Exception:
            pass
        return out

    def atm_option_for_vol(self, symbol):
        """An at-the-money quote for the volatility gauge, from the LIVE chain.

        Webull is the only implied-vol source available. Yahoo's options
        endpoint answers 401 without a crumb, and the NinjaTrader link is a
        one-way file drop - it takes orders, it returns nothing - so neither
        can price an option.

        If Webull's snapshot already carries implied vol or greeks, they are
        used as-is; a broker's own number beats anything inverted from a mid
        price. Otherwise the mid is handed back for Black-Scholes inversion.
        """
        spot = self._underlying(symbol)
        if spot is None:
            return None
        step = config.SYMBOLS.get(symbol, {}).get("strike_step", 1.0)
        strike = pick_strike(spot, "CALLS", step, "ATM1")
        a, b, m, row = self._od.ask_bid_mark(
            occ_symbol(symbol, _expiry_for(symbol), "CALL", strike))
        mid = m or ((a + b) / 2.0 if (a and b) else (a or b))
        if not mid:
            return None
        # Webull renames these between SDK versions, so match on any of them.
        def _num(*names):
            v = _find_key(row, *names)
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        iv = _num("implied_volatility", "impliedVolatility", "impVol", "iv")
        # Some feeds quote IV as a fraction, some as a percent. 3.0 would be a
        # 300% vol, which does happen on 0DTE, so the split is at 5.
        if iv is not None and iv < 5.0:
            iv *= 100.0
        return {"spot": float(spot), "strike": float(strike), "price": float(mid),
                "is_call": True, "iv_native": iv,
                "delta": _num("delta"), "theta": _num("theta"),
                "gamma": _num("gamma"), "vega": _num("vega")}

    def quote(self, symbol, side):
        if not config.SYMBOLS.get(symbol, {}).get("enabled", False):
            raise OrderRejected(
                f"{symbol} isn't enabled for trading here. Use SPY, QQQ or TSLA.")
        if quotes is None:
            raise OrderRejected("price feed unavailable (quotes.py failed to load)")
        spot = quotes.get_price(symbol)["price"]
        step = config.SYMBOLS[symbol]["strike_step"]
        strike = pick_strike(spot, side, step, self.settings["strike_mode"])
        option_type = "CALL" if side == "CALLS" else "PUT"
        a, b, m, row = self._od.ask_bid_mark(
            occ_symbol(symbol, _expiry_for(symbol), option_type, strike))
        if not a or a <= 0:
            raise OrderRejected(f"no ask in snapshot — response: {str(row)[:150]}")
        q = self.contract_quality(spot, strike, option_type, a, b)
        return {"symbol": symbol, "side": side, "spot": spot, "strike": strike,
                "ask": round(a, 2), "bid": b, "option_type": option_type,
                "quality": q, "tradable": q["ok"]}

    def place(self, symbol, side, qty):
        self._guard_open(qty)
        q = self.quote(symbol, side)
        # Hard stop. A contract that fails the quality filter is not shown and
        # cannot be bought - checked HERE as well as on screen, because the
        # screen can be stale and this cannot.
        if config.CONTRACT_QUALITY_ENFORCED and not q.get("tradable", True):
            raise OrderRejected(
                "Blocked — this contract would likely expire worthless.\n\n• "
                + "\n• ".join(q["quality"]["reasons"])
                + "\n\nTry a deeper strike (ITM2) or a different symbol.")
        limit = buy_limit(q["ask"])
        orders = config.build_option_order(
            client_order_id=uuid.uuid4().hex[:32], symbol=symbol, strike=q["strike"],
            expiration=_expiry_for(symbol), option_type=q["option_type"], side="BUY",
            quantity=qty, limit_price=limit)
        res = self.trade.order_v3.place_order(self.account_id, orders)
        body = {}
        try:
            body = res.json()
        except Exception:
            pass
        if getattr(res, "status_code", None) != 200:
            raise OrderRejected(f"order rejected (HTTP {getattr(res,'status_code','?')}): {str(body)[:200]}")
        self.position = {"symbol": symbol, "side": side, "qty": qty, "strike": q["strike"],
                         "option_type": q["option_type"], "expiration": _expiry_for(symbol),
                         "entry": q["ask"], "mark": q["ask"],
                         "opened_at": dt.datetime.now().strftime("%H:%M"),
                         "opened_ts": time.time(),
                         "client_order_id": orders[0]["client_order_id"],
                         "fill_checked": 0,
                         # THE TERMS OF THIS TRADE, frozen at the moment it
                         # opened. There is no SAVE button any more - every
                         # control writes the instant you touch it - so without
                         # this, nudging the step from 10 to 25 while a trade
                         # was live would move the stop underneath a position
                         # already running. Settings change the NEXT trade.
                         # ALWAYS on. The switch decides whether entries WAIT
                         # for a level; it does not decide whether a live
                         # position has a stop. Tying the two together meant
                         # that switching to instant fills - the only way to
                         # get in when a pullback never came - also left the
                         # trade with nothing managing the exit.
                         "ratchet_on": True,
                         "ratchet_step": float(self.settings.get("ratchet_step_pct") or 10.0),
                         # Captured HERE, at the fill, not at close: by the time
                         # a trade ends the market that produced it is gone.
                         "entry_ctx": self.entry_conditions(symbol, side, spot=q.get("spot"))}
        self._decorate_position(q)
        self.last_event = (f"ORDER SENT — BUY {qty} × {symbol} {int(q['strike'])}"
                           f"{'C' if side=='CALLS' else 'P'} limit ${limit:.2f} "
                           f"(marketable). Confirming the real fill…")
        return self.position

    def refresh_mark(self):
        self._bal_tick = getattr(self, "_bal_tick", 0) + 1
        if self._bal_tick >= 15:
            self._bal_tick = 0
            self._load_balance()
        # runs even when you're flat — the trade we're confirming is already closed
        try:
            self._confirm_close_fill()
        except Exception:
            self._pending_close = None
        p = self.position
        if not p:
            return None
        fc = p.get("fill_checked", 99)
        if fc < 8:
            p["fill_checked"] = fc + 1
            try:
                self._try_update_fill(p)
            except Exception:
                pass

        # Ask Webull what we actually hold BEFORE any bracket can fire. If you
        # closed by hand, the position below is fiction and a TP/SL firing
        # against it would send a SELL for contracts you do not own.
        try:
            if self.reconcile() is not None:
                return None                   # just cleared it; nothing to manage
        except Exception:
            pass                              # never let this break the poll

        try:
            a, b, m, _ = self._od.ask_bid_mark(
                occ_symbol(p["symbol"], p["expiration"], p["option_type"], p["strike"]))
            real = m or ((a + b) / 2 if a and b else a or b)
            if real:
                p["mark"] = round(float(real), 3)
            if b:
                p["bid"] = b
            if quotes is not None:
                p["spot"] = quotes.get_price(p["symbol"])["price"]
            p["pnl"] = round((p["mark"] - p["entry"]) * 100 * p["qty"], 2)
            p["pnl_pct"] = round((p["mark"] - p["entry"]) / p["entry"] * 100, 1)
            self._track_excursion(p)
            self._maybe_auto_close()
        except Exception:
            pass
        return self.position

    @staticmethod
    def _track_excursion(p):
        """Best and worst this trade ever got to, in premium and in percent.

        The high-water mark already existed but only inside the ratchet, so it
        was only tracked when the ratchet was on and vanished when the trade
        closed. These live on the position itself and get written to the log,
        which is what makes "did the ratchet actually work" answerable after
        the fact: if best was +38% and the trade closed at +20%, the stop did
        its job; if best was +38% and it closed at -10%, it did not.
        """
        entry, mark = p.get("entry"), p.get("mark")
        if not entry or mark is None:
            return
        pct = (mark - entry) / entry * 100.0
        if p.get("best_price") is None or mark > p["best_price"]:
            p["best_price"] = mark
            p["best_pct"] = round(pct, 2)
        if p.get("worst_price") is None or mark < p["worst_price"]:
            p["worst_price"] = mark
            p["worst_pct"] = round(pct, 2)
        # Never let the best read below zero or the worst above it: a trade that
        # only ever went up still has a worst of 0, which is the truth - you
        # were never down.
        p["best_pct"] = max(p.get("best_pct", 0.0), 0.0)
        p["worst_pct"] = min(p.get("worst_pct", 0.0), 0.0)

    def close(self):
        if not self.position:
            raise OrderRejected("no open position to close")
        self._guard_close()
        p = self.position
        bid = p.get("bid")
        try:
            a, b, m, _ = self._od.ask_bid_mark(
                occ_symbol(p["symbol"], p["expiration"], p["option_type"], p["strike"]))
            if b:
                bid = b
        except Exception:
            pass
        if not bid or bid <= 0:
            bid = max(0.02, p.get("mark", 0.05))
        limit = sell_limit(bid)
        orders = config.build_option_order(
            client_order_id=uuid.uuid4().hex[:32], symbol=p["symbol"], strike=p["strike"],
            expiration=p["expiration"], option_type=p["option_type"], side="SELL",
            quantity=p["qty"], limit_price=limit)
        res = self.trade.order_v3.place_order(self.account_id, orders)
        body = {}
        try:
            body = res.json()
        except Exception:
            pass
        if getattr(res, "status_code", None) != 200:
            raise OrderRejected(f"close rejected (HTTP {getattr(res,'status_code','?')}): {str(body)[:200]}")
        self.position = None
        # Write the trade down IMMEDIATELY, at the price we expect to get. This
        # is why DAY NET used to sit at $0.00 all day: the close was sent and the
        # position cleared, but nothing was ever added up. The number is marked
        # "~est" until Webull tells us the price it actually filled at, which we
        # go and ask for on the next few refreshes.
        pnl = self._record_close(p, bid, estimated=True)
        self._pending_close = {"coid": orders[0]["client_order_id"],
                               "row": len(self.blotter) - 1,
                               "entry": p["entry"], "qty": p["qty"], "tries": 0}
        sign = "+" if pnl >= 0 else "−"
        self.last_event = (f"CLOSE SENT — SELL {p['qty']} × {p['symbol']} limit "
                           f"${limit:.2f} (marketable). Booked {sign}${abs(pnl):.2f} at the "
                           f"bid; confirming the real fill…")
        return {"closed": True, "pnl": pnl, "estimated": True}

    def _confirm_close_fill(self):
        """Replace the estimated exit price with the price Webull actually filled
        at, and correct DAY NET by the difference. Gives up quietly after a few
        tries — an estimate at the bid is close enough to keep trading on."""
        pc = getattr(self, "_pending_close", None)
        if not pc:
            return
        pc["tries"] += 1
        if pc["tries"] > 10 or not (0 <= pc["row"] < len(self.blotter)):
            self._pending_close = None
            return
        fill = self._lookup_fill_price(pc["coid"])
        if fill is None:
            return
        self._pending_close = None
        row = self.blotter[pc["row"]]
        old = row["pnl"]
        new = round((fill - pc["entry"]) * 100 * pc["qty"], 2)
        row["pnl"] = new
        row["move"] = f"{pc['entry']:.2f} -> {fill:.2f}"
        row["desc"] = row["desc"].replace("  ~est", "")
        row["estimated"] = False
        self.day_realized = round(self.day_realized - old + new, 2)
        self._save_day()
        if abs(new - old) >= 0.01:
            self.last_event = (f"Fill confirmed at ${fill:.2f} — that trade was "
                               f"{'+' if new >= 0 else '−'}${abs(new):.2f}, not "
                               f"{'+' if old >= 0 else '−'}${abs(old):.2f}. DAY NET corrected.")

    def _lookup_fill_price(self, coid):
        """Ask Webull what an order actually filled at. None = it hasn't told us
        yet (or this SDK build has no way to ask)."""
        if not coid:
            return None
        for _name, fn in self._order_query_fns():
            for call in ((self.account_id, coid), (self.account_id,),
                         {"account_id": self.account_id, "client_order_id": coid},
                         {"account_id": self.account_id}):
                try:
                    res = fn(**call) if isinstance(call, dict) else fn(*call)
                    if getattr(res, "status_code", 200) != 200:
                        continue
                    body = res.json() if hasattr(res, "json") else res
                    if coid not in str(body):
                        continue
                    fp = _find_key(body, "filled_price", "avg_filled_price",
                                   "avgFilledPrice", "average_price", "averagePrice",
                                   "avg_price", "avgPrice", "filled_avg_price",
                                   "deal_price", "dealPrice", "trade_price")
                    fp = float(fp)
                    if 0 < fp < 10000:
                        return round(fp, 3)
                except (TypeError, ValueError):
                    continue
                except Exception:
                    continue
        return None


def _serialize_orders(cls):
    """Only ever let ONE order-sending method run at a time per session.

    It is what stops two SELLs going out for the same contracts when the
    automatic stop-loss and your CLOSE press land in the same second."""
    for name in ("place", "close", "arm", "disarm"):
        fn = cls.__dict__.get(name)
        if fn is None:
            continue

        def _wrap(inner):
            @functools.wraps(inner)
            def guarded(self, *a, **kw):
                with self._order_lock:
                    return inner(self, *a, **kw)
            return guarded

        setattr(cls, name, _wrap(fn))
    return cls


_serialize_orders(LiveSession)


# ---- Turning broker gibberish into English --------------------------------
# Webull answers a bad order with things like
#   ServerException: HTTP Status: 417, Code: OAUTH_OPENAPI_OPTION_CAVERED_CALL_STOCK_NO_ENOUGH
# which tells you nothing about what to actually do. Everything the screen shows
# you goes through here first.
_PLAIN = (
    (("CAVERED_CALL_STOCK_NO_ENOUGH", "COVERED_CALL_STOCK_NO_ENOUGH",
      "insufficient number of underlying shares"),
     "Webull couldn't find those contracts in your account, so it read the order as "
     "SELLING a brand-new call instead of closing yours — and that needs 100 shares of "
     "the stock as collateral, which you don't have. Almost always this means the BUY "
     "never actually filled, or the position was already closed (a stop-loss or "
     "take-profit may have beaten you to it). Open Webull and check your positions. If "
     "you really are flat, press “I'm actually flat — clear it” below to "
     "reset the screen."),
    (("NO_ENOUGH_BUYING_POWER", "BUYING_POWER", "INSUFFICIENT_FUND", "NOT_ENOUGH_FUND"),
     "Not enough buying power for that order. Lower the contract count, or free up cash "
     "in the account."),
    (("ORDER_NOT_EXIST", "ORDER_NOT_FOUND"),
     "Webull says that order no longer exists — it was probably already filled or "
     "cancelled. Check your positions in the Webull app."),
    (("TRADING_NOT_ALLOWED", "MARKET_CLOSED", "NOT_IN_TRADING"),
     "The market isn't accepting that order right now. Options trade 9:30am–4:00pm ET "
     "on weekdays."),
    (("OPTION_NOT_SUPPORT", "NOT_TRADABLE", "INSTRUMENT_NOT"),
     "Webull won't trade that specific contract through the API. Try the other strike "
     "setting (OTM/ITM) in SETTINGS, or trade it manually."),
    (("NOT_PERMITTED", "UNAUTHORIZED_TRADE", "OPTION_LEVEL", "NO_PERMISSION"),
     "Your Webull account isn't approved for this kind of options order. Check your "
     "options trading level in the Webull app."),
    (("X-SIGNATURE", "SIGNATURE", "401"),
     "Webull rejected your API key and secret. Re-copy BOTH from Webull, and make sure "
     "your PC clock is set to sync automatically — a clock that's off by a minute breaks "
     "the signature."),
    (("RATE_LIMIT", "TOO_MANY_REQUEST", "429"),
     "You're sending orders faster than Webull allows. Wait a few seconds and try again."),
    (("TIMEOUT", "TIMED OUT", "READ TIMED"),
     "Webull didn't answer in time. The order may or may not have gone through — check "
     "your Webull app before pressing anything again."),
)


def friendly_error(e):
    """Plain-language version of whatever went wrong, safe to show on screen."""
    raw = str(e) or e.__class__.__name__
    up = raw.upper()
    for needles, plain in _PLAIN:
        if any(n.upper() in up for n in needles):
            return plain
    if isinstance(e, OrderRejected):
        return raw                       # already written in English by us
    short = raw[:200].strip()
    return f"Webull turned the order down. It said: {short}"


def is_phantom_position(msg):
    """True when the reason we were turned down is 'those contracts aren't in
    your account' — the one case where clearing the screen is the right move."""
    up = str(msg or "").upper()
    return ("CAVERED_CALL" in up or "COVERED_CALL" in up
            or "UNDERLYING SHARES" in up or "CLEAR IT" in up)


def make_session(mode="LIVE"):
    """Always a LIVE session against your real Webull account.

    PAPER (the Webull sandbox) was removed in v3.6 — it needed its own separate
    sandbox API key, and simulated fills were teaching the wrong lessons about
    a strategy whose whole edge is real fills. `mode` is still accepted so
    existing callers don't break, but every value maps to LIVE.

    That makes ALLOW_LIVE=1 the only thing standing between this app and a real
    order, so it is now enforced on every session with no exceptions — see
    LiveSession._require_live_env. The launcher sets it; nothing else does."""
    return LiveSession()
