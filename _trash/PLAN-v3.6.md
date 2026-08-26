# MARKET SNIPER v3.6 — change plan

Five mods. Ordered so nothing breaks mid-stack: **display work first (sends
nothing to a broker), deletions last.** Reason is in "Build order" at the bottom.

---

## 1 · Options: entry-trigger preview line

**What you asked for:** "price is 719, you'd buy at 718" — a line showing the
underlying level your armed trade will actually fire at, before you commit.

**What already exists:** `webull_client.py:433-457` `arm()` computes
`target = float(round(spot))` — nearest whole dollar. That number is already
calculated; it's just invisible until after you press ARM.

**Change:**

| File | Change |
|---|---|
| `main.py` | New `GET /api/preview?symbol=&side=` — returns `{spot, target, strike, distance}`. Read-only, sends nothing to Webull. |
| `webull_client.py` | Extract the target math out of `arm()` into `preview_entry(symbol, side)`. `arm()` then calls it, so the preview and the real order can never disagree. |
| `index.html` | Line under the trade buttons, polled with the existing state loop: `QQQ 719.40 → triggers at 719.00 (0.40 away)` |

**⚠ Open question — rounding direction.** You said 719 → 718. Current code
rounds to *nearest*, so 719.40 → **719**, not 718. To get 718 you'd need
round-**down**-for-calls / round-**up**-for-puts (buy the pullback). Three ways:

- **(a) Show what it does now** — nearest. Preview only, zero behaviour change.
- **(b) Change to directional rounding** — calls floor, puts ceil. Changes when
  live trades fire. Real-money behaviour change.
- **(c) Preview now, decide after** — ship (a), watch the number for a day, then
  decide if it's wrong.

I'd ship (c). Tell me if you want (b) and I'll do it, but it changes fill
behaviour on live money and deserves its own testing day.

---

## 2 · Options: remove PAPER / Webull sandbox

| File | Change |
|---|---|
| `webull_client.py` | Delete `PaperSession` (~1087-1110). `make_session()` (1112-1115) returns `LiveSession()` always. |
| `main.py` | Drop `paper` field from `ConnectReq`; `connect()` stops branching on it. |
| `index.html` | Remove `#paperMode` checkbox (265), the `.modepill.PAPER` style (97), the paper text at 279, and the paper branches at 610-651. Mode pill and footer become LIVE-only. |
| `config.py` | `SANDBOX_TRADE_ENDPOINT` / `SANDBOX_EVENTS_ENDPOINT` — delete if futures also drops sandbox (it does, see §4). |
| `README.md`, `TUTORIAL.html`, `PROJECT-STATUS.md` | Strip PAPER instructions. |

**⚠ Cost of this:** after §2 and §4 land, **there is no non-live mode anywhere in
either app.** Every future test touches a real account. `ALLOW_LIVE=1` +
`MAX_CONTRACTS` + `DAILY_LOSS_LIMIT` remain your only guardrails. That's why the
deletions go last in the build order.

---

## 3 · Options: hide futures accounts from the picker

Today `webull_client.py:712-713` labels them `FUTURES ⚠` and 740-741 blocks the
pick with a redirect message. You want them gone from the list entirely.

**Change:** in the account-list loop (~705-720), filter out any account where
`_is_futures_account(aid, a)` is true instead of labelling it.

**Keep the guard at 740-741.** If your only account were a futures one, silently
filtering leaves an empty picker with no explanation. Keeping the block means
that edge case still tells you why. Filter for display, guard for safety.

---

## 4 · Futures: Topstep + NinjaTrader + Webull LIVE only

Confirmed: **remove PAPER, remove Tradovate. Keep Webull LIVE, Topstep, Ninja.**

| File | Change |
|---|---|
| `futures_client.py` | Delete `TradovateSession` (912-1034) and helpers `_tv_front_symbol` (888), `_tv_req` (901), `_fmt_px` (897 — check Topstep doesn't use it first). |
| `futures_client.py` | `make_session()` (1464-1476): drop `PAPER` and `TRADOVATE` branches. |
| `futures_client.py` | `WebullFuturesSession.__init__` default `mode="PAPER"` → `"LIVE"`. `_is_live` becomes permanently true, so `_require_live_env()` always enforces `ALLOW_LIVE=1`. **This is a safety tightening, not a loosening.** |
| `futures_app.py` | `connect()` mode whitelist → `("WEBULL", "LIVE", "TOPSTEP")`. Drop `tv_*` from `ConnectReq`, `PREF_KEYS`, `SECRET_KEYS`. |
| `futures_index.html` | Remove the Tradovate panel (~235-248) and PAPER mode option; 3 buttons remain. Line 385 mentions all three brokers — reword. |

**Naming wart to fix while we're in here:** `LIVE` currently means *NinjaTrader*
and `WEBULL` means *Webull live*. Confusing. Rename `LIVE` → `NINJA` in the mode
strings, keeping a compatibility shim so an old saved pref doesn't break login.

---

## 5 · Tape speed / market velocity — both apps

**Honest constraint first.** Neither app has tick data. `quotes.py` and
`futures_client.py` both pull **Yahoo 1-minute bars**. You cannot compute true
tape speed (trades per second) from 1-minute bars. Two real options:

**(a) Velocity proxy from bars — works today, free**
Roll a short window of 1m bars and compute: volume rate vs its own 20-bar
average, range expansion, and bar-over-bar acceleration. Output a 0-100
score + direction. This tells you *"the market is moving faster than usual
right now"* — which is most of what tape speed is used for. It is **not**
trades-per-second and I won't label it as such.

**(b) Real tick tape — needs data wiring**
Webull's data SDK exposes tick/trade endpoints. Real prints, real speed.
Costs: OPRA sub you already pay for options; futures tick data is the
expensive one. Meaningfully more work and a new failure surface on your
live path.

**Recommendation: build (a) now** as `tape.py` — one shared module both apps
import, mirroring how `quotes.py` already works. If the proxy proves useful,
upgrade its data source to (b) later without touching either UI.

| File | Change |
|---|---|
| `tape.py` *(new)* | `velocity(symbol, bars)` → `{score, rate, avg_rate, accel, state}` where state ∈ calm / normal / fast / violent. Pure function, no I/O. |
| `quotes.py` | Expose the 1m bar series it already fetches so `tape.py` can consume it. |
| `futures_client.py` | Same — `_ohlcv()` (366) already returns what's needed. |
| `main.py` / `futures_app.py` | `GET /api/tape?symbol=` on both. |
| `index.html` / `futures_index.html` | Velocity chip in the trend strip. Colour by state. |

Deferred per your call: the alert/visual layer on top of this.

---

## Build order

**Phase 1 — additive, zero risk to the live path**
1. §5 `tape.py` + endpoints + chips *(new file, nothing existing changes)*
2. §1 entry-trigger preview, option (a) *(read-only endpoint)*
3. §3 futures-account filter *(display filter, guard stays)*

→ **You test here, with PAPER still available as a fallback.**

**Phase 2 — deletions, after Phase 1 is confirmed working**
4. §2 options PAPER removal
5. §4 futures Tradovate + PAPER removal

Doing it this way means if Phase 1 has a bug, you still have a sandbox to debug
in. Reverse the order and you're debugging new code against a live account.

**Commits:** one per numbered section, so any single mod can be reverted alone.

---

## Needs your answer before I start

1. **§1 rounding** — (a) preview only, (b) directional rounding now, or (c) preview now / decide later? *I recommend (c).*
2. **§5 data source** — (a) bar-derived proxy now, or (b) hold out for real tick data? *I recommend (a).*
3. **§4 rename** — `LIVE` → `NINJA`? Cleaner, tiny risk to saved prefs (shimmed).
