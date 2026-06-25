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

from new_data_ingestion.ingestion_utils import ingest_today_bhavcopy, ingest_today_options

today = date.today()
print(f"--- Populating data for {today} ---")

print("\n1. Running Bhavcopy Job (Equity, Indices, FII/DII)...")
bhav_summary = ingest_today_bhavcopy(today)
print(f"Bhavcopy Summary: {bhav_summary}")

print("\n2. Running Snapshot Job (Options IV/OI/Premium)...")
snap_summary = ingest_today_options(today)
print(f"Snapshot Summary: {snap_summary}")

print("\n--- Population Complete ---")
