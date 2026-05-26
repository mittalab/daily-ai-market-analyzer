"""
Market regime detection — wired into the pipeline.

Loads Nifty50 + VIX price history from the DB and calls
indicators/regime.py to classify the current regime.

Call:
    result = run_market_regime(analysis_date)

Returns:
    {
        "regime":       "BULL_TRENDING",   # one of 6 strings
        "guidance":     {"favour": ..., "caution": ...},
        "nifty_close":  24350.5,
        "vix":          14.2,
        "rows_nifty":   62,
        "rows_vix":     35,
        "fallback":     False,   # True if insufficient data → SIDEWAYS_WIDE
    }
"""
import logging
from datetime import date

import pandas as pd

from database.queries import get_price_history
from indicators.regime import detect_regime, regime_signal_guidance

logger = logging.getLogger(__name__)

_NIFTY_SYMBOL = "NIFTY_50"
_VIX_SYMBOL   = "INDIA_VIX"
_MIN_NIFTY_ROWS = 20   # detect_regime falls back if < 20


def run_market_regime(analysis_date: date) -> dict:
    """
    Detect market regime for analysis_date using DB-stored price history.

    Nifty50 and VIX are stored by the daily bhavcopy job (6:30 PM).
    Falls back to SIDEWAYS_WIDE when < 20 rows available (early-stage DB).
    """
    # ── Nifty50 history ────────────────────────────────────────────────────────
    nifty_rows = get_price_history(_NIFTY_SYMBOL, days=65)
    nifty_df   = pd.DataFrame(nifty_rows)
    if not nifty_df.empty:
        for col in ("open", "high", "low", "close"):
            nifty_df[col] = pd.to_numeric(nifty_df[col], errors="coerce")
        nifty_df = nifty_df.dropna(subset=["close"])

    # ── VIX history ────────────────────────────────────────────────────────────
    vix_rows = get_price_history(_VIX_SYMBOL, days=35)
    vix_df   = pd.DataFrame(vix_rows)
    vix_series: pd.Series
    if not vix_df.empty:
        vix_df["close"] = pd.to_numeric(vix_df["close"], errors="coerce")
        vix_series = vix_df["close"].dropna().reset_index(drop=True)
    else:
        vix_series = pd.Series(dtype=float)

    # ── Detect ─────────────────────────────────────────────────────────────────
    fallback = len(nifty_df) < _MIN_NIFTY_ROWS
    if fallback:
        logger.warning(
            "Regime: only %d Nifty50 rows — falling back to SIDEWAYS_WIDE "
            "(bhavcopy accumulates ~1 row/day; enough data in ~%d trading days)",
            len(nifty_df),
            _MIN_NIFTY_ROWS - len(nifty_df),
        )

    regime   = detect_regime(nifty_df, vix_series)
    guidance = regime_signal_guidance(regime)

    nifty_close = float(nifty_df["close"].iloc[-1]) if not nifty_df.empty else None
    vix_latest  = float(vix_series.iloc[-1])        if len(vix_series) > 0 else None

    # Supporting values for regime print / diagnostics
    ema20 = ema50 = ret20d = None
    if not nifty_df.empty and len(nifty_df) >= 20:
        from indicators.technical import calculate_ema
        close = nifty_df["close"]
        ema20  = round(float(calculate_ema(close, 20).iloc[-1]), 2)
        ema50  = round(float(calculate_ema(close, 50).iloc[-1]), 2) if len(nifty_df) >= 50 else ema20
        ret20d = round((close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] * 100, 2)

    logger.info(
        "Market regime: %s | Nifty=%.1f | EMA20=%.1f | EMA50=%.1f | ret20d=%.2f%% | VIX=%.2f",
        regime,
        nifty_close or 0,
        ema20 or 0,
        ema50 or 0,
        ret20d or 0,
        vix_latest  or 0,
    )

    return {
        "regime":      regime,
        "guidance":    guidance,
        "nifty_close": nifty_close,
        "ema20":       ema20,
        "ema50":       ema50,
        "ret20d":      ret20d,
        "vix":         vix_latest,
        "rows_nifty":  len(nifty_df),
        "rows_vix":    len(vix_series),
        "fallback":    fallback,
    }
