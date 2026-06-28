import json
import os
import logging
from datetime import date

import anthropic
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")
logger = logging.getLogger(__name__)

from new_data_ingestion.nse_bhavcopy import last_trading_day
from database.queries import create_analysis_session, get_all_system_config, get_claude_turn
from pipeline.claude_session import _build_turn1_data, _build_turn1_prompt, _run_turn1


def main():
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not found in env.")
        return

    client       = anthropic.Anthropic(api_key=api_key)
    session_date = last_trading_day()
    session_id   = f"TEST_T1_{session_date.strftime('%Y%m%d')}"
    config       = get_all_system_config()

    print(f"\nRunning Turn 1 test for {session_id} on {session_date}")
    print("=" * 60)

    # Create a test session row so update_analysis_session has a target
    try:
        create_analysis_session(session_id, session_date)
        print("Session row created in DB.")
    except Exception as exc:
        print(f"Session row already exists or create failed: {exc}")

    # ── Step 1: data preparation only ─────────────────────────────────────────
    print("\n[1/3] Building Turn 1 data...")
    data = _build_turn1_data(session_date)

    ind = data["nifty"]["indicators"]
    print(f"  Nifty close  : {ind['current']}")
    print(f"  EMA 20/50    : {ind['ema20']} / {ind['ema50']}")
    print(f"  EMA 200      : {ind['ema200']}")
    print(f"  ret5d/20d/60d: {ind['ret5d']}% / {ind['ret20d']}% / {ind['ret60d']}%")
    print(f"  ATR%         : {ind['atr_pct']}%")
    print(f"  Nifty OHLCV rows (60d): {len(data['nifty']['ohlcv_60d'])}")
    print(f"  VIX rows (30d)        : {len(data['vix']['close_30d'])}")
    print(f"  FII/DII rows (30d)    : {len(data['fii_dii']['flows_30d'])} | quality={data['fii_dii']['data_quality']}")
    print(f"  Sectors available     : {list(data['sectors'].keys())}")
    print(f"  Options available     : {data['options']['available']}")
    if data["options"]["available"]:
        print(f"  CE walls  : {data['options']['ce_walls'][:3]}")
        print(f"  PE walls  : {data['options']['pe_walls'][:3]}")
        print(f"  PCR       : {data['options']['pcr_current']}")
        print(f"  Max Pain  : {data['options']['max_pain']}")

    # ── Step 2: prompt preview ─────────────────────────────────────────────────
    print("\n[2/3] Building prompt...")
    prompt = _build_turn1_prompt(data)
    print(f"  Prompt length: {len(prompt):,} chars  (~{len(prompt)//4:,} tokens estimated)")
    print(f"  First 300 chars:\n  {prompt[:300].replace(chr(10), ' ')}")

    # ── Step 3: full Turn 1 run ────────────────────────────────────────────────
    print("\n[3/3] Calling Claude (Turn 1)...")
    turn1_result, cost_info = _run_turn1(client, session_id, session_date, config)

    print("\n" + "=" * 60)
    print("TURN 1 COMPLETE")
    print("=" * 60)
    print(f"Input tokens  : {cost_info['input_tokens']:,}")
    print(f"Output tokens : {cost_info['output_tokens']:,}")
    print(f"Cost USD      : ${cost_info['total_cost_usd']:.4f}")

    print("\n── Core classifications ──────────────────────────────────")
    print(f"  market_trend      : {turn1_result.get('market_trend')}")
    print(f"  market_volatility : {turn1_result.get('market_volatility')}")
    print(f"  market_structure  : {turn1_result.get('market_structure')}")
    print(f"  execution_bias    : {turn1_result.get('execution_bias')}")
    print(f"  fii_dii_stance    : {turn1_result.get('fii_dii_stance')}")
    print(f"  session_risk_level: {turn1_result.get('session_risk_level')}")
    print(f"  conviction_mult   : {turn1_result.get('conviction_multiplier')}")
    print(f"  min_conviction_ovr: {turn1_result.get('min_conviction_override')}")

    print("\n── VIX assessment ────────────────────────────────────────")
    vix = turn1_result.get("vix_assessment") or {}
    print(f"  current           : {vix.get('current')}")
    print(f"  trend             : {vix.get('trend')}")
    print(f"  character         : {vix.get('character')}")
    print(f"  options_implication: {vix.get('options_implication')}")

    print("\n── FII/DII assessment ────────────────────────────────────")
    fii = turn1_result.get("fii_dii_assessment") or {}
    print(f"  fii_20d_character : {fii.get('fii_20d_character')}")
    print(f"  recent_shift      : {fii.get('recent_shift')}")
    print(f"  shift_description : {fii.get('shift_description')}")
    print(f"  divergence        : {fii.get('divergence')}")
    print(f"  key_insight       : {fii.get('key_insight')}")

    print("\n── Sector pictures ───────────────────────────────────────")
    sector_pics = turn1_result.get("sector_pictures") or {}
    print(f"  Sectors populated : {len(sector_pics)}/11")
    for name, pic in sector_pics.items():
        stance   = pic.get("stance", "?")
        strength = pic.get("strength", "?")
        trend    = pic.get("trend", "?")
        vs_nifty = pic.get("vs_nifty", "?")
        print(f"  {name:<10} : {stance:<10} {strength:<10} trend={trend}  vs_nifty={vs_nifty}")

    print("\n── Directional filters ───────────────────────────────────")
    df = turn1_result.get("directional_filters") or {}
    print(f"  avoid_longs_in    : {df.get('avoid_longs_in')}")
    print(f"  avoid_shorts_in   : {df.get('avoid_shorts_in')}")
    print(f"  caution_sectors   : {df.get('caution_sectors')}")

    print("\n── Pre-scan guidance ─────────────────────────────────────")
    pg = turn1_result.get("prescan_guidance") or {}
    print(f"  max_stocks_to_fwd : {pg.get('max_stocks_to_forward')}")
    print(f"  prefer_directions : {pg.get('prefer_directions')}")
    print(f"  prioritise_sectors: {pg.get('prioritise_sectors')}")
    print(f"  deprioritise      : {pg.get('deprioritise_sectors')}")
    print(f"  special_instructions: {pg.get('special_instructions')}")

    print("\n── Index key levels ──────────────────────────────────────")
    kl = turn1_result.get("index_key_levels") or {}
    print(f"  strong_support    : {kl.get('strong_support')}")
    print(f"  support           : {kl.get('support')}")
    print(f"  current           : {kl.get('current')}")
    print(f"  resistance        : {kl.get('resistance')}")
    print(f"  strong_resistance : {kl.get('strong_resistance')}")
    print(f"  max_pain          : {kl.get('max_pain')}")
    print(f"  pcr_signal        : {kl.get('pcr_signal')}")
    print(f"  levels_note       : {kl.get('levels_note')}")

    print("\n── Risk flags ────────────────────────────────────────────")
    for flag in (turn1_result.get("risk_flags") or []):
        print(f"  • {flag}")

    print("\n── Guidance ──────────────────────────────────────────────")
    g = turn1_result.get("guidance") or {}
    print(f"  favour  : {g.get('favour')}")
    print(f"  caution : {g.get('caution')}")

    print("\n── Session narrative ─────────────────────────────────────")
    print(f"  {turn1_result.get('session_narrative')}")

    print("\n── DB verification (session_claude_turns) ────────────────")
    row = get_claude_turn(session_id, 1)
    if row:
        stored = json.loads(row["output_text"])
        print(f"  turn_number   : {row['turn_number']}")
        print(f"  turn_type     : {row['turn_type']}")
        print(f"  input_tokens  : {row['input_tokens']}")
        print(f"  output_tokens : {row['output_tokens']}")
        print(f"  market_trend  : {stored.get('market_trend')}")
        print(f"  session_risk  : {stored.get('session_risk_level')}")
        print(f"  sectors in DB : {list((stored.get('sector_pictures') or {}).keys())}")
        print("  DB row matches in-memory result:", stored.get("market_trend") == turn1_result.get("market_trend"))
    else:
        print("  WARNING: Turn 1 row not found in session_claude_turns")

    print("\n── Full JSON output (saved to turn1_output.json) ─────────")
    with open("turn1_output.json", "w", encoding="utf-8") as f:
        json.dump(turn1_result, f, indent=2, ensure_ascii=False)
    print("  Written to turn1_output.json")


if __name__ == "__main__":
    main()
