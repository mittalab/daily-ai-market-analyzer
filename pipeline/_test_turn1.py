import os
import logging
from datetime import date
import anthropic
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")
logger = logging.getLogger(__name__)

from new_data_ingestion.nse_bhavcopy import last_trading_day
from pipeline.context_builder import build_context_bundle
from pipeline.system_prompt_builder import build_system_prompt
from pipeline.claude_session import run_turn1_market_context

def main():
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not found in env.")
        return
        
    client = anthropic.Anthropic(api_key=api_key)
    session_date = last_trading_day()
    session_id = f"TEST_SESSION_{session_date.strftime('%Y%m%d')}"
    
    print(f"Running isolated Turn 1 for {session_id} on {session_date}...")
    
    # 1. Build initial context bundle
    bundle = build_context_bundle(session_date, session_id, regime_result=None)
    
    # 2. Build system prompt
    system_text = build_system_prompt(bundle)
    
    # 3. Call Turn 1 context
    turn1_result, regime_result, messages, cost_info = run_turn1_market_context(
        client=client,
        session_id=session_id,
        session_date=session_date,
        system_text=system_text
    )
    
    # print("\n" + "=" * 60)
    # print("TURN 1 EXECUTION SUCCESSFUL")
    # print("=" * 60)
    # print(f"Input tokens  : {cost_info['input_tokens']}")
    # print(f"Output tokens : {cost_info['output_tokens']}")
    # print(f"Cost USD      : ${cost_info['total_cost_usd']:.4f}")
    # print("-" * 60)
    # print("Regime result classifications:")
    # print(f"  Regime      : {regime_result['regime']}")
    # print(f"  Trend       : {regime_result['market_trend']}")
    # print(f"  Volatility  : {regime_result['market_volatility']}")
    # print(f"  Structure   : {regime_result['market_structure']}")
    # print(f"  Bias        : {regime_result['execution_bias']}")
    # print(f"  Stance      : {regime_result['fii_dii_stance']}")
    # print(f"  Nifty close : {regime_result['nifty_close']}")
    # print(f"  VIX close   : {regime_result['vix']}")
    # print(f"  Sector Weights: {regime_result['sector_weights']}")
    # print(f"  Guidance    : {regime_result['guidance']}")

if __name__ == "__main__":
    main()
