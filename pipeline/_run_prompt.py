"""Quick smoke-test for system_prompt_builder."""
import sys
import logging
logging.basicConfig(level=logging.WARNING)

# Force UTF-8 output so Unicode characters in the prompt print correctly on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from integrations.nse_bhavcopy import last_trading_day
from pipeline.market_regime import run_market_regime
from pipeline.context_builder import build_context_bundle
from pipeline.system_prompt_builder import build_system_prompt

d  = last_trading_day()
r  = run_market_regime(d)
b  = build_context_bundle(d, f"SESSION_{d.strftime('%Y%m%d')}", regime_result=r)
p  = build_system_prompt(b)

print(f"Prompt length: {len(p)} chars")
print("--- First 600 chars ---")
print(p[:600])
