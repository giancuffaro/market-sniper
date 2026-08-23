"""MARKET SNIPER — central config. v3.1"""

APP_VERSION = "3.7"
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
    "strike_mode": "ITM3",
    "tp_enabled": False, "tp_value": 30.0, "tp_unit": "cents",
    "sl_enabled": False, "sl_value": 20.0, "sl_unit": "cents",
    # MY CONFIG — round-number armed entry, +$1 whole-number TP, 10% stop.
    "my_enabled": False,
}
# Auto-applied bracket when a MY CONFIG armed entry fires.
MY_CONFIG_SL_PCT = 10.0
