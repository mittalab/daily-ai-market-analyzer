import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")

from integrations.nse_bhavcopy import last_trading_day
from pipeline.market_regime import run_market_regime

analysis_date = last_trading_day()
r = run_market_regime(analysis_date)

print()
print("=" * 60)
print(f"MARKET REGIME — {analysis_date}")
print("=" * 60)
print(f"Regime        : {r['regime']}")
print(f"Nifty close   : {r['nifty_close']}")
print(f"EMA20         : {r['ema20']}")
print(f"EMA50         : {r['ema50']}")
print(f"20-day return : {r['ret20d']}%")
print(f"VIX           : {r['vix']}")
print(f"Data rows     : nifty={r['rows_nifty']}  vix={r['rows_vix']}")
print(f"Fallback      : {r['fallback']}")
print(f"Favour        : {r['guidance']['favour']}")
print(f"Caution       : {r['guidance']['caution']}")
