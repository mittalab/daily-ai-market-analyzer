import json
from datetime import date
from database.queries import get_latest_session, get_claude_turn
from pipeline.claude_session import _build_turn3_data

def test_run():
    # 1. Fetch latest session details
    session = get_latest_session()
    if not session:
        print("Error: No sessions found in the database. Cannot run test.")
        return
        
    session_id = session["session_id"]
    session_date = session["session_date"]
    
    # Check if session_date is a string or date object
    if isinstance(session_date, str):
        session_date = date.fromisoformat(session_date)
        
    print(f"Using Session ID: {session_id} on Date: {session_date}")
    
    # 2. Query Turn 1 and Turn 2 outputs from the DB
    t1_row = get_claude_turn(session_id, 1)
    t2_row = get_claude_turn(session_id, 2)
    
    if not t1_row or not t2_row:
        print("Error: Could not retrieve Turn 1 or Turn 2 rows for session.")
        return
        
    turn1_result = json.loads(t1_row["output_text"])
    turn2_result = json.loads(t2_row["output_text"])
    
    # If turn2_result is not list, handle it
    if not isinstance(turn2_result, list):
        if "stock_assessments" in turn2_result:
            turn2_result = turn2_result["stock_assessments"]
        else:
            turn2_result = [turn2_result]

    # Find a symbol that was forwarded or just use HDFCBANK
    symbol = "HDFCBANK"
    # for item in turn2_result:
    #     if item.get("forward_to_deep") or item.get("symbol"):
    #         symbol = item.get("symbol")
    #         break
            
    print(f"Building data package for symbol: {symbol}")
    print(f"Turn 1 result: {turn1_result}")
    print(f"Turn 2 result: {turn2_result}")
    # 3. Assemble package
    package = _build_turn3_data(symbol, session_date, turn1_result, turn2_result)
    
    # 4. Print results & assertions
    if not package:
        print("Failed to build package!")
        return
        
    print("Package built successfully!")
    print("\nPackage Sections Overview:")
    for section_name in ["section1", "section2", "section3", "section4", "section5", "section6", "section7", "section8"]:
        section = package.get(section_name, {})
        print(f"- {section_name}: keys = {list(section.keys()) if section else 'None'}")
        
    # Pretty-print Section 1 and Section 3
    print("\n--- Section 1: Stock Identity ---")
    print(json.dumps(package["section1"], indent=2))
    
    print("\n--- Section 3: Indicators ---")
    print(json.dumps(package["section3"], indent=2))
    
    print("\n--- Section 6: Sector Context ---")
    print(json.dumps(package["section6"], indent=2))

    # Basic validations
    assert "symbol" in package["section1"], "section1 should contain symbol"
    assert "ohlcv_180d" in package["section2"], "section2 should contain ohlcv_180d"
    assert "ema20" in package["section3"], "section3 should contain ema20"
    assert "futures_available" in package["section4"], "section4 should contain futures_available"
    assert "options_available" in package["section5"], "section5 should contain options_available"
    assert "sector_known" in package["section6"], "section6 should contain sector_known"
    assert "market_trend" in package["section7"], "section7 should contain market_trend"
    assert "turn2_assessment" in package["section8"], "section8 should contain turn2_assessment"
    
    print("\nAll package structure assertions passed successfully!")

    print(package)

if __name__ == "__main__":
    test_run()
