import sys
from datetime import date
sys.path.append(r"C:\Users\29abh\Projects\Trading\daily-ai-market-analyzer")

from new_data_ingestion.fo_bhavcopy import _make_udiff_session

def main():
    s = _make_udiff_session()
    d = date(2026, 6, 25)
    params = {
        "archives": "FO_BhavCopy",
        "date": d.strftime("%d-%b-%Y"),
        "type": "equity",
        "mode": "single",
    }
    url = "https://www.nseindia.com/api/reports"
    print(f"Querying {url} with params {params}...")
    r = s.get(url, params=params, timeout=30)
    print(f"Status code: {r.status_code}")
    print(f"Content length: {len(r.content)} bytes")
    print(f"Content: {r.content}")
    print(f"Text: {r.text}")

if __name__ == "__main__":
    main()
