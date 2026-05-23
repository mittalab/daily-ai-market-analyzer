"""
Kite Connect — Historical OI Fetcher (Futures + Options)
==========================================================
Source   : Zerodha Kite Connect API  (kite.historical_data with oi=True)
Auth     : Same as impl_04 — API key + daily access token
Covers   : NSE F&O futures (FUT) and options (CE/PE)

CONFIRMED WORKING: 2026-05-23
  NIFTY26MAYFUT    — 21 days OI returned  (16.07M → 11.34M, unwinding into expiry)
  NIFTY26MAY23700CE — 21 days OI returned  (2.18M → 4.82M, buildup)
  RELIANCE26MAYFUT — 21 days OI returned  (82.69M → 32.84M, unwinding)

EXACT RESPONSE STRUCTURE (list of dicts, one per candle):
  date    — timezone-aware datetime  e.g. 2026-05-22 00:00:00+05:30
  open    — float   opening price of the contract
  high    — float   intraday high
  low     — float   intraday low
  close   — float   closing price (settlement price for futures)
  volume  — int     contracts traded that day
  oi      — int     open interest at end of day (in number of shares, NOT lots)

CRITICAL: The oi=True flag
  kite.historical_data(..., oi=True)   ← OI column present and populated
  kite.historical_data(..., oi=False)  ← OI column absent (default behaviour)
  Without oi=True, you get no OI data. This flag is not optional.

OI UNITS — important:
  OI is returned in SHARES (underlying units), not lots.
  To convert to lots: oi_lots = oi / lot_size
  Lot sizes (as of 2026):
    NIFTY   : 75 shares per lot
    BANKNIFTY: 35 shares per lot
    RELIANCE: 250 shares per lot
  Lot size is in the instruments DataFrame — always read it dynamically.

INTERPRETING OI:
  Rising price + Rising OI  → Long buildup (bullish)
  Rising price + Falling OI → Short covering (bullish but weak)
  Falling price + Rising OI → Short buildup (bearish)
  Falling price + Falling OI → Long unwinding (bearish but weak)
"""

import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from kiteconnect import KiteConnect

# ── Credentials ────────────────────────────────────────────────────────────

API_KEY    = "xm82on7jif6xgpay"
TOKEN_FILE = Path("kite_access_token.txt")

_instrument_cache: dict[str, pd.DataFrame] = {}


def get_kite() -> KiteConnect:
    token = TOKEN_FILE.read_text().strip()
    kite  = KiteConnect(api_key=API_KEY)
    kite.set_access_token(token)
    return kite


def get_nfo_instruments(kite: KiteConnect) -> pd.DataFrame:
    """Load NFO instrument master (cached). ~46,000 rows covering all F&O contracts."""
    if "NFO" not in _instrument_cache:
        _instrument_cache["NFO"] = pd.DataFrame(kite.instruments("NFO"))
    return _instrument_cache["NFO"]


# ── Futures token lookup ────────────────────────────────────────────────────

def get_futures_token(
    kite: KiteConnect,
    symbol: str,
    expiry: date | None = None,
) -> tuple[int, int, date]:
    """
    Find the instrument_token for a stock/index futures contract.

    Args:
        symbol : underlying name, e.g. "NIFTY", "RELIANCE", "BANKNIFTY"
        expiry : specific expiry date, or None to get the nearest expiry

    Returns:
        (instrument_token, lot_size, expiry_date)
    """
    df = get_nfo_instruments(kite)
    futs = df[
        (df["name"] == symbol) &
        (df["instrument_type"] == "FUT")
    ].sort_values("expiry")

    if futs.empty:
        raise ValueError(f"No futures found for symbol '{symbol}'")

    if expiry:
        futs = futs[futs["expiry"] == expiry]
        if futs.empty:
            raise ValueError(f"No futures contract for {symbol} expiring {expiry}")

    row = futs.iloc[0]
    return int(row["instrument_token"]), int(row["lot_size"]), row["expiry"]


# ── Options token lookup ────────────────────────────────────────────────────

def get_option_token(
    kite: KiteConnect,
    symbol: str,
    strike: float,
    option_type: str,   # "CE" or "PE"
    expiry: date | None = None,
) -> tuple[int, int, date]:
    """
    Find the instrument_token for a specific options contract.

    Args:
        symbol      : underlying name, e.g. "NIFTY", "RELIANCE"
        strike      : strike price as float, e.g. 23700.0
        option_type : "CE" or "PE"
        expiry      : specific expiry date, or None to get nearest expiry

    Returns:
        (instrument_token, lot_size, expiry_date)
    """
    df = get_nfo_instruments(kite)
    opts = df[
        (df["name"] == symbol) &
        (df["instrument_type"] == option_type.upper()) &
        (df["strike"] == strike)
    ].sort_values("expiry")

    if opts.empty:
        raise ValueError(
            f"No {option_type} option found for {symbol} at strike {strike}"
        )

    if expiry:
        opts = opts[opts["expiry"] == expiry]
        if opts.empty:
            raise ValueError(
                f"No {symbol} {strike}{option_type} expiring {expiry}"
            )

    row = opts.iloc[0]
    return int(row["instrument_token"]), int(row["lot_size"]), row["expiry"]


# ── Core OI fetch ───────────────────────────────────────────────────────────

def fetch_oi_history(
    kite: KiteConnect,
    instrument_token: int,
    from_date: date,
    to_date: date,
    interval: str = "day",
) -> pd.DataFrame:
    """
    Fetch historical OHLCV + OI for any F&O instrument.

    Returns DataFrame with columns:
      date, open, high, low, close, volume, oi

    OI is in shares (raw units). Convert to lots using lot_size.
    """
    raw = kite.historical_data(
        instrument_token=instrument_token,
        from_date=str(from_date),
        to_date=str(to_date),
        interval=interval,
        oi=True,    # This flag is mandatory — without it, no OI is returned
    )

    if not raw:
        return pd.DataFrame(columns=["date","open","high","low","close","volume","oi"])

    df = pd.DataFrame(raw)
    return df


def fetch_futures_oi(
    kite: KiteConnect,
    symbol: str,
    days: int = 30,
    expiry: date | None = None,
) -> pd.DataFrame:
    """
    Convenience: fetch OI history for a futures contract by symbol.

    Adds 'oi_lots' column (oi / lot_size) for human-readable OI.
    Adds 'oi_change' column (day-over-day OI change in lots).
    Adds 'price_change' column (day-over-day close change %).
    Adds 'oi_signal' column interpreting buildup vs unwinding.
    """
    token, lot_size, expiry_date = get_futures_token(kite, symbol, expiry)
    to_dt   = date.today()
    from_dt = to_dt - timedelta(days=days)

    df = fetch_oi_history(kite, token, from_dt, to_dt)
    if df.empty:
        return df

    df["lot_size"]    = lot_size
    df["oi_lots"]     = df["oi"] / lot_size
    df["oi_change"]   = df["oi_lots"].diff()
    df["price_change"] = df["close"].pct_change() * 100
    df["oi_signal"]   = df.apply(_classify_oi_signal, axis=1)
    df["expiry"]      = expiry_date
    df["symbol"]      = symbol

    return df


def fetch_option_oi(
    kite: KiteConnect,
    symbol: str,
    strike: float,
    option_type: str,
    days: int = 30,
    expiry: date | None = None,
) -> pd.DataFrame:
    """
    Convenience: fetch OI history for a specific options strike.

    Same enrichment as fetch_futures_oi (oi_lots, oi_change, oi_signal).
    """
    token, lot_size, expiry_date = get_option_token(
        kite, symbol, strike, option_type, expiry
    )
    to_dt   = date.today()
    from_dt = to_dt - timedelta(days=days)

    df = fetch_oi_history(kite, token, from_dt, to_dt)
    if df.empty:
        return df

    df["lot_size"]     = lot_size
    df["oi_lots"]      = df["oi"] / lot_size
    df["oi_change"]    = df["oi_lots"].diff()
    df["price_change"] = df["close"].pct_change() * 100
    df["oi_signal"]    = df.apply(_classify_oi_signal, axis=1)
    df["expiry"]       = expiry_date
    df["symbol"]       = f"{symbol}{strike}{option_type}"

    return df


def _classify_oi_signal(row) -> str:
    """
    Classify each candle's OI movement relative to price direction.
    Returns one of: LONG_BUILDUP, SHORT_COVERING, SHORT_BUILDUP, LONG_UNWINDING
    """
    price_up = row.get("price_change", 0) >= 0
    oi_up    = row.get("oi_change", 0) >= 0

    if price_up and oi_up:
        return "LONG_BUILDUP"
    if price_up and not oi_up:
        return "SHORT_COVERING"
    if not price_up and oi_up:
        return "SHORT_BUILDUP"
    return "LONG_UNWINDING"


# ── Example usage ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    kite = get_kite()

    # Futures OI — NIFTY
    print("NIFTY Futures — last 21 days OI")
    df_fut = fetch_futures_oi(kite, "NIFTY", days=30)
    cols = ["date", "close", "volume", "oi_lots", "oi_change", "oi_signal"]
    print(df_fut[cols].tail(7).to_string(index=False))
    print(f"Lot size: {df_fut['lot_size'].iloc[0]}")

    print()

    # Stock futures OI — RELIANCE
    print("RELIANCE Futures — last 21 days OI")
    df_rel = fetch_futures_oi(kite, "RELIANCE", days=30)
    print(df_rel[cols].tail(7).to_string(index=False))

    print()

    # Options OI — NIFTY CE (nearest expiry, mid-chain strike)
    nfo  = get_nfo_instruments(kite)
    nifty_ce = nfo[
        (nfo["name"] == "NIFTY") & (nfo["instrument_type"] == "CE")
    ].sort_values("expiry")
    nearest_expiry = nifty_ce["expiry"].min()
    mid_strike = nifty_ce[
        nifty_ce["expiry"] == nearest_expiry
    ].sort_values("strike")["strike"].iloc[
        len(nifty_ce[nifty_ce["expiry"] == nearest_expiry]) // 2
    ]

    print(f"NIFTY {mid_strike:.0f}CE — last 21 days OI")
    df_ce = fetch_option_oi(kite, "NIFTY", mid_strike, "CE", days=30)
    print(df_ce[cols].tail(7).to_string(index=False))
