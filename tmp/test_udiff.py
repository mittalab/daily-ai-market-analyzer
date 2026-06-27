import sys
import re
sys.path.append(r"C:\Users\29abh\Projects\Trading\daily-ai-market-analyzer")

from new_data_ingestion.fo_bhavcopy import _make_udiff_session

def main():
    s = _make_udiff_session()
    
    # 1. Fetch all-reports page and search for UDiFF names
    url = "https://www.nseindia.com/all-reports"
    print(f"Fetching {url}...")
    r = s.get(url, timeout=15)
    print(f"Status: {r.status_code}, Length: {len(r.content)} bytes")
    
    # Search for UDiFF or FO-UDiFF or bhavcopy in the HTML text
    matches = re.findall(r'[^"\']*UDiFF[^"\']*', r.text)
    print("\n--- UDiFF matches in HTML ---")
    for m in set(matches):
        print(f"  {m.strip()}")
        
    matches2 = re.findall(r'[^"\']*Bhavcopy[^"\']*', r.text)
    print("\n--- Bhavcopy matches in HTML ---")
    for m in set(matches2):
        print(f"  {m.strip()}")

    # 2. Try fetching a script/json list of reports if it exists in the page
    # Look for script links that might contain the dropdown list config
    script_urls = re.findall(r'src="([^"]+\.js)"', r.text)
    print("\n--- JS script URLs in HTML ---")
    for script_url in script_urls[:10]:
        print(f"  {script_url}")

if __name__ == "__main__":
    main()
