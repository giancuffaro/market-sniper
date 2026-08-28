# MarketSniperTrend — NinjaTrader 8 indicator

The same trend logic as the Sniper's `DIRECTION` panel, running on your
NinjaTrader charts. Three signals, and it only calls a trend when they agree.

## Installing it

1. Open NinjaTrader 8.
2. **New → NinjaScript Editor** (or press `F11`).
3. In the left tree, right-click **Indicators → New Indicator**, click through
   the wizard and name it anything — you're about to replace the contents.
4. Delete everything in the new file, paste in all of `MarketSniperTrend.cs`,
   and press **F5** to compile.
5. If it compiles clean, the indicator is available on any chart:
   right-click a chart → **Indicators** → `MarketSniperTrend`.

Compile errors will show at the bottom of the editor with a line number. Send
them to me rather than guessing — NinjaScript is fussy about its `using`
statements between minor versions.

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
