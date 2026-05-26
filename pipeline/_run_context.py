"""
STEP 1 runner — build and print the context bundle for tonight's session.
"""
import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s - %(message)s")

from integrations.nse_bhavcopy import last_trading_day
from pipeline.market_regime import run_market_regime
from pipeline.context_builder import build_context_bundle

analysis_date = last_trading_day()
session_id    = f"SESSION_{analysis_date.strftime('%Y%m%d')}"

print(f"Building context bundle for {session_id} ({analysis_date})...")

regime_result = run_market_regime(analysis_date)
bundle        = build_context_bundle(analysis_date, session_id, regime_result=regime_result)

rc = bundle.get("rollover_context")

print()
print("=" * 60)
print("CONTEXT BUNDLE SUMMARY")
print("=" * 60)
print(f"  Session ID      : {bundle['session_id']}")
print(f"  Session date    : {bundle['session_date']}")
print(f"  Regime          : {bundle['regime']['regime'] if bundle['regime'] else 'N/A'}")
print(f"  Nifty close     : {bundle['regime']['nifty_close'] if bundle['regime'] else 'N/A'}")
print(f"  VIX             : {bundle['regime']['vix'] if bundle['regime'] else 'N/A'}")
print(f"  Available slots : {bundle['available_slots']} of {bundle['max_slots']}")
print(f"  Rollover phase  : {rc['rollover_phase'] if rc else 'N/A (no OI rows for date)'}")
if rc:
    print(f"  Near expiry     : {rc.get('near_expiry')}")
    print(f"  Rollover %      : {rc.get('rollover_pct')}")
print(f"  Watchlist stocks: {len(bundle['active_watchlist'])}")
print(f"  Open positions  : {len(bundle['open_positions'])}")
print(f"  Recent outcomes : {len(bundle['recent_outcomes'])}")
print(f"  Config keys     : {len(bundle['config'])}")
print(f"  System memory   : {len(bundle['system_memory'])} (Phase 1 — empty)")
print(f"  Active dirs     : {len(bundle['active_directives'])} (Phase 1 — empty)")

if bundle["open_positions"]:
    print("\n  Open positions:")
    for p in bundle["open_positions"]:
        print(f"    {p.get('symbol'):12} {p.get('direction','')}")

if bundle["active_watchlist"]:
    print("\n  Watchlist:")
    for w in bundle["active_watchlist"]:
        print(f"    {w.get('symbol'):12} stage={w.get('stage','')}")

if bundle["recent_outcomes"]:
    print("\n  Recent outcomes (last 7 days):")
    for o in bundle["recent_outcomes"]:
        print(f"    {o.get('symbol'):12} {o.get('setup_date')} "
              f"outcome={o.get('paper_outcome')}")
