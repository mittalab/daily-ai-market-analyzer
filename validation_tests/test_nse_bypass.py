
import logging
import sys
import os

# Add root to sys.path
sys.path.append(os.getcwd())

from integrations.nse_fii_dii import create_nse_session
from integrations.nse_option_chain import fetch_option_chain

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def test_bypass():
    print("Testing NSE Akamai bypass...")
    session = create_nse_session()
    print(f"Session cookies: {list(session.cookies.keys())}")
    
    for symbol in ["NIFTY", "HDFCBANK"]:
        print(f"\n--- Testing {symbol} ---")
        try:
            data = fetch_option_chain(session, symbol)
            records = data.get("records", {})
            print(f"SUCCESS! Fetched {len(records.get('data', []))} strikes for {symbol}")
        except Exception as exc:
            print(f"FAILURE for {symbol}: {exc}")

if __name__ == "__main__":
    test_bypass()
