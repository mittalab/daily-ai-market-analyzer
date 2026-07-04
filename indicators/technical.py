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


def compute_stock_indicators(df: pd.DataFrame) -> dict:
    """
    Computes technical indicators for a stock using pandas-ta with a fallback to self-computation.
    Assumes df has columns: open, high, low, close, volume.
    """
    warnings = []
    computation_method = "pandas_ta"
    
    if df is None or df.empty:
        return {
            "ema20": None, "ema50": None, "ema180": None,
            "atr14": None, "atr_pct": None, "rsi14": None,
            "macd_line": None, "macd_signal": None, "macd_histogram": None,
            "macd_histogram_direction": "SHRINKING",
            "rsi_last_20": [], "macd_hist_last_20": [],
            "price_vs_ema20": "unavailable", "price_vs_ema50": "unavailable", "price_vs_ema180": "unavailable",
            "ema_arrangement": "MIXED", "volume_ratio_20d": None,
            "computation_method": "fallback", "warnings": ["Empty input dataframe"]
        }

    # Ensure required columns are present and numeric
    df = df.copy()
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            df[col] = np.nan
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).reset_index(drop=True)

    if len(df) < 20:
        warnings.append(f"Insufficient data rows ({len(df)}), minimum 20 needed.")

    # Try pandas-ta primary computation
    try:
        import pandas_ta as ta
        
        # EMA
        ema20_series = ta.ema(df["close"], length=20)
        ema50_series = ta.ema(df["close"], length=50)
        
        if len(df) >= 180:
            ema180_series = ta.ema(df["close"], length=180)
        else:
            ema180_series = pd.Series(np.nan, index=df.index)
            warnings.append("EMA180 unavailable: data length < 180 rows")
            
        # ATR
        atr14_series = ta.atr(df["high"], df["low"], df["close"], length=14)
        
        # RSI
        rsi14_series = ta.rsi(df["close"], length=14)
        
        # MACD
        macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
        if macd_df is not None and not macd_df.empty:
            macd_line_series = macd_df["MACD_12_26_9"]
            macd_signal_series = macd_df["MACDs_12_26_9"]
            macd_hist_series = macd_df["MACDh_12_26_9"]
        else:
            raise ValueError("pandas-ta MACD returned None or empty DataFrame")
            
        # Volume ratio (rolling mean short=3, long=20)
        vol_short = df["volume"].rolling(3).mean()
        vol_long = df["volume"].rolling(20).mean()
        vol_ratio_series = vol_short / vol_long.replace(0, np.nan)
        
    except Exception as e:
        computation_method = "fallback"
        warnings.append(f"pandas-ta computation failed: {str(e)}. Using self-computation fallback.")
        
        # Fallback using custom functions
        ema20_series = calculate_ema(df["close"], 20)
        ema50_series = calculate_ema(df["close"], 50)
        if len(df) >= 180:
            ema180_series = calculate_ema(df["close"], 180)
        else:
            ema180_series = pd.Series(np.nan, index=df.index)
            warnings.append("EMA180 unavailable in fallback: data length < 180 rows")
            
        atr14_series = calculate_atr(df, 14)
        rsi14_series = calculate_rsi(df["close"], 14)
        
        macd_line_series, macd_signal_series, macd_hist_series = calculate_macd(df["close"])
        vol_ratio_series = volume_ratio(df["volume"], 3, 20)

    # Extract final values
    def _get_last_val(s: pd.Series, default=None) -> float | None:
        if s is None or s.empty:
            return default
        v = s.iloc[-1]
        return round(float(v), 4) if pd.notnull(v) else default

    ema20 = _get_last_val(ema20_series)
    ema50 = _get_last_val(ema50_series)
    ema180 = _get_last_val(ema180_series) if len(df) >= 180 else None
    atr14 = _get_last_val(atr14_series)
    
    # atr_pct
    close_last = float(df["close"].iloc[-1]) if not df.empty else 0.0
    atr_pct_val = round((atr14 / close_last * 100), 4) if atr14 is not None and close_last > 0 else None
    
    rsi14 = _get_last_val(rsi14_series)
    macd_line = _get_last_val(macd_line_series)
    macd_signal = _get_last_val(macd_signal_series)
    macd_histogram = _get_last_val(macd_hist_series)
    
    # MACD Histogram Direction
    macd_histogram_direction = "SHRINKING"
    if macd_hist_series is not None and len(macd_hist_series) >= 2:
        h1 = macd_hist_series.iloc[-1]
        h2 = macd_hist_series.iloc[-2]
        if pd.notnull(h1) and pd.notnull(h2):
            macd_histogram_direction = "GROWING" if h1 > h2 else "SHRINKING"
            
    # Series for last 20 sessions (rounded to 2 decimal places)
    rsi_last_20 = []
    if rsi14_series is not None:
        rsi_last_20 = [round(float(x), 2) for x in rsi14_series.tail(20).tolist() if pd.notnull(x)]
        
    macd_hist_last_20 = []
    if macd_hist_series is not None:
        macd_hist_last_20 = [round(float(x), 2) for x in macd_hist_series.tail(20).tolist() if pd.notnull(x)]

    # Price vs EMAs
    price_vs_ema20 = "below"
    if ema20 is not None:
        price_vs_ema20 = "above" if close_last > ema20 else "below"
        
    price_vs_ema50 = "below"
    if ema50 is not None:
        price_vs_ema50 = "above" if close_last > ema50 else "below"
        
    price_vs_ema180 = "unavailable"
    if ema180 is not None:
        price_vs_ema180 = "above" if close_last > ema180 else "below"

    # EMA arrangement: BULLISH (ema20 > ema50 > ema180), BEARISH (ema20 < ema50 < ema180), MIXED
    ema_arrangement = "MIXED"
    if ema20 is not None and ema50 is not None:
        if ema180 is not None:
            if ema20 > ema50 > ema180:
                ema_arrangement = "BULLISH"
            elif ema20 < ema50 < ema180:
                ema_arrangement = "BEARISH"
        else:
            if ema20 > ema50:
                ema_arrangement = "BULLISH"
            elif ema20 < ema50:
                ema_arrangement = "BEARISH"

    volume_ratio_20d = _get_last_val(vol_ratio_series)

    return {
        "ema20": ema20,
        "ema50": ema50,
        "ema180": ema180,
        "atr14": atr14,
        "atr_pct": atr_pct_val,
        "rsi14": rsi14,
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_histogram": macd_histogram,
        "macd_histogram_direction": macd_histogram_direction,
        "rsi_last_20": rsi_last_20,
        "macd_hist_last_20": macd_hist_last_20,
        "price_vs_ema20": price_vs_ema20,
        "price_vs_ema50": price_vs_ema50,
        "price_vs_ema180": price_vs_ema180,
        "ema_arrangement": ema_arrangement,
        "volume_ratio_20d": volume_ratio_20d,
        "computation_method": computation_method,
        "warnings": warnings
    }

