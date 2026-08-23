# MARKET SNIPER — Project Status
Current version **v3.9** (renamed from EZEXECUTION → Option Sniper → Market Sniper)

## Proven with real money
- LIVE connect, multi-account picker (stacked rows w/ full ID + buying power)
- Real OPRA option quotes · first live trade FILLED (QQQ 694P @ 2.15, price improvement)

## Built, running
- Marketable-limit orders (Webull forbids MARKET on options)
- True-fill sync (corrects entry to real avg fill) · TP/SL incl. whole-number + spot-level
- Mirror trading (own 2nd account) · auto-lock + LOCK · themes · saved key profiles
- Entry preview: shows the underlying trigger level + resolved strike before you ARM
- VELOCITY strip on both apps (tape.py)
- Futures app (MNQ/MES, trailing stop) — separate, port 8010

## v3.9 changes

**One tab only.** Every launch opened a new tab and the old ones piled up - and a
stale tab keeps polling a live trading account, which is worse than untidy. A new
tab announces itself on a BroadcastChannel; older ones stop their timers first,
then close. Chrome refuses `close()` on tabs it did not open by script, so if
that is blocked the old tab blanks itself and says "safe to close". Either way it
stops talking to the broker. Timestamps are tie-broken by id, otherwise two tabs
opened in the same millisecond would kill each other and leave none.

**A restarted server no longer leaves a tab spinning.** `/api/state` answers
"not connected" after a restart, and the old code polled it once a second
forever - that was the wall of 400s. Two consecutive misses now stop the timers
and return to the login screen.

**No more quoting a closed market.** On a Saturday the options app asked Webull
for a 0DTE chain every 5s and got a 400 each time. `refreshQuote` now checks the
velocity reading first (which already knows the tape is dead) and shows
MARKET CLOSED on the buy buttons instead. Velocity is fetched and awaited before
the first quote, or the opening request still went out.

**pip was installing into the WRONG Python.** `call .venv\Scripts\activate` did
not reliably put the venv first on PATH, so bare `pip` resolved to the system
Python 3.14 - pystray installed successfully into a Python the app never uses,
which is why the tray kept reporting itself missing. Every pip/python call in the
launcher now goes through `.venv\Scripts\python.exe` explicitly, and the tray
install is verified by import rather than trusted by exit code.

**Auto-sync heals stale git locks.** A crashed git leaves HEAD.lock behind and
every later command fails identically forever; the old code logged the same error
every 60s and never tried to clear it. If a command fails with a lock error the
lock is stale by definition - our own git just failed because of it - so it is
removed and the command retried once. Locks under 30s old are left alone so a
genuinely running git is never stomped.

**Tray icon + hidden launcher.** `run_all.py` optionally shows a system-tray
crosshair (open either app, view log, quit). `START HIDDEN (tray only).vbs` runs
the whole thing with no window. Tray deps are optional and kept OUT of
requirements.txt, which the launcher runs on every start.

**JS syntax gate in the suite.** These pages hold ~40KB of hand-edited
JavaScript; a stray bracket would blank the page with the error only in the
browser console. `node --check` now parses every script block.

Suite: 149 checks across 16 scenarios.

## v3.8 changes

**Stay logged into every broker at once.** The futures app held ONE session, so
picking a different broker logged you out of the last one. It now keeps a live
session per broker (`SESSIONS` keyed by mode) with `ACTIVE` deciding only which
one the buttons act on. New: `GET /api/sessions`, `POST /api/switch`,
`POST /api/disconnect {mode}` for one or all.

The trap this had to avoid: `refresh_mark()` is what evaluates TP, SL and the
trailing stop. Refreshing only the visible session would have meant switching
tabs silently stopped managing a live position on the broker you left. So
`_refresh_all()` ticks EVERY connected session on every poll. A position on an
inactive broker keeps its brackets.
UI: mode buttons get a green dot when logged in, amber when holding, and a
strip lists each broker with its position and P&L.

**Velocity no longer reads VIOLENT with the market shut.** The closing auction
is always a volume spike, so the last live minute of the week scored 100 and the
strip froze there all weekend. `velocity()` now checks how old the newest closed
bar is; past 10 minutes nothing is printing, so it reports CLOSED / 0 with the
reason. Asks the data rather than a market calendar, so it covers weekends,
holidays, halts and futures maintenance breaks alike. `compute()` stays pure.

**Fixed: open-position strike was truncated too.** `pos.strike|0` fed BOTH the
hero line and the CLOSE button, so a TSLA 332.5C showed as 332C while you held
it — including on the close confirmation.

**Browser autofill was overwriting the Topstep username.** The Webull fields had
`autocomplete="off"`; the Topstep and NinjaTrader ones never did, so Chrome kept
stuffing the saved email over the loaded username. This was a SECOND cause on
top of the persistence bug, which is why fixing persistence alone did not stop
it. NinjaTrader had `value="Sim101"` hardcoded in the markup, fighting the saved
account on every load. Both fixed.

**Regression suite (`test_all.py`).** 93 checks across 12 scenarios, every one
tied to a bug we actually hit. Runs against a copy of my-settings.json and
restores it. Secrets are redacted in output — a failing assertion once printed a
real API key.

## v3.7 changes

**Saved logins actually save now (both apps).** Three separate bugs made the
apps forget everything between sessions:

1. `wb_key` / `wb_sec` were in NO save list at all — the Webull futures key
   could never persist, no matter what you ticked.
2. `remember_login` defaulted to FALSE when absent, so every write ran the
   "forget my secrets" branch and DELETED saved keys. Absent meant "never
   chosen", not "no". It now defaults to remember; only an explicit untick wipes.
3. The remember checkbox only rendered in TOPSTEP mode, so in WEBULL and NINJA
   there was no way to opt in and the default did the wiping.

Also: fields saved only on a SUCCESSFUL connect, so a failed login threw away
what you had just typed — and you retyped it to try again. Every field now
saves as you type, 500ms debounced.

**Options key profiles moved from browser to disk.** They lived only in
localStorage, so clearing the browser or opening a different one lost every
account. They are in `my-settings.json` now, with localStorage kept in step as a
first-paint fallback. Existing browser profiles migrate to disk automatically on
first load.

**Show keys instead of dots.** Both apps, on by default, remembered per app.
Secrets are plain text in `my-settings.json` — gitignored, never leaves the PC.

**Buy buttons show what the trade costs.** `STRIKE 711 - $3.00 - $3,000`.
ITM3 contracts run ~3x an OTM1, so contract count stopped being a useful proxy
for what you are about to spend.

**Fixed: strike display truncated.** The buttons used `strike|0`, which showed
TSLA's 332.5 strike as "332" — a strike that does not exist. Orders were always
correct; only the display lied, on the screen you press. SPY/QQQ hid it because
their steps are whole dollars.

## v3.7 changes (earlier)

**Execute button now buys 3 strikes IN the money.** `pick_strike` is generalised
to any ITM/OTM depth (`ITM1..ITM20`, `OTM1..OTM20`); default is `ITM3`.
Depth counts STRIKES, not dollars — 3 deep is $3 on SPY/QQQ (step 1.0) but
$7.50 on TSLA (step 2.5). QQQ at 724: CALLS take the 721, PUTS take the 727.
The ARM trigger is UNCHANGED — still the nearest whole dollar. Only the
contract bought when it fires has moved.
`my-settings.json` was migrated from OTM1, otherwise the saved file would have
silently overridden the new default and nothing would have changed.

**Auto-sync (`auto_sync.py`).** Watches the folder, and every change is
committed and pushed on its own. No push, no UPDATE.bat, no git. Guards:
- refuses to push anything that does not compile (syntax-checks every .py first)
- never stages `my-settings.json` — gitignored AND explicitly unstaged, because
  one of those being wrong would publish API keys
- sweeps stray `webull_*.log` files off the root into `logs/` each cycle
- 4s debounce, so saving five files is one commit
- on push failure, work stays committed locally and it retries every 60s

**Launcher no longer destroys unpushed work.** It used to `git reset --hard`
unconditionally, which would discard any commit auto-sync had made but not yet
pushed (offline, GitHub down, expired credentials). It now pushes first, and
skips the reset entirely if that push fails.

**Folder cleaned.** All SDK logs moved to `logs/`. `PLAN-v3.6.md`,
`PUSH v3.6.bat`, `UPDATE.bat` and `Market-Sniper.html` retired to `_archive/`
(gitignored — kept locally as a safety copy, delete whenever).
`CHECK-SETUP.bat` and `INSTALL.bat` kept: both are diagnostics, not one-offs.

## v3.6 changes — LIVE-ONLY BUILD
**No paper mode anywhere.** Removed Webull sandbox from both apps and Tradovate
from futures. `ALLOW_LIVE=1` (launcher-set) is now the single gate on every
order either app can send; no session class can skip it.

- Options: `PaperSession` deleted, `make_session()` always LIVE. `paper` field
  still accepted and ignored on /api/connect so a stale browser tab can't 422.
- Options: futures accounts are FILTERED OUT of the picker rather than labelled.
  Guard kept for the by-ID case, so a futures-only key still gets an explanation
  instead of an empty list.
- Futures: `TradovateSession` + `_tv_req` + `_TV_BASE` deleted. `_tv_front_symbol`
  and `_fmt_px` KEPT — despite the names, Topstep and NinjaTrader both call them.
- Futures modes are now WEBULL / NINJA / TOPSTEP. "LIVE" used to mean NinjaTrader
  while "WEBULL" meant Webull-live, which read backwards. `normalize_mode()`
  maps the old "LIVE" onto NINJA so pre-v3.6 saved prefs still log in.
- `config.SANDBOX_*` endpoints deleted.
- New `/api/preview` (options) and `/api/tape` (both). Both read-only.

## New this version
**Entry preview** — under the trade buttons, live: `QQQ 719.40 → triggers at
719.00 (0.40 below)` plus the CALL/PUT strikes you'd hold. `arm()` and
`preview_entry()` share `entry_target()`, so the previewed number cannot drift
from the number that fires. Rounding behaviour UNCHANGED (nearest whole dollar)
— preview first, decide later whether it should round directionally.

**Velocity (tape.py)** — volume rate + range expansion over the last 5 minutes
vs the previous 30, blended 60/40, scored 0-100 where 50 = moving exactly as
fast as it has been. Trailing baseline, so it self-corrects for the session's
natural U-shape. States: calm / normal / fast / violent.
Honest limit: this is BAR velocity, NOT trades-per-second — the only feed
either app has is Yahoo 1-minute bars. Swap `tape._bars()` for a tick feed to
upgrade; nothing else changes.
Bug found and fixed during testing: Yahoo publishes the current minute with
price but zero volume, which pinned acceleration at -100% on every symbol.
Trailing zero-volume bars are now trimmed.

## Awaiting first live confirmation
First live CLOSE · fill-sync banner · TP/SL live fire · mirror live
· entry preview vs real fills · velocity readings across a full session

## Next candidates
gRPC fill-event stream · real tick tape (replaces the bar proxy)
· directional rounding for armed entries (calls floor / puts ceil) if the
  preview shows nearest-whole firing in the wrong place
· the deferred alert layer on top of velocity

## Facts
3:40 PM cutoff is ours (config ENTRY_CUTOFF), exchanges close options 4:00 PM ET.
Costs: $4.99/mo OPRA required.
The launcher does `git reset --hard origin/main` — anything not pushed to
GitHub is WIPED on next start. Commit and push, always.
