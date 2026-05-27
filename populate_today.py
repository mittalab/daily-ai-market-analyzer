import logging
import sys
from datetime import date
from dotenv import load_dotenv

# Setup logging to see what's happening
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

load_dotenv()

from pipeline.data_ingestion import run_bhavcopy_job, run_snapshot_job

today = date.today()
print(f"--- Populating data for {today} ---")

print("\n1. Running Bhavcopy Job (Equity, Indices, FII/DII)...")
bhav_summary = run_bhavcopy_job(today)
print(f"Bhavcopy Summary: {bhav_summary}")

print("\n2. Running Snapshot Job (Options IV/OI/Premium)...")
snap_summary = run_snapshot_job(today)
print(f"Snapshot Summary: {snap_summary}")

print("\n--- Population Complete ---")
