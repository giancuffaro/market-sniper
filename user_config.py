"""
MARKET SNIPER — settings that live on YOUR computer.

Everything you switch on in the app (Take Profit, Stop, Trailing, Round-Number
Entry, your strategies, the theme, which broker you connect to) is written to
my-settings.json in this folder.

Why a file instead of the browser: the browser forgets things. It forgets if you
clear history, if you open the app in a different browser, or if you visit
"localhost" one day and "127.0.0.1" the next. A file doesn't. It also survives
UPDATE.bat, because my-settings.json is in .gitignore — git never touches it,
so pulling a new version of the app can't wipe your setup.

This file is on your machine only. It is never committed and never uploaded.
"""

import json
import pathlib
import tempfile
import os

CONFIG_PATH = pathlib.Path(__file__).parent / "my-settings.json"


def load_all():
    """Read the whole config. A missing or damaged file is not an error —
    you just start from defaults."""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load(section, default=None):
    """Read one section, e.g. load("futures_settings", {})."""
    v = load_all().get(section)
    if v is None:
        return {} if default is None else default
    return v


def save(section, value):
    """Write one section and flush it to disk right away.

    Writes to a temp file first and then renames, so a crash mid-write can
    never leave you with a half-written config. Never raises: if the folder
    is read-only, the app keeps working, you just won't get persistence.
    """
    cfg = load_all()
    cfg[section] = value
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(CONFIG_PATH.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_PATH)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
    except Exception:
        pass
    return value


def where():
    """The path, for showing the user where their settings live."""
    return str(CONFIG_PATH)
