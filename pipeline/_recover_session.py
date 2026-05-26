
import json
import logging
import os
import sys

# Add root to sys.path
sys.path.append(os.getcwd())

from database.client import get_client
from pipeline.claude_session import create_trade_setup

def recover_setups():
    client = get_client()
    session_id = "SESSION_20260526"
    
    # 1. Fetch turns
    res = client.table("session_claude_turns").select("symbol,output_text").eq("session_id", session_id).eq("turn_type", "deep_analysis").execute()
    turns = res.data
    
    print(f"Analyzing {len(turns)} turns...")
    recovered = 0
    
    for t in turns:
        analysis = json.loads(t["output_text"])
        symbol = t["symbol"]
        stage = analysis.get("stage")
        
        if stage in ("WATCH", "TRADE_READY", "ON_RADAR"):
            print(f"Recovering {symbol} ({stage})...")
            try:
                # Map fields manually to fix the bug
                # Note: scoring_breakdown and signals_contributing are already objects in the turn
                # But the DB expectation is TEXT[] and JSONB. Supabase-py handles objects -> JSONB.
                
                setup_data = {
                    "session_id":       session_id,
                    "setup_date":       "2026-05-26",
                    "symbol":           symbol,
                    "direction":        analysis.get("direction"),
                    "stage":            stage,
                    "setup_type":       str(analysis.get("setup_type"))[:50],
                    "setup_maturity":   analysis.get("setup_maturity"),
                    "conviction_score": analysis.get("conviction_score"),
                    "strike":           analysis.get("strike"),
                    "option_type":      analysis.get("option_type"),
                    "expiry_date":      analysis.get("expiry_date"),
                    "entry_zone_low":   analysis.get("entry_premium_low"),
                    "entry_zone_high":  analysis.get("entry_premium_high"),
                    "stop_loss_premium": analysis.get("stop_loss_premium"),
                    "target_1_premium":  analysis.get("target_1_premium"),
                    "target_2_premium":  analysis.get("target_2_premium"),
                    "underlying_stop":  analysis.get("underlying_stop"),
                    "lots":             analysis.get("lots"),
                    "lot_size":         analysis.get("lot_size"),
                    "max_risk_inr":     analysis.get("max_risk_inr"),
                    "risk_reward":      analysis.get("risk_reward"),
                    "iv_assessment":    analysis.get("iv_assessment"),
                    "scoring_breakdown":    analysis.get("scoring_breakdown", {}),
                    "signals_contributing": analysis.get("signals_contributing", []),
                    "claude_full_rationale": analysis.get("claude_full_rationale"),
                    "mentor_explanation":   analysis.get("mentor_explanation"),
                    "key_learning_today":   analysis.get("key_learning_today"),
                    "why_could_be_wrong":   analysis.get("why_could_be_wrong"),
                }
                
                client.table("trade_setups").insert(setup_data).execute()
                recovered += 1
            except Exception as exc:
                print(f"Failed to recover {symbol}: {exc}")

    print(f"Recovery complete. Recovered {recovered} setups.")

if __name__ == "__main__":
    recover_setups()
