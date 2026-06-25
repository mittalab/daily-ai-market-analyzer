# Data Validation & Self-Healing Package (new_validation)

This package implements modular checks to verify that all pricing and derivative snapshots required for analysis are present in the database. When gaps are detected, a self-healing loop automatically invokes historical or today's ingestion routines to fetch and load the missing records.

## Usage

### Run daily validations (for Nifty 50 and active watchlist)
```bash
python new_validation/run_validation.py --mode daily
```

### Validate and backfill a specific stock
```bash
python new_validation/run_validation.py --mode manual --symbol Reliance
```

### Validate a specific historical date
```bash
python new_validation/run_validation.py --mode manual --symbol Reliance --date 2026-06-23
```

### Options:
- `--mode`: `daily` or `manual`.
- `--symbol`: Required for `manual` mode.
- `--date`: The date YYYY-MM-DD to validate (defaults to today's last trading day).
- `--force`: Bypass caching and run validation checks directly.

---

## Technical Details

### 1. Cache
Validation results are persisted in the `validation_states` DB table. If a symbol and date have a cached `PASSED` status, the script returns immediately in $O(1)$ time. 

### 2. The 8 Modular Checks
1. **validate_kite_token**: Verifies Kite Connect session status.
2. **validate_db_connectivity**: Pings the Supabase client.
3. **validate_stock_ohlcv**: Verifies &ge; 180 trading days of OHLCV history ending on target date.
4. **validate_stock_options**: Verifies options snapshots exist for stock expiries.
5. **validate_stock_futures**: Verifies futures snapshots exist for stock expiries.
6. **validate_index_ohlcv**: Verifies &ge; 180 trading days of Nifty 50 index pricing.
7. **validate_index_options**: Verifies option snapshots exist for weekly Nifty expiries.
8. **validate_india_vix**: Verifies &ge; 30 trading days of VIX pricing.
