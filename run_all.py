"""
RUN ALL — one window instead of four.

Before this, the launcher opened a console for the options server, another for
futures, another for auto-sync, plus the launcher itself. Four taskbar entries,
and when something went wrong you had to guess which window held the error.

This runs all three as children of ONE console and relays their output here,
tagged so you can tell who said what:

    [OPTIONS]  server on 8000
    [FUTURES]  server on 8010
    [SYNC]     auto-sync commit/push

Close this window (or Ctrl+C) and everything stops together. If any one of them
dies on its own — including the red X inside either app, which exits its own
server — the rest are shut down too, so you never end up with a half-running
system that looks fine from the outside.
"""

import os
import sys
import time
import signal
import threading
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable                      # the venv's python, since that is what launched us

CHILDREN = [
    ("OPTIONS", [PY, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"]),
    ("FUTURES", [PY, "-m", "uvicorn", "futures_app:app", "--host", "127.0.0.1", "--port", "8010"]),
    ("SYNC",    [PY, "auto_sync.py"]),
]

procs = []
stopping = threading.Event()


def pump(tag, stream):
    """Relay one child's output into this console, line by line, tagged."""
    try:
        for raw in iter(stream.readline, b""):
            if stopping.is_set():
                return
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                print("[%-7s] %s" % (tag, line), flush=True)
    except Exception:
        pass


def stop_all(reason=""):
    if stopping.is_set():
        return
    stopping.set()
    if reason:
        print("\n=== shutting everything down: %s ===" % reason, flush=True)
    for tag, p in procs:
        if p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
    # Give them a moment to go quietly, then insist.
    deadline = time.time() + 5
    for tag, p in procs:
        while p.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        if p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass


def main():
    print("=" * 62, flush=True)
    print("  MARKET SNIPER - all services in this one window", flush=True)
    print("  Options 8000  |  Futures 8010  |  Auto-sync", flush=True)
    print("  Close this window or press Ctrl+C to stop everything.", flush=True)
    print("=" * 62, flush=True)

    env = dict(os.environ)
    env["ALLOW_LIVE"] = "1"              # real orders are armed; the launcher's promise
    env["PYTHONUNBUFFERED"] = "1"        # otherwise child output arrives in useless clumps

    for tag, cmd in CHILDREN:
        try:
            p = subprocess.Popen(cmd, cwd=HERE, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except Exception as e:
            print("[%-7s] FAILED TO START: %s" % (tag, e), flush=True)
            stop_all("%s could not start" % tag)
            return 1
        procs.append((tag, p))
        threading.Thread(target=pump, args=(tag, p.stdout), daemon=True).start()
        print("[%-7s] started (pid %d)" % (tag, p.pid), flush=True)

    try:
        while True:
            time.sleep(0.5)
            for tag, p in procs:
                if p.poll() is not None:
                    # The red X inside either app exits its own server. Treat any
                    # exit as "shut the whole thing down" rather than leaving a
                    # half-running system that still looks alive.
                    stop_all("%s exited (code %s)" % (tag, p.returncode))
                    return 0
    except KeyboardInterrupt:
        stop_all("Ctrl+C")
        return 0


if __name__ == "__main__":
    try:
        signal.signal(signal.SIGTERM, lambda *_: stop_all("terminated"))
    except Exception:
        pass
    sys.exit(main())
