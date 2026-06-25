"""
Market regime detection — exact logic from spec Section 11.

detect_regime(nifty_df, vix_series) → one of:
  BULL_TRENDING, BEAR_TRENDING, SIDEWAYS_TIGHT,
  SIDEWAYS_WIDE, HIGH_VOLATILITY, BEAR_HIGH_VOLATILITY
"""
import pandas as pd

from indicators.technical import calculate_ema


#AI: Can we use claude AI to determine the Market Regime
def detect_regime(nifty_df: pd.DataFrame, vix_series: pd.Series) -> str:
    """
    Classify current market regime from spec Section 11.

    Args:
        nifty_df   : DataFrame with 'close' column, at least 60 rows.
        vix_series : Series of India VIX closing values, at least 30 rows.

    Returns one of the 6 regime strings.
    Falls back to SIDEWAYS_WIDE if insufficient data.
    """
    if len(nifty_df) < 20:
        return "SIDEWAYS_WIDE"

    close  = nifty_df["close"]
    ema20  = calculate_ema(close, 20).iloc[-1]
    ema50  = calculate_ema(close, 50).iloc[-1] if len(nifty_df) >= 50 else ema20
    price  = close.iloc[-1]

    # 20-day return
    ret20d = ((price - close.iloc[-20]) / close.iloc[-20] * 100) if len(close) >= 20 else 0

    # 15-day range for sideways detection
    last15 = close.iloc[-15:] if len(close) >= 15 else close
    range_pct = ((last15.max() - last15.min()) / last15.min() * 100) if last15.min() > 0 else 0

    # Latest VIX
    vix = float(vix_series.iloc[-1]) if len(vix_series) > 0 else 15.0

    # Spec Section 11 — exact logic (order matters)
    if vix > 20:
        return "BEAR_HIGH_VOLATILITY" if ret20d < -5 else "HIGH_VOLATILITY"

    if price > ema20 > ema50 and ret20d > 3:
        return "BULL_TRENDING"

    if price < ema20 < ema50 and ret20d < -3:
        return "BEAR_TRENDING"

    if range_pct < 4:
        return "SIDEWAYS_TIGHT"

    return "SIDEWAYS_WIDE"


def regime_signal_guidance(regime: str) -> dict:
    """
    Return trading guidance for the current regime (spec Section 11 table).
    Used by the Claude context builder to add regime-specific framing.
    """
    guidance = {
        "BULL_TRENDING": {
            "favour":  "Breakout longs, momentum setups",
            "caution": "Short setups — regime is bullish",
        },
        "BEAR_TRENDING": {
            "favour":  "Breakdown shorts, bearish setups",
            "caution": "Long setups — regime is bearish",
        },
        "SIDEWAYS_TIGHT": {
            "favour":  "Support/resistance bounces, range plays",
            "caution": "Breakouts — range-bound market, likely to fail",
        },
        "SIDEWAYS_WIDE": {
            "favour":  "Both directions at extremes of range",
            "caution": "Chasing breakouts in the middle of range",
        },
        "HIGH_VOLATILITY": {
            "favour":  "Wide stop setups only",
            "caution": "Naked option buying — inflated premiums",
        },
        "BEAR_HIGH_VOLATILITY": {
            "favour":  "Cash is a position — only highest conviction shorts",
            "caution": "Long setups — VIX > 20 + market falling",
        },
    }
    return guidance.get(regime, {"favour": "General analysis", "caution": "Elevated caution"})
