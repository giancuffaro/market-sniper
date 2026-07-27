# MARKET SNIPER · v3.0

One-tap 0DTE options execution for Webull (SPY/QQQ), plus a separate paper
futures app (MNQ/MES). Full instructions with pictures: **TUTORIAL.html**.

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
🔄 UPDATE.bat               manual update pull (rarely needed)
🧰 INSTALL.bat                one-time setup (SDK + dependencies)
CHECK-SETUP.bat            health report
TUTORIAL.html              the complete illustrated guide (Save-as-PDF button)
main.py / webull_client.py / config.py / index.html / quotes.py    options app
futures_app.py / futures_client.py / futures_index.html            futures app
requirements.txt           dependencies
PROJECT-STATUS.md          build history / state
```

## Costs
$4.99/mo OPRA options data (required for LIVE) + normal Webull contract fees.

## Key facts
- Orders are marketable LIMITs (Webull forbids market orders on options).
- The 3:40 PM ET new-trade cutoff is OUR safety buffer, not a broker rule —
  change ENTRY_CUTOFF in config.py to trade until 4:00 PM.
- One API key usually sees ALL your accounts; pick at login (full details shown).
- Futures accounts are labeled "MARGIN" by Webull's API; the app corrects known
  ones (config.FUTURES_ACCOUNT_SUFFIXES) and redirects to the futures app.
- PAPER + saved account = real market data with simulated fills.
- Server-side safety: MAX_CONTRACTS, DAILY_LOSS_LIMIT (config.py).
```
