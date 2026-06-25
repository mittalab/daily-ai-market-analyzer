import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")

from integrations.nse_bhavcopy import last_trading_day
from pipeline.market_regime import get_index_indicators

analysis_date = last_trading_day()
nifty = get_index_indicators(analysis_date, "NIFTY_50")
vix   = get_index_indicators(analysis_date, "INDIA_VIX")

print()
print("=" * 60)
print(f"INDEX INDICATORS — {analysis_date}")
print("=" * 60)
print(f"Nifty close   : {nifty['close']}")
print(f"EMA20         : {nifty['ema20']}")
print(f"EMA50         : {nifty['ema50']}")
print(f"20-day return : {nifty['ret20d']}%")
print(f"VIX close     : {vix['close']}")
