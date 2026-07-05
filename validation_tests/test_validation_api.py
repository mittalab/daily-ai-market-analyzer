import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from indicators.validation import validate_indicators_vs_manual

def run_test():
    print("Testing validate_indicators_vs_manual('HDFCBANK')...")
    res = validate_indicators_vs_manual("HDFCBANK")
    
    print("\nAPI Response Structure:")
    for k, v in res.items():
        if k == "indicators":
            print(f"- {k}: [")
            for ind_k, ind_v in v.items():
                print(f"    {ind_k}: {ind_v}")
            print("  ]")
        else:
            print(f"- {k}: {v}")
            
    # Assertions
    assert res["symbol"] == "HDFCBANK"
    assert "date" in res
    assert "indicators" in res
    assert len(res["indicators"]) == 8
    for ind in ["EMA20", "EMA50", "EMA180", "RSI14", "MACD_LINE", "MACD_SIGNAL", "MACD_HISTOGRAM", "ATR14"]:
        assert ind in res["indicators"]
        assert res["indicators"][ind]["system"] is not None
        
    print("\nPhase 7 Backend API Validation Passed Successfully!")

if __name__ == "__main__":
    run_test()
