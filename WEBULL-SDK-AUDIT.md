# What Market Sniper could take from the Webull SDK — and isn't

Read from the SDK actually installed in `.venv` (`webull-openapi-python-sdk
2.0.14`), not from the docs site. Every signature quoted here was read out of
`.venv/Lib/site-packages/webull/`. Nothing below is assumed.

The bot has its own audit at `discord-sniper/v3.5.0/SDK-AUDIT.md` and it is
good — it covers the **trade** side. This one is deliberately about the part it
doesn't: **market data**, which is where Market Sniper's gap actually is.

---

## THE ONE-LINE SUMMARY

Market Sniper pays for a Webull data entitlement and reads **Yahoo Finance**
instead — for prices, bars, velocity, volume and the trend module. Yahoo is
also the direct cause of three bugs found this week.

---

## WIN #1 — Yahoo is the source of the bugs, and it is replaceable

Every one of these came from the Yahoo feed, not from the logic:

| Bug | What Yahoo did |
|---|---|
| Velocity read "violent" on quiet tape | one 1-min bar carried 18,887,220 volume against 30–70k neighbours |
| Velocity read "calm" on the same tape | the same artifact landed in the baseline window instead |
| Volume gauge said QQQ traded 277,170,149 by lunch | 5-min bars summed to 6× a full normal day |
| Exchange clock hardcoded to −4 | Yahoo has no opinion; we guessed, and it would be an hour wrong all winter |

All of it is worked around now — medians, outlier caps, the daily bar for
totals. **The workarounds exist because the feed is wrong**, and there is a
first-party feed sitting unused in the same process:

```python
market_data.get_history_bar(symbol, category, timespan, count)
market_data.get_snapshot(symbols, category)
```

Same broker the orders go to, so the price that arms an entry is the price the
order prices against. That alone is worth the change.

**Cost:** it spends rate budget, and the budget is shared three ways. Bars are
cacheable for their own duration — a 1-minute bar cannot change until the
minute does — so this is affordable if it is done with a cache and not a poll.

---

## WIN #2 — `get_tick()` is REAL time-and-sales. Velocity is currently a guess.

```python
market_data.get_tick(symbol, category, count='200')   # max 1000
```

`tape.py` measures "speed of the tape" from **1-minute bars** — volume and
range compared to the last half hour. It says so in its own docstring: *"This
is BAR velocity, not trades-per-second."*

`get_tick` returns the actual prints. That turns velocity from an inference
into a measurement: trades per second, average size, and whether prints are
hitting the bid or lifting the offer. For a trader whose rule is *"silent tape
means don't enter"*, the difference between "the last bar was quiet" and
"eleven prints in the last ten seconds" is the whole signal.

**This is the highest-value data change on the list.**

---

## WIN #3 — `get_quotes(symbol, category, depth=...)` — the order book

Market Sniper has never seen a book. Depth answers the question the entry grid
is really asking: **is there size resting at 715, or is it air?** A round-number
entry into a level with no bid behind it is a different trade from one into a
wall.

---

## WIN #4 — `get_footprint()` — order flow at each price

```python
market_data.get_footprint(symbols, category, timespan, count=None, ...)
```

Bid volume versus ask volume per price level. This is the professional version
of the volume signal in `trend.py`, which currently infers direction from
whether a bar closed up or down. Footprint knows whether the volume was
*aggressive* — which is what "volume confirmation" is supposed to mean.

---

## WIN #5 — `get_noii_snapshot()` — the auction imbalance

```python
market_data.get_noii_snapshot(symbol, category, imbalance_action_type)
```

Net Order Imbalance Indicator: what the opening and closing auctions are
carrying. G trades the open. This is a published number about the open, and it
is free with the entitlement he already has.

---

## WIN #6 — `get_batch_history_bar()` — the Mag Seven in one call

```python
market_data.get_batch_history_bar(symbols, category, timespan, count)
```

`trend.py` fetches the lead symbol and seven Mag names **one Yahoo request at a
time** — eight round trips per basket read, every 15 seconds. This is one call.

The same applies to `market_breadth()`, which makes **eleven** sector requests.
Webull also publishes sector data directly:

```python
screener.get_market_sectors(category, ...)
screener.get_gainers_losers(rank_type, category, sort_by, ...)
screener.get_most_active(category, ...)
```

Breadth would stop being a proxy built from ETF prices and become the exchange's
own numbers.

---

## WIN #7 — `get_option_contracts()` — stop constructing OCC symbols by hand

```python
instrument.get_option_contracts(category, underlying_symbols=..., option_type=...,
                                strike_price_gte=..., strike_price_lte=..., ...)
```

`occ_symbol()` builds the contract string from parts and hopes. When it is
wrong the error is `INVALID_SYMBOL`, which the app currently reports as
*"the market may be closed"* — a guess that has been wrong at least once.
Asking for the real chain, filtered by strike range, removes the whole class.

---

## WIN #8 — `get_trade_calendar()` — a real calendar

```python
trade_calendar.get_trade_calendar(market, start, end)
```

Closed-market detection is currently *"has a bar printed in the last 25
minutes?"*. That was measured and tuned against five days of real data, and it
works — but it is a heuristic standing in for a published fact. Half days
around holidays are exactly where it will be wrong.

---

## WIN #9 — `preview_option()` before every order

```python
order_operation.preview_option(account_id, new_orders)
```

Ask Webull what it thinks of the order *before* sending it. Margin, fees, and
whether it will be rejected — without putting it in the book. Every rejection
G has seen mid-trade could have been a message before he clicked.

---

## WIN #10 — `replace_option()` — no naked window

```python
order_operation.replace_option(account_id, modify_orders)
```

This is handoff item 4. Cancel-then-place leaves a window with **no stop
resting**. `replace_option` is one call. Not load-bearing yet — Market Sniper
does not rest option stops — but it is the prerequisite for ever doing so.

---

## WIN #11 — Push order events instead of polling for fills

Same as the bot's WIN #1. `trade_events_client.py` is present in the installed
SDK. Market Sniper currently polls `_try_update_fill` up to eight times after
an order and infers the rest.

**Caveat, and it matters:** the handoff explicitly forbids installing the
`webull-python-sdk-*` streaming family into this Python — it pins incompatible
protobuf/paho/cachetools and broke the bridge on 9/2. Whether
`trade_events_client` in *this* package works without those is untested.
**Do not install anything to find out.**

---

## THE ORDER I'D DO THESE IN

1. **`get_tick` for velocity** (WIN #2) — biggest signal gain, self-contained,
   and replaces the weakest measurement in the app.
2. **`get_batch_history_bar` for the trend basket** (WIN #6) — removes 8 Yahoo
   calls per read and 11 more for breadth.
3. **`get_trade_calendar`** (WIN #8) — small, removes a heuristic.
4. **`preview_option`** (WIN #9) — small, turns rejections into warnings.
5. **`get_option_contracts`** (WIN #7) — removes the INVALID_SYMBOL class.
6. **Bars/quotes off Yahoo entirely** (WIN #1) — the big one, needs a cache
   layer first so it does not eat the shared budget.
7. **Depth, footprint, NOII** (WINS #3–5) — new signals, worth a session each.
8. **Push events** (WIN #11) — only after the dependency question is answered
   safely.

## WHAT NOT TO DO

- Do **not** poll any of these per second. The budget is 300 requests / 60 s
  shared with the bridge and the announcer. Everything above goes through
  `paced()` and gets a cache sized to how often the data can actually change.
- Do **not** install extra `webull-python-sdk-*` packages into this venv.
