"""
Kite Connect — Historical OHLCV Fetcher
=========================================
Source   : Zerodha Kite Connect API
Auth     : API key + access token (token expires daily at midnight IST)
Covers   : NSE equity, NSE F&O futures, NSE F&O options, BSE equity
Intervals: minute, 3minute, 5minute, 10minute, 15minute, 30minute, 60minute,
           day, week, month

CONFIRMED WORKING: 2026-05-23
  RELIANCE equity — 21 trading days of OHLCV returned correctly
  Response columns: date, open, high, low, close, volume

EXACT RESPONSE STRUCTURE (list of dicts, one per candle):
  date    — timezone-aware datetime  e.g. 2026-05-22 00:00:00+05:30
  open    — float   opening price
  high    — float   intraday high
  low     — float   intraday low
  close   — float   closing price
  volume  — int     total traded quantity (shares for equity, lots × lot_size for F&O)

  NOTE: 'oi' is NOT present when oi=False (default). See impl_05 for OI fetch.

DATA LOOKBACK LIMITS (Kite documented limits):
  day / week / month : up to 2000 days (~5.5 years)
  60minute           : up to 400 days
  minute             : up to 60 days
  Other intraday     : up to 100 days

TOKEN EXPIRY:
  Access tokens expire at midnight IST every day.
  You must re-run the OAuth flow (kite_login_helper.py) each day before use.
  Store the new token in kite_access_token.txt — impl_08 handles automation.
"""

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from kiteconnect import KiteConnect

# ── Credentials ────────────────────────────────────────────────────────────

API_KEY        = "xm82on7jif6xgpay"
TOKEN_FILE     = Path("kite_access_token.txt")

# ── Instrument token cache ─────────────────────────────────────────────────
# Calling kite.instruments() fetches ~4 MB. Cache it for the session.
_instrument_cache: dict[str, pd.DataFrame] = {}


def get_kite() -> KiteConnect:
    """
    Return an authenticated KiteConnect instance.
    Reads access token from kite_access_token.txt (written by kite_login_helper.py).
    """
    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            f"{TOKEN_FILE} not found. Run kite_login_helper.py to generate it."
        )
    token = TOKEN_FILE.read_text().strip()
    kite  = KiteConnect(api_key=API_KEY)
    kite.set_access_token(token)
    return kite


def get_instruments(kite: KiteConnect, exchange: str = "NSE") -> pd.DataFrame:
    """
    Load instrument master for a given exchange. Cached for the session.

    exchange options: "NSE" (equity), "NFO" (F&O), "BSE", "MCX"
    Columns: instrument_token, exchange_token, tradingsymbol, name,
             last_price, expiry, strike, tick_size, lot_size,
             instrument_type, segment, exchange
    """
    if exchange not in _instrument_cache:
        _instrument_cache[exchange] = pd.DataFrame(kite.instruments(exchange))
    return _instrument_cache[exchange]


def get_instrument_token(kite: KiteConnect, symbol: str, exchange: str = "NSE") -> int:
    """
    Look up the instrument_token for an NSE equity symbol.

    For F&O instruments (futures/options), use impl_05 which looks up by
    tradingsymbol, expiry, and strike instead.
    """
    df = get_instruments(kite, exchange)

    # NSE equity: instrument_type == "EQ", segment == "NSE"
    match = df[
        (df["tradingsymbol"] == symbol) &
        (df["instrument_type"] == "EQ")
    ]

    if match.empty:
        raise ValueError(f"Symbol '{symbol}' not found in {exchange} instruments")

    return int(match.iloc[0]["instrument_token"])


def fetch_ohlcv(
    kite: KiteConnect,
    instrument_token: int,
    from_date: date,
    to_date: date,
    interval: str = "day",
) -> pd.DataFrame:
    """
    Fetch historical OHLCV candles for a given instrument token.

    Returns a DataFrame with columns: date, open, high, low, close, volume
    The 'date' column is timezone-aware (Asia/Kolkata, +05:30).
    For simple date comparisons, use: df['date'].dt.date

    Args:
        instrument_token : integer token from kite.instruments()
        from_date        : start date (inclusive)
        to_date          : end date (inclusive)
        interval         : "day" for daily candles (see module docstring for others)
    """
    raw = kite.historical_data(
        instrument_token=instrument_token,
        from_date=str(from_date),
        to_date=str(to_date),
        interval=interval,
        oi=False,   # No OI for equity — use impl_05 for F&O OI
    )

    if not raw:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(raw)
    return df


def fetch_ohlcv_by_symbol(
    kite: KiteConnect,
    symbol: str,
    days: int = 252,
    exchange: str = "NSE",
    interval: str = "day",
) -> pd.DataFrame:
    """
    Convenience wrapper: fetch daily OHLCV for an NSE equity by ticker symbol.

    Args:
        symbol   : NSE ticker, e.g. "RELIANCE", "TCS", "HDFCBANK"
        days     : number of calendar days to look back (252 ≈ 1 trading year)
        exchange : "NSE" for equity
        interval : "day" for post-market swing trading use case

    Returns DataFrame with: date, open, high, low, close, volume
    """
    token    = get_instrument_token(kite, symbol, exchange)
    to_date  = date.today()
    from_date = to_date - timedelta(days=days)
    return fetch_ohlcv(kite, token, from_date, to_date, interval)


def fetch_ohlcv_batch(
    kite: KiteConnect,
    symbols: list[str],
    days: int = 252,
    exchange: str = "NSE",
) -> dict[str, pd.DataFrame]:
    """
    Fetch daily OHLCV for multiple symbols. Returns {symbol: DataFrame}.

    Kite does not have a batch OHLCV endpoint — each symbol is one API call.
    Space calls to avoid rate limiting (Kite allows ~3 req/sec on historical API).
    """
    import time
    results = {}
    for symbol in symbols:
        try:
            results[symbol] = fetch_ohlcv_by_symbol(kite, symbol, days, exchange)
        except Exception as e:
            print(f"  Warning: {symbol} failed — {e}")
            results[symbol] = pd.DataFrame()
        time.sleep(0.35)   # ~3 req/sec safe rate
    return results


# ── Example usage ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    kite = get_kite()

    # Single stock — 1 trading year
    print("Fetching RELIANCE daily OHLCV (1 year) ...")
    df = fetch_ohlcv_by_symbol(kite, "RELIANCE", days=365)
    print(f"Rows    : {len(df)}")
    print(f"Columns : {list(df.columns)}")
    print(f"\nLast 5 candles:")
    print(df.tail(5).to_string(index=False))

    # Batch fetch
    print("\nBatch fetching NIFTY50 sample (5 stocks, 60 days) ...")
    batch = fetch_ohlcv_batch(
        kite,
        ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"],
        days=60,
    )
    for sym, data in batch.items():
        print(f"  {sym}: {len(data)} rows")
