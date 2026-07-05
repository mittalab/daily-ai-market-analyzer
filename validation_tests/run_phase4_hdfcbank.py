import os
import json
import logging
from datetime import date
import sys
import anthropic
from dotenv import load_dotenv

# Set logs to error to avoid flooding stdout
logging.basicConfig(level=logging.ERROR)
load_dotenv()

# Add root folder to python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.queries import get_latest_session, get_claude_turn
from pipeline.claude_session import _build_turn3_data, _build_turn3_prompt, _validate_position_sizing_turn3
from pipeline.deep_analysis import call_claude_deep

def run_phase4():
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("Error: ANTHROPIC_API_KEY is not set in environment or .env.")
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
    print(f"Running Phase 4 Claude Deep Analysis for symbol: {symbol}")
    
    # 3. Assemble package and prompt
    package = _build_turn3_data(symbol, session_date, turn1_result, turn2_result)
    if not package:
        print("Failed to build package!")
        return
        
    prompt = _build_turn3_prompt(package)
    print("CLAUDE INPUT PROMPT")
    print(prompt)
    # 4. Call Claude
    print("Calling Claude deep analysis API model (please wait)...")
    try:
        from pipeline.deep_analysis import _MODEL, DEEP_SYSTEM
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=8000,
            system=[{
                "type": "text",
                "text": DEEP_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text
        print("\n--- Raw Claude Response ---")
        print(raw.encode('ascii', errors='replace').decode('ascii'))
        
        # Parse it
        t = raw.strip()
        if t.startswith("```"):
            t = t[t.index("\n") + 1:]
        if t.endswith("```"):
            t = t[:t.rindex("```")]
        analysis = json.loads(t.strip())
        in_tok = resp.usage.input_tokens
        out_tok = resp.usage.output_tokens
    except Exception as exc:
        print(f"Error calling Claude: {exc}")
        return
        
    print(f"Claude Call Success! Input Tokens: {in_tok}, Output Tokens: {out_tok}")
    
    # 5. Position Validation
    config = {"claude_capital_inr": 500000.0}
    analysis["symbol"] = symbol
    analysis = _validate_position_sizing_turn3(analysis, config)
    
    # 6. Output formatted response
    print("\n--- Claude JSON Response ---")
    print(json.dumps(analysis, indent=2).encode('ascii', errors='replace').decode('ascii'))
    
    # Save the output to a text file for record
    out_file = "hdfc_turn3_response.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)
    print(f"\nResponse saved to {out_file}")

if __name__ == "__main__":
    run_phase4()
