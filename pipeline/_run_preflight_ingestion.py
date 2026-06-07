"""
Uber Ingestion Script — Independent pre-flight data population.

Trigger this script to fill any missing data required for the 10 PM pipeline:
  - Kite Token validity check
  - NSE Bhavcopy (Equity + Indices)
  - NSE FII/DII data
  - Option Chain Snapshots (NSE + Kite Fallback)

Usage:
  python pipeline/_run_preflight_ingestion.py [--force]
"""
import logging
import sys
import argparse
from datetime import date
import pytz

# Add root to sys.path
import os
sys.path.append(os.getcwd())

from database.queries import (
    get_row_count, 
    get_latest_fii_dii, 
    keepalive
)
from pipeline.data_ingestion import (
    run_bhavcopy_job, 
    run_snapshot_job, 
    get_ingestion_symbols
)
from integrations.kite_oauth import validate_token
from scheduler import is_trading_day

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("uber_ingestion")

def run_uber_ingestion(force=False):
    today = date.today()
    IST = pytz.timezone("Asia/Kolkata")
    
    print(f"\n🚀 Starting Uber Ingestion for {today} (Force={force})")
    
    if not is_trading_day(today) and not force:
        print("⚠️ Today is not a trading day. Skipping (use --force to override).")
        return

    # 1. DB Keepalive
    print("\n--- [1/5] Database Check ---")
    if keepalive():
        print("✅ Database is reachable.")
    else:
        print("❌ Database unreachable! Check SUPABASE_URL/KEY.")
        return

    # 2. Kite Token
    print("\n--- [2/5] Kite Token Check ---")
    if validate_token():
        print("✅ Kite Token is valid.")
    else:
        print("❌ Kite Token is INVALID or EXPIRED.")
        print("👉 Action: Refresh at https://api.abhishekmittal.in/kite/refresh")
        # Continue anyway, some parts don't need Kite

    # 3. Bhavcopy (Equity + Indices)
    print("\n--- [3/5] Bhavcopy Check ---")
    # Check price_history for today
    symbols = get_ingestion_symbols(all_stages=True)
    nifty50_count = 50 # Heuristic
    
    current_rows = get_row_count("price_history", {"date": today})
    if current_rows < nifty50_count or force:
        print(f"📥 Bhavcopy missing or incomplete ({current_rows} rows). Fetching...")
        summary = run_bhavcopy_job(today)
        if summary.get("ok"):
            print(f"✅ Bhavcopy success: {summary.get('equity_rows')} equity rows, {summary.get('index_rows')} index rows.")
        else:
            print(f"⚠️ Bhavcopy partially failed: {summary.get('errors')}")
    else:
        print(f"✅ Bhavcopy already present ({current_rows} rows).")

    # 4. FII/DII Data
    print("\n--- [4/5] FII/DII Check ---")
    latest_fii = get_latest_fii_dii()
    fii_date = str(latest_fii.get("date")) if latest_fii else "None"
    
    if fii_date != str(today) or force:
        print(f"📥 FII/DII data stale (Latest: {fii_date}). Attempting fetch...")
        # Bhavcopy job already handles FII/DII, but we can call it again if needed
        # Or just rely on the fact that if Bhavcopy was run above, it tried FII.
        # If Bhavcopy wasn't run because rows were present, but FII is missing:
        summary = run_bhavcopy_job(today) 
        if summary.get("fii_ok"):
            print(f"✅ FII/DII success: Net {summary.get('fii_net_cr')} Cr.")
        else:
            print(f"⚠️ FII/DII fetch failed or returned cached data.")
    else:
        print(f"✅ FII/DII data is current ({fii_date}).")

    # 5. Option Snapshots
    print("\n--- [5/5] Option Snapshot Check ---")
    snap_rows = get_row_count("options_snapshots", {"snapshot_date": today})
    if snap_rows == 0 or force:
        print(f"📥 Option snapshots missing. Fetching (Priority: Kite Fallback + NSE)...")
        summary = run_snapshot_job(today)
        if summary.get("ok"):
            print(f"✅ Snapshot success: {summary.get('rows_stored')} rows stored via {summary.get('source')}.")
        else:
            print(f"❌ Snapshot failed.")
    else:
        print(f"✅ Option snapshots already present ({snap_rows} rows).")

    print("\n✨ Uber Ingestion Complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Uber Ingestion Script")
    parser.add_argument("--force", action="store_true", help="Force ingestion even if data exists")
    args = parser.parse_args()
    
    run_uber_ingestion(force=args.force)
