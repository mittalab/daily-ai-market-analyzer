
import logging
import time
import os
import sys
from datetime import date, timedelta
import pandas as pd

# Add root to sys.path
sys.path.append(os.getcwd())

from integrations.kite_oauth import get_authenticated_kite
from integrations.kite_ohlcv import fetch_ohlcv, get_equity_token, ohlcv_to_price_rows, get_instruments
from integrations.nse_bhavcopy import get_nifty50_symbols
from database.queries import upsert_price_history, get_watchlist

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

def run_backfill():
    logger.info("Starting backfill for Nifty50 and Watchlist stocks...")
    
    try:
        kite = get_authenticated_kite()
    except Exception as e:
        logger.error("Kite Authentication Failed: %s", e)
        print("CRITICAL: You must refresh the Kite token at https://api.abhishekmittal.in/kite/refresh before this backfill can run.")
        return
    
    # 1. Collect Nifty 50 symbols
    nifty50 = set(get_nifty50_symbols())
    
    # 2. Collect Watchlist symbols
    try:
        active_wl = get_watchlist()
        watchlist = {r["symbol"] for r in active_wl}
    except Exception as exc:
        logger.warning("Failed to fetch watchlist symbols: %s", exc)
        watchlist = set()
        
    all_targets = sorted(list(nifty50 | watchlist))
    logger.info("Total targets to backfill: %d (Nifty50: %d, Watchlist additions: %d)", 
                len(all_targets), len(nifty50), len(all_targets) - len(nifty50))

    to_date = date.today()
    from_date = to_date - timedelta(days=300) # Fetch extra days for indicators

    success_count = 0
    
    for symbol in all_targets:
        try:
            # Skip indices (VIX/NIFTY50) as they use different symbol format in Kite
            if symbol.startswith("NIFTY_") or symbol == "INDIA_VIX":
                # Special handling for Nifty Indices in Kite if needed
                # For now, focusing on the stocks as requested
                continue

            token = get_equity_token(kite, symbol)
            df = fetch_ohlcv(kite, token, from_date, to_date)
            
            if df.empty:
                logger.warning("No data returned for %s", symbol)
                continue
                
            rows = ohlcv_to_price_rows(symbol, df)
            n = upsert_price_history(rows)
            
            logger.info("Backfilled %s: %d rows", symbol, n)
            success_count += 1
            
        except Exception as exc:
            logger.error("Failed to backfill %s: %s", symbol, exc)
        
        time.sleep(0.35) # Rate limit

    logger.info("Backfill complete! Successfully processed %d/%d targets.", success_count, len(all_targets))

if __name__ == "__main__":
    run_backfill()
