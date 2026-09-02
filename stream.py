"""MARKET SNIPER — WEBULL STREAMING PRICES.

A push feed instead of a poll. Webull sends quotes as they happen over MQTT,
so this costs NOTHING from the 300-requests-per-60-seconds budget that Market
Sniper shares with the discord-sniper bridge and the Fill Announcer.

WHY THIS EXISTS
The price chips polled Yahoo every 5 seconds, and Yahoo caches for 5 seconds,
so polling faster showed the same number. Moving to Webull's snapshot gave a
real 1-second price but spent 60 requests a minute - a fifth of a budget three
processes share, and starving the bot's stops is the one failure the handoff
puts above all others. A stream has neither problem: real time, zero requests.

NOTHING HERE IS REQUIRED
The stream is strictly an accelerator. Every reader falls back to the polling
path when the stream is not connected, is stale, or has never had a message
for that symbol. A dropped connection must never blank the screen or stall the
ratchet - so `price()` returns None rather than a guess, and the caller polls.

DEPENDENCIES: none to install. data_streaming_client ships inside the
webull-openapi-python-sdk already in the venv, and its paho-mqtt, protobuf,
cachetools and jmespath are all present. This is NOT the separate
`webull-python-sdk-*` streaming family the handoff forbids - that one pins
conflicting versions and broke the bridge on 9/2. Do not install it.
"""

import threading
import time
import uuid

try:
    from webull.data.data_streaming_client import DataStreamingClient
    from webull.data.quotes.subscribe.payload_type import (
        PAYLOAD_TYPE_QUOTE, PAYLOAD_TYPE_SHAPSHOT, PAYLOAD_TYPE_TICK)
    STREAM_AVAILABLE = True
except Exception:                                            # noqa: BLE001
    DataStreamingClient = None
    PAYLOAD_TYPE_QUOTE = "quote"
    PAYLOAD_TYPE_SHAPSHOT = "snapshot"
    PAYLOAD_TYPE_TICK = "tick"
    STREAM_AVAILABLE = False

# A price older than this is not a live price. The reader falls back to polling
# rather than showing a number that has quietly stopped moving - a frozen quote
# is worse than a slow one, because it looks fine.
STALE_SECONDS = 8.0

# How long to wait for the first message before declaring the stream a failure.
CONNECT_TIMEOUT = 12.0


def _num(row, *names):
    for n in names:
        v = row.get(n) if isinstance(row, dict) else None
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


class PriceStream:
    """Live prices pushed from Webull. Read with price(sym); None means
    'no fresh stream value, go and poll'."""

    def __init__(self, app_key, app_secret, region="us"):
        self.app_key = app_key
        self.app_secret = app_secret
        self.region = region
        self._client = None
        self._lock = threading.Lock()
        self._rows = {}          # SYM -> {price, bid, ask, t}
        self._symbols = []
        self._connected = False
        self._started_at = 0.0
        self._messages = 0
        self._last_error = None
        self._stopping = False
        self._subscribed_types = []
        self._category = "US_STOCK"

    # ---- lifecycle -------------------------------------------------------
    def start(self, symbols, category="US_STOCK"):
        """Connect and subscribe. Never raises - a stream that will not start
        is a missing accelerator, not a broken app."""
        if not STREAM_AVAILABLE:
            self._last_error = "streaming client not importable"
            return False
        if self._client is not None:
            return self.subscribe(symbols, category)
        self._symbols = [s for s in symbols if s]
        self._category = category
        try:
            session_id = str(uuid.uuid4())
            c = DataStreamingClient(self.app_key, self.app_secret,
                                    self.region, session_id)

            def _on_subscribe(client, api_client, sid):
                # Called on every (re)connect, which is exactly when the
                # subscription has to be re-established - a reconnect that
                # does not resubscribe is a connection with no data.
                self._sub(client, self._symbols)

            c.on_quotes_subscribe = _on_subscribe
            c.on_quotes_message = self._on_message
            c.connect_and_loop_async(thread_daemon=True)
            self._client = c
            self._started_at = time.time()
            return True
        except Exception as e:                               # noqa: BLE001
            self._last_error = str(e)[:160]
            self._client = None
            return False

    def _sub(self, client, symbols):
        """Subscribe ONE payload type per call.

        Webull rejects a list: passing ["quote", "snapshot"] came back
        `UNSUPPORTED_SUB_TYPE: Subtype not supported:quotesnapshot` - the
        server concatenates the list rather than reading it as two types. That
        is why the stream never delivered a single price on 9/2 while looking
        like it had connected.

        Snapshot first: it carries the previous close, which is what the change
        and change-percent on the chips are measured from. Quote is the
        fallback and gives bid/ask.
        """
        ok = False
        for kind in (PAYLOAD_TYPE_SHAPSHOT, PAYLOAD_TYPE_QUOTE):
            try:
                client.subscribe(symbols, self._category, [kind])
                ok = True
                self._subscribed_types.append(kind)
            except Exception as e:                           # noqa: BLE001
                # Record it, but keep going - one working type is a stream.
                self._last_error = "subscribe(%s) failed: %s" % (kind, str(e)[:100])
        self._connected = ok
        return ok

    def subscribe(self, symbols, category="US_STOCK"):
        """Add symbols to a running stream (a new option contract, say)."""
        new = [s for s in symbols if s and s not in self._symbols]
        if not new or self._client is None:
            return bool(self._client)
        if self._sub(self._client, new):
            self._symbols.extend(new)
            return True
        return False

    def stop(self):
        self._stopping = True
        c, self._client = self._client, None
        self._connected = False
        if c is None:
            return
        for meth in ("loop_stop", "disconnect"):
            try:
                getattr(c, meth)()
            except Exception:                                # noqa: BLE001
                pass

    # ---- the data --------------------------------------------------------
    def _on_message(self, client, topic, payload):
        """Every pushed message lands here. Shapes vary by payload type, so
        this reads defensively and drops anything it cannot identify - a price
        attached to the wrong symbol is worse than no price."""
        try:
            rows = payload if isinstance(payload, list) else [payload]
            now = time.time()
            with self._lock:
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    sym = None
                    for k in ("symbol", "ticker", "instrumentId", "instrument_id"):
                        if row.get(k):
                            sym = str(row[k]).upper()
                            break
                    if not sym:
                        continue
                    px = _num(row, "price", "close", "last", "lastPrice", "tradePrice")
                    bid = _num(row, "bid", "bidPrice", "bid_price")
                    ask = _num(row, "ask", "askPrice", "ask_price")
                    if px is None and bid is None and ask is None:
                        continue
                    cur = self._rows.get(sym, {})
                    if px is not None:
                        cur["price"] = px
                    if bid is not None:
                        cur["bid"] = bid
                    if ask is not None:
                        cur["ask"] = ask
                    prev = _num(row, "preClose", "pre_close", "previousClose")
                    if prev:
                        cur["prev"] = prev
                    cur["t"] = now
                    self._rows[sym] = cur
                    self._messages += 1
                    self._connected = True
        except Exception:                                    # noqa: BLE001
            pass          # a bad message must never kill the reader thread

    def price(self, symbol):
        """Fresh streamed row for a symbol, or None. None means POLL."""
        if not symbol:
            return None
        with self._lock:
            row = self._rows.get(str(symbol).upper())
        if not row or row.get("price") is None:
            return None
        if time.time() - row.get("t", 0) > STALE_SECONDS:
            return None                      # gone quiet: do not trust it
        out = dict(row)
        prev = out.get("prev")
        px = out["price"]
        out["change"] = round(px - prev, 2) if prev else 0.0
        out["change_pct"] = round((px / prev - 1) * 100.0, 2) if prev else 0.0
        out["price"] = round(px, 2)
        out["live"] = True
        out["source"] = "stream"
        out["age"] = round(time.time() - out.get("t", 0), 2)
        return out

    def status(self):
        with self._lock:
            fresh = sum(1 for r in self._rows.values()
                        if time.time() - r.get("t", 0) <= STALE_SECONDS)
            known = len(self._rows)
        return {"available": STREAM_AVAILABLE,
                "running": self._client is not None,
                "connected": bool(self._connected),
                "symbols": list(self._symbols),
                "fresh_symbols": fresh,
                "known_symbols": known,
                "messages": self._messages,
                "subscribed_types": list(self._subscribed_types),
                "uptime": round(time.time() - self._started_at, 1) if self._started_at else 0,
                "stale_after": STALE_SECONDS,
                "error": self._last_error}
