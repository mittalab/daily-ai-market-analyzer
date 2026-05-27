import os
from dotenv import load_dotenv
from database.queries import get_kite_token
from kiteconnect import KiteConnect

load_dotenv()
row = get_kite_token()
api_key = os.getenv("KITE_API_KEY")

print(f"API_KEY: {api_key[:4]}...")
print(f"TOKEN:   {row['access_token'][:4]}...")

kite = KiteConnect(api_key=api_key)
kite.set_access_token(row['access_token'])

try:
    p = kite.profile()
    print(f"Profile: {p['user_id']}")
except Exception as e:
    print(f"Error: {e}")
