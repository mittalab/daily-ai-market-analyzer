import sys
from datetime import date
sys.path.append(r"C:\Users\29abh\Projects\Trading\daily-ai-market-analyzer")

from new_data_ingestion.fo_bhavcopy import _make_udiff_session

def test_request(s, archives_val, type_val, date_str):
    url = "https://www.nseindia.com/api/reports"
    params = {
        "archives": archives_val,
        "date": date_str,
        "type": type_val,
        "mode": "single",
    }
    
    try:
        r = s.get(url, params=params, timeout=15)
        print(f"Params: {params} -> Status: {r.status_code}, Length: {len(r.content)} bytes")
        if r.status_code == 200 and len(r.content) > 500:
            print(f"  [SUCCESS] Content starts with: {r.content[:50]}")
            return True
        elif r.status_code == 200:
            print(f"  [200 Error] {r.text}")
    except Exception as exc:
        print(f"  [ERROR] {exc}")
    return False

def main():
    s = _make_udiff_session()
    date_str = "07-Oct-2025"
    
    variations = [
        # 1. Standard archives as string
        ("FO_BhavCopy", "equity"),
        ("FO_BhavCopy", "equities"),
        
        # 2. JSON array variations for derivatives
        ('[{"name":"FO-UDiFF Common Bhavcopy Final (zip)","type":"daily-reports","category":"derivatives","section":"equities"}]', "equities"),
        ('[{"name":"FO - Bhavcopy(csv)","type":"archives","category":"derivatives","section":"equity"}]', "equity"),
        ('[{"name":"FO - Bhavcopy(csv)","type":"archives","category":"derivatives","section":"equities"}]', "equities"),
        ('[{"name":"FO - Bhavcopy(csv)","type":"daily-reports","category":"derivatives","section":"equities"}]', "equities"),
        
        # 3. Guessing other FO UDiFF names
        ('[{"name":"FO UDiFF Common Bhavcopy Final (zip)","type":"daily-reports","category":"derivatives","section":"equities"}]', "equities"),
        ('[{"name":"FO-UDiFF Common Bhavcopy (zip)","type":"daily-reports","category":"derivatives","section":"equities"}]', "equities"),
        ('[{"name":"FO UDiFF Common Bhavcopy (zip)","type":"daily-reports","category":"derivatives","section":"equities"}]', "equities"),
        ('[{"name":"FO-UDiFF Bhavcopy (zip)","type":"daily-reports","category":"derivatives","section":"equities"}]', "equities"),
        ('[{"name":"UDiFF Common Bhavcopy Final (zip)","type":"daily-reports","category":"derivatives","section":"equities"}]', "equities"),
        ('[{"name":"FO-UDiFF Common Bhavcopy Final (zip)","type":"daily-reports","category":"derivatives","section":"equities"}]', "equity"),
    ]
    
    for arch, typ in variations:
        if test_request(s, arch, typ, date_str):
            print("\nFOUND WORKING COMBINATION!")
            break

if __name__ == "__main__":
    main()
