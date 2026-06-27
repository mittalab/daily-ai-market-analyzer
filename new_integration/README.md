# new_integration

Live data integrations that pull from external broker/exchange APIs at runtime.

---

## kite_positions.py

Fetches current open F&O and commodity positions from Zerodha Kite.

### Output shape

```json
{
  "NFO": {
    "NIFTY_50": [
      { "symbol": "NIFTY2507FUT", "qty": 75, "avg": 24500.00, "ltp": 24623.45,
        "pnl": 9258.75, "unrealised": 9258.75, "realised": 0.0,
        "product": "NRML", "exchange": "NFO", "buy_qty": 75, "sell_qty": 0 }
    ],
    "RELIANCE": [ ... ]
  },
  "MCX": {
    "SILVER": [
      { "symbol": "SILVERM25JULFUT", "qty": -1, "avg": 95000.00, "ltp": 96200.00,
        "pnl": -1200.00, ... }
    ]
  }
}
```

- Top-level keys are exchanges (`NFO`, `MCX`). Both keys are always present even when empty.
- Second-level keys are underlying symbols. `NIFTY` is remapped to `NIFTY_50`.
- Only positions with non-zero net quantity are included.

### CLI usage

```bash
# Formatted table
py -m new_integration.kite_positions

# Include intraday (day) positions not present in net
py -m new_integration.kite_positions --include-day

# Raw JSON
py -m new_integration.kite_positions --json
```

### Programmatic usage

```python
from new_integration.kite_positions import fetch_fo_positions

positions = fetch_fo_positions()          # net only (default)
nfo = positions["NFO"]                    # all NFO underlyings
nifty_legs = nfo.get("NIFTY_50", [])     # list of NIFTY position dicts
```

### Auth

Reads `KITE_API_KEY` from `.env` and the access token from the `kite_tokens` table in Supabase. Run the OAuth flow (`new_data_ingestion/kite_oauth.py`) if the token is missing or expired.

---

## kite_holdings.py

Fetches open equity holdings from Zerodha Kite. It automatically calculates total quantities by summing up free/tradeable quantities, T1 (unsettled) quantities, and collateral (pledged) quantities.

### Output shape

```json
{
  "NSE": {
    "HINDALCO": [
      {
        "symbol": "HINDALCO",
        "qty": 700,
        "free_qty": 0,
        "t1_qty": 0,
        "collateral_qty": 700,
        "collateral_type": "pledge",
        "avg": 1032.1479,
        "ltp": 953.20,
        "close": 953.20,
        "pnl": -55263.50,
        "current_value": 667240.00,
        "investment_value": 722503.50,
        "day_change": 0.0,
        "day_change_pct": 0.0,
        "isin": "INE038A01020",
        "product": "CNC",
        "exchange": "NSE"
      }
    ]
  },
  "BSE": { ... }
}
```

- Top-level keys are exchanges (`NSE`, `BSE`). Both keys are always present even when empty.
- Second-level keys are symbols (e.g. `HINDALCO`).
- Only holdings with non-zero total quantity (free + T1 + collateral + MTF) are included.

### CLI usage

```bash
# Formatted table
py -m new_integration.kite_holdings

# Raw JSON
py -m new_integration.kite_holdings --json
```

### Programmatic usage

```python
from new_integration.kite_holdings import fetch_holdings

holdings = fetch_holdings()
nse_holdings = holdings["NSE"]
hindalco_legs = nse_holdings.get("HINDALCO", [])
```
