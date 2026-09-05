# MARKET SNIPER — HANDOFF
Written 9/4/26 by a Claude session that **read this folder's actual source**
before writing a word of it. Earlier versions of this document lived in the
discord-sniper folder and kept getting reverted by that repo's AUTO PUSH, so
it lives here now, with the project it describes.

Read this first. Then read the code. Where this disagrees with the source,
**the source wins** — and fix this file.

Also read `PROJECT-STATUS.md` in this folder. Its standing rule at the top —
*sweep the class, not the instance* — governs everything below.

---

## Who you're working with

G (giancuffaro230@gmail.com). **Non-coder.** Trades options and futures live,
real money. Replies CONDENSED — short, direct, no fluff. Active voice. Own
mistakes plainly, then fix them.

* **"Fix everything is default always."** Bugs get fixed without asking.
* **Real-money actions are HIS ALONE**: placing/cancelling orders, funding,
  unlocking accounts, questionnaires, accepting ToS, passwords, generating
  API keys. Never do them, however explicitly he asks — set the form up and
  hand him the last click.
* **Never run git write commands from a sandbox.** AUTO PUSH holds the lock;
  on 9/4 even a read-only `git log` hung for two minutes. Use file operations.
* **Compile-check everything you touch.** Never break the build.
* `my-settings.json` holds every key. Never commit it, never print its values.

---

## What Market Sniper is

G's own tool, **127.0.0.1:8000** (futures app on 8010). His **manual** scalps
— he clicks, it fires. v4.0. FastAPI + `index.html`.

Its sibling is **Discord Sniper** (`C:\\Users\\Hulk\\Desktop\\discord-sniper`,
port 8787), which copies alert rooms automatically. **Both trade the SAME
Webull margin account `ENIQGUV4LUTT3JSAA9NKLDDU19` with the SAME app key.**
Copy code FROM there; never edit that folder from a session here.

---

## STATE OF PLAY — verified against the source, 9/4 evening

| | state |
|---|---|
| Market data off Webull | **DONE — leave it alone** |
| Ratchet matching G's rule | **WRONG — running retired 9/2 tiers** |
| Anti-clip DTE gate | **MISSING** |
| Bracket stop clamped under the bid | **MISSING** |
| Run-up / drawdown / greeks per trade | **NOT STARTED** |

**The market-data work is finished and finished well — do not redo it.**
`dxlink.py` is here verbatim from Discord Sniper. `marketdata.py` wraps
tastytrade + Tradier with the correct ordering (Tradier for plain stock
prices, tastytrade for option quotes and greeks — tastytrade's REST market
data 403s on this account even though its stream works). `my-settings.json`
has `feeds.tasty_client_secret`, `feeds.tasty_refresh_token` and
`feeds.tradier_token` set. `main.py`'s price path is **stream → free feeds →
Webull → Yahoo**, Webull demoted to third. `/api/feeds/test` reports the
tastytrade LEVEL and says in plain words what delayed data would mean.

That matters because **Webull allows 300 requests / 60s PER APP KEY**, shared
by three processes. On 9/4 the Discord Sniper log carried 1,052 throttle
events. Every quote this app no longer asks Webull for is budget handed back
to the bot's stops.

---

## JOB 1 — THE RATCHET IS RUNNING RETIRED RULES

`ratchet_tiers.py` here still has the **9/2 price tiers**. G retired those on
9/3. Right now his two tools manage the same account by different rules, and
neither matches what he asked for.

```
THIS FOLDER (retired)                 DISCORD SNIPER (correct)
  <$1.00  arm +25%, lock +10%, 15%      every premium:
  $1-2    arm +15%, lock BE,   10%        arm +10%, lock BE, +10% rungs
  $2+     arm +10%, lock +5%,   5%
```

His rule, verbatim, 9/3:

> "It was supposed to start all along from -10% and +10%. When it touched
> +10% the new stop becomes automatically 0%, and the next target is 20%.
> When 20% is touched the new stop is +10% and the new target is +30%. When
> +30% is touched the new stop is +20%, and so on and so forth."

**Fix:** copy `discord-sniper/ratchet_tiers.py` over this one — same file,
`TIERS = ((None, (10.0, 0.0, 10.0)),)`, tier rationale kept as history, both
safety floors and the futures helpers unchanged.

Effect: a sub-$1.00 0DTE currently must run **+25%** before its stop moves at
all. Under his rule it goes to breakeven at **+10%**.

Evidence the correct ladder works: 9/4, two NVDA 235C 0DTE entries ratcheted
0.95→1.11 and 0.93→1.10 and **both stopped out in profit, +$20 combined.**

## JOB 2 — ANTI-CLIP IS FIRING WHERE HE SAID IT MUST NOT

`webull_client.py:1186` calls `rt.anti_clip(locked, peak)` with **no DTE
gate.** His rule, 9/3: *"my rule on 0 and 1dte and anticlip on later
expirations."* Anti-clip is **OFF on 0/1DTE** — a 0DTE has no tomorrow, theta
eats whatever it does not lock, so his uncapped ladder takes the gain. From
2 days out the trade can breathe and the 40%-of-gain cushion applies.

**Most of his manual scalps are 0DTE, so this is firing on nearly every one.**

```python
if dte is None or dte >= 2:
    locked = rt.anti_clip(locked, peak)
```

This is `PROJECT-STATUS.md`'s own rule proving itself: the DTE gate was added
to Discord Sniper on 9/3 and the copy here was missed — exactly the failure
that table predicts. **Sweep for it: check every anti_clip call site, options
and futures.**

## JOB 3 — THE BRACKET STOP MUST BE CLAMPED UNDER THE LIVE BID

Nothing here clamps a born-with-the-order stop against the live bid.

Discord Sniper paid for this on 9/4 morning. INTC 94C bought at the caller's
0.95 into a wide spread while the live **bid was 0.83**. The stop leg was a
flat −10% of the fill = 0.86 — *above the bid* — so it was **already
triggered at birth and filled 308 milliseconds after the buy.** Guaranteed
loss, no trade in between. `place_stop()` had clamped under the live market
since 9/1, but the leg born WITH the entry never saw that clamp — and that is
the leg resting on nearly every trade.

**Fix:** before resting any stop, read the bid. If the computed stop sits at
or above it, rest one tick UNDER the bid and say so in the log. It can only
tighten the distance from the fill, never widen it. Source: the `STOP-BORN`
block in `discord-sniper/webull_options.py`. **Sweep every place a stop price
is computed, including futures.**

While in there, confirm these exist (copy from `webull_options.py` if not):

* **`stop_below()`** — a stop may never rest AT the fill. A 0.22 bid rounded
  its stop UP to the 0.20 fill and stopped out seven seconds after filling.
* **Breached stop = SELL, never re-anchor.** If the market is more than 10%
  BELOW the intended stop, refuse to rest a lower one and let the watchdog
  sell. A gap re-anchored a stop 0.75 → 0.40 and rode it to −59%.
* **`replace_stop`** — atomic stop moves. Cancel-then-place leaves a naked
  window, and the cancel is async: placing the new stop while the old one
  still rests is every "couldn't move the resting stop" 417.
* **Option SELL orders are DAY-only at Webull** — every resting stop dies at
  the close. Anything held overnight needs re-arming at 9:31.

## JOB 4 — RECORD THE SHAPE OF EVERY TRADE

`trade_log.py` has no run-up, drawdown or greeks fields. The MOD-1 feed work
means the data is already in reach; this is just recording it.

**Per closed trade:** `fill`, `exit`, `pl`, **`max_runup_pct`**,
**`max_drawdown_pct`**, `occ`, `side`, `strike`, `expiry`, `dte`, `swing`,
`stop_at_exit`, `why`, `state`, `greeks_in`, `greeks_out`.

Max drawdown is the one that matters: **how far did a WINNER go against me
before it worked.** Entry, exit and P&L can never answer that, and it is the
only honest basis for any "give trades more room" rule. G asked for this
explicitly — he wants weeks of it so the ratchet can be tuned on evidence
instead of anecdote.

**Greeks:** `marketdata.py` already reaches DXLink. Subscribe each open
contract, stamp `greeks_in` at entry and `greeks_out` at exit. First greeks
arrive **0.06s** after subscribing, so even a 20-second scalp gets them.
**Stamp entry greeks within the first 60s only** — after that they are not
entry greeks and calling them so is a lie in the record. Measured caveat:
dxfeed publishes Greeks slowly (1–2 events per 20s; no
`acceptAggregationPeriod` value changed it), so on a fast scalp `greeks_out`
will be the same snapshot as `greeks_in`. Store the event timestamp so a
redundant pair reads as one sample, not two.

**Two bugs Discord Sniper hit doing exactly this. Both cost a full day of
data. Do not repeat them:**

* The record sat inside `if not p_live` — **only pretend trades were ever
  written down.** 26 day files held 2 rows between them against 125 fills.
* Then, after that was fixed, it was still inside `... and price is not
  None` — so **every exit where the fill price is not known yet recorded
  nothing at all.** That is every hand close, which here is *most exits*.
  The day book ended 9/4 with zero trades while the journal counted eleven.

**A close must ALWAYS leave a row.** Write it with `exit`/`pl` NULL and a
`pending_price` flag when the price is unknown, then fill them in from the
broker's order list. **Null is honest. Zero is a lie. Missing is worse.**

This folder's `logs/trades.csv`-first-then-rebuild-the-xlsx design is already
the right shape — the CSV append always succeeds even with the workbook open.
Add the columns there.

**A bug in Discord Sniper — do not copy it:** `restore_state` pops
`hi_pct`/`lo_pct` on restart, so a position held across a restart loses its
run-up/drawdown history. Still unfixed there. Persist them here.

---

## THE COEXISTENCE CONTRACT — DO NOT BREAK THIS

Both tools trade the same Webull account with the same app key.

* Positions this app did not originate are the BOT's. Leave them alone.
* Positions the bot did not originate are G's. Discord Sniper marks them
  visible, never stop-manages them, never sells them, and leaves anything
  larger than 3 contracts entirely alone — because a room's "all out of SPY"
  must never sell his 30-lot. On 9/4 that worked: *"ADOPT left SPY x5 alone —
  bigger than anything the bot trades, so it's YOURS."*
* **Historic failure to avoid:** this app has sold bot positions before (FLR
  and SPY 766C, logged in `discord-sniper/HANDOFF.md`). Whatever it closes,
  it must be sure it opened.
* Pace every remaining Webull call: ≥0.20s between calls, back off 20s on a
  429, never poll orders/positions faster than 2–5s.

## THE SOURCE-OF-TRUTH RULE

```
positions -> ask the ACCOUNT
prices    -> ask the ORDER HISTORY
reasoning -> read the logs
...and NEVER substitute one for another.
```

Learned expensively on 9/4: Claude told G he was holding a 5-lot SPY
position. He wasn't — it had closed an hour earlier. The claim came from a
log line that was TRUE when written and FALSE when read. **A log is a
narrative in the past tense; it is not a statement of state.** Then,
compounding it, Claude saw two earlier adoptions of 2 and 3 lots, decided
2+3=5, and accused working code of inventing the position — the order history
showed a real, separate 5-lot trade. **The code was right. A tidy theory beat
a ten-second check.**

Discord Sniper has `now.py` / `WHAT DO I HOLD.bat` for this. This app should
get the same: broker first, book second, log never.

## WEBULL FACTS TO RESPECT

* Limits are **per endpoint, per app key**: option snapshot 60/min (20 symbols
  per call); Order Detail / Positions / Balance 2 per 2s.
* **429 = throttle. 417 = business rejection.** Different problems.
* **No option streaming on Webull at any price.** Fills ARE pushed (gRPC).
* **No MARKET orders on options.** Combos = MASTER(LIMIT) + STOP_LOSS on
  SINGLE only. OTO/OCO/OTOCO are stock-only.
* Ticks: SPY/QQQ/IWM $0.01 always; Penny Program $0.01 <$3 / $0.05 ≥$3;
  else $0.05/$0.10.
* ETF options trade to 16:15. 0DTE auto-exercises at $0.01 ITM — flatten
  before the close.
* **Webull's API has NO historical option prices.** Anything not recorded as
  it happens is gone forever.

## STILL UNPROVEN — do not trust until tested

* **Tradier OTOCO** (`place_conditional_entry` in `discord-sniper/tradier.py`)
  — the conditional "buy when the underlying touches X" order, and the main
  reason to want Tradier. **UNVERIFIED.** Prove it in Tradier's sandbox before
  it ever sees real money: a conditional order that silently does nothing, or
  fires twice, is the worst thing to discover live.
* **Tradier option quotes** — needs a live OCC symbol to exercise.
* **tastytrade REST market data** — 403s on this account. Use the stream.

## WHAT TO COPY FROM DISCORD SNIPER

`ratchet_tiers.py` (the correct ladder) · `webull_options.py` (`stop_below`,
`place_stop`'s STOP-BORN clamp, `replace_stop`, `_pace`, `tick_round`) ·
`positions.py` (the Book, watchdog, adopt/reconcile, the closed-trade row) ·
`now.py` · `HANDOFF.md` — **the living memory; read it for anything not
covered here.**
