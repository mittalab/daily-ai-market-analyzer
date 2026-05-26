import requests, pandas as pd
from io import StringIO

headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept-Encoding": "gzip, deflate"}

# niftyindices.com provides official constituent CSV
urls = [
    "https://niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
]

for url in urls:
    try:
        r = requests.get(url, headers=headers, timeout=15)
        print(f"{url} -> {r.status_code}, size={len(r.content)}")
        if r.status_code == 200 and len(r.content) > 100:
            df = pd.read_csv(StringIO(r.text))
            print("Columns:", df.columns.tolist())
            sym_col = [c for c in df.columns if "symbol" in c.lower() or "ticker" in c.lower()]
            if sym_col:
                syms = sorted(df[sym_col[0]].dropna().str.strip().tolist())
                print(f"Nifty50 ({len(syms)} stocks):", syms)
            break
    except Exception as e:
        print(f"{url}: {e}")

# Also check the bhavcopy we have to find what's new
from integrations.nse_bhavcopy import get_nifty50_symbols
from integrations.nse_fii_dii import create_nse_session

sector_syms = get_nifty50_symbols()
# Try NSE F&O eligibles to cross-reference
r2 = requests.get(
    "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv",
    headers=headers, timeout=15,
)
print(f"\nfo_mktlots -> {r2.status_code}")
if r2.status_code == 200:
    df2 = pd.read_csv(StringIO(r2.text))
    print("fo_mktlots columns:", df2.columns.tolist()[:5])
    print("First 5 rows:")
    print(df2.head())
