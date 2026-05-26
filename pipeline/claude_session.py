"""
Claude multi-turn session manager — spec Sections 7, 8, 16.

Turn 1  : Market context (Nifty/VIX/FII-DII 30d) → JSON assessment
Turn 2  : Pre-scan all Level-1-passed stocks       → JSON array
Turns 3+: Deep analysis for each forwarded stock   → trade setup JSON

Call:
    result = run_claude_session(context_bundle, level1_passed, session_id)
"""
import json
import logging
import os
import time
from datetime import date

import anthropic
import pandas as pd
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
)
from indicators.technical import (
    atr_pct,
    calculate_ema,
    calculate_rsi,
    volume_ratio,
)
from pipeline.deep_analysis import (
    DEEP_SYSTEM,
    build_deep_prompt,
    build_stock_package,
    call_claude_deep,
    validate_position_sizing,
)
from pipeline.system_prompt_builder import build_system_prompt

load_dotenv()
logger = logging.getLogger(__name__)

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


# ── Turn 1: market context ────────────────────────────────────────────────────

def _build_turn1_message(session_date: date, regime_result: dict | None) -> str:
    nifty_rows = get_price_history("NIFTY_50",  days=32)
    vix_rows   = get_price_history("INDIA_VIX", days=32)
    fii_rows   = get_fii_dii_flows(days=32)

    nifty_30 = [
        {"date": r["date"], "close": r["close"], "high": r["high"], "low": r["low"]}
        for r in nifty_rows[-30:]
    ]
    vix_30  = [{"date": r["date"], "close": r["close"]} for r in vix_rows[-30:]]
    fii_30  = [
        {"date": r["date"], "fii_net_cr": r.get("fii_net_cr"), "dii_net_cr": r.get("dii_net_cr")}
        for r in fii_rows[-30:]
    ]

    payload = {
        "turn":         "market_context",
        "session_date": str(session_date),
        "regime":       regime_result.get("regime")      if regime_result else "UNKNOWN",
        "nifty_close":  regime_result.get("nifty_close") if regime_result else None,
        "vix_close":    regime_result.get("vix")         if regime_result else None,
        "ema20":        regime_result.get("ema20")        if regime_result else None,
        "ema50":        regime_result.get("ema50")        if regime_result else None,
        "ret20d_pct":   regime_result.get("ret20d")       if regime_result else None,
        "nifty_30d":    nifty_30,
        "vix_30d":      vix_30,
        "fii_dii_30d":  fii_30,
    }

    instructions = (
        "Analyse the market context above. "
        "Respond with ONLY a JSON object — no commentary outside the JSON:\n"
        "{\n"
        '  "session_narrative": "3-4 sentences on market condition and tone tonight",\n'
        '  "risk_flags": ["key risk 1", "key risk 2"],\n'
        '  "favourable_setups": "LONG | SHORT | NEUTRAL | BOTH",\n'
        '  "index_key_levels": {"support": 0, "resistance": 0}\n'
        "}"
    )

    return json.dumps(payload, ensure_ascii=False) + "\n\n" + instructions


# ── Turn 2: pre-scan ──────────────────────────────────────────────────────────

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


# ── Session cost JSON (FIX 7) ─────────────────────────────────────────────────

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
) -> dict:
    """
    Execute the full multi-turn Claude session:
      Turn 1  — market context assessment
      Turn 2  — pre-scan all Level 1 passed stocks
      Turns 3+ — deep analysis for each stock forwarded by pre-scan

    Persists turns to session_claude_turns and updates analysis_sessions.
    Raises BudgetExhaustedException if monthly Claude spend >= budget.
    Raises RuntimeError on API key missing or token ceiling exceeded.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is blank in .env. "
            "Set it at console.anthropic.com before running the Claude session."
        )

    # ── FIX 3: Budget circuit breaker ────────────────────────────────────────
    config        = get_all_system_config()
    budget_usd    = float(config.get("claude_monthly_budget_usd", _DEFAULT_BUDGET_USD))
    monthly_spent = 0.0
    try:
        monthly_spent = get_monthly_claude_spend()
        if monthly_spent >= budget_usd:
            from integrations.telegram import send_budget_exhausted
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
    regime_result = context_bundle.get("regime")
    system_text   = build_system_prompt(context_bundle)

    total_input  = 0
    total_output = 0
    messages: list[dict] = []
    turn_costs:  list[dict] = []

    # ── Turn 1: market context ────────────────────────────────────────────────
    logger.info("Turn 1: assembling market context data...")
    t1_text_user = _build_turn1_message(session_date, regime_result)
    messages.append({"role": "user", "content": t1_text_user})

    logger.info("Turn 1: calling Claude...")
    t1_resp     = _call_claude(client, system_text, messages, max_tokens=1500)
    t1_out_text = t1_resp.content[0].text

    u1 = t1_resp.usage
    total_input  += u1.input_tokens
    total_output += u1.output_tokens
    turn_costs.append(_turn_cost(1, "market_context", None, u1.input_tokens, u1.output_tokens))
    logger.info("Turn 1 done: in=%d out=%d cache_create=%s cache_read=%s",
                u1.input_tokens, u1.output_tokens,
                getattr(u1, "cache_creation_input_tokens", "-"),
                getattr(u1, "cache_read_input_tokens", "-"))

    save_claude_turn(session_id, 1, "market_context", None,
                     u1.input_tokens, u1.output_tokens, t1_out_text)
    messages.append({"role": "assistant", "content": t1_out_text})

    try:
        turn1_result = _parse_json(t1_out_text)
    except Exception as exc:
        logger.error("Turn 1 JSON parse failed: %s | raw=%s", exc, t1_out_text[:300])
        turn1_result = {"session_narrative": t1_out_text, "parse_error": str(exc)}

    update_analysis_session(session_id, {
        "stage_statuses": {"claude_turn1": "COMPLETE"},
        "prompt_versions": _PROMPT_VERSIONS,
    })

    # ── Token ceiling check before Turn 2 ────────────────────────────────────
    if total_input + total_output + 25_000 >= _TOKEN_CEILING:
        raise RuntimeError(
            f"Token ceiling ({_TOKEN_CEILING}) would be exceeded entering Turn 2 "
            f"({total_input + total_output} tokens used so far)."
        )

    # ── Turn 2: pre-scan ──────────────────────────────────────────────────────
    logger.info("Turn 2: assembling pre-scan data for %d stocks...", len(level1_passed))
    t2_text_user = _build_turn2_message(level1_passed, session_date)
    messages.append({"role": "user", "content": t2_text_user})

    logger.info("Turn 2: calling Claude...")
    t2_resp     = _call_claude(client, system_text, messages, max_tokens=12000)
    t2_out_text = t2_resp.content[0].text

    u2 = t2_resp.usage
    total_input  += u2.input_tokens
    total_output += u2.output_tokens
    turn_costs.append(_turn_cost(2, "prescan", None, u2.input_tokens, u2.output_tokens))
    logger.info("Turn 2 done: in=%d out=%d cache_read=%s",
                u2.input_tokens, u2.output_tokens,
                getattr(u2, "cache_read_input_tokens", "-"))

    save_claude_turn(session_id, 2, "prescan", None,
                     u2.input_tokens, u2.output_tokens, t2_out_text)
    messages.append({"role": "assistant", "content": t2_out_text})

    try:
        turn2_results = _parse_json(t2_out_text)
        if not isinstance(turn2_results, list):
            raise ValueError(f"Expected JSON array, got {type(turn2_results).__name__}")
    except Exception as exc:
        logger.error("Turn 2 JSON parse failed: %s | raw=%s", exc, t2_out_text[:300])
        turn2_results = []

    # Truncation / empty safety check
    n = len(turn2_results)
    if n == 0:
        reason = "Pre-scan returned 0 stocks — likely JSON parse failure or truncation"
        logger.error(reason)
        cost_usd = round(total_input / 1_000_000 * 3.00 + total_output / 1_000_000 * 15.00, 6)
        update_analysis_session(session_id, {
            "claude_tokens_input":  total_input,
            "claude_tokens_output": total_output,
            "claude_cost_usd":      cost_usd,
            "status":               "FAILED",
            "stage_statuses": {
                "claude_turn1":     "COMPLETE",
                "claude_turn2":     "FAILED",
                "failure_reason":   reason,
                "turn2_out_tokens": u2.output_tokens,
            },
            "prompt_versions": _PROMPT_VERSIONS,
        })
        _save_session_cost_json(session_id, session_date, turn_costs)
        raise RuntimeError(reason)
    elif n < 5:
        logger.warning("Pre-scan returned only %d stocks — possible truncation.", n)

    forwarded_stocks = [s for s in turn2_results if s.get("forward_to_deep")]
    # HIGH priority first
    forwarded_stocks.sort(key=lambda s: (s.get("priority") != "HIGH", s.get("priority") != "MEDIUM"))
    logger.info("Pre-scan: %d stocks forwarded to deep analysis (%d HIGH priority)",
                len(forwarded_stocks),
                sum(1 for s in forwarded_stocks if s.get("priority") == "HIGH"))

    # ── Turns 3+: deep analysis (FIX 1) ──────────────────────────────────────
    deep_results: list[dict] = []

    # Build index context once for all deep turns
    index_ctx = {
        "regime":      regime_result.get("regime")      if regime_result else "UNKNOWN",
        "nifty_close": regime_result.get("nifty_close") if regime_result else None,
        "vix":         regime_result.get("vix")         if regime_result else None,
        "ema20":       regime_result.get("ema20")        if regime_result else None,
        "ema50":       regime_result.get("ema50")        if regime_result else None,
        "ret20d_pct":  regime_result.get("ret20d")       if regime_result else None,
    }

    for i, prescan_stock in enumerate(forwarded_stocks):
        symbol    = prescan_stock.get("symbol", "")
        direction = prescan_stock.get("direction", "AUTO")
        turn_num  = 3 + i

        if not symbol:
            continue

        logger.info("Turn %d: deep analysis for %s (direction=%s)...", turn_num, symbol, direction)
        quality_notes: list[str] = []

        try:
            stock_pkg = build_stock_package(symbol, session_date, quality_notes)
        except Exception as exc:
            logger.error("Build stock package failed for %s: %s", symbol, exc)
            continue

        if not stock_pkg:
            logger.warning("Turn %d: no data for %s — skipping deep analysis", turn_num, symbol)
            deep_results.append({
                "symbol": symbol,
                "stage":  "SKIP",
                "skip_reason": "No price history available",
                "quality_notes": quality_notes,
            })
            continue

        prompt = build_deep_prompt(stock_pkg, index_ctx, direction)

        try:
            analysis, in_tok, out_tok = call_claude_deep(client, prompt)
        except Exception as exc:
            logger.error("Deep analysis Claude call failed for %s: %s", symbol, exc)
            deep_results.append({
                "symbol": symbol,
                "stage":  "SKIP",
                "skip_reason": f"Claude call failed: {exc}",
                "quality_notes": quality_notes,
            })
            continue

        total_input  += in_tok
        total_output += out_tok
        turn_costs.append(_turn_cost(turn_num, "deep_analysis", symbol, in_tok, out_tok))

        # FIX 2: Python position sizing validation
        analysis["symbol"] = symbol
        analysis = validate_position_sizing(analysis, config)

        save_claude_turn(session_id, turn_num, "deep_analysis", symbol,
                         in_tok, out_tok, json.dumps(analysis))

        stage = analysis.get("stage", "SKIP")
        logger.info(
            "Turn %d done: %s stage=%s conviction=%s lots=%s rr=%s in=%d out=%d",
            turn_num, symbol, stage,
            analysis.get("conviction_score"), analysis.get("lots"),
            analysis.get("risk_reward"), in_tok, out_tok,
        )

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
                    "stop_loss":        analysis.get("stop_loss_premium"),
                    "target_1":         analysis.get("target_1_premium"),
                    "target_2":         analysis.get("target_2_premium"),
                    "underlying_entry": analysis.get("entry_zone_low"),
                    "underlying_stop":  analysis.get("underlying_stop"),
                    "underlying_t1":    analysis.get("underlying_target_1"),
                    "underlying_t2":    analysis.get("underlying_target_2"),
                    "lots":             analysis.get("lots"),
                    "lot_size":         analysis.get("lot_size"),
                    "max_risk_inr":     analysis.get("max_risk_inr"),
                    "risk_reward":      analysis.get("risk_reward"),
                    "iv_assessment":    analysis.get("iv_assessment"),
                    "scoring_breakdown":    json.dumps(analysis.get("scoring_breakdown", {})),
                    "signals_contributing": json.dumps(analysis.get("signals_contributing", [])),
                    "claude_rationale":     analysis.get("claude_full_rationale"),
                    "mentor_explanation":   analysis.get("mentor_explanation"),
                    "why_could_be_wrong":   analysis.get("why_could_be_wrong"),
                })
                analysis["setup_id"] = setup_id
                logger.info("Trade setup saved: %s stage=%s id=%s", symbol, stage, setup_id)
            except Exception as exc:
                logger.error("Failed to save trade setup for %s: %s", symbol, exc)

        deep_results.append({
            "symbol":        symbol,
            "stage":         stage,
            "direction":     analysis.get("direction"),
            "conviction":    analysis.get("conviction_score"),
            "lots":          analysis.get("lots"),
            "risk_reward":   analysis.get("risk_reward"),
            "quality_notes": quality_notes,
            "analysis":      analysis,
        })

    # ── Cost calculation and session update ───────────────────────────────────
    cost_usd = round(
        total_input  / 1_000_000 * 3.00 +
        total_output / 1_000_000 * 15.00,
        6,
    )

    trade_ready = sum(1 for d in deep_results if d.get("stage") == "TRADE_READY")
    watch       = sum(1 for d in deep_results if d.get("stage") == "WATCH")
    on_radar    = sum(1 for d in deep_results if d.get("stage") == "ON_RADAR")
    skipped     = sum(1 for d in deep_results if d.get("stage") == "SKIP")
    prescan_fwd = sum(1 for s in turn2_results if s.get("forward_to_deep"))

    update_analysis_session(session_id, {
        "claude_tokens_input":  total_input,
        "claude_tokens_output": total_output,
        "claude_cost_usd":      cost_usd,
        "status":               "ANALYSIS_COMPLETE",
        "stage_statuses": {
            "claude_turn1":          "COMPLETE",
            "claude_turn2":          "COMPLETE",
            "deep_analysis":         "COMPLETE",
            "prescan_total":         len(turn2_results),
            "prescan_forwarded":     prescan_fwd,
            "prescan_high_pri":      sum(1 for s in turn2_results if s.get("priority") == "HIGH"),
            "deep_trade_ready":      trade_ready,
            "deep_watch":            watch,
            "deep_on_radar":         on_radar,
            "deep_skip":             skipped,
        },
        "prompt_versions": _PROMPT_VERSIONS,
    })

    logger.info(
        "Session complete: turns=%d in=%d out=%d cost=$%.4f | "
        "TRADE_READY=%d WATCH=%d ON_RADAR=%d SKIP=%d",
        2 + len(deep_results), total_input, total_output, cost_usd,
        trade_ready, watch, on_radar, skipped,
    )

    # FIX 7: write session cost JSON with full spec format
    _save_session_cost_json(
        session_id=session_id,
        session_date=session_date,
        turn_costs=turn_costs,
        regime=regime_result.get("regime") if regime_result else None,
        monthly_spent_before=monthly_spent if isinstance(monthly_spent, float) else 0.0,
        budget_usd=budget_usd,
        usd_to_inr=float(config.get("usd_to_inr_rate", 84.0)),
        deep_results=deep_results,
    )

    return {
        "turn1_result":        turn1_result,
        "turn2_results":       turn2_results,
        "deep_results":        deep_results,
        "trade_ready":         trade_ready,
        "watch":               watch,
        "total_input_tokens":  total_input,
        "total_output_tokens": total_output,
        "cost_usd":            cost_usd,
    }
