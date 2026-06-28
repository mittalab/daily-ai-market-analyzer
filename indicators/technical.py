"""
Technical indicators — pandas only, no TA-Lib or third-party libraries.

All functions accept/return pandas Series or DataFrame.
Formulas from Appendix C of spec.
"""
import pandas as pd
import numpy as np

def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """
    Exponential Moving Average.
    EMA(n) = close.ewm(span=n, adjust=False).mean()
    """
    return series.ewm(span=period, adjust=False).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index (Wilder smoothing via ewm).
    RSI = 100 - (100 / (1 + avg_gain / avg_loss))
    Returns NaN for the first `period` rows.
    """
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)

    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

    rs  = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average True Range using standard Welles Wilder smoothing.
    Perfectly matches TradingView, Zerodha Kite, and standard industry baselines.
    """
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    # 1. Calculate True Range (TR) - Your logic here was perfectly correct!
    tr_components = [
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ]
    tr = pd.concat(tr_components, axis=1).max(axis=1)

    # 2. Extract raw numpy values for custom Wilder's loop acceleration
    tr_values = tr.values
    atr_values = np.zeros_like(tr_values)

    if len(df) < period:
        return pd.Series(np.nan, index=df.index)

    # 3. Step 1 of Wilder's: Initialize the first N-period ATR with a Simple Moving Average
    # The first valid ATR value occurs at index (period - 1)
    atr_values[period - 1] = np.mean(tr_values[:period])

    # 4. Step 2 of Wilder's: Smooth the rest using the industry recursive formula
    # ATR_today = ((ATR_yesterday * (period - 1)) + TR_today) / period
    for i in range(period, len(df)):
        atr_values[i] = (atr_values[i - 1] * (period - 1) + tr_values[i]) / period

    # Fill the initial uncalculated buffer rows with NaN
    atr_values[:period - 1] = np.nan

    return pd.Series(atr_values, index=df.index)

def calculate_macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD — Moving Average Convergence Divergence.

    Returns (macd_line, signal_line, histogram).
    macd_line  = EMA(fast) - EMA(slow)
    signal_line = EMA(macd_line, signal)
    histogram   = macd_line - signal_line
    """
    ema_fast    = calculate_ema(series, fast)
    ema_slow    = calculate_ema(series, slow)
    macd_line   = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram

def atr_pct(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    ATR as a percentage of closing price.
    Used by Level 1 filter: ATR% < 0.8% → eliminate (dead zone).
    """
    if df.empty:
        return pd.Series(dtype=float, index=df.index)

    atr   = calculate_atr(df, period)
    close = df["close"]

    # Fast vectorized calculation.
    # If close is 0, pandas natively outputs 'inf', which we replace with NaN.
    return (atr / close * 100).replace([float('inf'), float('-inf')], pd.NA)


def volume_ratio(volume: pd.Series, short: int = 3, long: int = 20) -> pd.Series:
    """
    Volume ratio: rolling mean of last `short` days vs `long` days.
    Values > 1.5 indicate above-average volume (bullish signal context).
    """
    short_avg = volume.rolling(short).mean()
    long_avg  = volume.rolling(long).mean()
    return short_avg / long_avg.replace(0, float("nan"))
