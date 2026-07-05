import os
import sys
from dotenv import load_dotenv
import asyncio

load_dotenv()
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api.dashboard import get_active_trades

async def run_test():
    print("Testing get_active_trades()...")
    res = await get_active_trades()
    print("\nAPI Response keys:", list(res.keys()))
    print("Turns count:", len(res.get("turns", [])))
    print("Holdings count:", len(res.get("holdings", {})))
    print("Positions count:", len(res.get("positions", {})))
    import json
    print("Positions JSON:", json.dumps(res.get("positions", {}), indent=2))
    print("\nTurns details:")
    for turn in res.get("turns", []):
        print(f"- Symbol: {turn['symbol']} | Turn number: {turn['turn_number']} | Stage: {turn['analysis'].get('stage')}")
        
if __name__ == "__main__":
    asyncio.run(run_test())
