
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
from database.queries import upsert_price_history, get_row_count

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_backfill():
    logger.info("Starting one-time 250-day backfill...")
    
    kite = get_authenticated_kite()
    
    # 1. Collect all symbols
    symbols = list(get_nifty50_symbols())
    
    # Add main indices
    indices = ["NIFTY_50", "INDIA_VIX"]
    
    # Add sector indices from map
    try:
        import json
        with open("config/sector_map.json", encoding="utf-8") as f:
            sector_map = json.load(f)
            sector_indices = list(set(s["index"] for s in sector_map["stocks"].values()))
            indices.extend(sector_indices)
    except Exception as exc:
        logger.warning("Could not load sector indices from map: %s", exc)

    all_targets = sorted(list(set(symbols + indices)))
    logger.info("Total targets to backfill: %d", len(all_targets))

    to_date = date.today()
    from_date = to_date - timedelta(days=300)

    success_count = 0
    
    for symbol in all_targets:
        try:
            # Special handling for Nifty Indices in Kite
            # Kite symbol for Nifty 50 index is usually "NIFTY 50" under exchange "NSE"
            # Our DB uses "NIFTY_50"
            kite_symbol = symbol.replace("_", " ")
            
            # Find token
            token = None
            try:
                # Try as EQ first
                token = get_equity_token(kite, kite_symbol)
            except:
                # Try as Index
                inst_df = get_instruments(kite, "NSE")
                match = inst_df[inst_df["tradingsymbol"] == kite_symbol]
                if not match.empty:
                    token = int(match.iloc[0]["instrument_token"])
            
            if not token:
                logger.warning("Could not find token for %s", symbol)
                continue

            df = fetch_ohlcv(kite, token, from_date, to_date)
            if df.empty:
                logger.warning("No data returned for %s", symbol)
                continue
                
            rows = ohlcv_to_price_rows(symbol, df)
            upsert_price_history(rows)
            
            logger.info("Backfilled %s: %d rows", symbol, len(rows))
            success_count += 1
            
        except Exception as exc:
            logger.error("Failed to backfill %s: %s", symbol, exc)
        
        time.sleep(0.35) # Rate limit

    logger.info("Backfill complete! Successfully processed %d/%d targets.", success_count, len(all_targets))

if __name__ == "__main__":
    run_backfill()
