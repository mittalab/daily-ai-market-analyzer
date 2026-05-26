
import logging
import sys
import os
from datetime import date

# Add root to sys.path
sys.path.append(os.getcwd())

from pipeline.data_ingestion import run_snapshot_job
from database.queries import get_row_count

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def manual_backfill():
    today = date.today()
    print(f"🚀 Starting manual OI backfill for {today}...")
    
    # run_snapshot_job will try NSE first, then automatically fall back to Kite
    summary = run_snapshot_job(today)
    
    print("\n--- Execution Summary ---")
    print(f"Status: {'✅ SUCCESS' if summary['ok'] else '❌ FAILED'}")
    print(f"Source Used: {summary.get('source', 'UNKNOWN')}")
    print(f"Rows Stored: {summary.get('rows_stored', 0)}")
    
    # Final Ground Truth Verification
    actual_rows = get_row_count("options_snapshots", {"snapshot_date": today})
    print(f"Verified rows in DB for today: {actual_rows}")

if __name__ == "__main__":
    manual_backfill()
