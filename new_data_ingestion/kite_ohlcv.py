"""
Kite Connect — historical OHLCV for Nifty 50 equity stocks.

Credentials from .env: KITE_API_KEY.
Access token loaded from Supabase kite_tokens table.
Rate limit: 0.35s between calls.
Lookback: 180 calendar days.
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

# ── Instrument master cache ──
_instrument_cache: dict[str, pd.DataFrame] = {}


def get_kite(access_token: str) -> KiteConnect:
    """Return an authenticated KiteConnect instance."""
    api_key = os.getenv("KITE_API_KEY")
    if not api_key:
        raise RuntimeError("KITE_API_KEY not set in .env")
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


def get_instruments(kite: KiteConnect, exchange: str = "NSE") -> pd.DataFrame:
    """Load instrument master for exchange. Cached for the process lifetime."""
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
    """Fetch daily OHLCV for an instrument token."""
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
    """Fetch daily OHLCV for all target symbols."""
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


def get_option_symbols(kite: KiteConnect, symbol: str, expiries_count: int = 2) -> pd.DataFrame:
    """Fetch all NFO option instruments for a symbol, filtered by first N expiries."""
    df = get_instruments(kite, "NFO")
    if df.empty:
        return pd.DataFrame()
    
    options = df[(df["name"] == symbol) & (df["segment"] == "NFO-OPT")].copy()
    if options.empty:
        return pd.DataFrame()
    
    expiries = sorted(options["expiry"].unique())[:expiries_count]
    return options[options["expiry"].isin(expiries)]


def fetch_option_quotes(kite: KiteConnect, instruments: pd.DataFrame) -> dict:
    """Fetch real-time quotes (LTP, OI) for option instruments."""
    if instruments.empty:
        return {}
    
    tokens = [f"NFO:{s}" for s in instruments["tradingsymbol"].tolist()]
    try:
        quotes = kite.quote(tokens)
        return quotes
    except Exception as exc:
        logger.warning("Kite quote failed: %s", exc)
        return {}


def ohlcv_to_price_rows(symbol: str, df: pd.DataFrame) -> list[dict]:
    """Convert OHLCV DataFrame to price_history upsert rows."""
    rows = []
    for _, row in df.iterrows():
        dt = row["date"]
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
