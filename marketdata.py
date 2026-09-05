"""MARKET SNIPER — where PRICES come from. Not where orders go.

THE POINT, in one line: orders, positions and balances stay on Webull; only
market data moves off it.

Webull allows 300 requests per rolling 60 seconds PER APP KEY, and three
processes share that key — the discord-sniper bridge, the Fill Announcer and
this app. Quotes are the bulk of the volume, so every quote this app stops
asking Webull for is budget handed back to the bot's stops. On 9/4 the
bridge logged 1,052 throttle events and its ratchet spent a burst running on
fallback quotes because the shared bus could not keep up. That is the failure
the handoff's top rule exists to prevent, and this file is the cure.

Measured on this machine on 9/2-9/3, before any of it moved: one quote G
pressed for took 4.4 seconds, and a single session made 65 Webull calls while
dropping 203 more to protect the budget.

WHAT IS SAFE TO MOVE
    prices, greeks, bars          -> tastytrade / Tradier   (this file)
    orders, positions, balances   -> Webull, unchanged      (webull_client.py)

Nothing here can place, modify or cancel an order. There is no order code in
this module on purpose: a data feed that can trade is a data feed that can
lose money when it is wrong.

DEPENDENCIES: none. dxlink.py is a standard-library WebSocket client for the
same reason — the last streaming SDK installed into a broker's Python broke
its pins badly enough that discord-sniper still ships FIX SDK DEPS.bat to
undo it. Do not add one here.
"""

import json
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import dxlink
except Exception:                                            # noqa: BLE001
    dxlink = None

TASTY_API = "https://api.tastyworks.com"
TRADIER_API = "https://api.tradier.com/v1"

# A quote older than this is not a quote. Every reader falls back rather than
# showing a number that has quietly stopped moving.
STALE_SECONDS = 8.0

_UA = {"User-Agent": "MarketSniper/4.3", "Accept": "application/json"}


class FeedError(Exception):
    """This feed could not answer. Never fatal — the caller falls back."""


def _http(method, url, headers=None, body=None, timeout=8.0):
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in dict(_UA, **(headers or {})).items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise FeedError("HTTP %s: %s" % (e.code, e.read()[:160]))
    except Exception as e:                                   # noqa: BLE001
        raise FeedError(str(e)[:160])
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        raise FeedError("not JSON: %s" % raw[:120])


class Tastytrade:
    """Auth + quotes. Copied down to only what a DATA feed needs.

    The full broker class in discord-sniper can trade; this one cannot, and
    that is deliberate — see the module docstring.
    """

    def __init__(self, client_secret=None, refresh_token=None,
                 username=None, password=None, remember_token=None):
        self._secret = client_secret or None
        self._refresh = refresh_token or None
        self.oauth = bool(self._secret and self._refresh)
        self._user = username or None
        self._pass = password or None
        self._remember = remember_token or None
        self._tok = None
        self._tok_at = 0.0
        self._tok_ttl = 0.0
        self._lock = threading.Lock()
        self.last_error = None

    def configured(self):
        return bool(self.oauth or (self._user and (self._pass or self._remember)))

    def session(self):
        """A valid bearer token, refreshed BEFORE it expires.

        OAuth access tokens live 15 MINUTES (expires_in, usually 900) — not a
        day. Getting that wrong means a 401 in the middle of managing a
        position, which is the worst possible moment to find out. Refresh on a
        60-second margin.
        """
        with self._lock:
            if self._tok and (time.time() - self._tok_at) < self._tok_ttl:
                return self._tok
        if not self.configured():
            raise FeedError("tastytrade is not set up")

        if self.oauth:
            # THE TWO VALUES LOOK IDENTICAL AND GO IN ADJACENT BOXES.
            # A client secret and a refresh token are both opaque strings of
            # similar length, so swapping them is the obvious mistake - and
            # tastytrade answers it with `invalid_grant: Invalid JWT`, which
            # names neither field. Rather than making him guess, try the pair
            # the other way round once, and if THAT works, keep it that way
            # and say so.
            out, swapped = None, False
            try:
                out = _http("POST", TASTY_API + "/oauth/token", body={
                    "grant_type": "refresh_token",
                    "client_secret": self._secret,
                    "refresh_token": self._refresh})
            except FeedError as first:
                if "invalid_grant" not in str(first).lower():
                    raise
                try:
                    out = _http("POST", TASTY_API + "/oauth/token", body={
                        "grant_type": "refresh_token",
                        "client_secret": self._refresh,
                        "refresh_token": self._secret})
                    swapped = True
                except FeedError:
                    raise first          # report the ORIGINAL error, not the retry
            tok = (out or {}).get("access_token")
            if not tok:
                raise FeedError("tastytrade OAuth refresh failed: %s" % str(out)[:140])
            if swapped:
                # Correct them for good, so this costs one extra call once.
                self._secret, self._refresh = self._refresh, self._secret
                self.swapped_fix = True
            try:
                ttl = float((out or {}).get("expires_in") or 900)
            except (TypeError, ValueError):
                ttl = 900.0
            with self._lock:
                self._tok, self._tok_at = tok, time.time()
                self._tok_ttl = max(60.0, ttl - 60.0)
            return tok

        body = {"login": self._user, "remember-me": True}
        if self._remember:
            body["remember-token"] = self._remember
        else:
            body["password"] = self._pass
        out = _http("POST", TASTY_API + "/sessions", body=body)
        data = (out or {}).get("data") or {}
        tok = data.get("session-token")
        if not tok:
            raise FeedError("tastytrade login failed: %s" % str(out)[:140])
        with self._lock:
            self._tok, self._tok_at, self._tok_ttl = tok, time.time(), 20 * 3600
            if data.get("remember-token"):
                self._remember = data["remember-token"]
        return tok

    def _get(self, path):
        tok = self.session()
        auth = tok if self.oauth else tok
        prefix = "Bearer " if self.oauth else ""
        return _http("GET", TASTY_API + path, headers={"Authorization": prefix + auth})

    def quote_token(self):
        """The dxfeed token payload: {token, dxlink-url, level, expires-at}.

        `level` matters. An unfunded account gets `level: demo` on a URL
        ending /delayed; a funded one gets `level: api` on /realtime. Delayed
        greeks that read as live would move a stop off stale gamma, so
        dxlink.live_level() refuses them.
        """
        return (self._get("/api-quote-tokens") or {}).get("data") or {}

    def stock_price(self, symbol):
        d = (self._get("/market-data/by-type?equity=%s"
                       % urllib.parse.quote(symbol)) or {}).get("data") or {}
        items = d.get("items") or []
        if not items:
            raise FeedError("no quote for %s" % symbol)
        return _row_to_price(items[0])


class Tradier:
    """Plain underlying prices over REST. No published request cap.

    Kept because tastytrade's REST market-data endpoint 403'd where this one
    answered (SPY 770.19 on 9/4), so it is the better fallback for stock
    prices even though tastytrade is the better option feed.
    """

    def __init__(self, token=None, account=None):
        self._tok = token or None
        self.account = account or None
        self.last_error = None

    def configured(self):
        return bool(self._tok)

    def quotes(self, symbols):
        if not self.configured():
            raise FeedError("tradier is not set up")
        syms = ",".join(symbols)
        out = _http("GET", "%s/markets/quotes?symbols=%s"
                    % (TRADIER_API, urllib.parse.quote(syms)),
                    headers={"Authorization": "Bearer " + self._tok})
        q = ((out or {}).get("quotes") or {}).get("quote")
        if q is None:
            raise FeedError("no quotes returned")
        rows = q if isinstance(q, list) else [q]
        return {str(r.get("symbol", "")).upper(): _row_to_price(r) for r in rows}

    def stock_price(self, symbol):
        got = self.quotes([symbol])
        if symbol.upper() not in got:
            raise FeedError("no quote for %s" % symbol)
        return got[symbol.upper()]


def _num(row, *names):
    for n in names:
        v = row.get(n) if isinstance(row, dict) else None
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _row_to_price(row):
    """One shape for every feed, so callers never learn which one answered."""
    px = _num(row, "last", "last-price", "close", "mark", "price")
    prev = _num(row, "prevclose", "prev_close", "previous-close", "close-price")
    bid = _num(row, "bid", "bid-price")
    ask = _num(row, "ask", "ask-price")
    if px is None and bid and ask:
        px = round((bid + ask) / 2.0, 4)
    if px is None:
        raise FeedError("row carries no price: %s" % str(row)[:110])
    out = {"price": round(px, 4), "bid": bid, "ask": ask, "live": True}
    if prev:
        out["change"] = round(px - prev, 4)
        out["change_pct"] = round((px / prev - 1) * 100.0, 3)
    else:
        out["change"] = 0.0
        out["change_pct"] = 0.0
    return out


class Feed:
    """The one thing the app talks to. Tries each source, says which answered.

    ORDER MATTERS and it is not the obvious one. tastytrade STREAMS option
    quotes and greeks over DXLink, which Webull cannot do at any price;
    Tradier answers plain stock prices more reliably than tastytrade's REST.
    Webull stays last for market data because its budget is the thing being
    protected — it is not that it is bad, it is that it is expensive.
    """

    def __init__(self, tasty=None, tradier=None, log=None):
        self.tasty = tasty
        self.tradier = tradier
        self.log = log or (lambda *a, **k: None)
        self.bus = None                # DXLink greeks/quote stream, when armed
        self._last_source = {}
        self._errors = {}

    # ---- underlying prices ------------------------------------------------
    def stock_price(self, symbol):
        """A price and WHERE it came from, or None. Never a guess.

        Returns None rather than inventing anything. The futures feed once
        answered a failed fetch with a jittered copy of the last price, and a
        number that drifts a point at a time is indistinguishable from a quiet
        tape while the brackets compute off it.
        """
        for name, src in (("tradier", self.tradier), ("tastytrade", self.tasty)):
            if not (src and src.configured()):
                continue
            try:
                row = src.stock_price(symbol)
                row["source"] = name
                self._last_source[symbol] = name
                self._errors.pop(name, None)
                return row
            except Exception as e:                           # noqa: BLE001
                self._errors[name] = str(e)[:140]
        return None

    def stock_prices(self, symbols):
        """Several symbols, preferring the feed that can do them in one call."""
        out = {}
        if self.tradier and self.tradier.configured():
            try:
                got = self.tradier.quotes(list(symbols))
                for k, v in got.items():
                    v["source"] = "tradier"
                    out[k] = v
                    # RECORD IT. The batch path filled prices correctly but
                    # never updated _last_source, so /api/feeds reported an
                    # empty "last_source" while Tradier was serving every
                    # quote - a status panel that cannot say which feed is
                    # working is worth very little.
                    self._last_source[k] = "tradier"
                if len(out) == len(symbols):
                    self._errors.pop("tradier", None)
                    return out
            except Exception as e:                           # noqa: BLE001
                self._errors["tradier"] = str(e)[:140]
        for s in symbols:
            if s in out:
                continue
            row = self.stock_price(s)
            if row:
                out[s] = row
        return out

    # ---- option quotes ----------------------------------------------------
    def start_stream(self, allow_delayed=False):
        """Arm the DXLink stream. Never raises: it is an accelerator."""
        if dxlink is None or not (self.tasty and self.tasty.configured()):
            return False
        if self.bus is not None:
            return True
        try:
            self.bus = dxlink.GreeksBus(self.tasty.quote_token, log=self.log,
                                        allow_delayed=allow_delayed)
            self.bus.start()
            return True
        except Exception as e:                               # noqa: BLE001
            self._errors["dxlink"] = str(e)[:160]
            self.bus = None
            return False

    def stop_stream(self):
        bus, self.bus = self.bus, None
        if bus is not None:
            try:
                bus.stop()
            except Exception:                                # noqa: BLE001
                pass

    def watch(self, occ):
        if self.bus is not None and occ:
            try:
                self.bus.watch(occ)
            except Exception:                                # noqa: BLE001
                pass

    def unwatch(self, occ):
        if self.bus is not None and occ:
            try:
                self.bus.unwatch(occ)
            except Exception:                                # noqa: BLE001
                pass

    def option_quote(self, occ, max_age=STALE_SECONDS):
        """A streamed option quote, or None meaning 'ask the broker'.

        None is a real answer here. A stale streamed quote is worse than a
        slow polled one, because it looks fine while it has stopped moving.
        """
        if self.bus is None or not occ:
            return None
        try:
            row = self.bus.get(occ, max_age=max_age)
        except Exception:                                    # noqa: BLE001
            return None
        if not row:
            return None
        out = dict(row)
        out["source"] = "dxlink"
        return out

    # ---- what is actually working ----------------------------------------
    def status(self):
        st = {
            "tastytrade": bool(self.tasty and self.tasty.configured()),
            "tradier": bool(self.tradier and self.tradier.configured()),
            "stream": False,
            "stream_detail": None,
            "errors": dict(self._errors),
            "last_source": dict(self._last_source),
            "dxlink_available": dxlink is not None,
        }
        if self.bus is not None:
            try:
                st["stream_detail"] = self.bus.status()
                st["stream"] = True
            except Exception:                                # noqa: BLE001
                pass
        return st


def from_settings(cfg, log=None):
    """Build a Feed from whatever the user has actually set up.

    Absent credentials are not an error — they mean this app keeps using
    Webull and Yahoo exactly as before. Nothing here is required for the app
    to run; it only ever removes load.
    """
    cfg = cfg or {}
    tasty = Tastytrade(
        client_secret=cfg.get("tasty_client_secret"),
        refresh_token=cfg.get("tasty_refresh_token"),
        username=cfg.get("tasty_username"),
        password=cfg.get("tasty_password"),
        remember_token=cfg.get("tasty_remember_token"))
    tradier = Tradier(token=cfg.get("tradier_token"),
                      account=cfg.get("tradier_account"))
    return Feed(tasty=tasty if tasty.configured() else None,
                tradier=tradier if tradier.configured() else None,
                log=log)
