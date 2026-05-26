"""Diagnostic: inspect raw Turn 2 response and parsing logic."""
import json
import logging
import sys
logging.basicConfig(level=logging.WARNING)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from database.queries import get_claude_turns

SESSION = "SESSION_20260522"
turns   = get_claude_turns(SESSION)
print(f"Turns saved in DB: {len(turns)}")

for t in turns:
    num  = t["turn_number"]
    typ  = t["turn_type"]
    raw  = t["output_text"]
    print(f"\n{'='*60}")
    print(f"Turn {num} ({typ}) — in={t['input_tokens']} out={t['output_tokens']}")
    print(f"Raw output ({len(raw)} chars):")
    print(raw[:4000])
    if len(raw) > 4000:
        print(f"... [truncated, {len(raw)-4000} more chars]")

# ── Focus on Turn 2 ──────────────────────────────────────────────────────────
t2 = next((t for t in turns if t["turn_number"] == 2), None)
if not t2:
    print("\nTurn 2 NOT FOUND in DB")
    sys.exit(1)

raw = t2["output_text"]
print("\n" + "="*60)
print("DIAGNOSIS: Turn 2 JSON parsing")
print("="*60)

# Step 1: does it start with code fence?
stripped = raw.strip()
print(f"Starts with backtick fence : {stripped.startswith('```')}")
print(f"First 20 chars             : {repr(stripped[:20])}")

# Step 2: attempt parse with the same logic as claude_session._parse_json
def parse_json(text):
    t = text.strip()
    if t.startswith("```"):
        t = t[t.index("\n") + 1:]
    if t.endswith("```"):
        t = t[:t.rindex("```")]
    return json.loads(t.strip())

try:
    parsed = parse_json(raw)
    print(f"JSON parse result type     : {type(parsed).__name__}")
    if isinstance(parsed, list):
        print(f"Array length               : {len(parsed)}")
        if parsed:
            print(f"First element keys         : {list(parsed[0].keys())}")
            print(f"First element              : {json.dumps(parsed[0], indent=2)}")
            # Check forwarding
            fwd   = [s for s in parsed if s.get("forward_to_deep")]
            high  = [s for s in parsed if s.get("priority") == "HIGH"]
            med   = [s for s in parsed if s.get("priority") == "MEDIUM"]
            skip  = [s for s in parsed if s.get("direction") == "SKIP"]
            neutral = [s for s in parsed if s.get("direction") == "NEUTRAL"]
            print(f"\nForwarding summary:")
            print(f"  forward_to_deep=True  : {len(fwd)}")
            print(f"  priority=HIGH         : {len(high)}")
            print(f"  priority=MEDIUM       : {len(med)}")
            print(f"  direction=SKIP        : {len(skip)}")
            print(f"  direction=NEUTRAL     : {len(neutral)}")
            print(f"\nAll directions: {set(s.get('direction') for s in parsed)}")
            print(f"All priorities: {set(s.get('priority') for s in parsed)}")
            print(f"\nSample (first 5 stocks):")
            for s in parsed[:5]:
                print(f"  {s.get('symbol','?'):12} dir={s.get('direction','?'):8} "
                      f"pri={s.get('priority','?'):6} fwd={s.get('forward_to_deep')}")
    elif isinstance(parsed, dict):
        print(f"Got a dict, not an array. Keys: {list(parsed.keys())}")
        print(f"Content: {json.dumps(parsed, indent=2)[:1000]}")
except Exception as exc:
    print(f"JSON parse FAILED: {exc}")
    print(f"Raw (repr): {repr(raw[:500])}")
