# MARKET SNIPER — Project Status
Current version **v3.6** (renamed from EZEXECUTION → Option Sniper → Market Sniper)

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
