"""
Technical indicators — pandas only, no TA-Lib or third-party libraries.

All functions accept/return pandas Series or DataFrame.
Formulas from Appendix C of spec.
"""
import pandas as pd


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
    Matches TradingView, Zerodha Kite, and standard industry baselines.
    """
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    # Calculate individual components of True Range
    tr_components = [
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ]

    tr = pd.concat(tr_components, axis=1).max(axis=1)

    # Wilder's Smoothing formula translates to alpha = 1 / period in pandas EWM
    # We also add min_periods to prevent premature calculations on incomplete windows
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


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
    atr   = calculate_atr(df, period)
    close = df["close"]
    # Simple, vectorized protection against divide-by-zero errors
    return (atr / close * 100) if not close.empty else pd.Series(dtype=float)


def volume_ratio(volume: pd.Series, short: int = 3, long: int = 20) -> pd.Series:
    """
    Volume ratio: rolling mean of last `short` days vs `long` days.
    Values > 1.5 indicate above-average volume (bullish signal context).
    """
    short_avg = volume.rolling(short).mean()
    long_avg  = volume.rolling(long).mean()
    return short_avg / long_avg.replace(0, float("nan"))
