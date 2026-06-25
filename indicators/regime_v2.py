from dataclasses import dataclass
from enum import Enum
import pandas as pd


# ==========================================
# 1. ENUMS & DATA STRUCTURES
# ==========================================

class Trend(Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    SIDEWAYS = "SIDEWAYS"


class Volatility(Enum):
    COMPLACENT = "COMPLACENT"  # VIX < 12
    NORMAL = "NORMAL"          # VIX 12 - 20
    HIGH = "HIGH"              # VIX > 20


class Structure(Enum):
    TIGHT = "TIGHT"            # range_pct < 4
    WIDE = "WIDE"              # range_pct >= 4


@dataclass
class RegimeState:
    trend: Trend
    volatility: Volatility
    structure: Structure

    def __str__(self):
        return f"{self.trend.value}_{self.volatility.value}_{self.structure.value}"


# ==========================================
# 2. HELPER CALCULATIONS (Mock Implementation)
# ==========================================

def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """Helper to compute Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


# ==========================================
# 3. CORE REGIME DETECTION ENGINE
# ==========================================

def detect_regime(nifty_df: pd.DataFrame, vix_series: pd.Series) -> RegimeState:
    """
    Classify current market regime by separating Direction, Volatility, and Structure.

    Args:
        nifty_df   : DataFrame containing at least a 'close' column (min 50 rows).
        vix_series : Series of India VIX closing values.
    """
    # Safeguard for minimum required data to calculate indicators
    if len(nifty_df) < 50:
        return RegimeState(Trend.SIDEWAYS, Volatility.NORMAL, Structure.WIDE)

    close = nifty_df["close"]
    price = close.iloc[-1]

    # Calculate Core Metrics
    ema20 = calculate_ema(close, 20).iloc[-1]
    ema50 = calculate_ema(close, 50).iloc[-1]
    ret20d = ((price - close.iloc[-20]) / close.iloc[-20] * 100)

    last15 = close.iloc[-15:]
    range_pct = ((last15.max() - last15.min()) / last15.min() * 100) if last15.min() > 0 else 0
    vix = float(vix_series.iloc[-1]) if len(vix_series) > 0 else 15.0

    # Dimension 1: Determine Volatility Environment
    if vix < 12.0:
        vol = Volatility.COMPLACENT
    elif vix > 20.0:
        vol = Volatility.HIGH
    else:
        vol = Volatility.NORMAL

    # Dimension 2: Determine Market Structure
    struct = Structure.TIGHT if range_pct < 4 else Structure.WIDE

    # Dimension 3: Determine Directional Trend
    if price > ema20 > ema50 and ret20d > 3:
        current_trend = Trend.BULL
    elif price < ema20 < ema50 and ret20d < -3:
        current_trend = Trend.BEAR
    else:
        current_trend = Trend.SIDEWAYS

    return RegimeState(trend=current_trend, volatility=vol, structure=struct)


# ==========================================
# 4. TACTICAL REVISED GUIDANCE MATRIX
# ==========================================

def regime_signal_guidance(regime: RegimeState) -> dict:
    """
    Return tactical trading guidance based on the multi-dimensional matrix.
    Perfectly optimized to pass domain-specific framing to an LLM context builder.
    """

    # ----------------------------------------------------------------
    # Case A: Bull Trend Matrix
    # ----------------------------------------------------------------
    if regime.trend == Trend.BULL:
        if regime.volatility == Volatility.HIGH:
            return {
                "favour": "Small position sizes, wide stop-losses on long setups",
                "caution": "Chasing breakouts blindly; option buying due to inflated IV"
            }
        # Default Normal/Complacent Bull
        return {
            "favour": "Breakout longs, trailing momentum setups",
            "caution": "Shorting against a clean, low-stress bull trend"
        }

    # ----------------------------------------------------------------
    # Case B: Bear Trend Matrix
    # ----------------------------------------------------------------
    if regime.trend == Trend.BEAR:
        if regime.volatility == Volatility.HIGH:
            return {
                "favour": "Cash is a position. Wait for capitulation or trade ultra-high conviction setups",
                "caution": "Any levered long positions; shorting late when IV is peaked"
            }
        # Default Normal/Complacent Bear
        return {
            "favour": "Breakdown shorts, shorting rallies at resistance",
            "caution": "Catching falling knives / aggressive long entries"
        }

    # ----------------------------------------------------------------
    # Case C: Sideways / Non-Trending Matrix
    # ----------------------------------------------------------------
    if regime.trend == Trend.SIDEWAYS:

        # 1. Sideways Complacent (The Coiled Spring)
        if regime.volatility == Volatility.COMPLACENT and regime.structure == Structure.TIGHT:
            return {
                "favour": "Buying cheap long-dated options, preparing for explosive breakout expansion",
                "caution": "Shorting naked options / straddles. Extreme gamma risk"
            }

        # 2. Sideways Tight (Normal Volatility)
        if regime.structure == Structure.TIGHT:
            return {
                "favour": "Tight range-bound plays, scalping support/resistance",
                "caution": "Anticipating breakouts too early; they are likely to fail"
            }

        # 3. Sideways Wide (Choppy / Channel-bound)
        if regime.structure == Structure.WIDE:
            return {
                "favour": "Mean reversion. Selling extremes, buying major support levels",
                "caution": "Chasing breakouts in the middle of the established range"
            }

    # Clean fallback safety net for edge case exceptions
    return {
        "favour": "General risk preservation and baseline technical analysis",
        "caution": "Elevated tactical caution until standard parameters clear up"
    }