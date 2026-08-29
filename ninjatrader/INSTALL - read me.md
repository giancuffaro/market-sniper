# MarketSniperTrend — NinjaTrader 8 indicator

The same trend logic as the Sniper's `DIRECTION` panel, running on your
NinjaTrader charts. Three signals, and it only calls a trend when they agree.

## Installing — copy the file, do not use Strategy Builder

**Strategy Builder is the wrong tool.** It is a visual wizard that writes code
for you, and there is nowhere in it to paste a file. Ignore it entirely.

Copy the files instead. NinjaTrader reads whatever is in these folders:

```
Documents\NinjaTrader 8\bin\Custom\Indicators\MarketSniperTrend.cs
Documents\NinjaTrader 8\bin\Custom\Strategies\MarketSniperRatchet.cs
```

1. Open `Documents\NinjaTrader 8\bin\Custom\` in File Explorer.
2. **COPY, do not drag.** Right-click the file → Copy, then paste into the
   folder. Desktop and Documents are both on C:, and a drag between two places
   on the SAME drive is a MOVE — the file leaves the Market Sniper folder.
   (Ctrl held while dragging also copies.)
   - `MarketSniperTrend.cs` → `Indicators`
   - `MarketSniperRatchet.cs` → `Strategies`
3. In NinjaTrader, **Control Center → New → NinjaScript Editor**.
   Use the menu, not F11: function keys only fire when the Control Center has
   focus, and some laptops need Fn+F11.
4. In the editor, **right-click → Compile**. You do not need to open the files
   — compiling does the whole folder.
5. The bottom of the editor says "Compile successful" or lists errors.
   Send me the errors if there are any.

Then they show up like any built-in:

- **Indicator** — right-click a chart → Indicators → `MarketSniperTrend`
- **Strategy** — Control Center → **Strategies** tab → right-click →
  **New Strategy** → `MarketSniperRatchet`

## Reading it

A single plot in its own panel:

| Value | Meaning | Colour |
|---|---|---|
| **+1** | up — at least two signals agree, none disagree | green |
| **0** | chop | grey |
| **−1** | down | red |

**Chop is an answer, not a failure to decide.** A 2-1 split means the market is
arguing with itself, and that's the reading that keeps you out of the trade.

## The three signals

- **Slope** — is the 21-EMA actually *rising*, and is price on the right side
  of it? Both required. Measured in bar-ranges per bar, not in points or
  percent, so the same threshold works on MNQ, MES and any timeframe.
- **Structure** — higher highs **and** higher lows from swing pivots. Higher
  highs with lower lows is a widening range, not an uptrend.
- **Volume** — is volume arriving on up-bars or down-bars? Each bar is capped
  at 12× the window median first, so one bad print can't decide the vote.

## Sending data back to the Sniper

The Sniper's order link to NinjaTrader is **one-way** — it drops `oif_*.txt`
files into the `incoming` folder and NinjaTrader executes them. Nothing comes
back. That's why position and P&L on the futures screen are the app's own
estimate rather than NinjaTrader's truth.

Switching **Export state to file** on closes that gap for data. The indicator
writes its reading to a small text file every 15 seconds:

```
instrument=MNQ
timeframe=1 Minute
state=up
score=3
slope_vote=1
slope=0.184
structure_vote=1
volume_vote=1
up_share=61.4
price=20184.25
bar_time=2026-08-26 13:45:00
written=2026-08-26 13:45:07
```

It writes to a temp file and moves it into place, so the Sniper can never read
a half-written line. Any file error is swallowed — a charting indicator must
not be able to fail because a text file was locked.

Nothing in the Sniper reads this file yet. Tell me when you've got the
indicator compiling and I'll wire the futures screen to it.

## Keeping the two in step

The thresholds are duplicated in `trend.py` and in the `.cs` file, because
NinjaScript can't call Python. **If you change one, change the other** — the
constants are at the top of both files under the same names. Two copies of a
number drift apart unless someone is deliberate about it.


---

# MarketSniperRatchet — the ratchet, running inside NinjaTrader

Same rungs as the futures app, but the stop is a **real order at the
exchange**. It survives Market Sniper being closed, the browser being closed,
and the laptop going to sleep.

## Why you want this

The Sniper's link to NinjaTrader is one-way — it fires order files and hears
nothing back. So the ratchet ran *in the app*, on a one-second poll, and only
while the app was open. That leaves two real holes:

- a limit resting overnight can fill at Sunday's 18:00 ET reopen with nothing
  managing it
- closing the laptop mid-trade leaves the position unprotected

This closes both.

## Installing it

Already done if you copied both files above and pressed F5. To turn it on:

1. Control Center → **Strategies** tab → right-click → **New Strategy**.
2. Pick `MarketSniperRatchet`.
3. Set **Account**, **Instrument** = MNQ, **Step (points)** = `12.5`.
4. Check **Start behavior** = `Adopt account position`. It is the default in
   the code, but confirm it — without it the strategy ignores any position it
   did not open itself, which is every position, because it never opens one.
5. Tick **Enabled**.

## What it does, and only this

**It never enters a trade.** It watches whatever the account already holds —
opened by hand, by the Sniper, by anything — and keeps a stop behind it:

| best excursion | stop sits at |
|---|---|
| 0 | entry − 12.5 |
| +12.5 | breakeven |
| +25 | entry + 12.5 |
| +37.5 | entry + 25 |

The stop only moves in your favour. Never widened, never pulled back, never
cancelled while you're in.

## Three things it protects against

- **A stop through the market** would be rejected and leave you with *no* stop
  — worse than the one it replaced. It skips the move and retries next tick.
- **Amend spam.** It only sends when the price actually changes.
- **Carrying state between trades.** Going flat resets the peak, so the next
  trade's stop isn't set from the last trade's high.

## Keeping it honest

`StepPoints` here and `ratchet_points` in `futures_client.py` are the same
number in two places, because NinjaScript can't call Python. **Change one,
change the other.** The test suite compares the rung maths of both against the
same price sequence and fails if they ever disagree.

## While both are running

If the Sniper is open *and* this strategy is enabled, both will try to manage
the exit. They compute the identical stop, so they agree — but you'll get two
close attempts. **Pick one:** either switch the ratchet off in the Sniper's
futures CONFIGURATION and let NinjaTrader own the exit, or leave this strategy
disabled and let the app own it. NinjaTrader owning it is the safer choice.
