import logging
from datetime import date

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")

from integrations.kite_oauth import get_authenticated_kite
from integrations.nse_bhavcopy import get_nifty50_symbols, last_trading_day
from pipeline.level1_filter import run_level1_filter

kite          = get_authenticated_kite()
symbols       = sorted(get_nifty50_symbols())
analysis_date = last_trading_day()

print(f"Running Level 1 filter on {len(symbols)} symbols for {analysis_date}")
print("-" * 60)

result = run_level1_filter(symbols, analysis_date, kite)

print()
print(f"PASSED     : {len(result['passed'])} stocks")
print(f"ELIMINATED : {len(result['eliminated'])} stocks")
print(f"ERRORS     : {len(result['errors'])}")

if result["eliminated"]:
    print()
    print("Eliminated:")
    for e in result["eliminated"]:
        r = e["reason"]
        if r == "ATR_DEAD":
            print(f"  {e['symbol']:15} ATR_DEAD     (ATR%={e['value']}%)")
        elif r == "EARNINGS":
            print(f"  {e['symbol']:15} EARNINGS     ({e.get('detail','')})")
        elif r == "FNO_ILLIQUID":
            print(f"  {e['symbol']:15} FNO_ILLIQUID (ATM OI={e['atm_oi']})")

if result["filter_skipped"]:
    print()
    print(f"Skipped filters : {result['filter_skipped']}")

if result["errors"]:
    print()
    print("Errors:")
    for e in result["errors"]:
        print(f"  {e['symbol']}: {e['error']}")

print()
print("Passed:", result["passed"])
