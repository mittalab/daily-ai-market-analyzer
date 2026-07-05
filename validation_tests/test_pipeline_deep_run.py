import os
import json
import logging
import sys
from datetime import date
import anthropic
from dotenv import load_dotenv

# Set logs to error to avoid flooding stdout
logging.basicConfig(level=logging.ERROR)
load_dotenv()

# Add root folder to python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.client import get_client
from database.queries import get_latest_session, get_claude_turn
from pipeline.claude_session import run_turn_deep_analysis

def run_integration_test():
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("Error: ANTHROPIC_API_KEY is not set.")
        return
        
    client = anthropic.Anthropic(api_key=api_key)
    
    # 1. Fetch latest completed session
    session = get_latest_session()
    if not session:
        print("Error: No sessions found in database.")
        return
        
    session_id = session["session_id"]
    session_date = session["session_date"]
    if isinstance(session_date, str):
        session_date = date.fromisoformat(session_date)
        
    print(f"Using Session: {session_id} | Date: {session_date}")
    
    # 2. Get Turn 1 & Turn 2 results
    t1_row = get_claude_turn(session_id, 1)
    t2_row = get_claude_turn(session_id, 2)
    
    if not t1_row or not t2_row:
        print("Error: Turn 1 or Turn 2 data missing for this session.")
        return
        
    turn1_result = json.loads(t1_row["output_text"])
    turn2_result = json.loads(t2_row["output_text"])
    
    # If turn2_result is not list, handle it
    if not isinstance(turn2_result, list):
        if "stock_assessments" in turn2_result:
            turn2_result = turn2_result["stock_assessments"]
        elif "stocks" in turn2_result:
            turn2_result = turn2_result["stocks"]
        else:
            turn2_result = [turn2_result]
            
    symbol = "HDFCBANK"
    print(f"Running pipeline deep analysis integration test for: {symbol}")
    
    # Setup index context
    index_ctx = {
        "regime": "SIDEWAYS_LOW_WIDE",
        "market_trend": "SIDEWAYS",
        "market_volatility": "LOW",
        "market_structure": "WIDE",
        "execution_bias": "NEUTRAL",
        "fii_dii_stance": "NEUTRAL",
        "nifty_close": 24000.0,
        "vix": 12.0,
        "ema20": 23800.0,
        "ema50": 23500.0,
        "ret20d_pct": 2.0
    }
    
    # Run the deep analysis call (saves to DB internally!)
    trade_ready_list = []
    config = {"claude_capital_inr": 500000.0}
    
    print("Calling run_turn_deep_analysis() (this calls Claude live and saves to DB)...")
    analysis, cost = run_turn_deep_analysis(
        client=client,
        session_id=session_id,
        session_date=session_date,
        symbol=symbol,
        direction="LONG",
        is_re=False,
        days_in=0,
        index_ctx=index_ctx,
        config=config,
        turn_num=3,
        trade_ready_list=trade_ready_list,
        max_tokens=8000,
        turn1_result=turn1_result,
        turn2_result=turn2_result
    )
    
    print("\nDeep Analysis Run Completed!")
    print(f"Outcome Stage:      {analysis.get('stage')}")
    print(f"Conviction Score:   {analysis.get('conviction_score')}")
    print(f"Adjusted Score:     {analysis.get('adjusted_score')}")
    print(f"Lots Sized:         {analysis.get('lots')}")
    print(f"Risk INR:           {analysis.get('max_risk_inr')}")
    print(f"Cost USD:           {cost.get('cost_usd')}")
    
    # 3. Verify record was written to Supabase trade_setups table
    print("\nVerifying DB insertion...")
    db_resp = (
        get_client()
        .table("trade_setups")
        .select("*")
        .eq("session_id", session_id)
        .eq("symbol", symbol)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    
    if db_resp.data:
        record = db_resp.data[0]
        print("Success! Verified trade setup record exists in trade_setups table:")
        print(f"- ID: {record['id']}")
        print(f"- Stage: {record['stage']}")
        print(f"- Recommended Instrument: {record['instrument']}")
        print(f"- Strike: {record['strike']}")
        print(f"- Entry zone: {record['entry_zone_low']} to {record['entry_zone_high']}")
        print(f"- Stop Loss: {record['stop_loss_premium']}")
        print(f"- Lots Sized: {record['lots']}")
        print(f"- Max Risk INR: {record['max_risk_inr']}")
    else:
        print("Error: Could not find trade setup record in DB trade_setups table!")
        
    print("\nIntegration test finished!")

if __name__ == "__main__":
    run_integration_test()
