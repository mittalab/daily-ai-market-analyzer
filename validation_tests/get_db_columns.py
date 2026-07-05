import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.client import get_client

def run():
    client = get_client()
    resp = client.table("trade_setups").select("*").limit(1).execute()
    if resp.data:
        print("Columns in trade_setups table:")
        for col in sorted(resp.data[0].keys()):
            print(f"- {col} (current value: {resp.data[0][col]})")
    else:
        print("No rows in trade_setups table to inspect.")

if __name__ == "__main__":
    run()
