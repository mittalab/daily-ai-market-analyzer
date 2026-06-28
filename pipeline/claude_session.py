"""
Claude multi-turn session manager — spec Sections 7, 8, 16.

Turn 1  : Market context (Nifty/VIX/FII-DII 30d/Sectors) → JSON assessment & regime
Turn 2  : Pre-scan all Level-1-passed stocks             → JSON array
Turns 3+: Deep analysis for each forwarded stock         → trade setup JSON

Call:
    result = run_claude_session(context_bundle, level1_passed, session_id)
"""
import json
import logging
import os
import time
from datetime import date, datetime, timedelta

import anthropic
import pandas as pd
import pytz
from dotenv import load_dotenv

from database.queries import (
    create_trade_setup,
    get_all_system_config,
    get_continuous_oi,
    get_fii_dii_flows,
    get_futures_row,
    get_monthly_claude_spend,
    get_price_history,
    save_claude_turn,
    update_analysis_session,
    get_options_by_date,
)
from indicators.technical import (
    atr_pct,
    calculate_ema,
    calculate_rsi,
    volume_ratio,
)
from pipeline.deep_analysis import (
    DEEP_SYSTEM,
    _sector_info,
    build_deep_prompt,
    build_stock_package,
    call_claude_deep,
    oi_walls,
    validate_position_sizing,
)
from pipeline.system_prompt_builder import build_system_prompt

load_dotenv()
logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

_MODEL           = "claude-sonnet-4-6"
_TOKEN_CEILING   = 250_000
_MAX_RETRIES     = 3
_BACKOFF         = [5, 10, 20]
_PROMPT_VERSIONS = {
    "system_prompt":  "v1.0",
    "market_context": "v1.0",
    "prescan":        "v1.0",
    "deep_analysis":  "v1.0",
}

_DEFAULT_BUDGET_USD = 50.0


class BudgetExhaustedException(Exception):
    pass


# ── JSON helpers ──────────────────────────────────────────────────────────────

def _parse_json(text: str) -> dict | list:
    t = text.strip()
    if t.startswith("```"):
        t = t[t.index("\n") + 1:]
    if t.endswith("```"):
        t = t[:t.rindex("```")]
    return json.loads(t.strip())


# ── Claude API call with retry ────────────────────────────────────────────────

def _call_claude(
    client: anthropic.Anthropic,
    system_text: str,
    messages: list[dict],
    max_tokens: int = 4096,
) -> anthropic.types.Message:
    system = [{
        "type": "text",
        "text": system_text,
        "cache_control": {"type": "ephemeral"},
    }]
    last_exc = None

    for attempt in range(_MAX_RETRIES):
        try:
            return client.messages.create(
                model=_MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
            )
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            last_exc = exc
            wait = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
            logger.warning("Network error (attempt %d/%d) — waiting %ds: %s",
                           attempt + 1, _MAX_RETRIES, wait, exc)
            time.sleep(wait)
        except anthropic.RateLimitError as exc:
            last_exc = exc
            wait = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
            logger.warning("Rate limit (attempt %d/%d) — waiting %ds", attempt + 1, _MAX_RETRIES, wait)
            time.sleep(wait)
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                last_exc = exc
                wait = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
                logger.warning("Server error %d (attempt %d/%d) — waiting %ds",
                               exc.status_code, attempt + 1, _MAX_RETRIES, wait)
                time.sleep(wait)
            else:
                raise

    raise RuntimeError(f"Claude API failed after {_MAX_RETRIES} attempts") from last_exc


# ── Turn 1: Market Context ────────────────────────────────────────────────────

def run_turn1_market_context(
    client: anthropic.Anthropic,
    session_id: str,
    session_date: date,
    system_text: str,
    max_tokens: int = 1500,
) -> tuple[dict, dict, list[dict], dict]:
    """
    Execute Turn 1: Market Context.
    Fetches raw market context, queries Claude for dynamic regime classification,
    saves the turn, updates analysis_sessions with classifications, and returns
    parsed Turn 1 result, regime_result bundle, updated conversation history, and cost info.
    """
    logger.info("Turn 1: fetching raw market context data...")
    nifty_rows = get_price_history("NIFTY_50",  days=180)
    vix_rows   = get_price_history("INDIA_VIX", days=35)
    fii_rows   = get_fii_dii_flows(days=35)

    nifty_180d = [
        {"date": r["date"], "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"]}
        for r in nifty_rows
    ]
    vix_35d = [
        {"date": r["date"], "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"]}
        for r in vix_rows
    ]
    fii_dii_35d = [
        {"date": r["date"], "fii_net_cr": r.get("fii_net_cr"), "dii_net_cr": r.get("dii_net_cr")}
        for r in fii_rows
    ]

    # Fetch Nifty OI continuous series
    nifty_oi_rows = get_continuous_oi("NIFTY_50", days=30)
    nifty_oi_30d = [
        {
            "date": r.get("date"),
            "pcr_near": r.get("pcr_near"),
            "max_pain": r.get("max_pain"),
            "near_month_oi": r.get("near_month_oi"),
            "next_month_oi": r.get("next_month_oi"),
        }
        for r in nifty_oi_rows
    ]

    # Fetch nearest expiry options chain OI walls
    nifty_oi_walls = {}
    if nifty_rows:
        try:
            n_snap = get_options_by_date("NIFTY_50", session_date)
            if not n_snap:
                n_snap = get_options_by_date("NIFTY_50", session_date - timedelta(days=1))
            if n_snap:
                expiries = sorted(list(set(r["expiry_date"] for r in n_snap)))
                if expiries:
                    nifty_oi_walls = oi_walls(n_snap, expiries[0])
        except Exception as exc:
            logger.warning("Failed to calculate Nifty OI walls: %s", exc)

    # Compute multi-timeframe performance matrix for tracked sector indices
    sectors = [
        "NIFTY_BANK",
        "NIFTY_IT",
        "NIFTY_AUTO",
        "NIFTY_PHARMA",
        "NIFTY_FMCG",
        "NIFTY_METAL",
        "NIFTY_ENERGY",
        "NIFTY_FIN_SERVICE",
        "NIFTY_CONSUMPTION",
        "NIFTY_INFRA",
        "NIFTY_MEDIA",
    ]
    from pipeline.market_regime import get_index_indicators
    sector_performance = {}
    for sec in sectors:
        ind = get_index_indicators(session_date, sec)
        sector_performance[sec] = {
            "ret7d": ind.get("ret7d"),
            "ret20d": ind.get("ret20d"),
            "ret60d": ind.get("ret60d")
        }

    # Compute Nifty indicators for regime result
    nifty_ind = get_index_indicators(session_date, "NIFTY_50")
    nifty_close = nifty_ind.get("close") or (nifty_rows[-1]["close"] if nifty_rows else 0.0)
    vix_latest  = float(vix_rows[-1]["close"]) if vix_rows else 0.0

    payload = {
        "turn": "market_context",
        "session_date": str(session_date),
        "nifty_180d": nifty_180d,
        "vix_35d": vix_35d,
        "fii_dii_35d": fii_dii_35d,
        "nifty_oi_30d": nifty_oi_30d,
        "nifty_oi_walls": nifty_oi_walls,
        "sector_performance": sector_performance,
    }

    instructions = (
        "Analyse the market context data above. Provide a macro market context assessment.\n"
        "You must determine the following values using these strict enums:\n"
        "1. market_trend: BULLISH | BEARISH | SIDEWAYS\n"
        "2. market_volatility: LOW | NORMAL | HIGH\n"
        "3. market_structure: TIGHT | WIDE | STRETCHED\n"
        "4. execution_bias: FAVOUR_LONGS | FAVOUR_SHORTS | BOTH | CAUTIOUS | NEUTRAL\n"
        "5. fii_dii_stance: BULLISH | BEARISH | NEUTRAL\n\n"
        "Identify the leading sectors, lagging sectors, recommended strategies (favour), and caution notes.\n"
        "Identify the key levels (support, resistance) for the Nifty 50.\n"
        "Respond with ONLY a JSON object — no commentary outside the JSON:\n"
        "{\n"
        '  "session_narrative": "3-4 sentences on market condition and tone tonight",\n'
        '  "market_trend": "BULLISH | BEARISH | SIDEWAYS",\n'
        '  "market_volatility": "LOW | NORMAL | HIGH",\n'
        '  "market_structure": "TIGHT | WIDE | STRETCHED",\n'
        '  "execution_bias": "FAVOUR_LONGS | FAVOUR_SHORTS | BOTH | CAUTIOUS | NEUTRAL",\n'
        '  "fii_dii_stance": "BULLISH | BEARISH | NEUTRAL",\n'
        '  "sector_weights": {\n'
        '    "leading_sectors": ["SECTOR_1", "SECTOR_2"],\n'
        '    "lagging_sectors": ["SECTOR_3"]\n'
        '  },\n'
        '  "guidance": {\n'
        '    "favour": "recommended strategy or areas to favour",\n'
        '    "caution": "areas of caution or warnings"\n'
        '  },\n'
        '  "index_key_levels": {"support": 0, "resistance": 0},\n'
        '  "risk_flags": ["key risk 1", "key risk 2"]\n'
        "}"
    )

    t1_text_user = json.dumps(payload, ensure_ascii=False) + "\n\n" + instructions
    messages = [{"role": "user", "content": t1_text_user}]

    logger.info("Turn 1: calling Claude...")
    t1_resp = _call_claude(client, system_text, messages, max_tokens=max_tokens)
    t1_out_text = t1_resp.content[0].text

    u1 = t1_resp.usage
    cost_info = _turn_cost(1, "market_context", None, u1.input_tokens, u1.output_tokens)
    logger.info("Turn 1 done: in=%d out=%d cache_create=%s cache_read=%s",
                u1.input_tokens, u1.output_tokens,
                getattr(u1, "cache_creation_input_tokens", "-"),
                getattr(u1, "cache_read_input_tokens", "-"))

    save_claude_turn(session_id, 1, "market_context", None,
                     u1.input_tokens, u1.output_tokens, t1_text_user, t1_out_text)
    messages.append({"role": "assistant", "content": t1_out_text})

    try:
        turn1_result = _parse_json(t1_out_text)
    except Exception as exc:
        logger.error("Turn 1 JSON parse failed: %s | raw=%s", exc, t1_out_text[:300])
        turn1_result = {
            "session_narrative": t1_out_text,
            "market_trend": "SIDEWAYS",
            "market_volatility": "NORMAL",
            "market_structure": "WIDE",
            "execution_bias": "NEUTRAL",
            "fii_dii_stance": "NEUTRAL",
            "sector_weights": {"leading_sectors": [], "lagging_sectors": []},
            "guidance": {"favour": "General analysis", "caution": "Elevated caution"},
            "index_key_levels": {"support": 0, "resistance": 0},
            "risk_flags": [],
            "parse_error": str(exc)
        }

    trend_val  = turn1_result.get("market_trend", "SIDEWAYS")
    vol_val    = turn1_result.get("market_volatility", "NORMAL")
    struct_val = turn1_result.get("market_structure", "WIDE")
    bias_val   = turn1_result.get("execution_bias", "NEUTRAL")
    stance_val = turn1_result.get("fii_dii_stance", "NEUTRAL")

    # Combined market regime string
    market_regime = f"{trend_val}_{vol_val}_{struct_val}"

    regime_result = {
        "regime":            market_regime,
        "market_trend":      trend_val,
        "market_volatility":  vol_val,
        "market_structure":   struct_val,
        "execution_bias":     bias_val,
        "fii_dii_stance":     stance_val,
        "sector_weights":    turn1_result.get("sector_weights") or {"leading_sectors": [], "lagging_sectors": []},
        "guidance":          turn1_result.get("guidance") or {"favour": "General analysis", "caution": "Elevated caution"},
        "nifty_close":       nifty_close,
        "vix":               vix_latest,
        "ema20":             nifty_ind.get("ema20"),
        "ema50":             nifty_ind.get("ema50"),
        "ret20d":            nifty_ind.get("ret20d"),
        "index_key_levels":  turn1_result.get("index_key_levels") or {"support": 0, "resistance": 0},
        "session_narrative": turn1_result.get("session_narrative", ""),
        "risk_flags":        turn1_result.get("risk_flags", []),
    }

    # Save details to analysis_sessions table
    update_analysis_session(session_id, {
        "market_regime":     market_regime,
        "market_trend":      trend_val,
        "market_volatility":  vol_val,
        "market_structure":   struct_val,
        "execution_bias":     bias_val,
        "fii_dii_stance":     stance_val,
        "nifty_close":       nifty_close,
        "vix_close":         vix_latest,
        "stage_statuses": {
            "data_ingestion": "COMPLETE",
            "oi_series":      "COMPLETE",
            "regime_detect":  "COMPLETE",
            "claude_turn1":   "COMPLETE",
        }
    })

    return turn1_result, regime_result, messages, t1_cost


# ── Turn 2: Pre-scan ──────────────────────────────────────────────────────────

def _stock_data(symbol: str, session_date: date) -> dict | None:
    rows = get_price_history(symbol, days=40)
    if len(rows) < 20:
        return None

    df = pd.DataFrame(rows)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).reset_index(drop=True)

    closes  = df["close"]
    ema20_s = calculate_ema(closes, 20)
    ema50_s = calculate_ema(closes, 50) if len(df) >= 50 else ema20_s
    rsi_s   = calculate_rsi(closes, 14)
    atrp_s  = atr_pct(df, 14)
    volr_s  = volume_ratio(df["volume"], short=3, long=20)

    last30  = [round(float(c), 2) for c in closes.iloc[-30:].tolist()]

    oi_rows  = get_continuous_oi(symbol, days=10)
    oi_10d   = [r.get("near_month_oi") for r in oi_rows]
    latest   = oi_rows[-1] if oi_rows else {}

    fut       = get_futures_row(symbol, session_date)
    fut_price = float(fut["futures_price"]) if fut and fut.get("futures_price") else None
    basis_p   = float(fut["basis_pct"])     if fut and fut.get("basis_pct")     else None

    def _val(s: pd.Series) -> float | None:
        v = s.iloc[-1]
        return round(float(v), 2) if not pd.isna(v) else None

    return {
        "sym":            symbol,
        "close":          round(float(closes.iloc[-1]), 2),
        "closes_30d":     last30,
        "rsi14":          _val(rsi_s),
        "ema20":          round(float(ema20_s.iloc[-1]), 2),
        "ema50":          round(float(ema50_s.iloc[-1]), 2),
        "atr_pct14":      _val(atrp_s),
        "vol_ratio":      _val(volr_s),
        "oi_10d":         oi_10d,
        "futures_price":  fut_price,
        "basis_pct":      basis_p,
        "pcr_near":       latest.get("pcr_near"),
        "max_pain":       latest.get("max_pain"),
        "rollover_phase": latest.get("rollover_phase"),
    }


def _build_turn2_message(level1_passed: list[str], session_date: date) -> str:
    stocks: list[dict] = []
    skipped: list[str] = []

    for sym in level1_passed:
        data = _stock_data(sym, session_date)
        if data:
            stocks.append(data)
        else:
            skipped.append(sym)
            logger.warning("Pre-scan: insufficient data for %s — skipped", sym)

    if skipped:
        logger.warning("Pre-scan: %d skipped: %s", len(skipped), skipped)

    payload = {
        "turn":         "prescan",
        "session_date": str(session_date),
        "stock_count":  len(stocks),
        "stocks":       stocks,
    }

    instructions = (
        f"Pre-scan all {len(stocks)} stocks above. "
        "For each stock, assess direction and priority based on the data provided. "
        "Respond with ONLY a JSON array — one object per stock, no commentary:\n"
        "[\n"
        "  {\n"
        '    "symbol": "HDFCBANK",\n'
        '    "direction": "LONG",\n'
        '    "pre_scan_reasoning": "2-3 lines max",\n'
        '    "priority": "HIGH",\n'
        '    "forward_to_deep": true,\n'
        '    "override_level1": false,\n'
        '    "override_reason": null\n'
        "  },\n"
        "  ...\n"
        "]"
    )

    return json.dumps(payload, ensure_ascii=False) + "\n\n" + instructions


def run_turn2_prescan(
    client: anthropic.Anthropic,
    session_id: str,
    session_date: date,
    level1_passed: list[str],
    messages: list[dict],
    system_text: str,
    max_tokens: int = 12000,
) -> tuple[list[dict], list[dict], list[dict], dict]:
    """
    Execute Turn 2: Pre-scan.
    Fetches raw indicator data for all Level-1 passed stocks, constructs Turn 2 prompt,
    queries Claude, parses the pre-scan JSON output, saves turn history, and returns
    full results list, forwarded stocks list, updated messages, and cost info.
    """
    logger.info("Turn 2: assembling pre-scan data for %d stocks...", len(level1_passed))
    t2_text_user = _build_turn2_message(level1_passed, session_date)
    messages.append({"role": "user", "content": t2_text_user})

    logger.info("Turn 2: calling Claude...")
    t2_resp     = _call_claude(client, system_text, messages, max_tokens=max_tokens)
    t2_out_text = t2_resp.content[0].text

    u2 = t2_resp.usage
    cost_info = _turn_cost(2, "prescan", None, u2.input_tokens, u2.output_tokens)
    logger.info("Turn 2 done: in=%d out=%d cache_read=%s",
                u2.input_tokens, u2.output_tokens,
                getattr(u2, "cache_read_input_tokens", "-"))

    save_claude_turn(session_id, 2, "prescan", None,
                     u2.input_tokens, u2.output_tokens, t2_text_user, t2_out_text)
    messages.append({"role": "assistant", "content": t2_out_text})

    try:
        turn2_results = _parse_json(t2_out_text)
        if not isinstance(turn2_results, list):
            raise ValueError(f"Expected JSON array, got {type(turn2_results).__name__}")
    except Exception as exc:
        logger.error("Turn 2 JSON parse failed: %s | raw=%s", exc, t2_out_text[:300])
        turn2_results = []

    forwarded_stocks = [s for s in turn2_results if s.get("forward_to_deep")]
    # HIGH priority first
    forwarded_stocks.sort(key=lambda s: (s.get("priority") != "HIGH", s.get("priority") != "MEDIUM"))

    return turn2_results, forwarded_stocks, messages, cost_info


# ── Turn 3+: Deep Analysis ────────────────────────────────────────────────────

def run_turn_deep_analysis(
    client: anthropic.Anthropic,
    session_id: str,
    session_date: date,
    symbol: str,
    direction: str,
    is_re: bool,
    days_in: int,
    index_ctx: dict,
    config: dict,
    turn_num: int,
    trade_ready_list: list[dict],
    max_tokens: int = 3000,
) -> tuple[dict, dict]:
    """
    Execute a single stock's Deep Analysis (Turn 3+).
    Assembles stock package, queries Claude deep model, runs Python position validation,
    enforces sector rules, runs watchlist updates, writes setup to database, and returns
    processed deep analysis dictionary and cost info.
    """
    from database.queries import update_watchlist_staging, upsert_watchlist_staging
    from new_notifications.telegram import send_loud, send_silent, send_claude_cost

    logger.info("Turn %d: deep analysis for %s (direction=%s, re-analysis=%s)...", turn_num, symbol, direction, is_re)
    quality_notes: list[str] = []

    try:
        stock_pkg = build_stock_package(symbol, session_date, quality_notes)
    except Exception as exc:
        logger.error("Build stock package failed for %s: %s", symbol, exc)
        return {
            "symbol": symbol,
            "stage":  "SKIP",
            "skip_reason": f"Build stock package failed: {exc}",
            "quality_notes": quality_notes,
        }, _turn_cost(turn_num, "deep_analysis", symbol, 0, 0)

    if not stock_pkg:
        logger.warning("Turn %d: no data for %s — skipping deep analysis", turn_num, symbol)
        return {
            "symbol": symbol,
            "stage":  "SKIP",
            "skip_reason": "No price history available",
            "quality_notes": quality_notes,
        }, _turn_cost(turn_num, "deep_analysis", symbol, 0, 0)

    # Add watchlist re-analysis context to prompt
    custom_instructions = ""
    if is_re:
        prev_setups = stock_pkg.get("previous_setups", [])
        prev_score = prev_setups[0].get("conviction_score", "??") if prev_setups else "??"
        prev_type  = prev_setups[0].get("setup_type", "??") if prev_setups else "??"
        custom_instructions = (
            f"\n\nCONTEXT: This stock has been on Watch for {days_in} days. "
            f"Previous conviction: {prev_score}. Previous setup: {prev_type}. "
            "Re-evaluate with today's data. Has the setup confirmed or broken down?"
        )

    prompt = build_deep_prompt(stock_pkg, index_ctx, direction)
    if custom_instructions:
        prompt += custom_instructions

    try:
        analysis, in_tok, out_tok = call_claude_deep(client, prompt)
    except Exception as exc:
        logger.error("Deep analysis Claude call failed for %s: %s", symbol, exc)
        return {
            "symbol": symbol,
            "stage":  "SKIP",
            "skip_reason": f"Claude call failed: {exc}",
            "quality_notes": quality_notes,
        }, _turn_cost(turn_num, "deep_analysis", symbol, 0, 0)

    # Position sizing validation
    analysis["symbol"] = symbol
    analysis = validate_position_sizing(analysis, config)

    save_claude_turn(session_id, turn_num, "deep_analysis", symbol,
                     in_tok, out_tok, prompt, json.dumps(analysis))

    # Send cost notification per turn
    turn_cost = round(in_tok / 1_000_000 * 3.00 + out_tok / 1_000_000 * 15.00, 6)
    try:
        send_claude_cost(symbol, in_tok, out_tok, turn_cost)
    except Exception as exc:
        logger.warning("Failed to send Telegram cost notification: %s", exc)

    stage = analysis.get("stage", "SKIP")
    conviction = analysis.get("conviction_score", 0)

    # Sector correlation enforcement
    if stage == "TRADE_READY":
        sym_sector, _ = _sector_info(symbol)
        sym_direction = analysis.get("direction", "")
        conflict = next(
            (r for r in trade_ready_list
             if r["sector"] == sym_sector
             and r["direction"] == sym_direction
             and sym_sector != "UNKNOWN"),
            None,
        )
        if conflict:
            logger.info(
                "Sector correlation: %s downgraded — %s already has %s %s setup",
                symbol, sym_sector, sym_direction, conflict["symbol"],
            )
            stage = "WATCH"
            analysis["stage"] = "WATCH"
            analysis["skip_reason"] = (
                f"Sector correlation: {sym_sector} already has "
                f"{sym_direction} setup ({conflict['symbol']})"
            )
        else:
            trade_ready_list.append({
                "symbol":    symbol,
                "sector":    sym_sector,
                "direction": sym_direction,
            })

    # Watchlist Lifecycle Management
    if is_re:
        if stage == "TRADE_READY" or (conviction >= 75 and stage != "SKIP"):
            update_watchlist_staging(symbol, {"current_stage": "TRADE_READY", "updated_at": datetime.now(IST).isoformat()})
            send_loud(f"🚀 <b>{symbol} graduated</b>\nWatch → <b>Trade Ready</b> (Conviction: {conviction})")
            logger.info("Watchlist graduation: %s", symbol)
        elif conviction >= 55:
            # Maintain in watch, increment days
            new_days = days_in + 1
            if new_days > 10:
                update_watchlist_staging(symbol, {"current_stage": "EXPIRED", "updated_at": datetime.now(IST).isoformat()})
                send_silent(f"⏰ <b>{symbol} Watch expired</b>\nNo trigger in 10 days. Moved out of Watch.")
            else:
                update_watchlist_staging(symbol, {"days_in_stage": new_days, "updated_at": datetime.now(IST).isoformat()})
                logger.info("Watchlist maintenance: %s (Day %d)", symbol, new_days)
        else:
            # Conviction dropped
            update_watchlist_staging(symbol, {"current_stage": "DEGRADED", "updated_at": datetime.now(IST).isoformat()})
            send_silent(f"📉 <b>{symbol} removed from Watch</b>\nSetup broke (Conviction dropped to {conviction}).")
            logger.info("Watchlist degradation: %s", symbol)
    else:
        # New discovery — if it's WATCH or TRADE_READY, add to staging
        if stage in ("WATCH", "TRADE_READY", "ON_RADAR"):
            upsert_watchlist_staging({
                "symbol":            symbol,
                "current_stage":     stage,
                "direction_bias":    analysis.get("direction"),
                "days_in_stage":     0,
                "first_flagged_date": str(session_date),
                "updated_at":        datetime.now(IST).isoformat(),
            })
            logger.info("New watchlist discovery synced: %s stage=%s", symbol, stage)

    # Save to trade_setups if actionable
    if stage not in ("SKIP", None):
        try:
            setup_id = create_trade_setup({
                "session_id":       session_id,
                "setup_date":       str(session_date),
                "symbol":           symbol,
                "direction":        analysis.get("direction"),
                "stage":            stage,
                "setup_type":       analysis.get("setup_type"),
                "setup_maturity":   analysis.get("setup_maturity"),
                "conviction_score": analysis.get("conviction_score"),
                "strike":           analysis.get("strike"),
                "option_type":      analysis.get("option_type"),
                "expiry_date":      analysis.get("expiry_date"),
                "entry_zone_low":   analysis.get("entry_premium_low"),
                "entry_zone_high":  analysis.get("entry_premium_high"),
                "stop_loss_premium": analysis.get("stop_loss_premium"),
                "target_1_premium":  analysis.get("target_1_premium"),
                "target_2_premium":  analysis.get("target_2_premium"),
                "underlying_stop":  analysis.get("underlying_stop"),
                "lots":             analysis.get("lots"),
                "lot_size":         analysis.get("lot_size"),
                "max_risk_inr":     analysis.get("max_risk_inr"),
                "risk_reward":      analysis.get("risk_reward"),
                "iv_assessment":    analysis.get("iv_assessment"),
                "scoring_breakdown":    analysis.get("scoring_breakdown", {}),
                "signals_contributing": analysis.get("signals_contributing", []),
                "claude_full_rationale": analysis.get("claude_full_rationale"),
                "mentor_explanation":   analysis.get("mentor_explanation"),
                "key_learning_today":   analysis.get("key_learning_today"),
                "why_could_be_wrong":   analysis.get("why_could_be_wrong"),

                # Persistent regime dimensions
                "market_regime":     index_ctx.get("regime"),
                "market_trend":      index_ctx.get("market_trend"),
                "market_volatility":  index_ctx.get("market_volatility"),
                "market_structure":   index_ctx.get("market_structure"),
                "execution_bias":     index_ctx.get("execution_bias"),
                "fii_dii_stance":     index_ctx.get("fii_dii_stance"),
            })
            analysis["setup_id"] = setup_id
            logger.info("Trade setup saved: %s stage=%s id=%s", symbol, stage, setup_id)
        except Exception as exc:
            logger.error("Failed to save trade setup for %s: %s", symbol, exc)

    deep_result = {
        "symbol":        symbol,
        "stage":         stage,
        "direction":     analysis.get("direction"),
        "conviction":    analysis.get("conviction_score"),
        "lots":          analysis.get("lots"),
        "risk_reward":   analysis.get("risk_reward"),
        "quality_notes": quality_notes,
        "analysis":      analysis,
    }

    cost_info = _turn_cost(turn_num, "deep_analysis", symbol, in_tok, out_tok)
    return deep_result, cost_info


# ── Session cost JSON ─────────────────────────────────────────────────────────

def _turn_cost(turn_num: int, turn_type: str, symbol: str | None,
               in_tok: int, out_tok: int) -> dict:
    input_cost  = round(in_tok  / 1_000_000 * 3.00,  6)
    output_cost = round(out_tok / 1_000_000 * 15.00, 6)
    return {
        "turn_number":     turn_num,
        "turn_type":       turn_type,
        "symbol":          symbol,
        "input_tokens":    in_tok,
        "output_tokens":   out_tok,
        "input_cost_usd":  input_cost,
        "output_cost_usd": output_cost,
        "total_cost_usd":  round(input_cost + output_cost, 6),
    }


def _build_context_quality(deep_results: list[dict] | None) -> dict:
    all_notes: list[str] = []
    for dr in (deep_results or []):
        all_notes.extend(dr.get("quality_notes", []))

    def _has(keywords: list[str]) -> bool:
        return any(any(kw in n.lower() for kw in keywords) for n in all_notes)

    oi_ok    = not _has(["no oi series", "no futures series"])
    iv_ok    = not _has(["no options snapshot", "iv unavailable", "iv data from"])
    data_ok  = not _has(["no price history"])

    fii_source = "LIVE"
    try:
        from database.queries import get_latest_fii_dii
        row = get_latest_fii_dii()
        fii_source = row.get("source", "LIVE") if row else "UNKNOWN"
    except Exception:
        pass

    seen: set[str] = set()
    missing_flags: list[str] = []
    for n in all_notes:
        kw = n.lower()
        if any(w in kw for w in ("unavailable", "missing", "no ", "failed", "cache", "unknown")):
            if n not in seen:
                seen.add(n)
                missing_flags.append(n)

    return {
        "prescan_data_complete": True,
        "deep_data_complete":    data_ok,
        "oi_data_available":     oi_ok,
        "iv_data_available":     iv_ok,
        "fii_data_source":       fii_source,
        "missing_data_flags":    missing_flags[:10],
    }


def _save_session_cost_json(
    session_id: str,
    session_date: date,
    turn_costs: list[dict],
    regime: str | None = None,
    monthly_spent_before: float = 0.0,
    budget_usd: float = 50.0,
    usd_to_inr: float = 84.0,
    deep_results: list[dict] | None = None,
) -> None:
    try:
        logs_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
        os.makedirs(logs_dir, exist_ok=True)
        fname = os.path.join(logs_dir, f"session_cost_{session_date.strftime('%Y%m%d')}.json")

        total_in    = sum(t["input_tokens"]   for t in turn_costs)
        total_out   = sum(t["output_tokens"]  for t in turn_costs)
        total_cost  = round(sum(t["total_cost_usd"] for t in turn_costs), 4)
        monthly_now = monthly_spent_before + total_cost
        remaining   = max(0.0, budget_usd - monthly_now)
        sessions_est = int(remaining / total_cost) if total_cost > 0 else 0

        data = {
            "session_id":   session_id,
            "session_date": str(session_date),
            "model":        _MODEL,
            "regime":       regime or "UNKNOWN",
            "turns":        turn_costs,
            "totals": {
                "total_input_tokens":        total_in,
                "total_output_tokens":       total_out,
                "total_cost_usd":            total_cost,
                "total_cost_inr":            round(total_cost * usd_to_inr, 2),
                "monthly_budget_usd":        round(budget_usd, 2),
                "monthly_spent_usd":         round(monthly_now, 4),
                "monthly_remaining_usd":     round(remaining, 4),
                "sessions_remaining_estimate": sessions_est,
            },
            "context_quality": _build_context_quality(deep_results),
        }
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info("Session cost JSON written: %s (total=$%.4f)", fname, total_cost)
    except Exception as exc:
        logger.warning("Failed to write session cost JSON: %s", exc)


# ── Main entry ────────────────────────────────────────────────────────────────

def run_claude_session(
    context_bundle: dict,
    level1_passed:  list[str],
    session_id:     str,
    watchlist_priority: list[dict] | None = None,
) -> dict:
    """
    Execute the full multi-turn Claude session using modular turn-based functions.
    Raises BudgetExhaustedException if monthly Claude spend >= budget.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is blank in .env. "
            "Set it at console.anthropic.com before running the Claude session."
        )

    config        = get_all_system_config()
    budget_usd    = float(config.get("claude_monthly_budget_usd", _DEFAULT_BUDGET_USD))
    monthly_spent = 0.0
    try:
        monthly_spent = get_monthly_claude_spend()
        if monthly_spent >= budget_usd:
            from new_notifications.telegram import send_budget_exhausted
            send_budget_exhausted(monthly_spent, budget_usd, str(context_bundle.get("session_date")))
            raise BudgetExhaustedException(
                f"Monthly Claude budget exhausted: spent=${monthly_spent:.2f} "
                f"budget=${budget_usd:.2f}"
            )
        logger.info("Budget check OK: spent=$%.2f / $%.2f", monthly_spent, budget_usd)
    except BudgetExhaustedException:
        raise
    except Exception as exc:
        logger.warning("Budget check failed (non-fatal): %s — continuing", exc)

    client = anthropic.Anthropic(api_key=api_key, max_retries=0)
    session_date  = context_bundle["session_date"]

    # ── Turn 1: Market Context ────────────────────────────────────────────────
    # Initialize prompt builder with regime = None
    context_bundle["regime"] = None
    system_text = build_system_prompt(context_bundle)

    turn1_result, regime_result, messages, t1_cost = run_turn1_market_context(
        client=client,
        session_id=session_id,
        session_date=session_date,
        system_text=system_text,
    )
    turn_costs = [t1_cost]
    total_input = t1_cost["input_tokens"]
    total_output = t1_cost["output_tokens"]

    try:
        from new_notifications.telegram import send_phase1_complete
        send_phase1_complete(
            str(session_date),
            regime_result.get("regime", "UNKNOWN"),
            regime_result.get("execution_bias", "UNKNOWN"),
        )
    except Exception as _exc:
        logger.warning("Phase 1 notification failed: %s", _exc)

    # # Re-build system prompt using the dynamically generated regime context
    # context_bundle["regime"] = regime_result
    # system_text = build_system_prompt(context_bundle)
    #
    # # Token ceiling check before Turn 2
    # if total_input + total_output + 25_000 >= _TOKEN_CEILING:
    #     raise RuntimeError(
    #         f"Token ceiling ({_TOKEN_CEILING}) would be exceeded entering Turn 2 "
    #         f"({total_input + total_output} tokens used so far)."
    #     )
    #
    # # ── Turn 2: Pre-scan ──────────────────────────────────────────────────────
    # turn2_results, forwarded_stocks, messages, t2_cost = run_turn2_prescan(
    #     client=client,
    #     session_id=session_id,
    #     session_date=session_date,
    #     level1_passed=level1_passed,
    #     messages=messages,
    #     system_text=system_text,
    # )
    # turn_costs.append(t2_cost)
    # total_input += t2_cost["input_tokens"]
    # total_output += t2_cost["output_tokens"]
    #
    # try:
    #     from new_notifications.telegram import send_prescan_complete
    #     send_prescan_complete(str(session_date), len(forwarded_stocks), len(turn2_results))
    # except Exception as _exc:
    #     logger.warning("Prescan notification failed: %s", _exc)
    #
    # # Truncation / empty safety check
    # n = len(turn2_results)
    # if n == 0:
    #     reason = "Pre-scan returned 0 stocks — likely JSON parse failure or truncation"
    #     logger.error(reason)
    #     cost_usd = round(total_input / 1_000_000 * 3.00 + total_output / 1_000_000 * 15.00, 6)
    #     update_analysis_session(session_id, {
    #         "claude_tokens_input":  total_input,
    #         "claude_tokens_output": total_output,
    #         "claude_cost_usd":      cost_usd,
    #         "status":               "FAILED",
    #         "stage_statuses": {
    #             "claude_turn1":     "COMPLETE",
    #             "claude_turn2":     "FAILED",
    #             "failure_reason":   reason,
    #             "turn2_out_tokens": t2_cost["output_tokens"],
    #         },
    #     })
    #     _save_session_cost_json(session_id, session_date, turn_costs)
    #     raise RuntimeError(reason)
    # elif n < 5:
    #     logger.warning("Pre-scan returned only %d stocks — possible truncation.", n)
    #
    # # Combine pre-scan forwarded stocks with priority watchlist stocks
    # final_queue = forwarded_stocks[:]
    # for wl_stock in (watchlist_priority or []):
    #     if not any(fs["symbol"] == wl_stock["symbol"] for fs in forwarded_stocks):
    #         final_queue.insert(0, wl_stock)
    #     else:
    #         for fs in final_queue:
    #             if fs["symbol"] == wl_stock["symbol"]:
    #                 fs["is_watchlist_reanalysis"] = True
    #                 fs["days_in_stage"] = wl_stock.get("days_in_stage", 0)
    #
    # # ── Turns 3+: Deep Analysis ───────────────────────────────────────────────
    # deep_results: list[dict] = []
    # trade_ready_list: list[dict] = []
    #
    # # Pack the index dimensions explicitly for single stock runs
    # index_ctx = {
    #     "regime":            regime_result.get("regime")      if regime_result else "UNKNOWN",
    #     "market_trend":      regime_result.get("market_trend") if regime_result else "UNKNOWN",
    #     "market_volatility":  regime_result.get("market_volatility") if regime_result else "UNKNOWN",
    #     "market_structure":   regime_result.get("market_structure") if regime_result else "UNKNOWN",
    #     "execution_bias":     regime_result.get("execution_bias") if regime_result else "UNKNOWN",
    #     "fii_dii_stance":     regime_result.get("fii_dii_stance") if regime_result else "UNKNOWN",
    #     "nifty_close":       regime_result.get("nifty_close") if regime_result else None,
    #     "vix":               regime_result.get("vix")         if regime_result else None,
    #     "ema20":             regime_result.get("ema20")        if regime_result else None,
    #     "ema50":             regime_result.get("ema50")        if regime_result else None,
    #     "ret20d_pct":        regime_result.get("ret20d")       if regime_result else None,
    # }
    #
    # for i, prescan_stock in enumerate(final_queue):
    #     symbol    = prescan_stock.get("symbol", "")
    #     direction = prescan_stock.get("direction", "AUTO")
    #     is_re     = prescan_stock.get("is_watchlist_reanalysis", False)
    #     days_in   = prescan_stock.get("days_in_stage", 0)
    #     turn_num  = 3 + i
    #
    #     if not symbol:
    #         continue
    #
    #     deep_res, deep_cost = run_turn_deep_analysis(
    #         client=client,
    #         session_id=session_id,
    #         session_date=session_date,
    #         symbol=symbol,
    #         direction=direction,
    #         is_re=is_re,
    #         days_in=days_in,
    #         index_ctx=index_ctx,
    #         config=config,
    #         turn_num=turn_num,
    #         trade_ready_list=trade_ready_list,
    #     )
    #     deep_results.append(deep_res)
    #     turn_costs.append(deep_cost)
    #     total_input += deep_cost["input_tokens"]
    #     total_output += deep_cost["output_tokens"]
    #
    # cost_usd = round(
    #     total_input  / 1_000_000 * 3.00 +
    #     total_output / 1_000_000 * 15.00,
    #     6,
    # )
    #
    # trade_ready = sum(1 for d in deep_results if d.get("stage") == "TRADE_READY")
    # watch       = sum(1 for d in deep_results if d.get("stage") == "WATCH")
    # on_radar    = sum(1 for d in deep_results if d.get("stage") == "ON_RADAR")
    # skipped     = sum(1 for d in deep_results if d.get("stage") == "SKIP")
    # prescan_fwd = sum(1 for s in turn2_results if s.get("forward_to_deep"))
    #
    # try:
    #     from new_notifications.telegram import send_deep_analysis_complete
    #     send_deep_analysis_complete(str(session_date), trade_ready, watch, on_radar, skipped)
    # except Exception as _exc:
    #     logger.warning("Deep analysis complete notification failed: %s", _exc)
    #
    # update_analysis_session(session_id, {
    #     "claude_tokens_input":  total_input,
    #     "claude_tokens_output": total_output,
    #     "claude_cost_usd":      cost_usd,
    #     "status":               "ANALYSIS_COMPLETE",
    #     "trade_ready_count":    trade_ready,
    #     "watch_count":          watch,
    #     "radar_count":          on_radar,
    #     "stage_statuses": {
    #         "claude_turn1":          "COMPLETE",
    #         "claude_turn2":          "COMPLETE",
    #         "deep_analysis":         "COMPLETE",
    #         "prescan_total":         len(turn2_results),
    #         "prescan_forwarded":     prescan_fwd,
    #         "prescan_high_pri":      sum(1 for s in turn2_results if s.get("priority") == "HIGH"),
    #         "deep_trade_ready":      trade_ready,
    #         "deep_watch":            watch,
    #         "deep_on_radar":         on_radar,
    #         "deep_skip":             skipped,
    #     },
    #     "prompt_versions": _PROMPT_VERSIONS,
    # })
    #
    # logger.info(
    #     "Session complete: turns=%d in=%d out=%d cost=$%.4f | "
    #     "TRADE_READY=%d WATCH=%d ON_RADAR=%d SKIP=%d",
    #     2 + len(deep_results), total_input, total_output, cost_usd,
    #     trade_ready, watch, on_radar, skipped,
    # )
    #
    # _save_session_cost_json(
    #     session_id=session_id,
    #     session_date=session_date,
    #     turn_costs=turn_costs,
    #     regime=regime_result.get("regime") if regime_result else None,
    #     monthly_spent_before=monthly_spent if isinstance(monthly_spent, float) else 0.0,
    #     budget_usd=budget_usd,
    #     usd_to_inr=float(config.get("usd_to_inr_rate", 84.0)),
    #     deep_results=deep_results,
    # )
    #
    # return {
    #     "turn1_result":        turn1_result,
    #     "turn2_results":       turn2_results,
    #     "deep_results":        deep_results,
    #     "trade_ready":         trade_ready,
    #     "watch":               watch,
    #     "total_input_tokens":  total_input,
    #     "total_output_tokens": total_output,
    #     "cost_usd":            cost_usd,
    #     "regime_result":       regime_result,
    # }
    return {}
