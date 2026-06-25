"""
General index context provider — computes technical indicators for indices.
"""
import logging
from datetime import date
import pandas as pd

from database.queries import get_price_history
from indicators.technical import calculate_ema

logger = logging.getLogger(__name__)

def get_index_indicators(session_date: date, symbol: str) -> dict:
    """
    Fetch price history for a symbol and calculate:
    close, EMA20, EMA50, ret7d, ret20d, ret60d.
    """
    # Fetch 180 days of history to compute 50-day EMA and 60-day return
    rows = get_price_history(symbol, days=180)
    df = pd.DataFrame(rows)
    if df.empty:
        logger.warning("No price history found for %s on/before %s", symbol, session_date)
        return {
            "symbol": symbol,
            "close": None,
            "ema20": None,
            "ema50": None,
            "ret7d": None,
            "ret20d": None,
            "ret60d": None,
        }

    for col in ("open", "high", "low", "close"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).reset_index(drop=True)

    if len(df) == 0:
        return {
            "symbol": symbol,
            "close": None,
            "ema20": None,
            "ema50": None,
            "ret7d": None,
            "ret20d": None,
            "ret60d": None,
        }

    close_series = df["close"]
    close_latest = float(close_series.iloc[-1])

    # EMA calculations
    ema20_s = calculate_ema(close_series, 20)
    ema20 = round(float(ema20_s.iloc[-1]), 2) if not ema20_s.empty else None

    ema50_s = calculate_ema(close_series, 50) if len(close_series) >= 50 else ema20_s
    ema50 = round(float(ema50_s.iloc[-1]), 2) if not ema50_s.empty else None

    # Return calculations
    ret7d = 0.0
    if len(close_series) >= 7:
        ret7d = round((close_latest - close_series.iloc[-7]) / close_series.iloc[-7] * 100, 2)
    elif len(close_series) > 1:
        ret7d = round((close_latest - close_series.iloc[0]) / close_series.iloc[0] * 100, 2)

    ret20d = 0.0
    if len(close_series) >= 20:
        ret20d = round((close_latest - close_series.iloc[-20]) / close_series.iloc[-20] * 100, 2)
    elif len(close_series) > 1:
        ret20d = round((close_latest - close_series.iloc[0]) / close_series.iloc[0] * 100, 2)

    ret60d = 0.0
    if len(close_series) >= 60:
        ret60d = round((close_latest - close_series.iloc[-60]) / close_series.iloc[-60] * 100, 2)
    elif len(close_series) > 1:
        ret60d = round((close_latest - close_series.iloc[0]) / close_series.iloc[0] * 100, 2)

    return {
        "symbol": symbol,
        "close": close_latest,
        "ema20": ema20,
        "ema50": ema50,
        "ret7d": ret7d,
        "ret20d": ret20d,
        "ret60d": ret60d,
    }
