# MARKET SNIPER — Project Status
Current version **v3.0** (renamed from EZEXECUTION → Option Sniper → Market Sniper)

## Proven with real money
- LIVE connect, multi-account picker (stacked rows w/ full ID + buying power)
- Real OPRA option quotes · first live trade FILLED (QQQ 694P @ 2.15, price improvement)

## Built, running
- Marketable-limit orders (Webull forbids MARKET on options)
- True-fill sync (corrects entry to real avg fill) · TP/SL incl. whole-number + spot-level
- Mirror trading (own 2nd account) · auto-lock + LOCK · themes · saved key profiles
- Futures paper app (MNQ/MES, trailing stop) — separate, port 8010
- Futures account (…3T0B) auto-flagged FUTURES ⚠ and redirected (config.FUTURES_ACCOUNT_SUFFIXES)

## v3.0 changes
Single launcher (unblock merged in), INSTALL.bat, one merged TUTORIAL.html,
labels API KEY/SECRET, LIVE-warning removed, LIVE default, name = MARKET SNIPER.

## Awaiting first live confirmation
First live CLOSE · fill-sync banner · TP/SL live fire · mirror live

## Next candidates
gRPC fill-event stream · futures LIVE (needs approval + $228/mo CME + wiring)

## Facts
3:40 PM cutoff is ours (config ENTRY_CUTOFF), exchanges close options 4:00 PM ET.
Costs: $4.99/mo OPRA required.
