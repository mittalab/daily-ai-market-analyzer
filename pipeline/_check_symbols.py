from integrations.kite_oauth import get_authenticated_kite
from datetime import date, timedelta
import pandas as pd

kite = get_authenticated_kite()

# Check instruments NFO for futures — these symbols ARE on NSE F&O
instruments = kite.instruments('NFO')
df = pd.DataFrame(instruments)
fut = df[df['instrument_type']=='FUT']
tatam = fut[fut['tradingsymbol'].str.contains('TATAMOT', na=False)]
ltim  = fut[fut['tradingsymbol'].str.contains('LTIM', na=False)]
print("TATAMOTORS futures:", tatam['tradingsymbol'].tolist()[:5])
print("LTIM futures:", ltim['tradingsymbol'].tolist()[:5])

# Also check sector_map to see what symbols we loaded
from integrations.nse_bhavcopy import get_nifty50_symbols
syms = sorted(get_nifty50_symbols())
print(f"\nSymbols in sector_map ({len(syms)}):")
print(syms)

# Check NSE EQ for exact matches
nse = kite.instruments('NSE')
nse_df = pd.DataFrame(nse)
nse_eq = nse_df[nse_df['instrument_type']=='EQ']
for s in ['TATAMOTORS', 'LTIM', 'M&M']:
    row = nse_eq[nse_eq['tradingsymbol']==s]
    print(f"{s}: {'FOUND token='+str(row['instrument_token'].values[0]) if not row.empty else 'NOT FOUND'}")
