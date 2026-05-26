"""
Kite Connect — historical OHLCV for Nifty 50 equity stocks.

Credentials from .env: KITE_API_KEY.
Access token loaded from Supabase kite_tokens table (not a file).
Rate limit: 0.35s between calls (~3 req/sec — confirmed safe).
Lookback: 180 calendar days (spec: 6 months).
"""
import logging
import os
import time
from datetime import date, timedelta

import pandas as pd
from dotenv import load_dotenv
from kiteconnect import KiteConnect

load_dotenv()
logger = logging.getLogger(__name__)

# ── Instrument master cache (process-lifetime, fetched once per pipeline run) ──
_instrument_cache: dict[str, pd.DataFrame] = {}


def get_kite(access_token: str) -> KiteConnect:
    """
    Return an authenticated KiteConnect instance.
    Caller provides the access token (loaded from Supabase by data_ingestion).
    """
    api_key = os.getenv("KITE_API_KEY")
    if not api_key:
        raise RuntimeError("KITE_API_KEY not set in .env")
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


def get_instruments(kite: KiteConnect, exchange: str = "NSE") -> pd.DataFrame:
    """
    Load instrument master for exchange. Cached for the process lifetime.
    NEVER call per symbol — fetches ~4 MB once and caches.
    """
    if exchange not in _instrument_cache:
        logger.info("Loading %s instruments master (~4MB)...", exchange)
        _instrument_cache[exchange] = pd.DataFrame(kite.instruments(exchange))
        logger.info("Instruments loaded: %d rows", len(_instrument_cache[exchange]))
    return _instrument_cache[exchange]


def get_equity_token(kite: KiteConnect, symbol: str) -> int:
    """Look up instrument_token for an NSE equity symbol."""
    df = get_instruments(kite, "NSE")
    match = df[
        (df["tradingsymbol"] == symbol) &
        (df["instrument_type"] == "EQ")
    ]
    if match.empty:
        raise ValueError(f"Symbol '{symbol}' not found in NSE instruments")
    return int(match.iloc[0]["instrument_token"])


def fetch_ohlcv(
    kite: KiteConnect,
    instrument_token: int,
    from_date: date,
    to_date: date,
) -> pd.DataFrame:
    """
    Fetch daily OHLCV for an instrument token.
    Returns DataFrame: date, open, high, low, close, volume.
    Date column is timezone-aware (+05:30) — use .dt.date for comparisons.
    """
    raw = kite.historical_data(
        instrument_token=instrument_token,
        from_date=str(from_date),
        to_date=str(to_date),
        interval="day",
        oi=False,
    )
    if not raw:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    return pd.DataFrame(raw)


def fetch_ohlcv_all(
    kite: KiteConnect,
    symbols: list[str],
    days: int = 180,
) -> dict[str, pd.DataFrame]:
    """
    Fetch 6-month daily OHLCV for all Nifty 50 symbols.

    Returns {symbol: DataFrame}. Failed symbols get empty DataFrame.
    Sleeps 0.35s between calls (confirmed ~3 req/sec safe rate).
    """
    to_date   = date.today()
    from_date = to_date - timedelta(days=days)
    results: dict[str, pd.DataFrame] = {}

    for symbol in symbols:
        try:
            token = get_equity_token(kite, symbol)
            df    = fetch_ohlcv(kite, token, from_date, to_date)
            results[symbol] = df
            logger.debug("%s: %d OHLCV rows", symbol, len(df))
        except Exception as exc:
            logger.warning("OHLCV fetch failed for %s: %s", symbol, exc)
            results[symbol] = pd.DataFrame()
        time.sleep(0.35)

    ok = sum(1 for df in results.values() if not df.empty)
    logger.info("OHLCV batch complete: %d/%d symbols succeeded", ok, len(symbols))
    return results


def ohlcv_to_price_rows(symbol: str, df: pd.DataFrame) -> list[dict]:
    """Convert OHLCV DataFrame to price_history upsert rows."""
    rows = []
    for _, row in df.iterrows():
        dt = row["date"]
        # Kite returns timezone-aware datetimes — extract just the date
        if hasattr(dt, "date"):
            dt = dt.date()
        rows.append({
            "symbol": symbol,
            "date":   str(dt),
            "open":   float(row["open"]),
            "high":   float(row["high"]),
            "low":    float(row["low"]),
            "close":  float(row["close"]),
            "volume": int(row["volume"]),
        })
    return rows
