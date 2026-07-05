import os
import sys
from datetime import date
from dotenv import load_dotenv

from new_notifications.telegram import send_loud

load_dotenv()
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pipeline.morning_brief import generate_morning_brief

def run_test():
    session_date = date(2026, 7, 3)
    print(f"Generating Morning Brief for date: {session_date}")
    
    # Mock database call to return a mock TRADE_READY setup alongside original
    import pipeline.morning_brief
    original_get_setups = pipeline.morning_brief.get_trade_setups_by_date
    
    def mock_get_setups(d):
        original = original_get_setups(d)
        mock_ready = {
            "symbol": "HDFCBANK",
            "direction": "LONG",
            "stage": "TRADE_READY",
            "setup_type": "Bull Flag (Complete)",
            "conviction_score": 75,
            "instrument": "OPTIONS",
            "strike": 800.0,
            "option_type": "CE",
            "expiry_date": "2026-07-29",
            "entry_zone_low": 18.0,
            "entry_zone_high": 24.0,
            "stop_loss_premium": 11.0,
            "target_1_premium": 30.0,
            "target_2_premium": 42.0,
            "lots": 2,
            "lot_size": 550,
            "max_risk_inr": 11000.0,
            "risk_reward": 2.1,
            "mentor_explanation": "Bull Flag breakout with dynamic support from the rising 20 EMA."
        }
        return original + [mock_ready]
        
    pipeline.morning_brief.get_trade_setups_by_date = mock_get_setups
    
    try:
        msg, _ = generate_morning_brief(session_date)
        send_loud(msg)
    finally:
        pipeline.morning_brief.get_trade_setups_by_date = original_get_setups
    
    print("\n--- Morning Brief Output (Safe Print) ---")
    print(msg.encode("ascii", errors="replace").decode("ascii"))
    print("\n------------------------------------------")
    print(f"Total message length: {len(msg)} characters (Telegram limit: 4096)")
    
    # Assertions
    assert "Morning Brief" in msg
    assert "HDFCBANK" in msg
    assert "OPTIONS" in msg
    assert "Risk" in msg
    assert "lots" in msg
    assert "Dashboard" in msg
    print("\nPhase 6 Validation Passed Successfully!")

if __name__ == "__main__":
    run_test()
