"""
AUTO-SYNC — watches this folder and pushes every change to GitHub by itself.

You never run git again. Save a file, wait a few seconds, it is on GitHub.

WHY THIS EXISTS
    The launcher does `git reset --hard origin/main` on every start, which means
    GitHub is the source of truth: anything not pushed gets wiped on next launch.
    Rather than remember to push, this keeps GitHub continuously matching disk.

WHAT IT WILL NOT DO
    - It will never commit my-settings.json (your API keys live there). That file
      is gitignored, and this script additionally refuses to stage it. Two locks,
      because one of them being wrong would publish your keys.
    - It will never push code that does not compile. Every .py is syntax-checked
      first; if anything is broken it waits and retries instead of pushing a
      build that will not start. You cannot break the app for future-you by
      saving half a thought.
    - It will never touch logs, the venv, or __pycache__.

HOW IT DECIDES SOMETHING CHANGED
    Polls name+size+mtime every 2s. No dependencies — watchdog would be nicer but
    this folder is ~20 files and polling it is free. After a change it waits for
    4 seconds of quiet before acting, so saving five files in a row is one commit
    rather than five.

RUN IT
    python auto_sync.py            # watch forever (the launcher does this)
    python auto_sync.py --once     # single sync then exit, for testing
    python auto_sync.py --status   # what would happen, changes nothing
"""

import os
import re
import ast
import sys
import time
import subprocess
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
LOG_FILE = os.path.join(LOG_DIR, "auto-sync.log")

POLL_SECONDS = 2.0        # how often we look
QUIET_SECONDS = 4.0       # how long the folder must be still before we commit
RETRY_SECONDS = 60.0      # after a failed push, how long before trying again
BRANCH = "main"
REMOTE = "origin"

# Nothing in here is ever watched or committed.
SKIP_DIRS = {".git", ".venv", "__pycache__", "logs", "_archive", "data", "node_modules"}
SKIP_EXACT = {"my-settings.json"}          # secrets — belt AND braces with .gitignore
SKIP_PATTERNS = (
    re.compile(r"\.log$"),
    re.compile(r"\.log\."),                 # rotated SDK logs
    re.compile(r"\.pyc$"),
    re.compile(r"^_probe"),
)


def _skip(name):
    if name in SKIP_EXACT:
        return True
    return any(p.search(name) for p in SKIP_PATTERNS)


def log(msg):
    line = "%s  %s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        # Keep the log from growing forever; 1 MB is thousands of syncs.
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1_000_000:
            os.replace(LOG_FILE, LOG_FILE + ".old")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass          # logging must never be the thing that breaks the sync


# git's own words when a leftover lock is in the way.
_LOCK_ERRORS = ("cannot lock ref", "unable to create", "index.lock",
                "another git process", "file exists")

# On a lock error we clear locks older than this. Our command JUST failed
# because the lock was already there, so anything even seconds old is stale;
# 30s is well beyond how long a healthy git holds one, so this cannot stomp on
# a genuinely running command.
LOCK_STALE_ON_ERROR = 30


def _looks_like_lock_error(out):
    low = (out or "").lower()
    return any(w in low for w in _LOCK_ERRORS)


def _force_clear_locks(min_age=LOCK_STALE_ON_ERROR):
    """Delete leftover *.lock files under .git. Returns what it removed."""
    gitdir = os.path.join(HERE, ".git")
    cleared = []
    try:
        for root, dirs, files in os.walk(gitdir):
            if os.path.basename(root) in ("objects", "modules", "lfs"):
                dirs[:] = []
                continue
            for fn in files:
                if not fn.endswith(".lock"):
                    continue
                path = os.path.join(root, fn)
                try:
                    if time.time() - os.path.getmtime(path) >= min_age:
                        os.remove(path)
                        cleared.append(os.path.relpath(path, HERE))
                except OSError:
                    pass
    except OSError:
        pass
    return cleared


def _run_git(args, timeout):
    try:
        p = subprocess.run(("git", "-c", "core.quotePath=false") + tuple(args),
                           cwd=HERE, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return p.returncode == 0, p.stdout.decode("utf-8", "replace").strip()
    except subprocess.TimeoutExpired:
        return False, "git %s timed out" % (args[0] if args else "?")
    except FileNotFoundError:
        return False, "git is not installed or not on PATH"


def git(*args, timeout=120):
    """Run a git command, healing a stale lock if one blocks it.

    core.quotePath=false stops git octal-escaping non-ASCII filenames (the
    launcher's name starts with an emoji).

    The self-heal matters: a crashed git leaves HEAD.lock behind and EVERY
    later command fails identically forever. The old code just logged the same
    error every 60s and never tried to clear it. If a command fails with a lock
    error, the lock is stale by definition — our git just failed because of it —
    so we remove it and retry once.
    """
    ok, out = _run_git(args, timeout)
    if ok or not _looks_like_lock_error(out):
        return ok, out

    cleared = _force_clear_locks()
    if not cleared:
        return ok, out                       # nothing we could remove; report honestly
    log("cleared stale git lock(s) and retrying: %s" % ", ".join(cleared))
    return _run_git(args, timeout)


def snapshot():
    """name+size+mtime for everything we care about. Cheap and good enough."""
    out = {}
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if _skip(fn):
                continue
            path = os.path.join(root, fn)
            try:
                st = os.stat(path)
                out[path] = (st.st_size, int(st.st_mtime))
            except OSError:
                pass
    return out


def sweep_logs():
    """Move stray SDK logs out of the root and into logs/.

    The Webull SDK writes its log wherever the app was started from, so they
    keep reappearing in the folder root no matter how often you tidy. This
    quietly relocates them every cycle. A log the running app still has open
    cannot be moved on Windows — that is expected, and it gets swept on a later
    pass once the app releases it.
    """
    moved = 0
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        for fn in os.listdir(HERE):
            if not (fn.startswith("webull_") and ".log" in fn):
                continue
            src = os.path.join(HERE, fn)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(LOG_DIR, fn)
            try:
                if os.path.exists(dst):          # same name, different run
                    dst = os.path.join(LOG_DIR, "%s.%d" % (fn, int(time.time())))
                os.replace(src, dst)
                moved += 1
            except OSError:
                pass                             # in use — try again next cycle
    except OSError:
        pass
    if moved:
        log("tidied %d log file(s) into logs/" % moved)


def clear_stale_lock():
    """Clear ANY stale git lock, not just index.lock.

    git locks more than the index: HEAD.lock while moving the ref, config.lock,
    refs/heads/<branch>.lock, packed-refs.lock. The first version of this only
    swept index.lock, so a crashed commit left HEAD.lock behind and every later
    sync failed with "cannot lock ref 'HEAD'" — visible but unfixable, because
    the one thing that could clear it was not looking for it.

    Only removes locks over a minute old; anything younger may belong to a git
    command genuinely running right now.
    """
    gitdir = os.path.join(HERE, ".git")
    cleared = []
    try:
        for root, dirs, files in os.walk(gitdir):
            # refs/ and the top level are where locks live; skip the big ones.
            if os.path.basename(root) in ("objects", "modules", "lfs"):
                dirs[:] = []
                continue
            for fn in files:
                if not fn.endswith(".lock"):
                    continue
                path = os.path.join(root, fn)
                try:
                    if time.time() - os.path.getmtime(path) > 60:
                        os.remove(path)
                        cleared.append(os.path.relpath(path, HERE))
                except OSError:
                    pass
    except OSError:
        pass
    if cleared:
        log("cleared stale git lock(s): %s" % ", ".join(cleared))


def python_files_compile():
    """(ok, problem). Every .py in the folder root must parse."""
    for fn in sorted(os.listdir(HERE)):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(HERE, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                ast.parse(f.read(), filename=fn)
        except SyntaxError as e:
            return False, "%s line %s: %s" % (fn, e.lineno, e.msg)
        except Exception as e:
            return False, "%s: %s" % (fn, e)
    return True, None


def unstage_secrets():
    """Last line of defence. If a secret file ever reaches the index, drop it."""
    ok, staged = git("diff", "--cached", "--name-only")
    if not ok:
        return
    for name in staged.splitlines():
        base = os.path.basename(name.strip())
        if base in SKIP_EXACT or _skip(base):
            git("restore", "--staged", "--", name.strip())
            log("REFUSED to commit %s (secret or log)" % name.strip())


def describe(staged_names):
    """A commit subject that says what actually changed."""
    names = [os.path.basename(n.strip().strip('"')) for n in staged_names if n.strip()]
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    if not names:
        return "auto: sync %s" % stamp
    if len(names) <= 3:
        return "auto: %s (%s)" % (", ".join(names), stamp)
    return "auto: %s and %d more (%s)" % (", ".join(names[:3]), len(names) - 3, stamp)


def sync(reason="change"):
    """One full commit+push cycle. Returns True if the remote now matches disk."""
    sweep_logs()
    clear_stale_lock()

    ok, _ = git("rev-parse", "--git-dir")
    if not ok:
        log("not a git repo — nothing to sync")
        return False

    ok, err = python_files_compile()
    if not ok:
        log("HOLDING - python does not compile: %s" % err)
        log("        nothing pushed. Fix it and this will go automatically.")
        return False

    # Must be checked. A failing `git add` used to slip through silently and the
    # run would then report success while having committed nothing at all —
    # exactly the sort of quiet no-op this script exists to prevent.
    ok, out = git("add", "-A")
    if not ok:
        log("git add failed: %s" % out.splitlines()[0][:200] if out else "git add failed")
        if "index.lock" in out:
            log("        a stale lock is blocking git. It gets cleared "
                "automatically once it is over a minute old.")
        return False

    unstage_secrets()

    ok, staged = git("diff", "--cached", "--name-only")
    if not ok:
        log("could not read the staged file list: %s" % staged[:200])
        return False
    staged_names = [n for n in staged.splitlines() if n.strip()]

    if staged_names:
        ok, out = git("commit", "-q", "-m", describe(staged_names))
        if not ok:
            log("commit failed: %s" % out[:300])
            return False
        log("committed %d file(s): %s" % (len(staged_names),
                                          ", ".join(os.path.basename(n) for n in staged_names[:6])))

    # Anything local that the remote has not got yet (including earlier commits
    # made while the network was down).
    ok, ahead = git("log", "--oneline", "%s/%s..HEAD" % (REMOTE, BRANCH))
    if not ok:
        log("could not compare against %s/%s: %s" % (REMOTE, BRANCH, ahead[:200]))
        return False
    if not ahead.strip() and not staged_names:
        return True                                  # already in sync

    ok, out = git("fetch", REMOTE, BRANCH)
    if not ok:
        log("fetch failed (offline?): %s" % out[:200])
        return False

    # Rebase rather than merge so history stays a straight line and the
    # launcher's hard-reset can never land on a merge commit.
    ok, out = git("pull", "--rebase", REMOTE, BRANCH)
    if not ok:
        log("pull --rebase failed: %s" % out[:300])
        git("rebase", "--abort")
        log("        rebase aborted, your work is untouched. Will retry.")
        return False

    ok, out = git("push", REMOTE, BRANCH)
    if not ok:
        log("PUSH FAILED: %s" % out[:300])
        log("        work is committed locally and safe. Will retry.")
        return False

    log("pushed to GitHub - remote now matches your folder")
    return True


def status():
    ok, br = git("rev-parse", "--abbrev-ref", "HEAD")
    print("branch      :", br if ok else "?")
    ok, ahead = git("log", "--oneline", "%s/%s..HEAD" % (REMOTE, BRANCH))
    print("unpushed    :", len([l for l in ahead.splitlines() if l.strip()]) if ok else "?")
    ok, dirty = git("status", "--short")
    print("uncommitted :", len([l for l in dirty.splitlines() if l.strip()]) if ok else "?")
    good, err = python_files_compile()
    print("compiles    :", "yes" if good else "NO — " + str(err))
    print("watching    :", len(snapshot()), "files")


def watch():
    log("=" * 60)
    log("AUTO-SYNC started - watching %s" % HERE)
    log("every change is committed and pushed automatically. no git needed.")
    log("=" * 60)

    sync("startup")

    previous = snapshot()
    changed_at = None
    failed_at = None

    while True:
        time.sleep(POLL_SECONDS)
        try:
            current = snapshot()
        except Exception as e:
            log("scan error: %s" % e)
            continue

        if current != previous:
            previous = current
            changed_at = time.time()
            continue

        now = time.time()
        due_change = changed_at and (now - changed_at) >= QUIET_SECONDS
        due_retry = failed_at and (now - failed_at) >= RETRY_SECONDS

        if due_change or due_retry:
            changed_at = None
            failed_at = None if sync("retry" if due_retry else "change") else now


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--once":
        sys.exit(0 if sync("manual") else 1)
    elif arg == "--status":
        status()
    else:
        try:
            watch()
        except KeyboardInterrupt:
            log("auto-sync stopped")
