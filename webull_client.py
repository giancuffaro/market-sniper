"""MARKET SNIPER — Webull session wrapper. v3.1
(Symbols: SPY/QQQ daily-0DTE, TSLA weekly via nearest-Friday expiry.)"""

import os, uuid, math, random
import datetime as dt
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

import config
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
        SDK_HINT = "an OLDER Webull SDK is installed — double-click INSTALL.bat, then relaunch"
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

def pick_strike(spot, side, step, mode="OTM1"):
    if mode == "ITM1":
        if side == "CALLS":
            return math.ceil(spot / step) * step - step
        return math.floor(spot / step) * step + step
    return first_otm_strike(spot, side, step)

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


class OptionData:
    def __init__(self, api_client):
        self._api = api_client
        self._dc = None

    def _client(self):
        if self._dc is None:
            if DataClient is None:
                raise OrderRejected("webull.data.DataClient not importable — run INSTALL.bat again.")
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
                "subscription is missing/inactive (see TUTORIAL.html Part 3).")
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
        raise OrderRejected("option snapshot failed — " + " | ".join(errors[:3]))

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
    def __init__(self, mode):
        self.mode = mode
        self.account_id = None
        self.account_type = None
        self.buying_power = 0.0
        self.position = None
        self.day_realized = 0.0
        self.blotter = []
        self.settings = dict(config.DEFAULT_SETTINGS)
        self.last_event = None
        self.armed = None   # MY CONFIG pending round-number entry
        self.strategies = default_strategies()
        self._fired = set()   # (strategy_id, date) that already entered today

    def update_settings(self, new):
        s = self.settings
        if "strike_mode" in new and new["strike_mode"] in ("OTM1", "ITM1"):
            s["strike_mode"] = new["strike_mode"]
        if "tp_enabled" in new: s["tp_enabled"] = bool(new["tp_enabled"])
        if "sl_enabled" in new: s["sl_enabled"] = bool(new["sl_enabled"])
        if "my_enabled" in new: s["my_enabled"] = bool(new["my_enabled"])
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
        return s

    def _bracket_hit(self):
        p, s = self.position, self.settings
        if not p or p.get("mark") is None:
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
        hit = self._bracket_hit()
        if not hit:
            return
        try:
            pnl = round((self.position["mark"] - self.position["entry"]) * 100 * self.position["qty"], 2)
            self.close()
            if self.blotter:
                self.blotter[-1]["desc"] += f"  [{hit}]"
            sign = "+" if pnl >= 0 else "−"
            self.last_event = (f"{'TAKE PROFIT' if hit=='TP' else 'STOP LOSS'} HIT — "
                               f"position closed {sign}${abs(pnl):.2f}")
        except OrderRejected as e:
            self.last_event = f"{hit} hit but close blocked: {e}"

    # ---- MY CONFIG: round-number armed entry -------------------------------
    def _underlying(self, symbol):
        """Best available underlying price: sim spot in PAPER, live feed otherwise."""
        if hasattr(self, "_spot"):
            try:
                return float(self._spot(symbol))
            except Exception:
                pass
        try:
            import quotes
            return float(quotes.get_price(symbol)["price"])
        except Exception:
            return None

    def arm(self, symbol, side, qty):
        """Arm a MY CONFIG entry: wait for the underlying to reach the nearest
        whole dollar, then buy the ask. Auto-sets +$1 (next-whole) TP and 10% SL."""
        qty = int(qty)
        self._guard_open(qty)               # hours / max-contracts / no open pos
        spot = self._underlying(symbol)
        if spot is None:
            raise OrderRejected("no underlying price available to arm the entry")
        target = float(round(spot))         # closest round number
        s = self.settings
        s["my_enabled"] = True
        s["tp_enabled"] = True; s["tp_unit"] = "whole"
        s["sl_enabled"] = True; s["sl_unit"] = "pct"; s["sl_value"] = config.MY_CONFIG_SL_PCT
        self.armed = {"symbol": symbol, "side": side, "qty": qty,
                      "target": target, "spot_at_arm": round(spot, 2)}
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
        reached = (spot <= a["target"]) if a["side"] == "CALLS" else (spot >= a["target"])
        if not reached:
            return
        self.armed = None                   # clear first so we never double-fire
        try:
            self.place(a["symbol"], a["side"], a["qty"])
            p = self.position
            if p:
                # Anchor TP to the ROUND NUMBER (+$1 up for calls, −$1 for puts),
                # not the noisy fill price — that's the whole point of MY CONFIG.
                p["entry_round"] = a["target"]
                p["tp_spot"] = a["target"] + 1.0 if a["side"] == "CALLS" else a["target"] - 1.0
            self.last_event = (f"ENTRY TRIGGERED — {a['symbol']} reached "
                               f"{a['target']:.0f}, bought {a['side']} at the ask "
                               f"(TP {p['tp_spot']:.0f} · 10% stop)")
        except OrderRejected as e:
            self.last_event = f"MY CONFIG entry blocked at {a['target']:.0f}: {e}"

    # ---- Strategy engine: conditions that auto-execute --------------------
    def update_strategies(self, strategies):
        if not isinstance(strategies, list):
            raise OrderRejected("strategies must be a list")
        cleaned = [c for c in (_coerce_strategy(s) for s in strategies) if c]
        self.strategies = cleaned
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
        if not config.ENFORCE_MARKET_HOURS:
            return False
        if self.mode == "PAPER" and not config.ENFORCE_MARKET_HOURS_IN_PAPER:
            return False
        return True

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
        self._maybe_trigger_entry()   # fire armed MY CONFIG entry if price reached
        self._eval_strategies()       # fire any enabled strategy whose condition is met
        ev, self.last_event = self.last_event, None
        return {"mode": self.mode, "account_id": self.account_id,
                "account_type": self.account_type,
                "buying_power": round(self.buying_power, 2),
                "position": self.position, "armed": self.armed,
                "day_realized": round(self.day_realized, 2),
                "blotter": self.blotter[-20:], "settings": self.settings,
                "strategies": self.strategies, "event": ev}


class PaperSession(BaseSession):
    SPOTS = {"SPY": 626.40, "QQQ": 500.13, "TSLA": 330.00}

    def connect(self, app_key, app_secret, account_id=None):
        self.account_id = "PAPER-0000"
        self.buying_power = 24318.00
        self._od = None
        if app_key and app_secret and SDK_AVAILABLE:
            try:
                api = ApiClient(app_key, app_secret, config.REGION)
                api.add_endpoint(config.REGION, config.LIVE_TRADE_ENDPOINT)
                self._od = OptionData(api)
                self.account_type = "SIM · REAL DATA"
            except Exception:
                self._od = None
        if self._od is None:
            self.account_type = "SIM · FAKE DATA"
        return self.state()

    def _spot(self, symbol):
        if quotes is not None:
            try:
                return quotes.get_price(symbol)["price"]
            except Exception:
                pass
        return round(self.SPOTS[symbol] + random.uniform(-0.6, 0.6), 2)

    def quote(self, symbol, side):
        spot = self._spot(symbol)
        step = config.SYMBOLS[symbol]["strike_step"]
        strike = pick_strike(spot, side, step, self.settings["strike_mode"])
        option_type = "CALL" if side == "CALLS" else "PUT"
        ask = None
        if self._od is not None and config.SYMBOLS[symbol]["enabled"]:
            try:
                a, b, m, _ = self._od.ask_bid_mark(
                    occ_symbol(symbol, _expiry_for(symbol), option_type, strike))
                ask = a or m
            except Exception:
                ask = None
        if ask is None:
            ask = round(random.uniform(1.05, 1.45), 2)
        return {"symbol": symbol, "side": side, "spot": spot, "strike": strike,
                "ask": round(float(ask), 2), "option_type": option_type}

    def place(self, symbol, side, qty):
        self._guard_open(qty)
        q = self.quote(symbol, side)
        cost = q["ask"] * 100 * qty
        if cost > self.buying_power:
            raise OrderRejected(f"BUY {qty} x {symbol} {int(q['strike'])}"
                                f"{'C' if side=='CALLS' else 'P'} — insufficient buying power")
        self.buying_power -= cost
        self.position = {"symbol": symbol, "side": side, "qty": qty,
                         "strike": q["strike"], "option_type": q["option_type"],
                         "expiration": _expiry_for(symbol), "entry": q["ask"], "mark": q["ask"],
                         "opened_at": dt.datetime.now().strftime("%H:%M")}
        self._decorate_position(q)
        return self.position

    def refresh_mark(self):
        if not self.position:
            return None
        p = self.position
        updated = False
        if self._od is not None and config.SYMBOLS[p["symbol"]]["enabled"]:
            try:
                a, b, m, _ = self._od.ask_bid_mark(
                    occ_symbol(p["symbol"], p["expiration"], p["option_type"], p["strike"]))
                real = m or ((a + b) / 2 if a and b else a or b)
                if real:
                    p["mark"] = round(float(real), 3)
                    updated = True
            except Exception:
                pass
        p["spot"] = self._spot(p["symbol"])
        if not updated:
            entry_spot = p.get("entry_spot")
            if entry_spot and p["spot"]:
                move = (p["spot"] - entry_spot) if p["side"] == "CALLS" else (entry_spot - p["spot"])
                base = p["entry"] + config.SIM_DELTA * move
                p["mark"] = max(0.01, round(base + random.uniform(-0.01, 0.01), 3))
            else:
                p["mark"] = max(0.01, round(p["mark"] + random.uniform(-0.05, 0.05), 3))
        p["pnl"] = round((p["mark"] - p["entry"]) * 100 * p["qty"], 2)
        p["pnl_pct"] = round((p["mark"] - p["entry"]) / p["entry"] * 100, 1)
        self._maybe_auto_close()
        return self.position

    def close(self):
        if not self.position:
            raise OrderRejected("no open position to close")
        self._guard_close()
        p = self.position
        pnl = round((p["mark"] - p["entry"]) * 100 * p["qty"], 2)
        self.buying_power += p["mark"] * 100 * p["qty"]
        self.day_realized += pnl
        self.blotter.append({"time": p["opened_at"],
                             "desc": f"{p['symbol']} {int(p['strike'])}"
                                     f"{'C' if p['side']=='CALLS' else 'P'} x{p['qty']}",
                             "move": f"{p['entry']:.2f} -> {p['mark']:.2f}", "pnl": pnl})
        self.position = None
        return {"closed": True, "pnl": pnl}


class LiveSession(BaseSession):
    def __init__(self, mode="LIVE"):
        super().__init__(mode)
        self.trade = None
        self._endpoint = config.LIVE_TRADE_ENDPOINT

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
            raise OrderRejected("Webull SDK not usable" + detail + " — run INSTALL.bat, then relaunch.")
        if config.REQUIRE_LIVE_ENV_OK and os.environ.get("ALLOW_LIVE") != "1":
            raise OrderRejected("LIVE blocked: launch with START-MARKET-SNIPER (sets ALLOW_LIVE=1).")
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

        if account_id:
            match = [p for p in parsed if str(p[0]) == str(account_id)]
            if not match:
                raise OrderRejected(f"account {account_id} not found for this key")
            chosen = match[0]
        else:
            pref = (config.PREFERRED_ACCOUNT_TYPE or "").upper()
            chosen = None
            if pref:
                for aid, at, fut in parsed:
                    if pref in at and not fut:
                        chosen = (aid, at, fut)
                        break
            if chosen is None:
                if len(parsed) == 1:
                    chosen = parsed[0]
                else:
                    enriched = [(aid, at, self._balance_for(aid)) for aid, at, _ in parsed]
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
        return {"symbol": symbol, "side": side, "spot": spot, "strike": strike,
                "ask": round(a, 2), "bid": b, "option_type": option_type}

    def place(self, symbol, side, qty):
        self._guard_open(qty)
        q = self.quote(symbol, side)
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
                         "client_order_id": orders[0]["client_order_id"],
                         "fill_checked": 0}
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
            self._maybe_auto_close()
        except Exception:
            pass
        return self.position

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
        self.last_event = (f"CLOSE SENT — SELL {p['qty']} × {p['symbol']} limit "
                           f"${limit:.2f} (marketable). Verify the fill in your Webull app.")
        return {"closed": True}


def make_session(mode):
    return PaperSession(mode) if mode == "PAPER" else LiveSession(mode)
