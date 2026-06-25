# new_utils

Shared utility classes used across the daily pipeline, API, and integrations.

---

## stock_list.py

Builds the complete symbol universe for each daily analysis run by merging four sources into a single deduplicated map.

### Sources and mandate rules

| Source | Where | `mandate` |
|---|---|---|
| Nifty 50 symbols | `config/sector_map.json` | `False` |
| Active Kite NFO positions | Live Kite API (open trades) | **`True`** |
| `interested_stocks` config | `system_config` table (comma-separated) | `False` |
| Watchlist | `watchlist_staging` table | `False` |

`mandate=True` means the symbol must receive deep analysis in that day's run. If a symbol appears in multiple sources, `mandate` is `True` if **any** source sets it true.

### Return shape

```python
{
    "TATASTEEL": {"symbol": "TATASTEEL", "mandate": True,  "sources": ["active_trade", "nifty50"]},
    "JIOFIN":    {"symbol": "JIOFIN",    "mandate": False, "sources": ["interested_stocks"]},
    "TCS":       {"symbol": "TCS",       "mandate": False, "sources": ["nifty50", "watchlist"]},
}
```

### Programmatic usage

```python
from new_utils.stock_list import get_stock_list_for_analysis

universe = get_stock_list_for_analysis()               # all sources
universe = get_stock_list_for_analysis(include_kite_trades=False)  # offline / no token

# Mandated symbols only
mandated = [v["symbol"] for v in universe.values() if v["mandate"]]
```

### CLI usage

```bash
# Full table (mandated + standard)
py -m new_utils.stock_list

# Only mandated symbols (active trades)
py -m new_utils.stock_list --mandate-only

# Skip Kite call (offline mode)
py -m new_utils.stock_list --no-kite

# Raw JSON
py -m new_utils.stock_list --json
```

### Adding extra stocks

Insert or update the `interested_stocks` key in Supabase:

```sql
UPDATE system_config
SET value = 'JIOFIN,IRFC,DIXON'
WHERE key = 'interested_stocks';
```

These symbols appear in the universe with `mandate=False`. If you also hold an active NFO trade in one of them, `mandate` is automatically promoted to `True`.
