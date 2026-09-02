"""MARKET SNIPER — central config. v3.1"""

APP_VERSION = "4.3"
APP_NAME = "MARKET SNIPER"
REGION = "us"

# Hosts per Webull's own OpenAPI docs (developer.webull.com).
# v3.6 removed PAPER from both apps, so the sandbox hosts are gone with it —
# every order either app can send now goes to production, behind ALLOW_LIVE=1.
LIVE_TRADE_ENDPOINT  = "api.webull.com"
LIVE_EVENTS_ENDPOINT = "events-api.webull.com"

PREFERRED_ACCOUNT_TYPE = None   # "MARGIN"/"CASH" to skip the login picker

# Webull's API labels FUTURES accounts as "MARGIN". List the last characters
# of your futures account id(s) so the app labels them correctly and
# redirects to the futures app.
FUTURES_ACCOUNT_SUFFIXES = ["3T0B"]

# SPY/QQQ are daily-0DTE ETFs. TSLA replaces SPX (an INDEX option Webull's
# API can't serve): TSLA is a stock with tradable options, but its expirations
# are WEEKLY (mostly Fridays), so the app targets the nearest Friday for it.
SYMBOLS = {
    "SPY":  {"strike_step": 1.0, "style": "american", "settles": "shares", "enabled": True},
    "QQQ":  {"strike_step": 1.0, "style": "american", "settles": "shares", "enabled": True},
    "TSLA": {"strike_step": 2.5, "style": "american", "settles": "shares", "enabled": True},
}

def build_option_order(client_order_id, symbol, strike, expiration, option_type,
                       side, quantity, limit_price):
    leg = {
        "side": side, "quantity": str(quantity), "symbol": symbol,
        "strike_price": f"{float(strike):.2f}",
        "option_expire_date": expiration,
        "instrument_type": "OPTION", "option_type": option_type, "market": "US",
    }
    return [{
        "client_order_id": client_order_id, "combo_type": "NORMAL",
        "option_strategy": "SINGLE", "order_type": "LIMIT",
        "limit_price": f"{float(limit_price):.2f}", "quantity": str(quantity),
        "side": side, "time_in_force": "DAY", "entrust_type": "QTY",
        "instrument_type": "OPTION", "market": "US", "symbol": symbol, "legs": [leg],
    }]

MARKETABLE_BUFFER_PCT = 0.02
MARKETABLE_BUFFER_MIN = 0.02

MAX_CONTRACTS       = 10
DAILY_LOSS_LIMIT    = 500.0
REQUIRE_LIVE_ENV_OK = True
GUARDRAIL_WARN_AT_MINUTES_TO_CLOSE = 15
AUTO_FLATTEN_DEFAULT = False

# The 3:40 PM cutoff is OUR safety buffer, not a broker rule — options trade
# until 4:00 PM ET everywhere. Change ENTRY_CUTOFF to (15, 55) to trade later.
ENFORCE_MARKET_HOURS = True
MARKET_OPEN        = (9, 30)
ENTRY_CUTOFF       = (15, 40)
MARKET_CLOSE_HARD  = (16, 0)

DEFAULT_SETTINGS = {
    # v3.7: the execute button buys 3 strikes IN the money by default.
    # Depth counts STRIKES, not dollars — 3 deep is $3 on SPY/QQQ (step 1.0)
    # and $7.50 on TSLA (step 2.5). Format is ITM<n> / OTM<n>, n up to 20.
    "strike_mode": "ITM2",
    "tp_enabled": False, "tp_value": 30.0, "tp_unit": "cents",
    "sl_enabled": False, "sl_value": 20.0, "sl_unit": "cents",
    # ---- ABSOLUTE ENTRY + RATCHET -----------------------------------------
    # ONE setting, not two kept in step. Entry and ratchet used to be separate
    # flags and every layer had to remember to move them together - a rule you
    # can forget in one place, and then the app is arming entries with nothing
    # managing the exit. "ratchet_enabled" no longer exists; everything that
    # used to read it reads this.
    #
    #   ON  = the buy buttons ARM and wait for the underlying to reach the
    #         level in front of price, and the ratchet owns the exit
    #   OFF = the buy buttons fire at the ask, and nothing manages the exit
    #
    # The RATCHET stop climbs in steps and never comes back down. It always
    # sits one step BELOW the highest rung reached:
    #   best  0%  -> stop -10%   (the opening stop)
    #   best +10% -> stop   0%   (breakeven, now hunting +20%)
    #   best +20% -> stop +10%   (locked in, now hunting +30%)  ... forever
    # Percent is of the OPTION PREMIUM you paid. +10% is not somewhere you
    # sell - it is where the stop moves to breakeven. Which is why this
    # replaces take-profit and stop-loss rather than sitting alongside them.
    "my_enabled": True,
    # Step size, in percent. 10 is the default and what every rung above assumes.
    "ratchet_step_pct": 10.0,
    # TIERS: pick arm/lock/rung off the premium PAID rather than one flat
    # number, and never let the stop sit closer than 40% of the gain already
    # made. ratchet_step_pct above becomes the OPENING stop only.
    # Off = the flat rungs G designed. See ratchet_tiers.py for why.
    "ratchet_tiers": True,
}
# Legacy fallback stop, used ONLY if the ratchet is switched off. With the
# ratchet on (the default) nothing reads this.
MY_CONFIG_SL_PCT = 10.0

# ---- CONTRACT QUALITY -----------------------------------------------------
# A hard filter on what the app will let you buy. Fail any of these and the
# contract is not shown and cannot be ordered.
#
# Why these three, and why NOT delta/theta:
#   Theta is quoted per DAY and 0DTE has hours. What theta actually eats is the
#   EXTRINSIC (time) part of the premium, and by 4pm that is zero. So extrinsic
#   as a share of what you pay IS your theta exposure - and unlike delta/theta
#   it needs no greeks feed and no implied-vol guesswork. A fully OTM 0DTE is
#   100% extrinsic: every cent scheduled to die.
CONTRACT_MIN_PREMIUM     = 0.20   # under ~20c the spread alone eats the trade
CONTRACT_MAX_SPREAD_PCT  = 15.0   # (ask-bid)/mid. 20% spread = down 20% on fill
CONTRACT_REQUIRE_INTRINSIC = True # the real rule: never buy a fully-OTM contract

# WARNING THRESHOLD ONLY - this no longer blocks anything.
# It was a block, at 60%, then 88%, and both were wrong for the same reason:
# extrinsic % measures distance-to-strike and time-remaining, not quality. At
# the open nearly every 0DTE is 90%+ time value, and an ITM1 sitting a nickel
# from the strike is 95% by arithmetic. The rule refused trades at the busiest
# hour of the day. Being fully OUT of the money is what actually has no value,
# and CONTRACT_REQUIRE_INTRINSIC blocks that outright.
CONTRACT_MAX_EXTRINSIC_PCT = 88.0
CONTRACT_QUALITY_ENFORCED = True  # False = warn in the payload but allow it
