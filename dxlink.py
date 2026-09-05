"""dxlink.py — live option GREEKS from tastytrade's dxfeed, with NO new packages.

WHY THIS FILE EXISTS AND WHY IT IS WRITTEN THIS WAY (9/4/26)
------------------------------------------------------------
G wants greeks so the machine can reason about breathing room instead of
guessing at it. tastytrade streams them over DXLink, which is JSON over a
WebSocket.

The obvious move is `pip install websockets`. We are not doing that. On 9/2 a
streaming experiment upgraded four packages past what the bridge's Webull SDK
pins allow; the running bridge survived but the NEXT restart would have failed
to import, and "FIX SDK DEPS.bat" exists solely to undo that. A dependency
that can brick the thing that places real orders is not worth a convenience.

So the WebSocket client here is ~150 lines of stdlib: socket + ssl + struct +
base64 + hashlib. RFC 6455 client framing is genuinely small once you drop the
parts we don't need (we never send binary, we decline compression). Nothing
here can move a pin or break the bridge's imports.

THE ONE SAFETY RULE
-------------------
tastytrade hands out a DEMO token on an unfunded account — the URL literally
ends in /delayed and the token says `level: demo`. Delayed greeks that look
live are worse than no greeks, so `GreeksBus` refuses to serve or tape
anything unless the token level is live, and says so once. See `live_level()`.

WHAT IT PRODUCES
  greeks_tape.csv :  ts,occ,price,iv,delta,gamma,theta,vega,rho
  .get(occ)       :  the newest greeks dict for one contract, or None

option_tape.csv is deliberately left alone — its ts,occ,bid,ask schema is read
by other tools and a widened column set would break them silently.
"""
import base64
import csv
import hashlib
import json
import os
import socket
import ssl
import struct
import threading
import time

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"     # RFC 6455

# dxfeed event fields we ask for, in the order they come back.
GREEK_FIELDS = ["eventType", "eventSymbol", "price", "volatility",
                "delta", "gamma", "theta", "rho", "vega"]


# ---------------------------------------------------------------- WebSocket
class WS:
    """A minimal RFC 6455 TEXT-frame client. Stdlib only.

    Deliberately not a general library: no compression, no binary, no
    continuation-fragment sending. It reads fragmented frames (servers do
    send those) and answers pings, which is all DXLink needs from us.
    """

    def __init__(self, url, timeout=20.0):
        if url.startswith("wss://"):
            host_path, secure, port = url[6:], True, 443
        elif url.startswith("ws://"):
            host_path, secure, port = url[5:], False, 80
        else:
            raise ValueError("not a websocket url: %s" % url)
        host, _, path = host_path.partition("/")
        if ":" in host:
            host, _, p = host.partition(":")
            port = int(p)
        self.host, self.path = host, "/" + path
        self._buf = b""
        self.closed = False

        raw = socket.create_connection((host, port), timeout=timeout)
        if secure:
            ctx = ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=host)
        raw.settimeout(timeout)
        self.sock = raw

        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET %s HTTP/1.1\r\n"
               "Host: %s\r\n"
               "Upgrade: websocket\r\n"
               "Connection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n"
               "User-Agent: discord-sniper/1.0\r\n\r\n"
               % (self.path, host, key))
        self.sock.sendall(req.encode())

        head = self._read_until(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise IOError("websocket handshake refused: %s"
                          % head.split(b"\r\n")[0][:120])
        want = base64.b64encode(
            hashlib.sha1((key + GUID).encode()).digest()).decode().lower()
        if want.encode() not in head.lower():
            raise IOError("websocket handshake key mismatch — not a real "
                          "websocket endpoint")

    # -- byte plumbing ---------------------------------------------------
    def _read_until(self, marker):
        while marker not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise IOError("socket closed during handshake")
            self._buf += chunk
        head, _, rest = self._buf.partition(marker)
        self._buf = rest
        return head + marker

    def _read_exactly(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise IOError("socket closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    # -- frames ----------------------------------------------------------
    def _send_frame(self, opcode, payload=b""):
        if self.closed:
            raise IOError("send on a closed websocket")
        head = bytearray()
        head.append(0x80 | opcode)                       # FIN + opcode
        n = len(payload)
        if n < 126:
            head.append(0x80 | n)                        # MASK + len
        elif n < (1 << 16):
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)
        head += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(head) + masked)

    def send(self, obj):
        self._send_frame(0x1, json.dumps(obj).encode())

    def recv(self):
        """Next TEXT message as a dict, or None on a non-data frame.

        Answers ping with pong and handles fragmentation. Raises on close.
        """
        data = b""
        opcode = None
        while True:
            b0, b1 = self._read_exactly(2)
            fin = b0 & 0x80
            op = b0 & 0x0F
            masked = b1 & 0x80
            ln = b1 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read_exactly(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read_exactly(8))[0]
            key = self._read_exactly(4) if masked else None
            payload = self._read_exactly(ln) if ln else b""
            if key:
                payload = bytes(c ^ key[i % 4] for i, c in enumerate(payload))

            if op == 0x8:                                # close
                self.closed = True
                raise IOError("server closed the websocket")
            if op == 0x9:                                # ping -> pong
                self._send_frame(0xA, payload)
                continue
            if op == 0xA:                                # pong
                continue
            if op in (0x1, 0x2):
                opcode = op
                data = payload
            elif op == 0x0:                              # continuation
                data += payload
            if fin:
                break
        if opcode != 0x1 or not data:
            return None
        try:
            return json.loads(data.decode("utf-8", "replace"))
        except ValueError:
            return None

    def close(self):
        try:
            if not self.closed:
                self._send_frame(0x8, b"\x03\xe8")
        except Exception:                                # noqa: BLE001
            pass
        self.closed = True
        try:
            self.sock.close()
        except Exception:                                # noqa: BLE001
            pass


# ------------------------------------------------------------ occ <-> dxfeed
def occ_to_dx(occ):
    """NVDA260904C00235000 -> .NVDA260904C235

    dxfeed wants the strike written plainly, no zero padding and no trailing
    .0 — `.SPY260918C660`, not `.SPY260918C660.0`. Half-strikes keep their
    decimal (`.IWM260904P243.5`).
    """
    s = str(occ or "").strip().replace(" ", "")          # tastytrade pads roots
    if len(s) < 15:
        return None
    tail = s[-15:]                                       # YYMMDD C/P + 8 digits
    root, ymd, cp, strike8 = s[:-15], tail[:6], tail[6], tail[7:]
    if cp not in ("C", "P") or not strike8.isdigit():
        return None
    k = int(strike8) / 1000.0
    ks = ("%.3f" % k).rstrip("0").rstrip(".")
    return ".%s%s%s%s" % (root, ymd, cp, ks)


def live_level(level, url=""):
    """Is this token good for REAL-TIME data?

    An unfunded tastytrade account gets `level: demo` on a URL ending
    /delayed. Delayed greeks that get mistaken for live would put a stop in
    the wrong place off stale gamma, so this is checked, not assumed.
    """
    lv = str(level or "").strip().lower()
    u = str(url or "").lower()
    if "delayed" in u or "demo" in u:
        return False
    return lv not in ("demo", "delayed", "")


# ---------------------------------------------------------------- the bus
class GreeksBus:
    """Streams greeks for whatever contracts it is told to watch.

    Give it a `token_fn` that returns tastytrade's /api-quote-tokens payload
    (the adapter's `quote_token()`), because the token expires and has to be
    re-fetched on reconnect.
    """

    def __init__(self, token_fn, log=None, tape=None, allow_delayed=False):
        self._token_fn = token_fn
        self._log = log or (lambda *a, **k: None)
        self._tape = tape
        self._allow_delayed = bool(allow_delayed)
        self._want = set()                 # dxfeed symbols
        self._greeks = {}                  # dx symbol -> (dict, ts)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._ws = None
        self.live = False                  # is the CURRENT feed real-time?
        self.level = None
        self.connected = False
        self.events = 0
        self._told_delayed = False

    # -- what to watch ---------------------------------------------------
    def watch(self, occ):
        dx = occ_to_dx(occ)
        if not dx:
            return
        with self._lock:
            if dx in self._want:
                return
            self._want.add(dx)
        self._subscribe([dx])

    def unwatch(self, occ):
        dx = occ_to_dx(occ)
        with self._lock:
            self._want.discard(dx)
            self._greeks.pop(dx, None)

    def get(self, occ, max_age=30.0):
        """Newest greeks for a contract, or None. Never returns delayed data
        as if it were live, and never returns a stale row."""
        if not self.live:
            return None
        dx = occ_to_dx(occ)
        with self._lock:
            row = self._greeks.get(dx)
        if not row:
            return None
        g, ts = row
        if max_age and (time.time() - ts) > max_age:
            return None
        return g

    def status(self):
        with self._lock:
            n = len(self._greeks)
        return {"connected": self.connected, "live": self.live,
                "level": self.level, "watching": len(self._want),
                "with_greeks": n, "events": self.events}

    # -- lifecycle -------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            if self._ws:
                self._ws.close()
        except Exception:                                # noqa: BLE001
            pass

    def _subscribe(self, dxs):
        ws = self._ws
        if not ws or not dxs:
            return
        try:
            ws.send({"type": "FEED_SUBSCRIPTION", "channel": 1,
                     "add": [{"type": "Greeks", "symbol": d} for d in dxs]})
        except Exception:                                # noqa: BLE001
            pass          # the reconnect re-subscribes everything anyway

    def _run(self):
        backoff = 2.0
        while not self._stop.is_set():
            try:
                self._session()
                backoff = 2.0
            except Exception as e:                       # noqa: BLE001
                self.connected = False
                if not self._stop.is_set():
                    self._log("[greeks] %s — retrying in %.0fs"
                              % (str(e)[:120], backoff))
            self._stop.wait(backoff)
            backoff = min(60.0, backoff * 2)

    def _session(self):
        d = self._token_fn() or {}
        url = d.get("dxlink-url") or d.get("websocket-url")
        tok = d.get("token")
        self.level = d.get("level")
        if not url or not tok:
            raise IOError("no dxlink token available")

        self.live = live_level(self.level, url)
        if not self.live and not self._allow_delayed:
            if not self._told_delayed:
                self._told_delayed = True
                self._log("[greeks] tastytrade handed back a '%s' token on %s "
                          "— that is DELAYED data. Not connecting: delayed "
                          "greeks that look live would move a stop off stale "
                          "gamma. Fund the account and this switches itself on."
                          % (self.level, url))
            raise IOError("delayed feed — standing down")

        ws = WS(url)
        self._ws = ws
        try:
            # THE HANDSHAKE IS SEQUENTIAL, NOT A BURST (9/4). Firing SETUP,
            # AUTH, CHANNEL_REQUEST and FEED_SETUP back to back gets you
            # "AUTH step missing" from the real endpoint, forever, while the
            # socket stays happily connected — a silent no-data failure.
            # Each step waits for the server to say it is ready.
            ws.send({"type": "SETUP", "channel": 0, "version": "0.1-ds/1.0",
                     "keepaliveTimeout": 60, "acceptKeepaliveTimeout": 60})
            self._await(ws, lambda m: m.get("type") == "SETUP", "SETUP")

            ws.send({"type": "AUTH", "channel": 0, "token": tok})
            self._await(ws,
                        lambda m: (m.get("type") == "AUTH_STATE"
                                   and m.get("state") == "AUTHORIZED"),
                        "AUTH")

            ws.send({"type": "CHANNEL_REQUEST", "channel": 1,
                     "service": "FEED", "parameters": {"contract": "AUTO"}})
            self._await(ws, lambda m: m.get("type") == "CHANNEL_OPENED",
                        "CHANNEL_OPENED")

            ws.send({"type": "FEED_SETUP", "channel": 1,
                     "acceptEventFields": {"Greeks": GREEK_FIELDS}})
            self._await(ws, lambda m: m.get("type") == "FEED_CONFIG",
                        "FEED_CONFIG")

            with self._lock:
                pending = sorted(self._want)
            if pending:
                self._subscribe(pending)
            self.connected = True
            self._log("[greeks] connected — %s feed, %d contract(s)"
                      % (self.level or "?", len(pending)))

            last_ka = time.time()
            while not self._stop.is_set():
                if time.time() - last_ka > 25:
                    ws.send({"type": "KEEPALIVE", "channel": 0})
                    last_ka = time.time()
                try:
                    msg = ws.recv()
                except socket.timeout:
                    continue
                if not msg:
                    continue
                t = msg.get("type")
                if t == "KEEPALIVE":
                    ws.send({"type": "KEEPALIVE", "channel": 0})
                    last_ka = time.time()
                elif t == "ERROR":
                    self._log("[greeks] server error: %s"
                              % str(msg.get("message") or msg)[:140])
                elif t == "FEED_DATA":
                    self._absorb(msg.get("data"))
        finally:
            self.connected = False
            self._ws = None
            ws.close()

    def _await(self, ws, test, what, seconds=15.0):
        """Read until the server says `what` happened. Data frames that
        arrive early are absorbed rather than dropped, and an ERROR is raised
        with the server's own words instead of timing out silently."""
        end = time.time() + seconds
        while time.time() < end:
            try:
                m = ws.recv()
            except socket.timeout:
                continue
            if not m:
                continue
            if m.get("type") == "KEEPALIVE":
                ws.send({"type": "KEEPALIVE", "channel": 0})
                continue
            if m.get("type") == "FEED_DATA":
                self._absorb(m.get("data"))
                continue
            if m.get("type") == "ERROR":
                raise IOError("dxlink refused %s: %s"
                              % (what, str(m.get("message") or m)[:120]))
            if test(m):
                return m
        raise IOError("dxlink never confirmed %s" % what)

    def _absorb(self, data):
        """FEED_DATA arrives either as a list of dicts (COMPACT off) or as
        ["Greeks", [flat, values, ...]] — handle both, guess at neither."""
        rows = []
        if isinstance(data, list) and len(data) == 2 \
                and isinstance(data[0], str) and isinstance(data[1], list):
            flat, n = data[1], len(GREEK_FIELDS)
            for i in range(0, len(flat) - n + 1, n):
                rows.append(dict(zip(GREEK_FIELDS, flat[i:i + n])))
        elif isinstance(data, list):
            rows = [r for r in data if isinstance(r, dict)]
        elif isinstance(data, dict):
            rows = [data]

        now = time.time()
        taped = []
        for r in rows:
            sym = r.get("eventSymbol")
            if not sym:
                continue
            g = {}
            for k in ("price", "volatility", "delta", "gamma", "theta",
                      "vega", "rho"):
                v = r.get(k)
                try:
                    g[k] = float(v) if v is not None else None
                except (TypeError, ValueError):
                    g[k] = None
            g["symbol"] = sym
            g["t"] = now
            with self._lock:
                self._greeks[sym] = (g, now)
                self.events += 1
            taped.append(g)
        if taped:
            self._write_tape(taped, now)

    def _write_tape(self, rows, now):
        if not self._tape:
            return
        try:
            new = not os.path.exists(self._tape)
            with open(self._tape, "a", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["ts", "symbol", "price", "iv", "delta",
                                "gamma", "theta", "vega", "rho"])
                for g in rows:
                    w.writerow(["%.3f" % now, g["symbol"], g["price"],
                                g["volatility"], g["delta"], g["gamma"],
                                g["theta"], g["vega"], g["rho"]])
        except Exception:                                # noqa: BLE001
            pass          # a tape write must never take the stream down
