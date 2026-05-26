"""
One-shot bootstrap: fetch 6-month NIFTY_50 + INDIA_VIX history from Kite.
Run once to seed price_history so market regime has enough rows.
"""
import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s - %(message)s")

from datetime import date, timedelta
import pandas as pd
from integrations.kite_oauth import get_authenticated_kite
from database.queries import upsert_price_history

kite = get_authenticated_kite()

# Find index tokens from NSE instruments master
instruments = kite.instruments("NSE")
df = pd.DataFrame(instruments)

nifty_row = df[df["tradingsymbol"] == "NIFTY 50"]
vix_row   = df[df["tradingsymbol"] == "INDIA VIX"]

print("NIFTY 50 rows:", len(nifty_row))
print("INDIA VIX rows:", len(vix_row))

if nifty_row.empty:
    # Also try NFO or NSE indices
    df2 = pd.DataFrame(kite.instruments("BSE"))
    nifty_row = df2[df2["tradingsymbol"] == "NIFTY 50"]
    print("BSE NIFTY 50:", len(nifty_row))

to_date   = date.today()
from_date = to_date - timedelta(days=200)

stored = 0
for symbol_key, row_df in [("NIFTY_50", nifty_row), ("INDIA_VIX", vix_row)]:
    if row_df.empty:
        print(f"{symbol_key}: NOT FOUND in instruments — skipping")
        continue

    token = int(row_df.iloc[0]["instrument_token"])
    print(f"\n{symbol_key}: token={token}, fetching {from_date} → {to_date}")

    try:
        raw = kite.historical_data(
            instrument_token=token,
            from_date=str(from_date),
            to_date=str(to_date),
            interval="day",
        )
        if not raw:
            print(f"  No data returned")
            continue

        rows = []
        for r in raw:
            dt = r["date"]
            if hasattr(dt, "date"):
                dt = dt.date()
            rows.append({
                "symbol": symbol_key,
                "date":   str(dt),
                "open":   float(r.get("open") or 0),
                "high":   float(r.get("high") or 0),
                "low":    float(r.get("low")  or 0),
                "close":  float(r.get("close") or 0),
                "volume": int(r.get("volume") or 0),
            })

        n = upsert_price_history(rows)
        print(f"  Stored {n} rows")
        stored += n
    except Exception as e:
        print(f"  ERROR: {e}")

print(f"\nTotal stored: {stored} rows")
