"""
Configurable thresholds for the option-sell recommendation engine.
All values are readable from environment variables with sensible defaults.
"""
import os

# Strike distance from zone edge (in ATR multiples)
BUFFER_MULTIPLIER   = float(os.getenv("OPTSELL_BUFFER_MULTIPLIER", "0.25"))

# Delta band for qualifying options (OTM but not too far)
DELTA_BAND_LOW      = float(os.getenv("OPTSELL_DELTA_LOW",          "0.15"))
DELTA_BAND_HIGH     = float(os.getenv("OPTSELL_DELTA_HIGH",         "0.30"))

# Minimum open interest to ensure liquidity
MIN_OI              = int(  os.getenv("OPTSELL_MIN_OI",              "500"))

# Minimum single-expiry ROI (as decimal) — 0.005 = 0.5%
MIN_ROI_PCT         = float(os.getenv("OPTSELL_MIN_ROI_PCT",         "0.005"))

# Zone staleness: zones older than this many days are skipped
STALENESS_DAYS      = int(  os.getenv("OPTSELL_STALENESS_DAYS",      "90"))

# Zones with touch_count >= this override the staleness filter
HIGH_TOUCH_OVERRIDE = int(  os.getenv("OPTSELL_HIGH_TOUCH_OVERRIDE", "6"))

# Risk-free rate used in Black-Scholes (annualized, decimal)
RISK_FREE_RATE      = float(os.getenv("OPTSELL_RISK_FREE_RATE",      "0.07"))

# ATR period for volatility scaling
ATR_PERIOD          = int(  os.getenv("OPTSELL_ATR_PERIOD",          "14"))

# OHLCV lookback in days for ATR computation
OHLCV_LOOKBACK      = int(  os.getenv("OPTSELL_OHLCV_LOOKBACK",      "60"))
