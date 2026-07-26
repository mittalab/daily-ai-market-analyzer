"""
Run Turn 3 deep analysis for a single stock — dry run, no DB writes.

Usage:
    python validation_tests/test_turn3_single.py SYMBOL [LONG|SHORT]

    SYMBOL    — NSE symbol (e.g. HDFCBANK, RELIANCE, INFY)
    DIRECTION — optional override: LONG or SHORT
                if omitted, uses the direction from tonight's Turn 2 prescan.
                if the symbol wasn't in tonight's prescan, direction is required.

Reads from DB: latest session, Turn 1 market context, Turn 2 prescan,
               price history, futures, options chain, previous setups.
Calls Claude API live (billed).
Prints full prompt, raw Claude response, and position-sized summary.
Does NOT write to DB (no save_claude_turn, no create_trade_setup).
"""
import json
import logging
import os
import sys
import time
from datetime import date

import anthropic
from dotenv import load_dotenv

logging.basicConfig(level=logging.WARNING)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.queries import get_all_system_config, get_claude_turn, get_latest_session
from pipeline.claude_session import (
    _build_turn3_data,
    _build_turn3_prompt,
    _validate_position_sizing_turn3,
)
from pipeline.deep_analysis import DEEP_SYSTEM, _MODEL

DIV  = "─" * 80
DIV2 = "═" * 80


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_json(raw: str) -> dict:
    t = raw.strip()
    if t.startswith("```"):
        t = t[t.index("\n") + 1:]
    if t.endswith("```"):
        t = t[:t.rindex("```")]
    return json.loads(t.strip())


def _call_claude(client: anthropic.Anthropic, prompt: str, max_tokens: int = 8000):
    """
    Inline API call — returns (raw_text, parsed_dict, input_tokens, output_tokens).
    Unlike call_claude_deep, preserves the raw response text for display.
    Retries 3× on transient errors.
    """
    backoff = [5, 10, 20]
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=_MODEL,
                max_tokens=max_tokens,
                system=[{
                    "type": "text",
                    "text": DEEP_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text
            parsed = _parse_json(raw)
            return raw, parsed, resp.usage.input_tokens, resp.usage.output_tokens
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            if attempt == 2:
                raise
            print(f"  [network error, retry {attempt + 1}/3: {exc}]")
            time.sleep(backoff[attempt])
        except anthropic.RateLimitError as exc:
            if attempt == 2:
                raise
            print(f"  [rate limit, retry {attempt + 1}/3]")
            time.sleep(backoff[attempt])
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500 and attempt < 2:
                print(f"  [server error {exc.status_code}, retry {attempt + 1}/3]")
                time.sleep(backoff[attempt])
            else:
                raise


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # if len(sys.argv) < 2:
    #     print(__doc__)
    #     sys.exit(1)

    #symbol = sys.argv[1].upper()
    symbol = "INFY"
    forced_direction: str | None = sys.argv[2].upper() if len(sys.argv) >= 3 else None
    if forced_direction and forced_direction not in ("LONG", "SHORT"):
        print(f"Error: direction must be LONG or SHORT, got '{forced_direction}'")
        sys.exit(1)

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set in environment / .env")
        sys.exit(1)

    # ── 1. Latest session ─────────────────────────────────────────────────────
    session = get_latest_session()
    if not session:
        print("Error: no session found in DB.")
        sys.exit(1)

    session_id   = session["session_id"]
    session_date = session["session_date"]
    if isinstance(session_date, str):
        session_date = date.fromisoformat(session_date)

    print(f"Session : {session_id}")
    print(f"Date    : {session_date}")

    # ── 2. Turn 1 (market context) ────────────────────────────────────────────
    t1_row = get_claude_turn(session_id, 1)
    if not t1_row:
        print("Error: Turn 1 not found for this session — run the pipeline first.")
        sys.exit(1)
    turn1_result = json.loads(t1_row["output_text"])

    # ── 3. Turn 2 (prescan) ───────────────────────────────────────────────────
    t2_row = get_claude_turn(session_id, 2)
    turn2_result: list = []
    if t2_row:
        raw2 = json.loads(t2_row["output_text"])
        if isinstance(raw2, dict):
            turn2_result = raw2.get("stock_assessments") or raw2.get("stocks") or []
        else:
            turn2_result = raw2
    else:
        print("Warning: Turn 2 not in DB — prescan context will be synthetic.")

    # ── 4. Resolve direction ──────────────────────────────────────────────────
    t2_entry = next((a for a in turn2_result if a.get("symbol") == symbol), None)

    if forced_direction:
        direction = forced_direction
        if t2_entry is None:
            # Inject a minimal synthetic prescan entry so _build_turn3_data finds the symbol
            t2_entry = {
                "symbol":                symbol,
                "preliminary_direction": direction,
                "direction":             direction,
                "reason":                f"Direction manually overridden to {direction} — symbol was not in tonight's prescan.",
                "forward_to_deep":       True,
            }
            turn2_result.append(t2_entry)
            print(f"Note: {symbol} not in Turn 2; injected synthetic prescan entry with direction={direction}")
        else:
            t2_entry["preliminary_direction"] = direction
            t2_entry["direction"]             = direction
            print(f"Note: overriding Turn 2 direction for {symbol} → {direction}")
    elif t2_entry:
        direction = (
            t2_entry.get("preliminary_direction")
            or t2_entry.get("direction")
            or "LONG"
        )
    else:
        print(f"Error: {symbol} was not found in tonight's Turn 2 prescan.")
        print(f"  Re-run with a direction override:")
        print(f"  python validation_tests/test_turn3_single.py {symbol} LONG")
        sys.exit(1)

    print(f"Symbol  : {symbol}  |  Direction: {direction}")

    # ── 5. Build data package ─────────────────────────────────────────────────
    print("\nAssembling Turn 3 data package...")
    stock_pkg = _build_turn3_data(symbol, session_date, turn1_result, turn2_result)
    if not stock_pkg:
        print(f"Error: data package returned empty for {symbol} — price history missing?")
        sys.exit(1)

    # ── 6. Build prompt ───────────────────────────────────────────────────────
    prompt = _build_turn3_prompt(stock_pkg)

    # ── 7. Print prompt ───────────────────────────────────────────────────────
    print(f"\n{DIV2}")
    print("CLAUDE INPUT — FULL PROMPT")
    print(DIV2)
    print(prompt)
    print(DIV2)
    print(f"Prompt: {len(prompt):,} chars")

    # ── 8. Call Claude ────────────────────────────────────────────────────────
    print(f"\nCalling Claude for {symbol}…")
    client = anthropic.Anthropic(api_key=api_key)
    raw_output, analysis, in_tok, out_tok = _call_claude(client, prompt)

    cost_usd = round(in_tok / 1_000_000 * 3.00 + out_tok / 1_000_000 * 15.00, 4)

    # ── 9. Print raw Claude response ──────────────────────────────────────────
    print(f"\n{DIV2}")
    print("CLAUDE OUTPUT — RAW RESPONSE")
    print(DIV2)
    print(raw_output)
    print(DIV2)
    print(f"Tokens — in: {in_tok:,}  out: {out_tok:,}  cost: ${cost_usd}")

    # ── 10. Position sizing (no DB writes) ────────────────────────────────────
    config = get_all_system_config()
    config.setdefault("claude_capital_inr", "500000")

    analysis["symbol"] = symbol
    analysis = _validate_position_sizing_turn3(analysis, config)

    # ── 11. Summary ───────────────────────────────────────────────────────────
    instr_dec = analysis.get("instrument_decision") or {}
    wall      = instr_dec.get("oi_wall_proximity_check") or {}
    delta     = analysis.get("setup_delta_vs_previous") or {}
    regimes   = analysis.get("price_oi_regime_last_3") or []

    print(f"\n{DIV2}")
    print("SUMMARY (post position-sizing)")
    print(DIV2)
    print(f"  Stage            : {analysis.get('stage')}")
    print(f"  Direction        : {analysis.get('direction')}")
    print(f"  Conviction       : {analysis.get('conviction_score')}  (adjusted: {analysis.get('adjusted_score')})")
    print(f"  Actionable Now   : {analysis.get('actionable_now')}  — {analysis.get('actionable_note')}")
    print(f"  Hard Gate        : {analysis.get('hard_gate_triggered')}  — {analysis.get('hard_gate_reason')}")
    print(f"  Instrument       : {analysis.get('instrument_recommendation')}")
    print(f"  OI Wall pass     : {wall.get('pass')}  nearest={wall.get('nearest_obstructing_wall_strike')}  ratio={wall.get('wall_oi_vs_neighbors_ratio')}")
    print(f"  Lots × Lot Size  : {analysis.get('lots')} × {analysis.get('lot_size')}")
    print(f"  Max Risk INR     : {analysis.get('max_risk_inr')}  ({analysis.get('risk_pct_capital')}%)")
    print(f"  R:R              : {analysis.get('risk_reward')}")
    if regimes:
        regime_str = "  →  ".join(
            f"{r.get('date', '?')} {r.get('regime', '?')} (Δprice {r.get('price_change_pct', '?')}% / ΔOI {r.get('oi_change_pct', '?')}%)"
            for r in regimes
        )
        print(f"  Price/OI Regimes : {regime_str}")
    if delta.get("direction_changed"):
        print(f"  Direction Flip   : {delta.get('previous_direction')} → {analysis.get('direction')}  Δscore={delta.get('score_delta')}")
        print(f"  Flip Justific.   : {delta.get('justification')}")
    if analysis.get("rejection_reason"):
        print(f"  Rejection Reason : {analysis.get('rejection_reason')}")
    print(DIV2)
    print("DB writes: NONE")


if __name__ == "__main__":
    main()
