import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s - %(message)s")

from integrations.kite_oauth import get_authenticated_kite
from pipeline.data_ingestion import run_kite_data_fetch

kite = get_authenticated_kite()
print("Fetching 6-month OHLCV for all 50 stocks (takes ~3 min)...")
summary = run_kite_data_fetch(kite)
ohlcv   = summary["symbols_ohlcv"]
oi      = summary["symbols_oi"]
errors  = summary["errors"]
print(f"OHLCV symbols: {ohlcv}")
print(f"OI symbols   : {oi}")
print(f"Errors       : {errors}")
