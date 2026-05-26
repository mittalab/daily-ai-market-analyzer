"""
Fetch live Nifty50 constituent list from niftyindices.com,
cross-reference with Kite NSE instruments, and compare with sector_map.json.
"""
import requests, json, pandas as pd
from io import StringIO
from pathlib import Path
from integrations.kite_oauth import get_authenticated_kite

# ── 1. Fetch official list from niftyindices.com ──────────────────────────────
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Encoding": "gzip, deflate",
}
url = "https://niftyindices.com/IndexConstituent/ind_nifty50list.csv"
r = requests.get(url, headers=headers, timeout=20)
print(f"niftyindices.com -> {r.status_code}, {len(r.content)} bytes")

nse_symbols = set()
if r.status_code == 200 and len(r.content) > 100:
    df = pd.read_csv(StringIO(r.text))
    df.columns = df.columns.str.strip()
    print(f"Columns: {df.columns.tolist()}")
    sym_col = [c for c in df.columns if "symbol" in c.lower()][0]
    nse_symbols = set(df[sym_col].str.strip().dropna().tolist())
    print(f"\nNSE official Nifty50 ({len(nse_symbols)} stocks):")
    for s in sorted(nse_symbols):
        print(f"  {s}")
else:
    print("FAILED to fetch — check connectivity")

# ── 2. Cross-check with Kite NSE EQ instruments ───────────────────────────────
print("\n" + "-"*60)
kite = get_authenticated_kite()
instr = pd.DataFrame(kite.instruments("NSE"))
eq = instr[instr["instrument_type"] == "EQ"]

print("\nKite lookup for each NSE symbol:")
kite_symbols = set()
not_found_in_kite = []
for sym in sorted(nse_symbols):
    row = eq[eq["tradingsymbol"] == sym]
    if not row.empty:
        kite_sym = row.iloc[0]["tradingsymbol"]
        kite_symbols.add(kite_sym)
        print(f"  {sym:20} -> Kite: {kite_sym}  token={row.iloc[0]['instrument_token']}")
    else:
        # Try partial match
        partial = eq[eq["tradingsymbol"].str.startswith(sym[:6])]
        print(f"  {sym:20} -> NOT FOUND in Kite EQ  (partial: {partial['tradingsymbol'].tolist()[:3]})")
        not_found_in_kite.append(sym)

# ── 3. Compare with sector_map.json ──────────────────────────────────────────
print("\n" + "-"*60)
sector_map_path = Path("config/sector_map.json")
sector_map = json.loads(sector_map_path.read_text())
map_symbols = set(sector_map["stocks"].keys())

in_nse_not_map = nse_symbols - map_symbols
in_map_not_nse = map_symbols - nse_symbols

print(f"\nsector_map.json has {len(map_symbols)} stocks")
print(f"NSE official list has {len(nse_symbols)} stocks")

if in_nse_not_map:
    print(f"\n[ADD to sector_map] In NSE list but NOT in sector_map ({len(in_nse_not_map)}):")
    for s in sorted(in_nse_not_map):
        print(f"  + {s}")

if in_map_not_nse:
    print(f"\n[REMOVE from sector_map] In sector_map but NOT in NSE list ({len(in_map_not_nse)}):")
    for s in sorted(in_map_not_nse):
        print(f"  - {s}")

if not in_nse_not_map and not in_map_not_nse:
    print("\nSector map is IN SYNC with NSE official list.")

if not_found_in_kite:
    print(f"\n[WARNING] These NSE symbols not found in Kite EQ instruments:")
    for s in not_found_in_kite:
        print(f"  ? {s}")
