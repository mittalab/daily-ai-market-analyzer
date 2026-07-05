import json
from datetime import date
from database.queries import get_latest_session, get_claude_turn
from pipeline.claude_session import _build_turn3_data, _build_turn3_prompt

def run_test():
    # 1. Fetch latest completed session
    session = get_latest_session()
    if not session:
        print("Error: No sessions found in the database. Cannot run test.")
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
            
    # Use HDFCBANK
    symbol = "HDFCBANK"
    print(f"Building Turn 3 prompt for symbol: {symbol}")
    
    # 3. Assemble package and prompt
    package = _build_turn3_data(symbol, session_date, turn1_result, turn2_result)
    if not package:
        print("Failed to build package!")
        return
        
    prompt = _build_turn3_prompt(package)
    
    # 4. Assert and print details
    word_count = len(prompt.split())
    # Est. token count (1 word ≈ 1.33 tokens)
    token_est = int(word_count * 1.33)
    
    print("\nPROMPT ASSEMBLY REPORT:")
    print("==========================================")
    print(f"Word count:         {word_count}")
    print(f"Estimated tokens:   {token_est}")
    print(f"Is within 10k:      {'YES (PASS)' if token_est < 10000 else 'NO (FAIL)'}")
    
    print("\nPrompt Section Check:")
    sections = [
        "[SECTION A: ROLE AND TASK DEFINITION]",
        "[SECTION B: STOCK CONTEXT]",
        "[SECTION C: MARKET CONTEXT]",
        "[SECTION D: PRICE DATA]",
        "[SECTION E: F&O DATA]",
        "[SECTION F: SCORING INSTRUCTIONS]",
        "[SECTION G: OUTPUT SPECIFICATION]"
    ]
    for sec in sections:
        present = sec in prompt
        print(f"- {sec:<40}: {'PRESENT' if present else 'MISSING'}")
        assert present, f"Missing section {sec}"
        
    print("\n--- First 1500 characters of the Prompt ---")
    print(prompt[:1500])
    print("\n... [Time series data] ...\n")
    print("--- Last 1000 characters of the Prompt ---")
    print(prompt[-1000:])
    
    print("\nAll prompt structure checks passed successfully!")
    print(prompt)

if __name__ == "__main__":
    run_test()
