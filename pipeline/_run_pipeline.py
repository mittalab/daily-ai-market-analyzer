"""
Week 4 end-to-end test — runs the full pipeline for last_trading_day().

Requires ANTHROPIC_API_KEY to be set in .env.
Will abort with a clear message if the key is blank.

Run:
    python.exe -m pipeline._run_pipeline
"""
import logging
import sys
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import argparse

parser = argparse.ArgumentParser(description="Run the Swing Trading Analysis Pipeline")
parser.add_argument(
    "--mandatory",
    type=str,
    help="Comma-separated list of mandatory stock symbols to include, e.g. INFY,TCS",
)
args = parser.parse_args()

mandatory_stocks = []
if args.mandatory:
    mandatory_stocks = [s.strip().upper() for s in args.mandatory.split(",") if s.strip()]

from integrations.nse_bhavcopy import last_trading_day
from pipeline.orchestrator import run_pipeline

analysis_date = last_trading_day()

print(f"Starting pipeline for {analysis_date}...")
if mandatory_stocks:
    print(f"Mandatory stocks specified: {mandatory_stocks}")
print("=" * 60)

result = run_pipeline(analysis_date, mandatory_stocks=mandatory_stocks)

if "error" in result:
    print(f"\nPipeline aborted: {result['error']}")
    sys.exit(1)

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("PIPELINE RESULT SUMMARY")
print("=" * 60)
print(f"  Session ID      : {result['session_id']}")
print(f"  Regime          : {result['regime']}")
print(f"  Level 1 passed  : {result['level1_passed']} stocks")
print(f"  Prescan results : {len(result['turn2_results'])} stocks")
print(f"  Forwarded deep  : {result['prescan_forwarded']}")
print(f"  Tokens in/out   : {result['total_input_tokens']} / {result['total_output_tokens']}")
print(f"  Cost            : ${result['cost_usd']:.4f}")

# ── Turn 1 JSON output ────────────────────────────────────────────────────────
t1 = result["turn1_result"]
print("\n--- Turn 1: Market Context ---")
print(f"  Narrative   : {t1.get('session_narrative', '')[:200]}")
print(f"  Favourable  : {t1.get('favourable_setups', 'N/A')}")
print(f"  Risk flags  : {t1.get('risk_flags', [])}")
print(f"  Key levels  : {t1.get('index_key_levels', {})}")

# ── Turn 2: Pre-scan sample ───────────────────────────────────────────────────
t2 = result["turn2_results"]
print(f"\n--- Turn 2: Pre-Scan ({len(t2)} stocks) ---")

high_pri = [s for s in t2 if s.get("priority") == "HIGH"]
if high_pri:
    print(f"\n  HIGH priority ({len(high_pri)}):")
    for s in high_pri[:5]:
        print(f"    {s.get('symbol'):12} {s.get('direction'):8} fwd={s.get('forward_to_deep')} | {s.get('pre_scan_reasoning','')[:80]}")

fwd_all = [s for s in t2 if s.get("forward_to_deep")]
print(f"\n  All forwarded to deep: {[s.get('symbol') for s in fwd_all]}")
print(f"  Not forwarded (SKIP): {[s.get('symbol') for s in t2 if not s.get('forward_to_deep')][:10]}...")

# ── Supabase session record ───────────────────────────────────────────────────
from database.queries import get_analysis_session
s = get_analysis_session(result["session_id"])
if s:
    print(f"\n--- Supabase session record ---")
    print(f"  status              : {s.get('status')}")
    print(f"  market_regime       : {s.get('market_regime')}")
    print(f"  stocks_level1_passed: {s.get('stocks_level1_passed')}")
    print(f"  claude_tokens_input : {s.get('claude_tokens_input')}")
    print(f"  claude_tokens_output: {s.get('claude_tokens_output')}")
    print(f"  claude_cost_usd     : {s.get('claude_cost_usd')}")
    print(f"  stage_statuses      : {s.get('stage_statuses')}")
else:
    print("\n[WARNING] Session not found in Supabase")
