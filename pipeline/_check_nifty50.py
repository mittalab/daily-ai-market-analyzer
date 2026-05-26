from integrations.nse_fii_dii import create_nse_session
from integrations.nse_bhavcopy import get_nifty50_symbols
import json

session = create_nse_session()

# Try equity-master which lists all F&O stocks
r = session.get("https://www.nseindia.com/api/equity-master", timeout=15)
data = r.json()
print("equity-master keys:", list(data.keys())[:5])

# Check allIndices for Nifty50
r2 = session.get("https://www.nseindia.com/api/allIndices", timeout=15)
d2 = r2.json()
for idx in d2.get("data", []):
    if "50" in str(idx.get("indexSymbol","")) and "NIFTY" in str(idx.get("indexSymbol","")):
        print("Index:", idx.get("indexSymbol"), "Advance:", idx.get("advances"))

# Diff: sector_map vs actual bhavcopy results
import requests, pandas as pd
from io import StringIO
headers = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip, deflate"}
r3 = requests.get("https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_22052026.csv", headers=headers, timeout=30)
df = pd.read_csv(StringIO(r3.text))
df.columns = df.columns.str.strip()
df["SYMBOL"] = df["SYMBOL"].str.strip()
bhavcopy_eq = set(df[df["SERIES"].str.strip()=="EQ"]["SYMBOL"].tolist())

sector_map_syms = get_nifty50_symbols()
missing = sector_map_syms - bhavcopy_eq
extra   = set()

print(f"\nIn sector_map but NOT in bhavcopy ({len(missing)}): {sorted(missing)}")

# What are the candidates for replacement? Look at Nifty50 URL
r4 = session.get("https://www.nseindia.com/api/index-names", timeout=15)
names = r4.json()
nifty50_names = [n for n in names if "NIFTY 50" in str(n).upper()]
print("\nNifty50 related index names:", nifty50_names[:10])
