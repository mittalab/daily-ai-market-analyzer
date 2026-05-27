import requests
import time
import random
from integrations.nse_fii_dii import create_nse_session

def debug_nse_snapshot(symbol):
    session = create_nse_session()
    url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Referer": "https://www.nseindia.com/option-chain"
    }
    session.headers.update(headers)
    
    print(f"Fetching {url}...")
    r = session.get(url, timeout=20)
    print(f"Status: {r.status_code}")
    print(f"Length: {len(r.content)}")
    print(f"Content (first 500): {r.text[:500]}")

if __name__ == "__main__":
    debug_nse_snapshot("RELIANCE")
