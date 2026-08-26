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
import webbrowser

# Tray icon is OPTIONAL. If pystray/Pillow are missing or the tray fails to
# start, the app must still run exactly as before — a missing decoration is
# never a reason for a trading app not to launch.
try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except Exception:
    TRAY_AVAILABLE = False

# Show the console while everything boots, then hide it. You get to see the
# startup (and any error) and still end up with a clean desktop.
HIDE_AFTER_SECONDS = 6

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
    # Bring the window back on the way out, so a shutdown message is never
    # delivered to a window nobody can see.
    if _console_hidden:
        _show_console(True)
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


# ---- Console show/hide (Windows) -----------------------------------------
_console_hidden = False

def _console_hwnd():
    """Handle of our own console window, or None if we do not have one."""
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        return hwnd or None
    except Exception:
        return None                            # not Windows, or no console


def _show_console(show=True):
    global _console_hidden
    hwnd = _console_hwnd()
    if not hwnd:
        return False
    try:
        import ctypes
        ctypes.windll.user32.ShowWindow(hwnd, 5 if show else 0)   # SW_SHOW / SW_HIDE
        _console_hidden = not show
        return True
    except Exception:
        return False


def hide_console_when_ready(tray):
    """Hide the window a few seconds after startup - but ONLY if the tray icon
    is actually running.

    That condition is the whole safety of this feature. Hiding the console
    without a tray would leave the app running with no window and no menu: no
    way to stop it, no way to see what it is doing. If the tray failed for any
    reason the console stays put, and says why.
    """
    if tray is None:
        print("[TRAY   ] no tray icon, so the window stays visible - it would "
              "otherwise be your only way to stop the app.", flush=True)
        return

    def _later():
        time.sleep(HIDE_AFTER_SECONDS)
        if stopping.is_set():
            return
        if _show_console(False):
            print("[TRAY   ] console hidden - use the tray icon from here on.", flush=True)
    threading.Thread(target=_later, daemon=True).start()


def _icon_image():
    """A small green crosshair, drawn in code so there is no .ico to ship."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    green = (62, 207, 142, 255)
    d.ellipse([6, 6, 58, 58], outline=green, width=5)
    d.line([32, 0, 32, 20], fill=green, width=5)
    d.line([32, 44, 32, 64], fill=green, width=5)
    d.line([0, 32, 20, 32], fill=green, width=5)
    d.line([44, 32, 64, 32], fill=green, width=5)
    d.ellipse([27, 27, 37, 37], fill=green)
    return img


def start_tray():
    """System-tray icon: open either app, view the log, or quit everything.

    Runs on its own thread. Any failure here is swallowed — the servers keep
    running headless and you can still reach them in the browser."""
    if not TRAY_AVAILABLE:
        print("[TRAY   ] pystray/Pillow not installed - running without a tray icon.", flush=True)
        print("[TRAY   ] install with:  pip install pystray pillow", flush=True)
        return None

    def _open(url):
        return lambda icon, item: webbrowser.open(url)

    def _open_trades(icon, item):
        path = os.path.join(HERE, "logs", "Market Sniper Trade Log.xlsx")
        try:
            os.startfile(path)
        except Exception:
            webbrowser.open("file:///" + path.replace("\\", "/"))

    def _open_log(icon, item):
        path = os.path.join(HERE, "logs", "auto-sync.log")
        try:
            os.startfile(path)                    # Windows only; harmless elsewhere
        except Exception:
            webbrowser.open("file:///" + path.replace("\\", "/"))

    def _toggle_console(icon, item):
        _show_console(_console_hidden)

    def _quit(icon, item):
        icon.visible = False
        icon.stop()
        stop_all("quit from tray")

    menu = pystray.Menu(
        pystray.MenuItem("Open OPTIONS  (8000)", _open("http://127.0.0.1:8000"), default=True),
        pystray.MenuItem("Open FUTURES  (8010)", _open("http://127.0.0.1:8010")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open trade log (Excel)", _open_trades),
        pystray.MenuItem("View sync log", _open_log),
        pystray.MenuItem("Show / hide console", _toggle_console),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Market Sniper", _quit),
    )
    try:
        icon = pystray.Icon("market_sniper", _icon_image(), "MARKET SNIPER", menu)
        threading.Thread(target=icon.run, daemon=True).start()
        print("[TRAY   ] tray icon running - double-click it to open OPTIONS", flush=True)
        return icon
    except Exception as e:
        print("[TRAY   ] could not start tray (%s) - continuing without it." % e, flush=True)
        return None


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

    tray = start_tray()
    hide_console_when_ready(tray)

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
