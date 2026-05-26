"""
Claude API connectivity test — confirms key, model, and token logging work.
"""
import json
import os
import sys

from dotenv import load_dotenv
import anthropic

load_dotenv()

MODEL      = "claude-sonnet-4-6"
MAX_TOKENS = 100
PROMPT     = 'Reply with ONLY a JSON object: {"status": "ok", "model": "working"}'

api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
if not api_key:
    print("FAIL: ANTHROPIC_API_KEY is blank in .env")
    sys.exit(1)

client = anthropic.Anthropic(api_key=api_key, max_retries=0)

print(f"Model   : {MODEL}")
print(f"Prompt  : {PROMPT}")
print()

response = client.messages.create(
    model=MODEL,
    max_tokens=MAX_TOKENS,
    messages=[{"role": "user", "content": PROMPT}],
)

raw_text = response.content[0].text
u        = response.usage

# Cost: claude-sonnet-4-6 = $3.00/M in, $15.00/M out
cost_usd = (u.input_tokens / 1_000_000 * 3.00) + (u.output_tokens / 1_000_000 * 15.00)

print(f"Response text   : {raw_text}")
print(f"Input tokens    : {u.input_tokens}")
print(f"Output tokens   : {u.output_tokens}")
print(f"Estimated cost  : ${cost_usd:.6f}")
print()

try:
    parsed = json.loads(raw_text.strip())
    print(f"JSON parse      : OK — {parsed}")
    print()
    print("PASS: Claude API is working correctly.")
except json.JSONDecodeError as exc:
    print(f"JSON parse      : WARN — response is not clean JSON ({exc})")
    print("PASS: API connected and responded (non-JSON output).")
