import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import logging; logging.basicConfig(level=logging.WARNING)

from database.queries import get_claude_turns
turns = get_claude_turns("SESSION_20260522")
t2    = next(t for t in turns if t["turn_number"] == 2)
raw   = t2["output_text"]

print(f"Total chars    : {len(raw)}")
print(f"output_tokens  : {t2['output_tokens']}")
print()
print("Last 300 chars (showing truncation point):")
print(repr(raw[-300:]))
