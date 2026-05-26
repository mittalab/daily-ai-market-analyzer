
import logging
import sys
import os
from datetime import date

# Add root to sys.path
sys.path.append(os.getcwd())

# Setup logging to console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

from scheduler import job_preflight_check

def run_manual_preflight():
    print(f"🚀 Triggering Hardened Pre-flight Check for {date.today()}...")
    try:
        job_preflight_check()
        print("\n✅ Pre-flight execution finished. Check Telegram for the result.")
    except Exception as exc:
        print(f"\n❌ Pre-flight CRASHED: {exc}")

if __name__ == "__main__":
    run_manual_preflight()
