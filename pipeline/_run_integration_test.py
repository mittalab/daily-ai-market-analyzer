"""
Week 3 Integration Test — all 3 pre-processing stages in sequence.

Stages (spec Section 10):
  2.5 OI Continuous Series Builder → continuous_oi_series
  2.6 Market Regime Detection      → regime string
  3   Level 1 Filter               → passed / eliminated lists

Also creates + updates an analysis_sessions record (STEP 5).
"""
import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s - %(message)s")

from datetime import datetime

import pytz

from new_integrations.nse_bhavcopy import last_trading_day
from pipeline.oi_series_builder import run_oi_series_builder
from pipeline.market_regime import get_index_indicators
from pipeline.level1_filter import run_level1_filter, fetch_nse_earnings_window
from database.queries import create_analysis_session, update_analysis_session, get_analysis_session

IST = pytz.timezone("Asia/Kolkata")

# ── Setup ──────────────────────────────────────────────────────────────────────
symbols       = sorted(get_nifty50_symbols())
analysis_date = last_trading_day()
session_id    = f"SESSION_{analysis_date.strftime('%Y%m%d')}"
started_at    = datetime.now(IST).isoformat()

# kite is accepted by run_level1_filter but not actually called — pass None safely
try:
    from integrations.kite_oauth import get_authenticated_kite
    kite = get_authenticated_kite()
except Exception:
    kite = None

print(f"Week 3 Integration Test")
print(f"Date    : {analysis_date}")
print(f"Session : {session_id}")
print(f"Symbols : {len(symbols)}")
print("=" * 60)

# ── STEP 5: Create session record ──────────────────────────────────────────────
try:
    create_analysis_session(session_id, analysis_date)
    print(f"\n[SESSION] Created {session_id}")
except Exception as e:
    print(f"\n[SESSION] Already exists ({e.__class__.__name__}) — continuing")

update_analysis_session(session_id, {
    "status":         "RUNNING",
    "started_at":     started_at,
    "stage_statuses": {"data_ingestion": "COMPLETE", "started_ist": started_at},
})

# ── STAGE 2.5: OI Continuous Series Builder ───────────────────────────────────
print("\n[STAGE 2.5] OI Continuous Series Builder...")
oi_result = run_oi_series_builder(symbols, analysis_date)
print(f"  Stored    : {oi_result['stored']} rows")
print(f"  No futures: {len(oi_result['no_futures'])}")
print(f"  No options: {len(oi_result['no_options'])} (snapshot runs at 3:25 PM)")
print(f"  Errors    : {len(oi_result['errors'])}")

update_analysis_session(session_id, {
    "stage_statuses": {
        "data_ingestion": "COMPLETE",
        "oi_series":      "COMPLETE" if not oi_result["errors"] else "PARTIAL",
        "oi_stored":      oi_result["stored"],
    }
})

# ── STAGE 2.6: Index Indicators ───────────────────────────────────────────────
print("\n[STAGE 2.6] Index Indicators...")
nifty = get_index_indicators(analysis_date, "NIFTY_50")
vix   = get_index_indicators(analysis_date, "INDIA_VIX")
print(f"  Nifty close   : {nifty['close']}")
print(f"  EMA20         : {nifty['ema20']}")
print(f"  EMA50         : {nifty['ema50']}")
print(f"  20-day return : {nifty['ret20d']}%")
print(f"  VIX close     : {vix['close']}")

update_analysis_session(session_id, {
    "nifty_close":   nifty["close"],
    "vix_close":     vix["close"],
    "stage_statuses": {
        "data_ingestion": "COMPLETE",
        "oi_series":      "COMPLETE" if not oi_result["errors"] else "PARTIAL",
    }
})

# ── STAGE 3: Level 1 Filter ───────────────────────────────────────────────────
print("\n[STAGE 3] Level 1 Filter...")
print("  Fetching NSE earnings calendar...")
earnings_window = fetch_nse_earnings_window(analysis_date)
print(f"  Stocks with earnings events : {len(earnings_window)}")

l1_result = run_level1_filter(symbols, analysis_date, kite, earnings_window)
print(f"  PASSED    : {len(l1_result['passed'])}")
print(f"  ELIMINATED: {len(l1_result['eliminated'])}")
print(f"  ERRORS    : {len(l1_result['errors'])}")
if l1_result["filter_skipped"]:
    print(f"  SKIPPED   : {l1_result['filter_skipped']}")

print("\n  Eliminated stocks:")
for e in l1_result["eliminated"]:
    r = e["reason"]
    if r == "EARNINGS":
        print(f"    {e['symbol']:15} EARNINGS     {e.get('detail','')}")
    elif r == "ATR_DEAD":
        print(f"    {e['symbol']:15} ATR_DEAD     ({e['value']}%)")
    elif r == "FNO_ILLIQUID":
        print(f"    {e['symbol']:15} FNO_ILLIQUID (ATM OI={e['atm_oi']})")

# ── STEP 5: Final session update ──────────────────────────────────────────────
update_analysis_session(session_id, {
    "stocks_level1_passed": len(l1_result["passed"]),
    "nifty_close":          nifty["close"],
    "vix_close":            vix["close"],
    "status":               "PRE_PROCESSING_COMPLETE",
    "stage_statuses": {
        "data_ingestion": "COMPLETE",
        "level1_filter":  "COMPLETE" if not l1_result["errors"] else "PARTIAL",
        "oi_series":      "COMPLETE" if not oi_result["errors"] else "PARTIAL",
        "l1_passed":      len(l1_result["passed"]),
        "l1_eliminated":  len(l1_result["eliminated"]),
        "eliminated_list": [e["symbol"] for e in l1_result["eliminated"]],
    }
})

# ── Final summary ──────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("WEEK 3 INTEGRATION TEST — SUMMARY")
print("=" * 60)
print(f"Date           : {analysis_date}")
print(f"Session ID     : {session_id}")
print(f"Nifty close    : {nifty['close']}  (VIX={vix['close']})")
print(f"OI Rows Stored : {oi_result['stored']}")
print(f"Level 1 Passed : {len(l1_result['passed'])} / {len(symbols)}")
print(f"Eliminated     : {[e['symbol'] for e in l1_result['eliminated']]}")
print(f"Errors         : {len(l1_result['errors']) + len(oi_result['errors'])}")

# ── STEP 5: Verify session row from Supabase ──────────────────────────────────
print()
s = get_analysis_session(session_id)
if s:
    print(f"[DB] session_id            : {s['session_id']}")
    print(f"[DB] session_date          : {s['session_date']}")
    print(f"[DB] status                : {s['status']}")
    print(f"[DB] market_regime         : {s['market_regime']}")
    print(f"[DB] nifty_close           : {s['nifty_close']}")
    print(f"[DB] vix_close             : {s['vix_close']}")
    print(f"[DB] stocks_level1_passed  : {s['stocks_level1_passed']}")
    print(f"[DB] stage_statuses        : {s['stage_statuses']}")
    print(f"[DB] started_at            : {s['started_at']}")
else:
    print("[DB] WARNING: session not found in Supabase")
