"""Preview morning brief output using yesterday's session data (no Telegram send)."""
import sys
import os
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import date, timedelta
from dotenv import load_dotenv
load_dotenv()

from pipeline.morning_brief import generate_morning_brief
from integrations.nse_bhavcopy import last_trading_day

session_date = last_trading_day()
print(f"Previewing brief for session_date: {session_date}")
print("=" * 60)

msg1, msg2 = generate_morning_brief(session_date)

print("── MESSAGE 1 (LOUD) ──────────────────────────────────────")
print(msg1)
print(f"\n[{len(msg1)} chars / 4096 limit]")
print()
print("── MESSAGE 2 (SILENT) ────────────────────────────────────")
print(msg2)
print(f"\n[{len(msg2)} chars / 4096 limit]")
