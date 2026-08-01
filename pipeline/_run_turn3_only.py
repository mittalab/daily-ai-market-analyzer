"""
Resume Turn 3 (deep analysis) for the latest session that has Turn 2 but no Turn 3 turns.

Preconditions checked at runtime:
  - Latest session has turn_number=1 (market_context) and turn_number=2 (pre_scan) in DB
  - Latest session has ZERO turn_type='deep_analysis' rows — i.e. Turn 3+ was never run

Use case: pipeline crashed or was interrupted after pre-scan; this resumes from Turn 3
without re-running Turn 1 or Turn 2.

Run:
    python.exe -m pipeline._run_turn3_only
"""
import json
import logging
import os
import sys
from datetime import date

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

import anthropic

from database.queries import (
    get_all_system_config,
    get_claude_turn,
    get_claude_turns,
    get_latest_session,
)
from pipeline.claude_session import run_turn_deep_analysis


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_json(text: str):
    t = text.strip()
    if t.startswith("```"):
        t = t[t.index("\n") + 1:]
    if t.endswith("```"):
        t = t[:t.rindex("```")]
    return json.loads(t.strip())


def _build_index_ctx(turn1_result: dict) -> dict:
    """Reconstruct the index_ctx dict from a stored Turn 1 result."""
    return {
        "regime": (
            f"{turn1_result.get('market_trend', 'SIDEWAYS')}_"
            f"{turn1_result.get('market_volatility', 'NORMAL')}_"
            f"{turn1_result.get('market_structure', 'WIDE')}"
        ),
        "market_trend":      turn1_result.get("market_trend", "SIDEWAYS"),
        "market_volatility": turn1_result.get("market_volatility", "NORMAL"),
        "market_structure":  turn1_result.get("market_structure", "WIDE"),
        "execution_bias":    turn1_result.get("execution_bias", "NEUTRAL"),
        "fii_dii_stance":    turn1_result.get("fii_dii_stance", "NEUTRAL"),
        "nifty_close":      (turn1_result.get("index_key_levels") or {}).get("current"),
        "vix":              (turn1_result.get("vix_assessment") or {}).get("current"),
        "ema20":             turn1_result.get("ema20"),
        "ema50":             turn1_result.get("ema50"),
        "ret20d_pct":        turn1_result.get("ret20d"),
    }


def _build_forward_list(t2_parsed: dict) -> list[dict]:
    """
    Extract the stocks Claude forwarded to deep analysis from Turn 2 output.
    Returns items shaped for run_turn_deep_analysis (symbol, direction).
    """
    stock_assessments = t2_parsed.get("stock_assessments", [])
    forward_list = []
    for s in stock_assessments:
        if s.get("claude_forward_decision") == "FORWARD":
            forward_list.append({
                "symbol":    s["symbol"],
                "direction": s.get("preliminary_direction", "LONG"),
                "is_watchlist_reanalysis": False,
                "days_in_stage": 0,
            })
    return forward_list


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # ── 1. Find latest session ────────────────────────────────────────────────
    session = get_latest_session()
    if not session:
        print("ERROR: No sessions found in DB.")
        sys.exit(1)

    session_id   = session["session_id"]
    session_date = date.fromisoformat(str(session["session_date"]))
    print(f"Latest session : {session_id}  ({session_date})")
    print(f"Session status : {session.get('status')}")

    # ── 2. Load all existing turns ────────────────────────────────────────────
    turns      = get_claude_turns(session_id)
    deep_turns = [t for t in turns if t.get("turn_type") == "deep_analysis"]
    turn1_row  = next((t for t in turns if t["turn_number"] == 1), None)
    turn2_row  = next((t for t in turns if t["turn_number"] == 2), None)

    print(f"Turns in DB    : {sorted(t['turn_number'] for t in turns)}")

    # ── 3. Guard checks ───────────────────────────────────────────────────────
    if not turn1_row or not turn1_row.get("output_text"):
        print(f"ERROR: Turn 1 (market_context) missing for session {session_id}.")
        sys.exit(1)

    if not turn2_row or not turn2_row.get("output_text"):
        print(f"ERROR: Turn 2 (pre_scan) missing for session {session_id}. Nothing to resume.")
        sys.exit(1)

    if deep_turns:
        done = [t.get("symbol") for t in deep_turns]
        print(
            f"ERROR: Session {session_id} already has {len(deep_turns)} deep analysis turn(s): {done}.\n"
            f"       Turn 3 already ran — there is nothing to resume."
        )
        sys.exit(1)

    print("Guard OK: Turn 1 + Turn 2 present, no deep analysis turns yet.\n")

    # ── 4. Parse Turn 1 → index_ctx ──────────────────────────────────────────
    try:
        turn1_result = _parse_json(turn1_row["output_text"])
    except Exception as exc:
        print(f"ERROR: Failed to parse Turn 1 output: {exc}")
        sys.exit(1)

    index_ctx = _build_index_ctx(turn1_result)
    print(
        f"Market context : trend={index_ctx['market_trend']}  "
        f"volatility={index_ctx['market_volatility']}  "
        f"bias={index_ctx['execution_bias']}"
    )

    # ── 5. Parse Turn 2 → forward list ───────────────────────────────────────
    try:
        t2_parsed = _parse_json(turn2_row["output_text"])
    except Exception as exc:
        print(f"ERROR: Failed to parse Turn 2 output: {exc}")
        sys.exit(1)

    forward_list = _build_forward_list(t2_parsed)
    if not forward_list:
        print("No stocks were forwarded to deep analysis in Turn 2. Nothing to run.")
        sys.exit(0)

    print(f"Forwarded ({len(forward_list)}): {[s['symbol'] for s in forward_list]}\n")

    # ── 6. Anthropic client + system config ──────────────────────────────────
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key, max_retries=0)
    config = get_all_system_config()

    # ── 7. Run Turn 3+ ────────────────────────────────────────────────────────
    trade_ready_list: list[dict] = []
    results: list[dict] = []
    total_cost = 0.0

    for i, stock in enumerate(forward_list):
        symbol    = stock["symbol"]
        direction = stock["direction"]
        turn_num  = 3 + i

        print(f"[{i+1}/{len(forward_list)}] Turn {turn_num}: {symbol}  direction={direction}")

        deep_res, deep_cost = run_turn_deep_analysis(
            client=client,
            session_id=session_id,
            session_date=session_date,
            symbol=symbol,
            direction=direction,
            is_re=False,
            days_in=0,
            index_ctx=index_ctx,
            config=config,
            turn_num=turn_num,
            trade_ready_list=trade_ready_list,
            turn1_result=turn1_result,
            turn2_result=None,  # fetched from DB automatically inside run_turn_deep_analysis
            max_tokens=16000,
        )

        results.append(deep_res)
        turn_total = deep_cost.get("total_cost_usd", 0.0)
        total_cost += turn_total

        stage = deep_res.get("stage", "UNKNOWN")
        print(
            f"  → stage={stage}  "
            f"score={deep_res.get('conviction_score', '-')}  "
            f"in={deep_cost.get('input_tokens', 0)}  "
            f"out={deep_cost.get('output_tokens', 0)}  "
            f"cost=${turn_total:.4f}"
        )

        if stage == "TRADE_READY":
            trade_ready_list.append(deep_res)

    # ── 8. Summary ────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"TURN 3 COMPLETE — {len(results)} stocks analysed")
    print("=" * 60)

    for r in results:
        print(
            f"  {r.get('symbol', '?'):12}  "
            f"{r.get('stage', '?'):12}  "
            f"score={r.get('conviction_score', '-')}"
        )

    trade_ready = [r for r in results if r.get("stage") == "TRADE_READY"]
    watch       = [r for r in results if r.get("stage") == "WATCH"]
    on_radar    = [r for r in results if r.get("stage") == "ON_RADAR"]
    rejected    = [r for r in results if r.get("stage") in ("REJECT", "SKIP")]

    print(f"\n  TRADE_READY : {len(trade_ready)}")
    print(f"  WATCH       : {len(watch)}")
    print(f"  ON_RADAR    : {len(on_radar)}")
    print(f"  REJECT/SKIP : {len(rejected)}")
    print(f"  Total cost  : ${total_cost:.4f}")


if __name__ == "__main__":
    main()
