# MARKET SNIPER · v3.7

One-tap 0DTE options execution for Webull (SPY/QQQ), plus a separate live
futures app (MNQ/MES) routing to Webull, NinjaTrader or Topstep.
Full instructions with pictures: **TUTORIAL.html**.

> **There is no paper mode.** v3.6 removed the Webull sandbox from both apps.
> Every order either app sends is real money, gated behind `ALLOW_LIVE=1`,
> which only the launcher sets.

## You never run git
`auto_sync.py` starts with the app and pushes every change by itself — no
UPDATE.bat, no manual push. It will not push code that fails to compile, and
never commits `my-settings.json`. Watch it work in `logs/auto-sync.log`.

## Daily use
Double-click **🎯 START MARKET SNIPER.bat** → BOTH apps start (options 8000 +
futures 8010, auto-updated from GitHub) → browser opens → jump between them
with the ⇄ buttons → the red ✕ inside either app shuts everything down.

## New computer (once)
1. Install Git (git-scm.com) and Python 3 (python.org, "Add Python to PATH")
2. `git clone https://github.com/giancuffaro/market-sniper.git`
3. Double-click **🧰 INSTALL.bat**, wait for VERIFIED
4. **🎯 START MARKET SNIPER.bat**

## The files
```
🎯 START MARKET SNIPER.bat  daily launcher (auto-update, starts BOTH apps)
🛑 STOP EVERYTHING.bat      emergency shutdown (red ✕ in-app does it too)
auto_sync.py               watches the folder, commits + pushes on its own
🧰 INSTALL.bat                one-time setup (SDK + dependencies)
CHECK-SETUP.bat            health report
TUTORIAL.html              the complete illustrated guide (Save-as-PDF button)
main.py / webull_client.py / config.py / index.html / quotes.py    options app
futures_app.py / futures_client.py / futures_index.html            futures app
tape.py                    market velocity, shared by both apps
requirements.txt           dependencies
PROJECT-STATUS.md          build history / state
```

## Costs
$4.99/mo OPRA options data (required) + normal Webull contract fees.

## Key facts
- Orders are marketable LIMITs (Webull forbids market orders on options).
- The execute button buys 3 strikes IN the money by default (ITM3). Depth counts
  strikes, not dollars. Change it under STRIKE SELECTION in settings.
- The ARM trigger is still the nearest whole dollar; only the strike changed.
- The options screen previews where a MY CONFIG entry will fire (underlying
  trigger level + the strike you'd hold) before you press anything.
- VELOCITY on both screens is bar velocity vs the last half hour, not
  trades-per-second — the feed is 1-minute bars. See tape.py.
- Futures routes: WEBULL (real money), NINJA (NinjaTrader ATI), TOPSTEP.
- The 3:40 PM ET new-trade cutoff is OUR safety buffer, not a broker rule —
  change ENTRY_CUTOFF in config.py to trade until 4:00 PM.
- One API key usually sees ALL your accounts; pick at login (full details shown).
- Futures accounts are labeled "MARGIN" by Webull's API; the options app hides
  them from the picker entirely (config.FUTURES_ACCOUNT_SUFFIXES).
- Server-side safety: MAX_CONTRACTS, DAILY_LOSS_LIMIT (config.py).
```
