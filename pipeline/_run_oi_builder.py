import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")

from integrations.nse_bhavcopy import get_nifty50_symbols, last_trading_day
from pipeline.oi_series_builder import run_oi_series_builder, determine_rollover_phase, _trading_days_to
from datetime import date

symbols       = sorted(get_nifty50_symbols())
analysis_date = last_trading_day()

# ── Rollover phase preview (using near_expiry confirmed from Kite) ─────────────
NEAR_EXPIRY = date(2026, 5, 26)   # confirmed from Kite
NEXT_EXPIRY = date(2026, 6, 26)   # verify
days_left   = _trading_days_to(analysis_date, NEAR_EXPIRY)
phase       = determine_rollover_phase(analysis_date, NEAR_EXPIRY)

print("=" * 60)
print(f"ROLLOVER PHASE CHECK")
print(f"  analysis_date : {analysis_date}")
print(f"  near_expiry   : {NEAR_EXPIRY}")
print(f"  next_expiry   : {NEXT_EXPIRY}")
print(f"  trading_days  : {days_left} days to expiry")
print(f"  phase         : {phase}")
print("=" * 60)

print(f"\nRunning OI series builder on {len(symbols)} symbols for {analysis_date}")
print("-" * 60)

result = run_oi_series_builder(symbols, analysis_date)

print(f"\nStored         : {result['stored']} rows in continuous_oi_series")
print(f"No futures     : {len(result['no_futures'])} symbols  {result['no_futures'] or ''}")
print(f"No options     : {len(result['no_options'])} symbols  (expected — snapshot runs at 3:25 PM)")
print(f"Errors         : {len(result['errors'])}")

if result["errors"]:
    for e in result["errors"]:
        print(f"  {e['symbol']}: {e['error']}")

# Show sample rows from DB
from database.queries import get_continuous_oi, get_futures_row

print("\n--- Sample continuous_oi_series rows ---")
for sym in ["HDFCBANK", "RELIANCE", "BAJFINANCE"]:
    rows = get_continuous_oi(sym, days=1)
    if rows:
        r = rows[-1]
        print(f"  {sym:12} date={r['date']} phase={r['rollover_phase']} "
              f"near_oi={r['near_month_oi']} rollover%={r['rollover_pct']}")
    else:
        print(f"  {sym:12} no row")

print("\n--- Sample futures_continuous_series (spot_price patched) ---")
for sym in ["HDFCBANK", "RELIANCE", "BAJFINANCE"]:
    row = get_futures_row(sym, analysis_date)
    if row:
        print(f"  {sym:12} futures={row['futures_price']} spot={row['spot_price']} "
              f"basis={row['basis']} basis%={row['basis_pct']}")
    else:
        print(f"  {sym:12} no row")

# ── Row counts ──────────────────────────────────────────────────────────────────
from database.client import get_client
_db = get_client()
oi_count  = _db.table("continuous_oi_series").select("symbol", count="exact").execute()
fut_count = _db.table("futures_continuous_series").select("symbol", count="exact").execute()
print(f"\n--- Table row counts ---")
print(f"  continuous_oi_series      : {oi_count.count} rows")
print(f"  futures_continuous_series : {fut_count.count} rows")
