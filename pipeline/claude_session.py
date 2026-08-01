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

from config.constants import SYMBOL_NIFTY_50, SYMBOL_INDIA_VIX, SYMBOL_NIFTY_BANK, SYMBOL_NIFTY_IT, SYMBOL_NIFTY_AUTO, \
    SYMBOL_NIFTY_PHARMA, SYMBOL_NIFTY_FMCG, SYMBOL_NIFTY_METAL, SYMBOL_NIFTY_ENERGY, SYMBOL_NIFTY_FIN_SERVICE, \
    SYMBOL_NIFTY_CONSUMPTION, SYMBOL_NIFTY_INFRA, SYMBOL_NIFTY_MEDIA
from database.queries import (
    create_trade_setup,
    get_all_system_config,
    get_claude_turn,
    get_continuous_oi,
    get_fii_dii_flows,
    get_futures_row,
    get_monthly_claude_spend,
    get_price_history,
    save_claude_turn,
    update_analysis_session,
    get_options_by_date,
    get_options_snapshot,
    get_analysis_session,
)
from indicators.technical import (
    atr_pct,
    calculate_atr,
    calculate_ema,
    calculate_rsi,
    volume_ratio,
    compute_stock_indicators,
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
    "pre_scan":        "v1.0",
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


# ── Turn 1: Market Intelligence Layer ────────────────────────────────────────

_SECTOR_SYMBOL_MAP = {
    SYMBOL_NIFTY_BANK:        "BANKING",
    SYMBOL_NIFTY_IT:          "IT",
    SYMBOL_NIFTY_AUTO:        "AUTO",
    SYMBOL_NIFTY_PHARMA:      "PHARMA",
    SYMBOL_NIFTY_FMCG:        "FMCG",
    SYMBOL_NIFTY_METAL:       "METAL",
    SYMBOL_NIFTY_ENERGY:      "ENERGY",
    SYMBOL_NIFTY_FIN_SERVICE: "FINSERV",
    SYMBOL_NIFTY_INFRA:       "INFRA",
    SYMBOL_NIFTY_CONSUMPTION: "CONSUMER",
    SYMBOL_NIFTY_MEDIA:       "MEDIA",
}

_TURN1_SYSTEM = (
    "You are an experienced Indian F&O swing trading analyst and mentor. "
    "You think like a disciplined proprietary trader — capital preservation first, "
    "high conviction setups only — and you teach like a mentor, always explaining "
    "your reasoning so the user can learn to spot these setups themselves. "
    "You specialise in Nifty 50 stock options, 2-5 day swing trades, monthly Tuesday expiry."
)

_TURN1_REQUIRED_KEYS = [
    "session_narrative", "market_trend", "market_volatility", "market_structure",
    "execution_bias", "fii_dii_stance", "session_risk_level", "conviction_multiplier",
    "vix_assessment", "fii_dii_assessment", "sector_pictures", "directional_filters",
    "prescan_guidance", "nifty_price_structure", "index_key_levels", "risk_flags", "guidance",
    "mentor_notes",
]

_TURN1_REQUIRED_SECTORS = [
    "BANKING", "IT", "AUTO", "PHARMA", "FMCG",
    "METAL", "ENERGY", "FINSERV", "INFRA", "CONSUMER", "MEDIA",
]

_TURN1_REQUIRED_PRICE_STRUCTURE_KEYS = [
    "overall_structure", "trend_quality", "swing_points", "key_price_zones",
    "pattern_identified", "ema_structure", "breakout_conditions", "volume_analysis",
    "price_narrative", "trading_implication",
]

_TURN1_REQUIRED_TRADING_IMPLICATION_KEYS = [
    "summary", "index_bias", "conviction_adjustment", "key_condition_to_watch",
]

_TURN1_DEFAULT_SECTOR_PICTURE = {
    "trend": "SIDEWAYS", "momentum": "STABLE", "stance": "NEUTRAL", "strength": "WEAK",
    "structure": "RANGE",
    "key_levels": {"support": None, "resistance": None, "breakout_above": None, "breakdown_below": None},
    "momentum_note": "", "character": "", "trading_note": "",
}

_TURN1_DEFAULTS = {
    "session_narrative":       "Market context unavailable — treating as neutral session.",
    "market_trend":            "SIDEWAYS",
    "market_volatility":       "NORMAL",
    "market_structure":        "WIDE",
    "execution_bias":          "NEUTRAL",
    "fii_dii_stance":          "NEUTRAL",
    "session_risk_level":      "MEDIUM",
    "conviction_multiplier":   0.95,
    "vix_assessment":          {"current": None, "trend": "STABLE", "character": "", "options_implication": ""},
    "fii_dii_assessment":      {"fii_20d_character": "", "recent_shift": "NO", "shift_description": None,
                                "dii_stance_description": "", "divergence": "NO", "key_insight": ""},
    "sector_pictures":         {name: dict(_TURN1_DEFAULT_SECTOR_PICTURE) for name in _TURN1_REQUIRED_SECTORS},
    "directional_filters":     {"avoid_longs_in": [], "avoid_shorts_in": [], "caution_sectors": []},
    "prescan_guidance":        {"max_stocks_to_forward": 10, "prefer_directions": ["LONG", "SHORT"],
                                "prioritise_sectors": [], "deprioritise_sectors": [], "special_instructions": None,
                                "expiry_note": None},
    "nifty_price_structure":   {
        "overall_structure": "RANGE", "trend_quality": "CONFLICTING",
        "swing_points": {"major_high": {"price": None, "date": None}, "major_low": {"price": None, "date": None},
                         "recent_high": {"price": None, "date": None}, "recent_low": {"price": None, "date": None}},
        "key_price_zones": [],
        "pattern_identified": {
            "classical_pattern":   {"name": "NONE", "completion": "FAILED", "target": None, "note": ""},
            "candlestick_pattern": {"name": "NONE", "location": "MID_RANGE", "significance": "LOW", "note": ""},
            "volume_pattern":      {"name": "NONE", "note": ""},
        },
        "ema_structure": {"short_term": "", "medium_term": "", "long_term": "", "arrangement": "MIXED", "arrangement_note": ""},
        "breakout_conditions": {"bull_breakout_above": None, "bull_breakout_note": "",
                                "bear_breakdown_below": None, "bear_breakdown_note": ""},
        "volume_analysis": {"recent_trend": "STABLE", "anomaly_days": [], "confirmation": "MIXED", "note": ""},
        "price_narrative": "Price structure unavailable — treating as neutral session.",
        "trading_implication": {
            "summary": "No structural read available — apply standard caution.",
            "index_bias": "NEUTRAL", "conviction_adjustment": "NEUTRAL",
            "key_condition_to_watch": "",
        },
    },
    "index_key_levels":        {"strong_support": 0, "support": 0, "current": 0,
                                "resistance": 0, "strong_resistance": 0,
                                "max_pain": None, "pcr_signal": "NEUTRAL", "levels_note": ""},
    "risk_flags":              [],
    "guidance":                {"favour": "No specific guidance.", "caution": "Exercise standard caution."},
    "mentor_notes":            {
        "todays_key_lesson": "",
        "what_i_looked_at_first": "",
        "pattern_to_watch": "",
        "sector_rotation_insight": "",
        "fii_dii_reading": "",
    },
}


def _next_thursday(d: date) -> date:
    """Nifty weekly index options expire on Thursday. Returns d itself if d is a Thursday."""
    return d + timedelta(days=(3 - d.weekday()) % 7)

def _next_tuesday(d: date) -> date:
    """Nifty weekly index options expire on Tuesday. Returns d itself if d is a Tuesday."""
    return d + timedelta(days=(1 - d.weekday()) % 7)

def _expiry_oi_block(snapshot_rows: list[dict], expiry_d: date) -> dict | None:
    """Build CE/PE walls + PCR + max pain for one expiry from a day's options_snapshots rows."""
    from pipeline.oi_series_builder import _calc_max_pain, _pcr_and_oi_from_options

    expiry_str = str(expiry_d)
    rows = [r for r in snapshot_rows if str(r.get("expiry_date")) == expiry_str]
    if not rows:
        return None

    walls = oi_walls(rows, expiry_str, top_n=5)
    pcr_near, _, _, _ = _pcr_and_oi_from_options(rows, expiry_str)
    max_pain = _calc_max_pain(rows, expiry_str)
    return {
        "expiry_date": expiry_str,
        "ce_walls": [{"strike": float(w["strike"]), "oi": int(w["oi"] or 0)} for w in walls["ce_walls"]],
        "pe_walls": [{"strike": float(w["strike"]), "oi": int(w["oi"] or 0)} for w in walls["pe_walls"]],
        "pcr_near": pcr_near,
        "max_pain": max_pain,
    }


def _build_turn1_data(session_date: date) -> dict:
    """
    Reads all required data from DB for Turn 1 — market intelligence layer only.
    No API calls. No external dependencies. No stock-level data.
    Returns structured dict for prompt injection.
    """
    from pipeline.oi_series_builder import _trading_days_to
    from new_data_ingestion.nse_bhavcopy import get_holiday_dates
    _holidays_str = {str(d) for d in get_holiday_dates()}

    # ── Source 1: Nifty 50 ───────────────────────────────────────────────────
    nifty_rows = get_price_history(SYMBOL_NIFTY_50, days=180)
    df_nifty = pd.DataFrame(nifty_rows)
    for col in ("open", "high", "low", "close", "volume"):
        df_nifty[col] = pd.to_numeric(df_nifty[col], errors="coerce")
    df_nifty = df_nifty.dropna(subset=["close"]).reset_index(drop=True)

    close_series = df_nifty["close"]
    price        = float(close_series.iloc[-1])
    ema20_val    = round(float(calculate_ema(close_series, 20).iloc[-1]), 2)
    ema50_val    = round(float(calculate_ema(close_series, 50).iloc[-1]), 2)
    ema180_val   = round(float(calculate_ema(close_series, 180).iloc[-1]), 2) if len(df_nifty) >= 180 else None
    ret5d_val    = round((price / float(close_series.iloc[-5])  - 1) * 100, 2) if len(df_nifty) >= 5  else None
    ret20d_val   = round((price / float(close_series.iloc[-20]) - 1) * 100, 2) if len(df_nifty) >= 20 else None
    ret60d_val   = round((price / float(close_series.iloc[-60]) - 1) * 100, 2) if len(df_nifty) >= 60 else None
    atr_series   = calculate_atr(df_nifty, 14)
    atr_pct_val  = round(float(atr_series.iloc[-1]) / price * 100, 2) if not pd.isna(atr_series.iloc[-1]) else None

    price_vs_ema20  = "above" if price > ema20_val else "below"
    price_vs_ema50  = "above" if price > ema50_val else "below"
    price_vs_ema180 = ("above" if price > ema180_val else "below") if ema180_val is not None else "unavailable"
    ema20_vs_ema50  = ">" if ema20_val > ema50_val else "<"
    ema_arrangement = "bullish" if ema20_val > ema50_val else "bearish"

    df_120 = df_nifty[~df_nifty["date"].astype(str).isin(_holidays_str)].tail(120)
    nifty_ohlcv_120d = [
        {
            "date":   str(r["date"]),
            "open":   float(r["open"]),
            "high":   float(r["high"]),
            "low":    float(r["low"]),
            "close":  float(r["close"]),
            "volume": int(r["volume"]) if not pd.isna(r["volume"]) else 0,
        }
        for _, r in df_120.iterrows()
    ]

    nifty_indicators = {
        "current_price": round(price, 2),
        "ema20":   ema20_val,
        "ema50":   ema50_val,
        "ema180":  ema180_val,
        "ret5d":   ret5d_val,
        "ret20d":  ret20d_val,
        "ret60d":  ret60d_val,
        "atr_pct": atr_pct_val,
    }
    nifty_ema_relationships = {
        "price_vs_ema20":  price_vs_ema20,
        "price_vs_ema50":  price_vs_ema50,
        "price_vs_ema180": price_vs_ema180,
        "ema20_vs_ema50":  ema20_vs_ema50,
        "ema_arrangement": ema_arrangement,
    }

    # ── Source 2: India VIX ──────────────────────────────────────────────────
    vix_rows = get_price_history(SYMBOL_INDIA_VIX, days=30)
    vix_close_30d = [
        {"date": str(r["date"]), "close": float(r["close"])}
        for r in vix_rows
        if r.get("close") is not None and float(r["close"]) != 0 and str(r["date"]) not in _holidays_str
    ]

    # ── Source 3: FII / DII flows ────────────────────────────────────────────
    fii_rows = get_fii_dii_flows(days=30)
    fii_dii_flows_30d = [
        {
            "date":       str(r["date"]),
            "fii_net_cr": r.get("fii_net_cr"),
            "dii_net_cr": r.get("dii_net_cr"),
        }
        for r in fii_rows
    ]

    data_quality = "LIVE"
    last_val = 0
    if fii_rows:
        last_val = fii_rows[-1].get("fii_net_cr")
        consecutive_same = 1
        for i in range(len(fii_rows) - 2, -1, -1):
            if fii_rows[i].get("fii_net_cr") == last_val:
                consecutive_same += 1
            else:
                break
        if consecutive_same >= 3:
            data_quality = f"STALE_{consecutive_same}d"
        else:
            try:
                last_date = date.fromisoformat(str(fii_rows[-1]["date"]))
                if last_date < session_date - timedelta(days=2):
                    data_quality = "CACHED_1D"
            except Exception:
                pass

    # ── Source 4: Sector indices ─────────────────────────────────────────────
    sectors_data: dict[str, dict] = {}
    for symbol, display_name in _SECTOR_SYMBOL_MAP.items():
        sec_rows = get_price_history(symbol, days=60)
        df_sec = pd.DataFrame(sec_rows)
        if not df_sec.empty:
            for col in ("open", "high", "low", "close"):
                df_sec[col] = pd.to_numeric(df_sec[col], errors="coerce")
            df_sec = df_sec.dropna(subset=["close"]).reset_index(drop=True)
            df_sec = df_sec[~df_sec["date"].astype(str).isin(_holidays_str)].reset_index(drop=True)

        if len(df_sec) < 7:
            logger.warning("Insufficient data for sector %s after holiday filter — skipping", symbol)
            continue

        sec_close = df_sec["close"]
        sec_price = float(sec_close.iloc[-1])
        s_ret7d  = round((sec_price / float(sec_close.iloc[-7])  - 1) * 100, 2) if len(df_sec) >= 7  else None
        s_ret20d = round((sec_price / float(sec_close.iloc[-20]) - 1) * 100, 2) if len(df_sec) >= 20 else None
        s_ret60d = round((sec_price / float(sec_close.iloc[-60]) - 1) * 100, 2) if len(df_sec) >= 60 else None
        vs_nifty = round(s_ret20d - ret20d_val, 2) if (s_ret20d is not None and ret20d_val is not None) else None

        df_30 = df_sec.tail(30)
        sec_ohlcv_30d = [
            {
                "date":  str(r["date"]),
                "open":  float(r["open"]),
                "high":  float(r["high"]),
                "low":   float(r["low"]),
                "close": float(r["close"]),
            }
            for _, r in df_30.iterrows()
        ]

        sectors_data[display_name] = {
            "returns":    {"ret7d": s_ret7d, "ret20d": s_ret20d, "ret60d": s_ret60d, "vs_nifty_20d": vs_nifty},
            "ohlcv_30d":  sec_ohlcv_30d,
        }

    # ── Source 5: Nifty weekly options positioning ───────────────────────────
    current_weekly_expiry = _next_tuesday(session_date)
    next_weekly_expiry    = current_weekly_expiry + timedelta(days=7)
    days_to_nifty_expiry  = _trading_days_to(session_date, current_weekly_expiry)

    if days_to_nifty_expiry == 0:
        reliability = "UNRELIABLE"
        reliability_note = ("Expiry today — OI reflects only expiring positions. "
                            "Do not use for forward sentiment assessment.")
    elif days_to_nifty_expiry == 1:
        reliability = "DISTORTED"
        reliability_note = ("Expiry tomorrow — positions actively unwinding. "
                            "Use for broad directional read only, not precise level assessment.")
    elif days_to_nifty_expiry == 2:
        reliability = "MODERATE"
        reliability_note = ("2 days to expiry — some distortion from position management. "
                            "Weight next week OI more heavily.")
    else:
        reliability = "HIGH"
        reliability_note = f"{days_to_nifty_expiry} days to expiry — OI positions are active and reliable."

    dual_expiry = days_to_nifty_expiry <= 2

    options_available = False
    current_oi_block = None
    next_oi_block = None
    try:
        snap_rows = get_options_by_date(SYMBOL_NIFTY_50, session_date)
        if snap_rows:
            current_oi_block = _expiry_oi_block(snap_rows, current_weekly_expiry)
            options_available = current_oi_block is not None
            if dual_expiry:
                next_oi_block = _expiry_oi_block(snap_rows, next_weekly_expiry)
    except Exception as exc:
        logger.warning("Turn 1 Nifty weekly options fetch failed: %s", exc)

    nifty_options = {
        "current_weekly_expiry": str(current_weekly_expiry),
        "next_weekly_expiry":    str(next_weekly_expiry),
        "days_to_nifty_expiry":  days_to_nifty_expiry,
        "reliability":           reliability,
        "reliability_note":      reliability_note,
        "dual_expiry":           dual_expiry,
        "options_available":     options_available,
        "current": current_oi_block or {"ce_walls": [], "pe_walls": [], "pcr_near": None, "max_pain": None},
        "next":    next_oi_block,
    }

    logger.info(
        "Turn 1 data ready: nifty=%d rows, vix=%d rows, fii=%d rows, sectors=%d/11 available, "
        "options=%s, dual_expiry=%s",
        len(nifty_ohlcv_120d),
        len(vix_close_30d),
        len(fii_dii_flows_30d),
        len(sectors_data),
        "YES" if options_available else "NO",
        "YES" if dual_expiry else "NO",
    )

    return {
        "session_date": str(session_date),
        "nifty": {
            "indicators":        nifty_indicators,
            "ema_relationships": nifty_ema_relationships,
            "ohlcv_120d":        nifty_ohlcv_120d,
        },
        "vix": {
            "close_30d": vix_close_30d,
        },
        "fii_dii": {
            "data_quality": data_quality,
            "flows_30d":    fii_dii_flows_30d,
            "fii_net_cr":   last_val,
        },
        "sectors": sectors_data,
        "nifty_options": nifty_options,
    }


def _build_turn1_prompt(data: dict) -> str:
    """
    Builds the Turn 1 user message from prepared data.
    Returns plain text string ready for Claude.
    Turn 1 is the market intelligence layer only — no stock-level scope.
    """
    _j = lambda arr: json.dumps(arr, separators=(",", ":"))

    ind     = data["nifty"]["indicators"]
    rel     = data["nifty"]["ema_relationships"]
    opt     = data["nifty_options"]
    fii     = data["fii_dii"]
    vix     = data["vix"]

    ema180_str = str(ind["ema180"]) if ind["ema180"] is not None else "Unavailable"
    price  = ind["current_price"]
    ema20  = ind["ema20"]
    ema50  = ind["ema50"]
    ema180 = ind["ema180"]

    # ── Options section (Section 5) ──────────────────────────────────────────
    def _expiry_oi_text(block: dict, label: str) -> str:
        if not block or not block.get("ce_walls"):
            return f"{label}: no options data available."
        ce_lines = "\n".join(f"  Strike {w['strike']}: OI {w['oi']:,}" for w in block["ce_walls"])
        pe_lines = "\n".join(f"  Strike {w['strike']}: OI {w['oi']:,}" for w in block["pe_walls"])
        pcr_str  = str(round(block["pcr_near"], 2)) if block.get("pcr_near") is not None else "Unavailable"
        mp_str   = str(int(block["max_pain"]))      if block.get("max_pain") is not None else "Unavailable"
        return (
            f"{label} (expiry {block['expiry_date']}):\n"
            f"CE walls (resistance / supply):\n{ce_lines}\n\n"
            f"PE walls (support / demand):\n{pe_lines}\n\n"
            f"PCR: {pcr_str}\nMax Pain: {mp_str}"
        )

    if not opt["options_available"]:
        options_block = "No Nifty weekly options data available for today.\nUse price structure and FII flows only — do not infer OI-based sentiment."
    elif opt["dual_expiry"]:
        options_block = (
            f"{_expiry_oi_text(opt['current'], 'CURRENT WEEK')}\n\n"
            f"{_expiry_oi_text(opt['next'], 'NEXT WEEK')}\n\n"
            "Next week OI is more relevant for forward-looking sentiment — "
            "current week positions are actively unwinding into expiry. "
            "Compare sentiment between the two expiries, note any divergence, "
            "and state which expiry better represents forward positioning."
        )
    else:
        options_block = _expiry_oi_text(opt["current"], "CURRENT WEEK")

    # FII data quality warning
    dq = fii["data_quality"]
    dq_warning = ""
    if dq != "LIVE":
        dq_warning = f"\nWARNING: Flows may not reflect today's actual data ({dq}). Factor this uncertainty into your assessment.\n"

    # Sector blocks
    sector_blocks = []
    for name, sec in data["sectors"].items():
        r = sec["returns"]
        sector_blocks.append(
            f"── {name} ──────────────────────────────────────────────────\n"
            f"Returns: 7d={r['ret7d']}% | 20d={r['ret20d']}% | 60d={r['ret60d']}% | vs_nifty_20d={r['vs_nifty_20d']}%\n"
            f"30d OHLCV: {_j(sec['ohlcv_30d'])}"
        )
    sectors_text = "\n\n".join(sector_blocks)

    prompt = f"""You are performing post-market analysis for {data['session_date']}. Market has closed for the day.
All data below reflects today's final values.

Turn 1 is the market intelligence layer only — Nifty 50, VIX, FII/DII flows,
sector indices, and Nifty weekly options sentiment. It has NO stock-level scope.
Do not analyse or reference individual stocks, stock option expiries, or stock
futures anywhere in this turn.

Analyse all sections thoroughly.
Think step by step through each data source.
Build a complete market intelligence picture.
Be specific — cite actual numbers in your output.
This picture guides all stock analysis tonight.

════════════════════════════════════════════════════
SECTION 1: NIFTY 50 PRICE ACTION
════════════════════════════════════════════════════

Pre-computed indicators:
  Current price : {price}
  EMA 20        : {ema20}
  EMA 50        : {ema50}
  EMA 180       : {ema180_str}
  5-day return  : {ind['ret5d']}%
  20-day return : {ind['ret20d']}%
  60-day return : {ind['ret60d']}%
  ATR% (14)     : {ind['atr_pct']}%

Pre-computed EMA relationships:
  Price vs EMA20  : {rel['price_vs_ema20']}
  Price vs EMA50  : {rel['price_vs_ema50']}
  Price vs EMA180 : {rel['price_vs_ema180']}
  EMA20 {rel['ema20_vs_ema50']} EMA50 ({rel['ema_arrangement']} short-term arrangement)

Last 120 days OHLCV — read the price action:
Note: Today's volume shows 0 — this is expected
as exchange volume data is finalized after market
close and may not yet be available. Ignore volume
for today's candle only. Use all prior days'
volume for momentum and participation assessment.
{_j(data['nifty']['ohlcv_120d'])}

Perform a full price action analysis. Identify:
  Overall price structure, trend quality and consistency
  All significant swing highs and lows with actual price values
  Consolidation zones and ranges
  Classical chart patterns (flags, pennants, triangles, wedges,
    head and shoulders, double top/bottom, cup and handle, rectangles)
  Candlestick patterns at key levels (engulfing, doji, pin bars,
    morning/evening star, inside bars)
  Volume character: is volume confirming price moves? Any climax
    or dry-up patterns? Flag anomalous volume days (2x+ average volume)
  Key price zones (not just points): support zones (demand areas)
    and resistance zones (supply areas)
  Breakout/breakdown levels: what price confirms bullish structure?
    What price invalidates the current recovery?

════════════════════════════════════════════════════
SECTION 2: INDIA VIX
════════════════════════════════════════════════════

Last 30 days VIX closing values:
{_j(vix['close_30d'])}

Determine from the raw data — do not rely on any pre-computed trend:
  Current level and what it signals for India
  30-day trend: FALLING, RISING, STABLE, or CHOPPY
  Character: gradual move or spike-driven
  Whether current level is at an extreme (low or high) relative
    to the 30-day range shown
  Implication for option pricing tonight

════════════════════════════════════════════════════
SECTION 3: FII / DII INSTITUTIONAL FLOWS
════════════════════════════════════════════════════

Data quality: {dq}{dq_warning}
Last 30 days daily institutional flows (Crores):
{_j(fii['flows_30d'])}

Determine from the raw data — do not rely on any pre-computed aggregates:
  Cumulative FII direction over last 20 days
  Cumulative DII direction over last 20 days
  Whether FII behaviour shifted in last 5 days
  Whether selling/buying is consistent or event-driven
  DII stance — absorbing FII or following same direction
  Meaningful divergence between FII and DII
  Most important observation from the flow data

════════════════════════════════════════════════════
SECTION 4: SECTOR ANALYSIS
════════════════════════════════════════════════════

For each sector you have summary returns and 30 days of price action to read.

vs_nifty_20d guide:
  > +3%       : TAILWIND STRONG
  +1 to +3%   : TAILWIND MODERATE
  -1 to +1%   : NEUTRAL
  -1 to -3%   : HEADWIND MODERATE
  < -3%       : HEADWIND STRONG

For each sector determine:
  Overall trend and momentum character
  Price structure type
  Key price levels from the 30-day data: support (recent swing low
    or demand zone), resistance (recent swing high or supply zone),
    breakout level (price that upgrades sector stance), breakdown
    level (price that downgrades sector stance)
  Volume character of recent moves
  Whether outperforming or underperforming Nifty
  Stance and strength for tonight

{sectors_text}

════════════════════════════════════════════════════
SECTION 5: NIFTY WEEKLY OPTIONS POSITIONING
════════════════════════════════════════════════════

IMPORTANT: These are Nifty INDEX options (weekly expiry). We do NOT trade
Nifty options in this system. Use this section ONLY to read market sentiment
and understand where index option writers are positioned. Do NOT cite these
levels as trading levels for stock setups.

Current weekly expiry : {opt['current_weekly_expiry']}
Next weekly expiry    : {opt['next_weekly_expiry']}
Days to expiry        : {opt['days_to_nifty_expiry']}
Reliability           : {opt['reliability']}
{opt['reliability_note']}

{options_block}

PCR interpretation (contrarian indicator):
  PCR < 0.7   -> excessive bullishness (contrarian bearish)
  PCR 0.7-1.1 -> neutral positioning
  PCR 1.1-1.3 -> mild protective hedging
  PCR > 1.3   -> excessive bearishness (contrarian bullish)

════════════════════════════════════════════════════
REQUIRED OUTPUT
════════════════════════════════════════════════════

SECTOR PICTURE INSTRUCTIONS:
Provide a complete assessment for ALL 11 sectors.
No shortcuts. No placeholders. No ellipsis.
Every sector must have all 9 fields completed.

Required sectors (all 11 mandatory):
  BANKING, IT, AUTO, PHARMA, FMCG,
  METAL, ENERGY, FINSERV, INFRA, CONSUMER, MEDIA

Field value options (9 fields per sector):
  trend       : UPTREND | DOWNTREND | SIDEWAYS
  momentum    : ACCELERATING | DECELERATING | STABLE
  stance      : TAILWIND | NEUTRAL | HEADWIND
  strength    : STRONG | MODERATE | WEAK
  structure   : BREAKOUT | BREAKDOWN | RANGE | UPTREND_PULLBACK | DOWNTREND_RALLY
  key_levels  : object with 4 price numbers —
                support, resistance, breakout_above, breakdown_below
  momentum_note: 1 sentence on price momentum
                 character from the OHLCV data.
                 Since sector indices have no volume,
                 assess momentum from: pace of price
                 moves, consistency of direction,
                 size of pullbacks vs advances,
                 and candle character (strong closes
                 vs weak closes, gap behaviour).
                 e.g. 'Steady acceleration visible —
                 each week closing higher with
                 controlled pullback ranges suggesting
                 institutional accumulation pace'
  character   : 1 sentence on price action citing actual price levels seen
                e.g. "Broke out from 54000 to 58177 with consistent higher highs"
  trading_note: 1 sentence on stock selection implication for tonight
                e.g. "Favour LONG setups — strong sector tailwind from
                      Banking outperformance"

Note: there is no vs_nifty enum field — stance + strength already covers
relative performance, and vs_nifty_20d is given numerically in the returns
data above.

Produce the market intelligence JSON below.
Every field is consumed by downstream stock analysis.
Null only where explicitly marked nullable.
No text outside the JSON. No markdown fences.

{{
  "session_narrative": "3-4 sentences: (1) Nifty trend and price structure, (2) VIX level and direction you read, (3) Institutional flow character, (4) Implication for tonight. Cite actual numbers.",

  "market_trend": "BULLISH | BEARISH | SIDEWAYS",
  // BULLISH  : price > ema20 > ema50
  //            AND price > ema180
  //            All three timeframes aligned bullish
  //
  // BEARISH  : price < ema20 < ema50
  //            Short and medium-term trend broken
  //
  // SIDEWAYS : Any other arrangement, including:
  //            price above ema20/ema50 but below ema180
  //            ema20 < ema50 but price above both
  //            price between ema20 and ema50
  //            Conflicting signals across timeframes
  //
  // Tonight's EMA values (pre-computed):
  //   Price  : {price}
  //   EMA20  : {ema20} (price {rel['price_vs_ema20']})
  //   EMA50  : {ema50} (price {rel['price_vs_ema50']})
  //   EMA180 : {ema180} (price {rel['price_vs_ema180']})
  //   EMA20 {rel['ema20_vs_ema50']} EMA50
  //         ({rel['ema_arrangement']} short-term arrangement)

  "market_volatility": "LOW | NORMAL | HIGH",

  "market_structure": "TIGHT | WIDE | STRETCHED",

  "execution_bias": "FAVOUR_LONGS | FAVOUR_SHORTS | BOTH | CAUTIOUS | NEUTRAL",

  "fii_dii_stance": "BULLISH | BEARISH | NEUTRAL",

  "session_risk_level": "LOW | MEDIUM | HIGH | EXTREME",

  "conviction_multiplier": 0.0,
  // Output a single float value between 0.70 and 1.10
  // Do not output a range — one number only
  // Guidelines for your judgment:
  //   Clear bull trend + LOW VIX + FII buying : 1.05-1.10
  //   Recovering trend + NORMAL VIX + mixed   : 1.00-1.05
  //   Sideways + NORMAL VIX + neutral flows   : 0.95-1.00
  //   High VIX or conflicting signals          : 0.80-0.90
  //   EXTREME conditions                       : 0.70-0.75
  // Example output: 0.95

  "vix_assessment": {{
    "current": float,
    "trend": "FALLING | RISING | STABLE | CHOPPY",
    "character": "1 sentence on nature of VIX move",
    "options_implication": "1 sentence on what this means for option buyers"
  }},

  "fii_dii_assessment": {{
    "fii_20d_character": "1 sentence on FII behaviour over 20 days",
    "recent_shift": "YES | NO",
    "shift_description": "string or null",
    "dii_stance_description": "1 sentence on DII behaviour",
    "divergence": "YES | NO",
    "key_insight": "Most important observation from flow data"
  }},

  "sector_pictures": {{
    "BANKING": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "momentum_note": "", "character": "", "trading_note": "" }},
    "IT": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "momentum_note": "", "character": "", "trading_note": "" }},
    "AUTO": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "momentum_note": "", "character": "", "trading_note": "" }},
    "PHARMA": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "momentum_note": "", "character": "", "trading_note": "" }},
    "FMCG": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "momentum_note": "", "character": "", "trading_note": "" }},
    "METAL": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "momentum_note": "", "character": "", "trading_note": "" }},
    "ENERGY": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "momentum_note": "", "character": "", "trading_note": "" }},
    "FINSERV": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "momentum_note": "", "character": "", "trading_note": "" }},
    "INFRA": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "momentum_note": "", "character": "", "trading_note": "" }},
    "CONSUMER": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "momentum_note": "", "character": "", "trading_note": "" }},
    "MEDIA": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "momentum_note": "", "character": "", "trading_note": "" }}
  }},

  "directional_filters": {{
    "avoid_longs_in": ["sectors with HEADWIND stance"],
    "avoid_shorts_in": ["sectors with TAILWIND stance"],
    "caution_sectors": ["sectors with conflicting signals"]
  }},

  "prescan_guidance": {{
    "max_stocks_to_forward": integer,
    // Base from session_risk_level: LOW=15, MEDIUM=12, HIGH=8, EXTREME=5
    // Hard minimum: never below 3
    "prefer_directions": [...],
    "prioritise_sectors": [...],
    "deprioritise_sectors": [...],
    "special_instructions": string or null,
    "expiry_note": string or null
  }},

  "nifty_price_structure": {{
    "overall_structure": "UPTREND | DOWNTREND | RECOVERY | DISTRIBUTION | RANGE | BREAKDOWN",
    // UPTREND: consistent higher highs/lows, above key EMAs
    // DOWNTREND: consistent lower highs/lows, below key EMAs
    // RECOVERY: recovering from lows but not yet confirmed uptrend
    // DISTRIBUTION: topping pattern, losing momentum at highs
    // RANGE: defined support and resistance, no directional bias
    // BREAKDOWN: breaking down from range or prior support

    "trend_quality": "STRONG | MODERATE | WEAK | CONFLICTING",

    "swing_points": {{
      "major_high":  {{ "price": 0, "date": "" }},
      "major_low":   {{ "price": 0, "date": "" }},
      "recent_high": {{ "price": 0, "date": "" }},
      "recent_low":  {{ "price": 0, "date": "" }}
    }},

    "key_price_zones": [
      // 2 to 5 zones. Price-derived only (not OI-derived — those live in index_key_levels).
      {{ "type": "SUPPORT | RESISTANCE", "zone_low": 0, "zone_high": 0,
        "significance": "MAJOR | MINOR", "note": "why this zone matters" }}
    ],

    "pattern_identified": {{
      "classical_pattern": {{
        "name": "pattern name or NONE",
        "completion": "COMPLETE | FORMING | FAILED",
        "target": 0,
        "note": "1 sentence explanation"
      }},
      "candlestick_pattern": {{
        "name": "pattern name or NONE",
        "location": "AT_SUPPORT | AT_RESISTANCE | MID_RANGE",
        "significance": "HIGH | MEDIUM | LOW",
        "note": "1 sentence explanation"
      }},
      "volume_pattern": {{
        "name": "CLIMAX | DRY_UP | CONFIRMATION | DIVERGENCE | ANOMALY | NONE",
        "note": "1 sentence on what volume is telling us about the price move"
      }}
    }},

    "ema_structure": {{
      "short_term": "price vs EMA20 relationship",
      "medium_term": "price vs EMA50 relationship",
      "long_term": "price vs EMA180 relationship",
      "arrangement": "BULLISH | BEARISH | MIXED",
      "arrangement_note": "1 sentence on what the EMA arrangement means for trend direction"
    }},

    "breakout_conditions": {{
      "bull_breakout_above": 0,
      "bull_breakout_note": "what happens if price crosses this level with volume",
      "bear_breakdown_below": 0,
      "bear_breakdown_note": "what happens if price breaks this level"
    }},

    "volume_analysis": {{
      "recent_trend": "INCREASING | DECREASING | STABLE",
      "anomaly_days": ["dates with anomalous volume and a brief note on each"],
      "confirmation": "YES | NO | MIXED",
      "note": "1 sentence on overall volume character"
    }},

    "price_narrative": "2-3 sentences describing the complete price structure story from the data. Must cite actual prices and dates.",

    "trading_implication": {{
      "summary": "2-3 sentences: is Nifty structure helping or hurting stock long setups tonight? What condition would change this view? What does this mean for conviction scoring? Must be actionable, not generic.",
      "index_bias": "SUPPORTIVE | NEUTRAL | RESISTANT",
      // SUPPORTIVE: price above key EMAs, clear uptrend, at support, pattern bullish
      // NEUTRAL: sideways, mixed signals, mid-range
      // RESISTANT: at resistance, below EMA180, weak pattern, distribution signals
      "conviction_adjustment": "ADD_2 | NEUTRAL | SUBTRACT_2 | SUBTRACT_5",
      // Applied to Layer 3 (Index F&O Context) score in deep analysis turns
      // ADD_2: SUPPORTIVE + at support + volume confirmation
      // NEUTRAL: NEUTRAL or mixed signals
      // SUBTRACT_2: RESISTANT + at resistance + weak volume
      // SUBTRACT_5: RESISTANT + confirmed downtrend + breakdown pattern forming
      "key_condition_to_watch": "Single most important price level or event that would change the view tonight, e.g. 'Break above 24261 with volume > 500M would upgrade index_bias to SUPPORTIVE and remove the SUBTRACT_2 adjustment'"
    }}
  }},

  "index_key_levels": {{
    "strong_support": integer,
    "support": integer,
    "current": integer,
    "resistance": integer,
    "strong_resistance": integer,
    "max_pain": integer or null,
    "pcr_signal": "BULLISH | BEARISH | NEUTRAL",
    "levels_note": "1 sentence on key level implication tonight"
  }},
  // These are OI-derived levels (from Section 5). nifty_price_structure
  // above holds the price-derived levels. Note in levels_note when the
  // two confluence or diverge.

  "risk_flags": [],
  // Required: 2 to 4 items.
  // Rule 1: If Nifty weekly options reliability is UNRELIABLE or DISTORTED,
  //         first item MUST be the expiry warning, e.g.:
  //         "Nifty weekly expiry today/tomorrow ({opt['days_to_nifty_expiry']}d) —
  //          OI positioning unreliable for forward sentiment."
  // Rule 2: All other flags must cite actual numbers.
  //         GOOD: "VIX at 13.05 near 30-day low of 12.67 — complacency risk if event triggers"
  //         BAD:  "Market is volatile"
  // Rule 3: Minimum 2 flags always required.

  "guidance": {{
    "favour": "1-2 sentences on best setups tonight",
    "caution": "1-2 sentences on what to avoid tonight"
  }},

  "mentor_notes": {{
    "todays_key_lesson": string,
    // The single most important market reading
    // lesson from tonight's data for the user
    // to internalise. Must be specific to tonight
    // not generic market wisdom.
    // Cite actual numbers from tonight's session.

    "what_i_looked_at_first": string,
    // Which data source gave the clearest signal
    // tonight and exactly what it showed you.
    // Helps user learn analytical prioritisation.

    "pattern_to_watch": string,
    // One specific price pattern or signal to
    // track over the next 2-5 sessions.
    // Must include specific price levels and
    // volume thresholds where relevant.
    // Teaches sequential thinking not snapshots.

    "sector_rotation_insight": string,
    // What tonight's sector relative performance
    // reveals about broader market character.
    // Must cite actual sector return numbers.
    // Helps user read rotation as market signal.

    "fii_dii_reading": string
    // How to interpret tonight's institutional
    // flow data and what it implies going forward.
    // Must cite actual flow numbers.
    // Teaches institutional flow analysis.
  }}
}}"""

    return prompt


def _run_turn1(
    client: anthropic.Anthropic,
    session_id: str,
    session_date: date,
    config: dict,
) -> tuple[dict, dict]:
    """
    Runs Turn 1 complete: builds data, builds prompt, calls Claude,
    parses response, saves to DB, sends Telegram notification.
    Returns (turn1_result, cost_info).
    """
    from new_notifications.telegram import send_loud, send_silent

    # Crash recovery: if this session's Turn 1 already completed, reuse it
    existing = get_claude_turn(session_id, 1)
    if existing and existing.get("output_text"):
        logger.info("Turn 1: found existing turn in session_claude_turns — skipping Claude call")
        try:
            result = _parse_json(existing["output_text"])
        except Exception as exc:
            logger.warning("Turn 1 recovery parse failed: %s — re-running", exc)
            result = None
        if result:
            in_tok  = existing.get("input_tokens", 0)
            out_tok = existing.get("output_tokens", 0)
            return result, _turn_cost(1, "market_context", None, in_tok, out_tok)

    data   = _build_turn1_data(session_date)
    prompt = _build_turn1_prompt(data)
    messages = [{"role": "user", "content": prompt}]

    logger.info("Turn 1: calling Claude (max_tokens=15000)...")

    try:
        response = _call_claude(client, _TURN1_SYSTEM, messages, max_tokens=15000)
    except Exception as exc:
        logger.critical("Turn 1 Claude API failed: %s", exc)
        try:
            send_loud("❌ Turn 1 failed — pipeline cannot continue")
        except Exception:
            pass
        raise

    out_text = response.content[0].text
    print("CLAUDE OUTPUT: ", out_text)
    u1 = response.usage
    logger.info(
        "Turn 1 done: in=%d out=%d cache_create=%s cache_read=%s",
        u1.input_tokens, u1.output_tokens,
        getattr(u1, "cache_creation_input_tokens", "-"),
        getattr(u1, "cache_read_input_tokens", "-"),
    )

    # Parse response
    try:
        result = _parse_json(out_text)
        if not isinstance(result, dict):
            raise ValueError(f"Expected JSON object, got {type(result).__name__}")
    except Exception as exc:
        logger.error("Turn 1 JSON parse failed: %s | raw=%s", exc, out_text[:400])
        result = dict(_TURN1_DEFAULTS)

    # Validate required top-level keys — fill defaults for any missing
    missing = [k for k in _TURN1_REQUIRED_KEYS if k not in result]
    if missing:
        logger.error("Turn 1 response missing keys: %s — applying defaults", missing)
        for k in missing:
            result[k] = _TURN1_DEFAULTS.get(k)

    # Clamp conviction_multiplier
    cm = result.get("conviction_multiplier", 0.95)
    try:
        cm = float(cm)
    except (TypeError, ValueError):
        cm = 0.95
    if not (0.70 <= cm <= 1.10):
        clamped = max(0.70, min(1.10, cm))
        logger.warning("conviction_multiplier %s out of range — clamped to %s", cm, clamped)
        cm = clamped
    result["conviction_multiplier"] = cm

    # Validate sector_pictures has all 11 sectors
    sector_pics = result.get("sector_pictures") or {}
    missing_sectors = [s for s in _TURN1_REQUIRED_SECTORS if s not in sector_pics]
    if missing_sectors:
        logger.warning("Turn 1 sector_pictures missing sectors: %s — applying defaults", missing_sectors)
        for s in missing_sectors:
            sector_pics[s] = dict(_TURN1_DEFAULT_SECTOR_PICTURE)
        result["sector_pictures"] = sector_pics

    # Validate nifty_price_structure
    nps = result.get("nifty_price_structure") or {}
    missing_nps = [k for k in _TURN1_REQUIRED_PRICE_STRUCTURE_KEYS if k not in nps]
    if missing_nps:
        logger.warning("Turn 1 nifty_price_structure missing keys: %s — applying defaults", missing_nps)
        defaults_nps = _TURN1_DEFAULTS["nifty_price_structure"]
        for k in missing_nps:
            nps[k] = defaults_nps.get(k)

    trading_impl = nps.get("trading_implication") or {}
    missing_ti = [k for k in _TURN1_REQUIRED_TRADING_IMPLICATION_KEYS if k not in trading_impl]
    if missing_ti:
        logger.warning("Turn 1 trading_implication missing keys: %s — applying defaults", missing_ti)
        defaults_ti = _TURN1_DEFAULTS["nifty_price_structure"]["trading_implication"]
        for k in missing_ti:
            trading_impl[k] = defaults_ti.get(k)
        nps["trading_implication"] = trading_impl
    result["nifty_price_structure"] = nps

    # Save to session_claude_turns
    save_claude_turn(
        session_id=session_id,
        turn_number=1,
        turn_type="market_context",
        symbol=None,
        input_tokens=u1.input_tokens,
        output_tokens=u1.output_tokens,
        input_text=prompt,
        output_text=out_text,
    )

    # Update analysis_sessions
    vix_current = (result.get("vix_assessment") or {}).get("current")
    update_analysis_session(session_id, {
        "market_trend":          result.get("market_trend"),
        "market_volatility":     result.get("market_volatility"),
        "market_structure":      result.get("market_structure"),
        "execution_bias":        result.get("execution_bias"),
        "fii_dii_stance":        result.get("fii_dii_stance"),
        "session_risk_level":    result.get("session_risk_level"),
        "conviction_multiplier": result.get("conviction_multiplier"),
        "nifty_close":           data["nifty"]["indicators"]["current_price"],
        "vix_close":             vix_current,
        "stage_statuses":        {"turn1": "COMPLETE"},
        "fii_net_flow_cr":      data["fii_dii"]["fii_net_cr"],
    })

    # Telegram silent notification for Turn 1 Context Complete
    try:
        from new_notifications.telegram import send_phase1_complete
        turn1_cost = round(u1.input_tokens / 1_000_000 * 3.00 + u1.output_tokens / 1_000_000 * 15.00, 6)
        send_phase1_complete(
            trade_date=str(session_date),
            market_trend=result.get("market_trend", ""),
            market_volatility=result.get("market_volatility", ""),
            market_structure=result.get("market_structure", ""),
            execution_bias=result.get("execution_bias", ""),
            nifty_close=data["nifty"]["indicators"]["current_price"],
            vix=vix_current,
            cost_usd=turn1_cost,
            mentor_notes={},
            #mentor_notes=result.get("mentor_notes", {}),
        )
    except Exception as exc:
        logger.warning("Turn 1 Telegram notification failed: %s", exc)

    cost_info = _turn_cost(1, "market_context", None, u1.input_tokens, u1.output_tokens)
    return result, cost_info


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
        "turn":         "pre_scan",
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


_SECTOR_MAP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "config", "sector_map.json"
)

def _load_sector_map() -> dict:
    try:
        with open(_SECTOR_MAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Could not load sector map from %s: %s", _SECTOR_MAP_PATH, exc)
        return {"stocks": {}}

def _get_sector(symbol: str, sector_map: dict) -> str:
    entry = sector_map.get("stocks", {}).get(symbol, {})
    raw_sector = entry.get("sector", "OTHER")
    SECTOR_MAPPING = {
        "BANKING": "BANKING",
        "IT": "IT",
        "AUTO": "AUTO",
        "PHARMA": "PHARMA",
        "FMCG": "FMCG",
        "METALS": "METAL",
        "METAL": "METAL",
        "ENERGY": "ENERGY",
        "FINSERV": "FINSERV",
        "INFRA": "INFRA",
        "CONSUMER": "CONSUMER",
        "MEDIA": "MEDIA"
    }
    return SECTOR_MAPPING.get(raw_sector, "OTHER")

def _build_turn2_data(
    session_date: date,
    turn1_result: dict,
    mandatory_stocks: list[str],
) -> dict:
    """
    Step 1: Build base universe, unioning with caller-supplied mandatory stocks.
    Step 2: Programmatically pre-filter non-mandatory stocks based on stale data & volume.
    Step 3: Build per-stock lightweight data fields.
    Step 4: Return dictionary structure for prompt builder.
    """
    from new_utils.stock_list import get_stock_list_for_analysis

    # Step 1: Base Universe
    base_stocks = get_stock_list_for_analysis() # dict keyed by symbol
    base_symbols = set(base_stocks.keys())
    
    derived_mandatory = [sym for sym, info in base_stocks.items() if info.get("mandate") is True]
    mandatory_set = set(derived_mandatory) | set(mandatory_stocks or [])
    full_universe = base_symbols | mandatory_set

    extra_mandatory = mandatory_set - base_symbols
    logger.info(
        "Turn 2 Universe: base count = %d, mandatory count = %d, union count = %d",
        len(base_symbols), len(mandatory_set), len(full_universe)
    )
    if extra_mandatory:
        logger.info("Extra mandatory stocks being tracked: %s", sorted(extra_mandatory))

    # Step 2: Programmatic Pre-Filter
    filtered_universe = []
    excluded_stocks = [] # list of {"symbol": str, "reason": str}
    sess_date_str = session_date.strftime("%Y-%m-%d") if hasattr(session_date, "strftime") else str(session_date)
    sector_map = _load_sector_map()

    # Pre-fetch and filter symbols
    for symbol in sorted(full_universe):
        is_mandatory = symbol in mandatory_set
        rows = get_price_history(symbol, days=80)

        if not rows:
            reason = "No price history available"
            if is_mandatory:
                logger.warning("Mandatory stock %s has no price history, keeping with partial data", symbol)
                filtered_universe.append(symbol)
            else:
                logger.info("Excluding %s: %s", symbol, reason)
                excluded_stocks.append({"symbol": symbol, "reason": reason})
            continue

        # Find row for session_date
        today_row_idx = None
        for idx, r in enumerate(rows):
            if str(r.get("date")) == sess_date_str:
                today_row_idx = idx
                break

        if today_row_idx is None:
            reason = f"No price_history row for session_date ({sess_date_str})"
            if is_mandatory:
                logger.warning("Mandatory stock %s: %s, keeping with partial data", symbol, reason)
                filtered_universe.append(symbol)
            else:
                logger.info("Excluding %s: %s", symbol, reason)
                excluded_stocks.append({"symbol": symbol, "reason": reason})
            continue

        today_row = rows[today_row_idx]
        today_vol = today_row.get("volume")

        # Check volume ratio and 20-day average volume
        prev_rows = rows[:today_row_idx]
        prev_vols = [float(r["volume"]) for r in prev_rows[-20:] if r.get("volume") is not None]

        if len(prev_vols) < 20:
            reason = f"Insufficient price history before session_date (only {len(prev_vols)} days of volume data)"
            if is_mandatory:
                logger.warning("Mandatory stock %s: %s, keeping with partial data", symbol, reason)
                filtered_universe.append(symbol)
            else:
                logger.info("Excluding %s: %s", symbol, reason)
                excluded_stocks.append({"symbol": symbol, "reason": reason})
            continue

        avg_vol_20d = sum(prev_vols) / len(prev_vols)

        if today_vol is None:
            reason = "Today's volume is missing/null"
            if is_mandatory:
                logger.warning("Mandatory stock %s: %s, keeping with partial data", symbol, reason)
                filtered_universe.append(symbol)
            else:
                logger.info("Excluding %s: %s", symbol, reason)
                excluded_stocks.append({"symbol": symbol, "reason": reason})
            continue

        today_vol_val = float(today_vol)
        vol_ratio_val = today_vol_val / avg_vol_20d if avg_vol_20d > 0 else 0.0

        if vol_ratio_val < 0.5:
            reason = f"Abnormally low volume check failed (today's volume {today_vol_val} < 50% of 20-day average volume {avg_vol_20d:.1f}, ratio = {vol_ratio_val:.2f})"
            if is_mandatory:
                logger.warning("Mandatory stock %s would be filtered because: %s, but keeping it", symbol, reason)
                filtered_universe.append(symbol)
            else:
                logger.info("Excluding %s: %s", symbol, reason)
                excluded_stocks.append({"symbol": symbol, "reason": reason})
            continue

        # Passes all filters
        filtered_universe.append(symbol)

    # Step 3 & 4: Build per-stock lightweight data
    stocks_data = []
    for symbol in filtered_universe:
        is_mandatory = symbol in mandatory_set
        
        # Initialize fields
        sector = _get_sector(symbol, sector_map)
        last_close = None
        ret_5d = None
        ret_20d = None
        ema_position = None
        volume_ratio_20d = None
        pcr = None

        try:
            rows = get_price_history(symbol, days=80)
            today_row_idx = None
            for idx, r in enumerate(rows):
                if str(r.get("date")) == sess_date_str:
                    today_row_idx = idx
                    break

            if today_row_idx is not None:
                today_row = rows[today_row_idx]
                last_close = round(float(today_row["close"]), 2) if today_row.get("close") is not None else None

                # Return % calculations
                if today_row_idx >= 5:
                    p_5d = float(rows[today_row_idx - 5]["close"])
                    if p_5d > 0 and last_close is not None:
                        ret_5d = round(((last_close / p_5d) - 1.0) * 100, 2)
                
                if today_row_idx >= 20:
                    p_20d = float(rows[today_row_idx - 20]["close"])
                    if p_20d > 0 and last_close is not None:
                        ret_20d = round(((last_close / p_20d) - 1.0) * 100, 2)

                # EMA calculations
                closes_slice = pd.Series([float(r["close"]) for r in rows[:today_row_idx + 1] if r.get("close") is not None])
                ema20_val = None
                ema50_val = None
                
                if len(closes_slice) >= 20:
                    ema20_series = calculate_ema(closes_slice, 20)
                    if not ema20_series.empty:
                        ema20_val = float(ema20_series.iloc[-1])
                
                if len(closes_slice) >= 50:
                    ema50_series = calculate_ema(closes_slice, 50)
                    if not ema50_series.empty:
                        ema50_val = float(ema50_series.iloc[-1])
                elif len(closes_slice) >= 20:
                    # Default ema50 to ema20 as fallback
                    ema50_val = ema20_val

                if last_close is not None and ema20_val is not None and ema50_val is not None:
                    if last_close > ema20_val and last_close > ema50_val:
                        ema_position = "ABOVE_BOTH"
                    elif last_close < ema20_val and last_close < ema50_val:
                        ema_position = "BELOW_BOTH"
                    else:
                        ema_position = "MIXED"
                else:
                    ema_position = "MIXED"

                # Volume ratio
                prev_rows = rows[:today_row_idx]
                prev_vols = [float(r["volume"]) for r in prev_rows[-20:] if r.get("volume") is not None]
                if prev_vols:
                    avg_vol_20d = sum(prev_vols) / len(prev_vols)
                    today_vol = today_row.get("volume")
                    if today_vol is not None and avg_vol_20d > 0:
                        volume_ratio_20d = round(float(today_vol) / avg_vol_20d, 2)

                # PCR
                oi_rows = get_continuous_oi(symbol, days=10)
                oi_row_tonight = None
                for r in oi_rows:
                    if str(r.get("date")) == sess_date_str:
                        oi_row_tonight = r
                        break

                pcr_fallback = None
                near_expiry_str = None
                if oi_row_tonight:
                    pcr_fallback = oi_row_tonight.get("pcr_near")
                    near_expiry_str = oi_row_tonight.get("near_expiry")

                if near_expiry_str:
                    try:
                        near_exp_date = date.fromisoformat(near_expiry_str)
                        opt_rows = get_options_snapshot(symbol, session_date, near_exp_date)
                        if opt_rows:
                            near_ce = 0
                            near_pe = 0
                            for r in opt_rows:
                                oi = int(r.get("oi") or 0)
                                if r.get("option_type") == "CE":
                                    near_ce += oi
                                elif r.get("option_type") == "PE":
                                    near_pe += oi
                            if near_ce > 0:
                                pcr = round(near_pe / near_ce, 2)
                    except Exception as exc:
                        logger.warning("Error computing PCR from option snapshot for %s: %s", symbol, exc)

                if pcr is None and pcr_fallback is not None:
                    pcr = round(float(pcr_fallback), 2)

        except Exception as exc:
            msg = f"Failed calculation for {symbol}: {exc}"
            if is_mandatory:
                logger.warning("%s (mandatory, continuing with partial data)", msg)
            else:
                logger.warning("%s", msg)

        stocks_data.append({
            "symbol": symbol,
            "sector": sector,
            "last_close": last_close,
            "ret_5d": ret_5d,
            "ret_20d": ret_20d,
            "ema_position": ema_position,
            "volume_ratio_20d": volume_ratio_20d,
            "pcr": pcr,
        })

    # Extracted Turn 1 context
    t1_ctx = {}
    for field in ("market_trend", "market_volatility", "execution_bias", "session_risk_level", "conviction_multiplier"):
        t1_ctx[field] = turn1_result.get(field)

    nps = turn1_result.get("nifty_price_structure") or {}
    ti = nps.get("trading_implication") or {}
    t1_ctx["nifty_trading_implication"] = {
        "summary": ti.get("summary"),
        "index_bias": ti.get("index_bias"),
        "conviction_adjustment": ti.get("conviction_adjustment"),
        "key_condition_to_watch": ti.get("key_condition_to_watch"),
    }

    sec_pics = turn1_result.get("sector_pictures") or {}
    t1_ctx["sector_pictures"] = {}
    for sec_name, pic in sec_pics.items():
        t1_ctx["sector_pictures"][sec_name] = {
            "stance": pic.get("stance"),
            "strength": pic.get("strength"),
            "structure": pic.get("structure"),
            "trading_note": pic.get("trading_note")
        }

    df_filters = turn1_result.get("directional_filters") or {}
    t1_ctx["directional_filters"] = {
        "avoid_longs_in": df_filters.get("avoid_longs_in"),
        "avoid_shorts_in": df_filters.get("avoid_shorts_in"),
        "caution_sectors": df_filters.get("caution_sectors"),
    }

    pg = turn1_result.get("prescan_guidance") or {}
    t1_ctx["prescan_guidance"] = {
        "max_stocks_to_forward": pg.get("max_stocks_to_forward"),
        "prefer_directions": pg.get("prefer_directions"),
        "prioritise_sectors": pg.get("prioritise_sectors"),
        "deprioritise_sectors": pg.get("deprioritise_sectors"),
        "special_instructions": pg.get("special_instructions"),
        "expiry_note": pg.get("expiry_note"),
    }

    # Logging summary and data quality warnings
    total_sent = len(stocks_data)
    mand_sent = sum(1 for s in stocks_data if s["symbol"] in mandatory_set)
    excl_count = len(excluded_stocks)
    
    warnings_found = []
    for s in stocks_data:
        missing_fields = [k for k, v in s.items() if v is None and k != "pcr"]
        if missing_fields:
            warnings_found.append(f"{s['symbol']} missing: {missing_fields}")

    logger.info("Turn 2 data build complete: total sent to Claude = %d, mandatory count within sent = %d, excluded count = %d", 
                total_sent, mand_sent, excl_count)
    if warnings_found:
        logger.warning("Data quality warnings: %s", "; ".join(warnings_found))

    return {
        "session_date": sess_date_str,
        "stocks": stocks_data,
        "mandatory_stocks": list(mandatory_stocks or []),
        "excluded_stocks": excluded_stocks,
        "turn1_context": t1_ctx,
    }

def _build_turn2_prompt(turn2_data: dict) -> str:
    ctx = turn2_data["turn1_context"]
    
    sections = []
    sections.append("TONIGHT'S MARKET CONTEXT")
    sections.append("========================")
    sections.append(f"Market Trend: {ctx.get('market_trend')}")
    sections.append(f"Market Volatility: {ctx.get('market_volatility')}")
    sections.append(f"Execution Bias: {ctx.get('execution_bias')}")
    sections.append(f"Session Risk Level: {ctx.get('session_risk_level')}")
    sections.append(f"Conviction Multiplier: {ctx.get('conviction_multiplier')}")
    sections.append("")
    
    # Nifty Trading Implication
    ti = ctx.get("nifty_trading_implication") or {}
    sections.append("Nifty Price Structure & Trading Implication:")
    sections.append(f"- Summary: {ti.get('summary')}")
    sections.append(f"- Index Bias: {ti.get('index_bias')}")
    sections.append(f"- Conviction Adjustment: {ti.get('conviction_adjustment')}")
    sections.append(f"- Key Condition to Watch: {ti.get('key_condition_to_watch')}")
    sections.append("")
    
    # Sector Pictures
    sections.append("Sector Outlooks:")
    for sec_name, pic in (ctx.get("sector_pictures") or {}).items():
        sections.append(f"- {sec_name}: Stance={pic.get('stance')}, Strength={pic.get('strength')}, Structure={pic.get('structure')}")
        sections.append(f"  Note: {pic.get('trading_note')}")
    sections.append("")
    
    # Directional Filters
    df_filters = ctx.get("directional_filters") or {}
    sections.append("Directional Filters:")
    sections.append(f"- Avoid LONGs in sectors: {', '.join(df_filters.get('avoid_longs_in') or []) or 'None'}")
    sections.append(f"- Avoid SHORTs in sectors: {', '.join(df_filters.get('avoid_shorts_in') or []) or 'None'}")
    sections.append(f"- Caution Sectors: {', '.join(df_filters.get('caution_sectors') or []) or 'None'}")
    sections.append("")
    
    # Prescan Guidance
    pg = ctx.get("prescan_guidance") or {}
    sections.append("Pre-Scan Guidance:")
    sections.append(f"- Max stocks to forward: {pg.get('max_stocks_to_forward')}")
    sections.append(f"- Preferred directions: {', '.join(pg.get('prefer_directions') or []) or 'None'}")
    sections.append(
        "  Note on preferred directions: this indicates tonight's macro LEAN based on index and "
        "sector conditions — it is NOT a hard rule that suppresses the opposite direction. If a "
        "stock shows genuinely strong SHORT characteristics (confirmed downtrend, volume confirming "
        "the decline, weak/deteriorating structure) and is NOT in an avoid_shorts_in sector, you "
        "should forward it as a SHORT candidate even though tonight's preference is LONG. Do not "
        "reject a strong SHORT setup merely because it lacks a 'sector tailwind' — a HEADWIND sector "
        "is itself the tailwind for a SHORT trade. Apply the same conviction bar to SHORT setups as "
        "you do to LONG setups: strong individual data + permitted sector = forward it, regardless "
        "of tonight's directional lean."
    )
    sections.append(f"- Prioritise sectors: {', '.join(pg.get('prioritise_sectors') or []) or 'None'}")
    sections.append(f"- Deprioritise sectors: {', '.join(pg.get('deprioritise_sectors') or []) or 'None'}")
    sections.append(f"- Special instructions: {pg.get('special_instructions')}")
    sections.append(f"- Expiry note: {pg.get('expiry_note')}")
    sections.append("")

    sections.append("STOCK UNIVERSE TO ASSESS")
    sections.append("========================")
    sections.append("Below is the list of stocks in the F&O universe to assess today, formatted as a JSON array.")
    sections.append("Field Interpretation Guide:")
    sections.append("- ema_position: ABOVE_BOTH means close price is above both EMA20 and EMA50. BELOW_BOTH means below both. MIXED means between them or conflicting.")
    sections.append("- volume_ratio_20d: Today's volume divided by the 20-day average volume (excluding today). Values > 1.2 suggest high volume participation; values < 0.8 suggest low participation.")
    sections.append("- pcr: Put-Call Ratio for the near-month option expiry. Low PCR (< 0.7) indicates excessive bullishness (contrarian bearish). High PCR (> 1.3) indicates excessive bearishness (contrarian bullish). PCR 0.7-1.1 is neutral.")
    sections.append("")
    
    stocks_json = json.dumps(turn2_data["stocks"], ensure_ascii=False)
    sections.append(stocks_json)
    sections.append("")

    instructions = (
        "TASK INSTRUCTIONS\n"
        "=================\n"
        "For EACH stock in the list above, assess it independently using tonight's market context.\n"
        "Do not skip any stock. Do not group stocks — provide one assessment object per stock.\n\n"
        "For each stock determine:\n\n"
        "1. preliminary_direction: LONG or SHORT\n"
        "   Base this on:\n"
        "   - Sector stance and strength from tonight's context: TAILWIND sectors favour LONG, "
        "HEADWIND sectors favour SHORT. Treat these symmetrically — a HEADWIND sector is genuine "
        "supporting evidence FOR a short setup, not a reason to avoid forwarding shorts. Do not let "
        "tonight's prefer_directions guidance from Turn 1 suppress a well-supported SHORT call when "
        "the sector and stock data both point that way and the sector is not in avoid_shorts_in.\n"
        "   - EMA position (ABOVE_BOTH supports LONG, BELOW_BOTH supports SHORT, MIXED needs sector context to break the tie)\n"
        "   - Volume ratio (>1.2 suggests conviction in the current move; <0.8 suggests weak participation)\n"
        "   - PCR if available (low PCR = bullish positioning, high PCR = bearish positioning, per the interpretation guide used in Turn 1)\n"
        "   Respect directional_filters — do not assign LONG to a stock in avoid_longs_in sectors, do not assign SHORT to a stock in avoid_shorts_in sectors unless the stock's own data strongly contradicts its sector.\n\n"
        "2. reason: ONE sentence citing the SPECIFIC signal(s) that drove this direction. Must cite actual numbers from the stock's data or sector context. No generic statements.\n"
        "   - GOOD: 'Banking TAILWIND STRONG sector, price above EMA20/50, volume 1.3x 20-day average confirming participation'\n"
        "   - BAD: 'Stock looks bullish'\n\n"
        "3. claude_forward_decision: FORWARD or REJECT\n"
        "   - FORWARD if the stock represents a genuinely promising setup worth deep analysis tonight.\n"
        "   - REJECT if the setup is weak, contradicts sector context, has poor volume confirmation, or sits in a deprioritised/caution sector without strong individual override signals.\n\n"
        "   You may FORWARD more or fewer stocks than max_stocks_to_forward — that cap is applied separately after your response. Use your honest judgment per stock, not a target count."
    )
    sections.append(instructions)
    sections.append("")

    output_format = (
        "OUTPUT FORMAT INSTRUCTIONS\n"
        "==========================\n"
        "Produce the JSON below. Assess EVERY stock from the input list — the output array must have exactly the same number of entries as stocks provided. No omissions. No placeholders. No text outside the JSON. No markdown fences.\n\n"
        "Required Output Schema:\n"
        "{\n"
        '  "stock_assessments": [\n'
        "    {\n"
        '      "symbol": string,\n'
        '      "preliminary_direction": "LONG | SHORT",\n'
        '      "reason": string,\n'
        '      "claude_forward_decision": "FORWARD | REJECT"\n'
        "    }\n"
        "  ],\n"
        '  "scan_summary": {\n'
        '    "total_assessed": integer,\n'
        '    "forward_count": integer,\n'
        '    "long_bias_count": integer,\n'
        '    "short_bias_count": integer,\n'
        '    "notable_observations": string\n'
        "  }\n"
        "}"
    )
    sections.append(output_format)
    
    return "\n".join(sections)

def _run_turn2(
    client: anthropic.Anthropic,
    session_id: str,
    session_date: date,
    turn1_result: dict,
    mandatory_stocks: list[str],
) -> tuple[list[dict], list[dict], dict]:
    """
    Execute Turn 2: Pre-scan.
    Prepares raw indicator data, queries Claude, parses/validates the output,
    handles Python overrides and trimming, saves to DB, sends Telegram notification.
    Returns (final_forward_list, compat_turn2_results, cost_info).
    """
    from new_notifications.telegram import send_loud, send_silent

    # 1. Prep data
    turn2_data = _build_turn2_data(session_date, turn1_result, mandatory_stocks)
    
    # 2. Build prompt
    prompt = _build_turn2_prompt(turn2_data)

    # 3. Crash recovery check
    existing = get_claude_turn(session_id, 2)
    if existing and existing.get("output_text"):
        logger.info("Turn 2: found existing turn in session_claude_turns — parsing cached output")
        raw_response = existing["output_text"]
        in_tok = existing.get("input_tokens", 0)
        out_tok = existing.get("output_tokens", 0)
        cost_info = _turn_cost(2, "pre_scan", None, in_tok, out_tok)
    else:
        messages = [{"role": "user", "content": prompt}]
        logger.info("Turn 2: calling Claude (max_tokens=10000)...")
        try:
            response = _call_claude(client, _TURN1_SYSTEM, messages, max_tokens=10000)
            raw_response = response.content[0].text
            u2 = response.usage
            cost_info = _turn_cost(2, "pre_scan", None, u2.input_tokens, u2.output_tokens)

            print("TURN 2 PROMPT: ", prompt)
            print("TURN 2 Response:" , raw_response)
            print("TURN 2 Cost", cost_info)
            # Save to session_claude_turns
            save_claude_turn(
                session_id=session_id,
                turn_number=2,
                turn_type="pre_scan",
                symbol=None,
                input_tokens=u2.input_tokens,
                output_tokens=u2.output_tokens,
                input_text=prompt,
                output_text=raw_response,
            )
        except Exception as exc:
            logger.critical("Turn 2 Claude call failed: %s", exc)
            try:
                send_loud(f"🚨 Turn 2 pre-scan failed — pipeline stopped: {exc}")
            except Exception:
                pass
            raise

    # 4. Response parsing & validation
    try:
        parsed = _parse_json(raw_response)
        if not isinstance(parsed, dict) or "stock_assessments" not in parsed:
            raise ValueError("Turn 2 response JSON missing 'stock_assessments'")
    except Exception as exc:
        logger.critical("Turn 2 response parsing failed: %s | raw=%s", exc, raw_response[:300])
        try:
            send_loud(f"🚨 Turn 2 response parse failed: {exc}")
        except Exception:
            pass
        raise

    stock_assessments = parsed.get("stock_assessments") or []
    scan_summary = parsed.get("scan_summary") or {}
    
    input_symbols = {s["symbol"] for s in turn2_data["stocks"]}
    assessed_symbols = {s.get("symbol") for s in stock_assessments if s.get("symbol")}

    # Validate stock_assessments array length matches input stock count EXACTLY
    if len(stock_assessments) != len(turn2_data["stocks"]):
        missing = input_symbols - assessed_symbols
        extra = assessed_symbols - input_symbols
        msg = f"Turn 2 stock count mismatch! Input: {len(turn2_data['stocks'])}, Claude: {len(stock_assessments)}. Missing: {missing}, Extra: {extra}"
        logger.critical(msg)
        try:
            send_loud(f"🚨 {msg}")
        except Exception:
            pass
        raise ValueError(msg)

    # Validate every symbol exists in input (no hallucinated symbols)
    valid_assessments = []
    for s in stock_assessments:
        sym = s.get("symbol")
        if sym not in input_symbols:
            logger.warning("Dropping hallucinated symbol from Claude Turn 2 response: %s", sym)
            continue
        valid_assessments.append(s)

    # Validate preliminary_direction and claude_forward_decision
    final_validated_assessments = []
    
    # We resolve the full list of mandatory stocks including derived ones
    from new_utils.stock_list import get_stock_list_for_analysis
    base_stocks = get_stock_list_for_analysis()
    derived_mandatory = [sym for sym, info in base_stocks.items() if info.get("mandate") is True]
    mandatory_set = set(derived_mandatory) | set(mandatory_stocks or [])

    for s in valid_assessments:
        sym = s["symbol"]
        is_mandatory = sym in mandatory_set
        
        direction = s.get("preliminary_direction")
        if direction not in ("LONG", "SHORT"):
            msg = f"Invalid direction '{direction}' for {sym}"
            if is_mandatory:
                logger.critical("%s. Mandatory stock defaulting to LONG.", msg)
                s["preliminary_direction"] = "LONG"
                s["reason"] = f"WARNING: {s.get('reason') or ''} [Invalid direction '{direction}' replaced with default LONG]"
            else:
                logger.warning("%s. Defaulting to NEUTRAL-skip.", msg)
                s["preliminary_direction"] = "NEUTRAL-skip"
                s["claude_forward_decision"] = "REJECT"

        fwd_dec = s.get("claude_forward_decision")
        if fwd_dec not in ("FORWARD", "REJECT"):
            logger.warning("Invalid forward decision '%s' for %s. Defaulting to REJECT.", fwd_dec, sym)
            s["claude_forward_decision"] = "REJECT"
            
        final_validated_assessments.append(s)

    # Build compat list
    compat_turn2_results = []
    for s in final_validated_assessments:
        compat_s = {
            "symbol": s["symbol"],
            "preliminary_direction": s["preliminary_direction"],
            "direction": s["preliminary_direction"], # compatibility
            "reason": s["reason"],
            "pre_scan_reasoning": s["reason"], # compatibility
            "claude_forward_decision": s["claude_forward_decision"],
            "forward_to_deep": s["claude_forward_decision"] == "FORWARD", # compatibility
            "priority": "HIGH" if s["claude_forward_decision"] == "FORWARD" else "LOW", # compatibility
        }
        compat_turn2_results.append(compat_s)

    # 5. Python Post-Processing (Mandatory Override and Trimming)
    claude_forwarded = [s for s in compat_turn2_results if s["claude_forward_decision"] == "FORWARD"]
    
    # Deduplicate and tag mandatory
    seen_symbols = set()
    final_forward_list = []
    
    # Process forwarded and mandatory
    for s in compat_turn2_results:
        sym = s["symbol"]
        is_fwd = s["claude_forward_decision"] == "FORWARD"
        is_mand = sym in mandatory_set
        
        if is_fwd or is_mand:
            if sym not in seen_symbols:
                seen_symbols.add(sym)
                if is_mand and is_fwd:
                    inclusion_reason = "BOTH"
                elif is_mand:
                    inclusion_reason = "MANDATORY_OVERRIDE"
                else:
                    inclusion_reason = "CLAUDE_FORWARD"
                fwd_item = {
                    "symbol": sym,
                    "preliminary_direction": s["preliminary_direction"],
                    "direction": s["preliminary_direction"],  # For Turn 3+ compatibility
                    "reason": s["reason"],
                    "pre_scan_reasoning": s["reason"],  # For compat
                    "is_mandatory": is_mand,
                    "claude_forward_decision": s["claude_forward_decision"],
                    "inclusion_reason": inclusion_reason,
                }
                final_forward_list.append(fwd_item)

    # Separate mandatory and non-mandatory forwarded
    non_mandatory_fwd = []
    mandatory_fwd = []
    for item in final_forward_list:
        if item["is_mandatory"]:
            mandatory_fwd.append(item)
        else:
            non_mandatory_fwd.append(item)

    pg = turn1_result.get("prescan_guidance") or {}
    max_stocks_to_forward = pg.get("max_stocks_to_forward")
    try:
        max_stocks_to_forward = int(max_stocks_to_forward)
    except (TypeError, ValueError):
        max_stocks_to_forward = 10

    prioritise_sectors = [s.upper() for s in (pg.get("prioritise_sectors") or [])]
    sector_map = _load_sector_map()

    trimmed_count = 0
    if len(non_mandatory_fwd) > max_stocks_to_forward:
        # Sort non-mandatory by sector priority (prioritized sectors first)
        def sort_key(item):
            sec = _get_sector(item["symbol"], sector_map).upper()
            return 0 if sec in prioritise_sectors else 1
            
        non_mandatory_fwd.sort(key=sort_key)
        kept_non_mandatory = non_mandatory_fwd[:max_stocks_to_forward]
        trimmed_non_mandatory = non_mandatory_fwd[max_stocks_to_forward:]
        trimmed_count = len(trimmed_non_mandatory)

        for t in trimmed_non_mandatory:
            logger.info("Trimming non-mandatory stock %s (sector: %s) because max_stocks_to_forward cap (%d) exceeded",
                        t["symbol"], _get_sector(t["symbol"], sector_map), max_stocks_to_forward)

        final_forward_list = mandatory_fwd + kept_non_mandatory
    else:
        final_forward_list = mandatory_fwd + non_mandatory_fwd

    # Log final counts
    final_total = len(final_forward_list)
    final_mandatory = sum(1 for s in final_forward_list if s["is_mandatory"])
    logger.info("Turn 2 post-processing: final forwarded = %d (incl. %d mandatory), trimmed = %d",
                final_total, final_mandatory, trimmed_count)

    # 6. Save to DB
    session = get_analysis_session(session_id) or {}
    stage_statuses = session.get("stage_statuses") or {}
    if not isinstance(stage_statuses, dict):
        stage_statuses = {}
    stage_statuses["turn2"] = "COMPLETE"

    update_analysis_session(session_id, {
        "forward_list": final_forward_list,
        "stage_statuses": stage_statuses
    })

    # 7. Telegram Notification
    try:
        from new_notifications.telegram import send_prescan_complete
        send_prescan_complete(
            trade_date=str(session_date),
            forwarded_symbols=final_forward_list,
            cost_usd=cost_info.get("total_cost_usd", 0.0),
        )
    except Exception as exc:
        logger.warning("Turn 2 Telegram notification failed: %s", exc)

    return final_forward_list, compat_turn2_results, cost_info

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


def _build_turn3_data(
    symbol: str,
    session_date: date,
    turn1_result: dict,
    turn2_result: list[dict]
) -> dict:
    """
    Assembles the complete data package for one stock (conforming to Section 1-8 of Turn 3 Spec).
    """
    logger.info("Assembling Turn 3 data package for %s on %s...", symbol, session_date)
    
    # ── Section 1: Stock Identity ─────────────────────────────────────────────
    assessments = []
    if isinstance(turn2_result, dict):
        assessments = turn2_result.get("stock_assessments", []) or turn2_result.get("stocks", []) or []
    elif isinstance(turn2_result, list):
        assessments = turn2_result
        
    stock_assessment = next(
        (a for a in assessments if a.get("symbol") == symbol),
        None
    )

    print(stock_assessment)
    is_mandatory = stock_assessment.get("is_mandatory", False) if stock_assessment else False
    preliminary_direction = (
        stock_assessment.get("preliminary_direction") 
        or stock_assessment.get("direction") 
        or "LONG"
    ) if stock_assessment else "LONG"
    
    preliminary_reason = (
        stock_assessment.get("reason") 
        or stock_assessment.get("preliminary_reason") 
        or ""
    ) if stock_assessment else ""
    
    if not preliminary_reason:
        logger.warning("preliminary_reason empty for %s — Turn 2 may not have included reason", symbol)
        preliminary_reason = "No reason provided by Turn 2"
    
    from database.queries import get_recent_setups_for_symbol
    prev_setups_raw = []
    try:
        prev_setups_raw = get_recent_setups_for_symbol(symbol, limit=3)
    except Exception as exc:
        logger.warning("Failed to query previous setups for %s: %s", symbol, exc)
        
    previous_setups = []
    for s in prev_setups_raw:
        previous_setups.append({
            "setup_date": str(s.get("setup_date", "")),
            "direction": s.get("direction"),
            "conviction_score": s.get("conviction_score"),
            "stage": s.get("stage"),
            "setup_type": s.get("setup_type"),
            "paper_outcome": s.get("paper_outcome")
        })
        
    identity = {
        "symbol": symbol,
        "session_date": str(session_date),
        "is_mandatory": is_mandatory,
        "preliminary_direction": preliminary_direction,
        "preliminary_reason": preliminary_reason,
        "previous_setups": previous_setups
    }
    
    # ── Section 2: Price History & Section 3: Pre-Computed Indicators ─────────
    price_rows = []
    try:
        price_rows = get_price_history(symbol, days=250)
    except Exception as exc:
        logger.warning("Failed to query price history for %s: %s", symbol, exc)
        
    if not price_rows:
        logger.error("Price history is empty for %s. Cannot build Turn 3 package.", symbol)
        return {}
        
    df = pd.DataFrame(price_rows)
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    
    ohlcv_180d = []
    for _, r in df.tail(180).iterrows():
        ohlcv_180d.append({
            "date": str(r["date"]),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": int(r["volume"])
        })
        
    indicators_result = {}
    try:
        indicators_result = compute_stock_indicators(df)
    except Exception as exc:
        logger.error("Failed to compute indicators for %s: %s", symbol, exc)
        
    volume_ratio_20d = indicators_result.get("volume_ratio_20d", 1.0)
    
    price_history = {
        "ohlcv_180d": ohlcv_180d,
        "volume_ratio_20d": volume_ratio_20d
    }
    
    price = df['close'].iloc[-1]
    ema20_val  = indicators_result.get('ema20')
    ema50_val  = indicators_result.get('ema50')
    ema180_val = indicators_result.get('ema180')

    if (ema180_val is not None and ema20_val is not None and ema50_val is not None and
        price > ema20_val and
        price > ema50_val and
        price > ema180_val):
        ema_arrangement = "BULLISH"
    elif (ema20_val is not None and ema50_val is not None and
          price < ema20_val and
          price < ema50_val):
        ema_arrangement = "BEARISH"
    else:
        ema_arrangement = "MIXED"

    indicators = {
        "ema20": ema20_val,
        "ema50": ema50_val,
        "ema180": ema180_val,
        "atr14": indicators_result.get("atr14"),
        "atr_pct": indicators_result.get("atr_pct"),
        "rsi14": indicators_result.get("rsi14"),
        "macd_line": indicators_result.get("macd_line"),
        "macd_signal": indicators_result.get("macd_signal"),
        "macd_histogram": indicators_result.get("macd_histogram"),
        "macd_histogram_direction": indicators_result.get("macd_histogram_direction", "SHRINKING"),
        "rsi_last_20": indicators_result.get("rsi_last_20", []),
        "macd_hist_last_20": indicators_result.get("macd_hist_last_20", []),
        "price_vs_ema20": indicators_result.get("price_vs_ema20", "below"),
        "price_vs_ema50": indicators_result.get("price_vs_ema50", "below"),
        "price_vs_ema180": indicators_result.get("price_vs_ema180", "unavailable"),
        "ema_arrangement": ema_arrangement
    }
    
    # ── Section 4: Futures Data ───────────────────────────────────────────────
    from database.queries import get_futures_series, get_continuous_oi, get_options_snapshot
    
    futures_available = False
    futures_30d = []
    basis_current = None
    basis_trend = "STABLE"
    rollover_phase = "NORMAL"
    days_to_expiry = None
    near_month_oi_trend = "STABLE"
    
    fut_rows = []
    try:
        fut_rows = get_futures_series(symbol, days=30)
    except Exception as exc:
        logger.warning("Failed to query futures series for %s: %s", symbol, exc)
        
    if fut_rows:
        futures_available = True
        for row in fut_rows:
            futures_30d.append({
                "date": row["date"],
                "futures_price": row["futures_price"],
                "futures_open": row.get("futures_open"),
                "futures_high": row.get("futures_high"),
                "futures_low": row.get("futures_low"),
                "futures_volume": row.get("futures_volume"),
                "basis": row["basis"],
                "near_month_oi": row["near_month_oi"]
            })
            
        basis_current = futures_30d[-1]["basis"]
        rollover_phase = fut_rows[-1].get("rollover_phase", "NORMAL")
        
        if len(futures_30d) >= 5:
            old_basis = futures_30d[-5]["basis"]
            new_basis = futures_30d[-1]["basis"]
            if old_basis is not None and new_basis is not None:
                diff = new_basis - old_basis
                if diff > 0.5:
                    basis_trend = "EXPANDING"
                elif diff < -0.5:
                    basis_trend = "CONTRACTING"
                    
        if len(futures_30d) >= 5:
            old_oi = futures_30d[-5]["near_month_oi"]
            new_oi = futures_30d[-1]["near_month_oi"]
            if old_oi and new_oi and old_oi > 0:
                pct_change = (new_oi - old_oi) / old_oi * 100
                if pct_change > 5.0:
                    near_month_oi_trend = "INCREASING"
                elif pct_change < -5.0:
                    near_month_oi_trend = "DECREASING"
                    
    # Next-month futures series (same rows, next contract fields)
    futures_30d_next = []
    next_month_expiry = None
    next_month_dte = None
    next_month_latest_basis = None
    near_month_expiry = None
    if fut_rows:
        near_month_expiry = fut_rows[-1].get("near_expiry")
        next_month_expiry = fut_rows[-1].get("next_expiry")
        for row in fut_rows:
            if row.get("next_futures_price") is not None:
                futures_30d_next.append({
                    "date": row["date"],
                    "futures_price": row["next_futures_price"],
                    "basis": row["next_basis"],
                    "oi": int(row.get("next_month_oi") or 0),
                })
        if next_month_expiry:
            try:
                next_month_dte = (date.fromisoformat(next_month_expiry) - session_date).days
            except Exception:
                pass
        if futures_30d_next:
            next_month_latest_basis = futures_30d_next[-1]["basis"]

    futures_data = {
        "futures_available": futures_available,
        "futures_30d": futures_30d,
        "futures_30d_next": futures_30d_next,
        "basis_current": basis_current,
        "basis_trend": basis_trend,
        "rollover_phase": rollover_phase,
        "days_to_expiry": None,
        "near_month_expiry": near_month_expiry,
        "next_month_expiry": next_month_expiry,
        "next_month_dte": next_month_dte,
        "next_month_latest_basis": next_month_latest_basis,
        "near_month_oi_trend": near_month_oi_trend
    }
    
    # ── Section 5: Options Data ───────────────────────────────────────────────
    options_available = False
    pcr_near = None
    max_pain = None
    atm_strike = None
    summary_atm_iv = None
    atm_ce_iv = None
    atm_pe_iv = None
    options_note = ""
    ce_walls = []
    pe_walls = []
    
    oi_rows = []
    try:
        oi_rows = get_continuous_oi(symbol, days=30)
    except Exception as exc:
        logger.warning("Failed to query continuous OI series for %s: %s", symbol, exc)
        
    latest_oi = oi_rows[-1] if oi_rows else {}
    near_expiry_str = latest_oi.get("near_expiry")
    
    if near_expiry_str:
        try:
            near_expiry_date = date.fromisoformat(near_expiry_str)
            days_to_expiry = (near_expiry_date - session_date).days
            futures_data["days_to_expiry"] = days_to_expiry
            if not futures_data.get("near_month_expiry"):
                futures_data["near_month_expiry"] = near_expiry_str
        except Exception as exc:
            logger.warning("Failed to compute DTE for %s: %s", symbol, exc)
            
        pcr_near = latest_oi.get("pcr_near")
        max_pain = latest_oi.get("max_pain")
        
        options = []
        try:
            options = get_options_snapshot(symbol, session_date, date.fromisoformat(near_expiry_str))
        except Exception as exc:
            logger.warning("Failed to fetch options snapshot for %s: %s", symbol, exc)
            
        if options:
            options_available = True
            spot_price = float(df["close"].iloc[-1])
            
            strikes = list(set(float(r["strike"]) for r in options))
            if strikes:
                atm_strike = min(strikes, key=lambda s: abs(s - spot_price))
                
            ce_atm = [r for r in options if r.get("option_type") == "CE" and float(r["strike"]) == atm_strike]
            pe_atm = [r for r in options if r.get("option_type") == "PE" and float(r["strike"]) == atm_strike]
            if ce_atm and ce_atm[0].get("iv") is not None:
                atm_ce_iv = round(float(ce_atm[0]["iv"]), 2)
                summary_atm_iv = atm_ce_iv  # backward-compat alias
            if pe_atm and pe_atm[0].get("iv") is not None:
                atm_pe_iv = round(float(pe_atm[0]["iv"]), 2)
                
            try:
                from pipeline.deep_analysis import oi_walls
                walls = oi_walls(options, near_expiry_str, top_n=3)
                ce_walls = walls.get("ce_walls", [])
                pe_walls = walls.get("pe_walls", [])
            except Exception as exc:
                logger.warning("Failed to compute OI walls for %s: %s", symbol, exc)
                
    options_data = {
        "options_available": options_available,
        "pcr_near": pcr_near,
        "max_pain": max_pain,
        "atm_strike": atm_strike,
        "summary_atm_iv": summary_atm_iv,
        "atm_ce_iv": atm_ce_iv,
        "atm_pe_iv": atm_pe_iv,
        "options_note": options_note,
        "ce_walls": ce_walls,
        "pe_walls": pe_walls,
        "options_chain_last_day": [
            {"date": r.get("snapshot_date"), "strike": r["strike"], "type": r["option_type"],
             "oi": r.get("oi"), "iv": r.get("iv"),
             "premium": r.get("premium_close")}
            for r in options
        ]
    }
    
    # ── Section 6: Sector Context ─────────────────────────────────────────────
    stock_sector, _ = _sector_info(symbol)
    sector_known = (stock_sector != "UNKNOWN")
    
    sector_picture = None
    if sector_known:
        sector_pictures = turn1_result.get("sector_pictures", {})
        for k, v in sector_pictures.items():
            if k.upper() == stock_sector.upper():
                sector_picture = v
                break
                
    sector_context = {
        "sector_known": sector_known,
        "stock_sector": stock_sector,
        "sector_picture": sector_picture
    }
    
    # ── Section 7: Market Context ─────────────────────────────────────────────
    market_trend = turn1_result.get("market_trend", "SIDEWAYS")
    market_volatility = turn1_result.get("market_volatility", "LOW")
    execution_bias = turn1_result.get("execution_bias", "CAUTIOUS")
    session_risk_level = turn1_result.get("session_risk_level", "MEDIUM")
    conviction_multiplier = turn1_result.get("conviction_multiplier", 1.0)
    
    nifty_price_structure = turn1_result.get("nifty_price_structure", {})
    if not nifty_price_structure:
        nifty_price_structure = {
            "overall_structure": turn1_result.get("market_structure", "RECOVERY"),
            "trend_quality": turn1_result.get("trend_quality", "CONFLICTING"),
            "trading_implication": {
                "summary": turn1_result.get("trading_implication", {}).get("summary", ""),
                "index_bias": turn1_result.get("trading_implication", {}).get("index_bias", "NEUTRAL"),
                "conviction_adjustment": turn1_result.get("trading_implication", {}).get("conviction_adjustment", "NEUTRAL"),
                "key_condition_to_watch": turn1_result.get("trading_implication", {}).get("key_condition_to_watch", "")
            }
        }
        
    market_context = {
        "market_trend": market_trend,
        "market_volatility": market_volatility,
        "execution_bias": execution_bias,
        "session_risk_level": session_risk_level,
        "conviction_multiplier": conviction_multiplier,
        "nifty_price_structure": nifty_price_structure,
        "vix_assessment": turn1_result.get("vix_assessment", {}),
        "fii_dii_assessment": turn1_result.get("fii_dii_assessment", {}),
        "prescan_guidance": turn1_result.get("prescan_guidance", {})
    }
    
    # ── Section 8: Turn 2 Context ─────────────────────────────────────────────
    turn2_assessment = {
        "symbol": symbol,
        "preliminary_direction": preliminary_direction,
        "reason": preliminary_reason,
        "claude_forward_decision": "FORWARD",
        "is_mandatory": is_mandatory,
        "inclusion_reason": (stock_assessment.get("inclusion_reason") or "CLAUDE_FORWARD") if stock_assessment else "CLAUDE_FORWARD"
    }
    
    package = {
        "section1": identity,
        "section2": price_history,
        "section3": indicators,
        "section4": futures_data,
        "section5": options_data,
        "section6": sector_context,
        "section7": market_context,
        "section8": {
            "turn2_assessment": turn2_assessment
        }
    }
    
    return package


def _build_turn3_prompt(data_package: dict) -> str:
    """
    Assembles the plain-text Claude prompt from the data package conforming to Turn 3 Spec.
    """
    sec1 = data_package["section1"]
    sec2 = data_package["section2"]
    sec3 = data_package["section3"]
    sec4 = data_package["section4"]
    sec5 = data_package["section5"]
    sec6 = data_package["section6"]
    sec7 = data_package["section7"]
    sec8 = data_package["section8"]["turn2_assessment"]
    
    symbol = sec1["symbol"]
    direction = sec1["preliminary_direction"]
    is_mandatory_str = "YES (Mandatory stock analysis)" if sec1["is_mandatory"] else "NO"
    
    # Format lists/dicts compactly to stay well under token limits
    ohlcv_compact = json.dumps(sec2["ohlcv_180d"], separators=(',', ':'))
    options_compact = json.dumps(sec5, separators=(',', ':')) if sec5["options_available"] else "{}"

    if sec4["futures_available"]:
        # Rename near_month_oi → oi in prompt JSON so both series use the same generic field name.
        # Internal Python dicts retain near_month_oi for OI-trend calculations.
        near_rows_renamed = [
            {k: v for k, v in r.items() if k != "near_month_oi"} | {"oi": r["near_month_oi"]}
            for r in sec4["futures_30d"]
        ]
        near_compact = json.dumps(near_rows_renamed, separators=(',', ':'))
        next_compact = json.dumps(sec4.get("futures_30d_next", []), separators=(',', ':'))
    else:
        near_compact = "[]"
        next_compact = "[]"
    previous_setups_compact = json.dumps(sec1["previous_setups"], separators=(',', ':'))
    
    # Format Sector Context
    sector_pic = sec6["sector_picture"]
    if sec6["sector_known"] and sector_pic:
        sector_picture_text = f"""- Sector: {sec6["stock_sector"]}
- Stance: {sector_pic.get("stance", "UNKNOWN")}
- Trend: {sector_pic.get("trend", "UNKNOWN")}
- Momentum: {sector_pic.get("momentum", "UNKNOWN")}
- Strength: {sector_pic.get("strength", "UNKNOWN")}
- Structure: {sector_pic.get("structure", "UNKNOWN")}
- Support Level: {sector_pic.get("key_levels", {}).get("support", "N/A")}
- Resistance Level: {sector_pic.get("key_levels", {}).get("resistance", "N/A")}
- Momentum Analysis: {sector_pic.get("momentum_note", "N/A")}
- Character & Behaviour: {sector_pic.get("character", "N/A")}
- Trading Note: {sector_pic.get("trading_note", "N/A")}"""
    else:
        sector_picture_text = f"- Sector: {sec6['stock_sector']}\n- Sector analysis details: UNKNOWN"

    # Format Section E: F&O Data
    if sec4["futures_available"]:
        _next_meta = ""
        if sec4.get("next_month_expiry"):
            _next_meta = f"""
- Next-Month Contract (Expiry: {sec4['next_month_expiry']}, DTE: {sec4['next_month_dte']} trading days, Latest Basis: {sec4['next_month_latest_basis']}):
  futures_30d_next_month (fields: date, futures_price, basis, oi):
{next_compact}"""
        else:
            _next_meta = "\n- Next-Month Contract: NOT AVAILABLE"
        futures_text = f"""- Futures Available: True
- Near-Month Contract (Expiry: {sec4.get('near_month_expiry') or 'N/A'}, DTE: {sec4['days_to_expiry']} trading days):
  futures_30d_near_month (fields: date, futures_price, futures_open, futures_high, futures_low, futures_volume, basis, oi):
{near_compact}
- Basis Current: {sec4['basis_current']} ({sec4['basis_trend']} trend)
- Rollover Phase: {sec4['rollover_phase']}
- Near Month OI Trend: {sec4['near_month_oi_trend']}{_next_meta}"""
    else:
        futures_text = "- Futures: NOT AVAILABLE for this stock"

    if sec5["options_available"]:
        chain_compact = json.dumps(sec5['options_chain_last_day'], separators=(',', ':'))
        _ce_iv_str = str(sec5['atm_ce_iv']) if sec5['atm_ce_iv'] is not None else 'null'
        _pe_iv_str = str(sec5['atm_pe_iv']) if sec5['atm_pe_iv'] is not None else 'null'
        options_text = f"""- Options Available: True
- PCR Near Month: {sec5['pcr_near']}
- Max Pain: {sec5['max_pain']}
- ATM Strike: {sec5['atm_strike']}
- ATM CE IV (from chain, at strike {sec5['atm_strike']}): {_ce_iv_str}
- ATM PE IV (from chain, at strike {sec5['atm_strike']}): {_pe_iv_str}
- Options Note: {sec5['options_note'] if sec5['options_note'] else 'None'}
- CE Walls (resistance): {json.dumps(sec5['ce_walls'])}
- PE Walls (support): {json.dumps(sec5['pe_walls'])}
- Option Chain Last Day: {chain_compact}

[IV SOURCE RULE]
iv_source_used = "chain_iv" if ANY row in Option Chain Last Day has a non-null iv value (even off-ATM).
iv_source_used = "vix_proxy" ONLY when every row in the chain has null iv.
A null ATM CE IV or ATM PE IV in the pre-computed values above does NOT mean the chain is empty —
it only means that specific side was missing/null at the exact ATM strike. Apply the nearby-strike
fallback in [ATM_IV_COMPUTATION] before concluding a side is unavailable.
Never write "IV unavailable" when the chain contains any non-null IV values.

[ATM_IV_COMPUTATION — populate options_setup IV fields]
For each side (CE and PE) independently, apply this priority order:
1. Use the pre-computed value shown above if non-null — use as-is.
2. If null: check the nearest available strike within 1 step in Option Chain Last Day for that side
   (one strike immediately above or below ATM, whichever has data). If found with non-null iv, use it
   and note the substitution in iv_note (e.g. "atm_pe_iv sourced from 1045 PE — exact ATM 1040 PE row missing").
3. If step 2 also yields null: set that side's IV field to null and state in iv_note that IV could not
   be determined for the traded side. Do not substitute VIX for an individual side's IV field.

Set iv_source_used = "vix_proxy" only after confirming every chain row has null iv.

Do NOT average atm_ce_iv and atm_pe_iv — a trade uses only one side; averaging dilutes with the unused
instrument and hides real skew/liquidity signals.
- atm_iv_skew = atm_ce_iv − atm_pe_iv, rounded to 2 dp; null if either is null.
- iv_used_for_trade = atm_ce_iv if direction == "LONG" (trade uses CE); atm_pe_iv if direction == "SHORT"
  (trade uses PE). Derived deterministically from direction — same pattern as walls_checked_side.
- If abs(atm_iv_skew) > 5 points, flag in iv_note as a possible liquidity/staleness signal on the thinner
  side — state BOTH possibilities (genuine market skew vs. stale/thin quote), not just one.
- When iv_source_used = "vix_proxy", set atm_ce_iv, atm_pe_iv, atm_iv_skew, and iv_used_for_trade all to null."""
    else:
        options_text = "- Options: NOT AVAILABLE — use VIX as IV proxy for premium estimation"

    prompt = f"""[SECTION A: ROLE AND TASK DEFINITION]
You are a highly experienced hedge fund manager and swing trading mentor specializing in the Indian F&O (Futures & Options) markets. Your task is to perform a meticulous deep analysis on {symbol} for the session date {sec1["session_date"]}.

Your goal is to evaluate if there is a valid swing setup (2-5 days hold) on this stock matching the Turn 2 preliminary direction ({direction}).
You must apply the 100-point Conviction Scoring Framework and enforce all operational hard gates to determine the trade readiness of the setup: TRADE_READY, WATCH, ON_RADAR, or REJECT.

[SECTION B: STOCK CONTEXT]
- Symbol: {symbol}
- Preliminary Direction: {direction}
- Preliminary Reason from Pre-Scan: {sec1["preliminary_reason"]}
- Previous Setups for Symbol: {previous_setups_compact}

[SECTION C: MARKET CONTEXT]
- Market Trend: {sec7["market_trend"]}
- Volatility Stance: {sec7["market_volatility"]}
- Execution Bias: {sec7["execution_bias"]}
- Session Risk Level: {sec7["session_risk_level"]}
- Conviction Multiplier: {sec7["conviction_multiplier"]}
- Nifty Price Structure: {json.dumps(sec7["nifty_price_structure"], separators=(',', ':'))}
- VIX Assessment: {json.dumps(sec7["vix_assessment"], separators=(',', ':'))}
- FII/DII Net Flows: {json.dumps(sec7["fii_dii_assessment"], separators=(',', ':'))}
- Prescan Guidance: {json.dumps(sec7["prescan_guidance"], separators=(',', ':'))}

- Sector Context:
{sector_picture_text}

[SECTION D: STOCK PRICE DATA]
- 180 Days OHLCV Time Series:
{ohlcv_compact}
- Volume Ratio (20d): {sec2["volume_ratio_20d"]}
- Pre-Computed Indicators:
  - EMA20: {sec3["ema20"]} (Price vs EMA20: {sec3["price_vs_ema20"]})
  - EMA50: {sec3["ema50"]} (Price vs EMA50: {sec3["price_vs_ema50"]})
  - EMA180: {sec3["ema180"]} (Price vs EMA180: {sec3["price_vs_ema180"]})
  - EMA Arrangement: {sec3["ema_arrangement"]}
  - Current Close Price (Spot): {sec2["ohlcv_180d"][-1]["close"] if sec2.get("ohlcv_180d") else "N/A"}
  - ATR14: {sec3["atr14"]} (ATR% of Price: {sec3["atr_pct"]}%)
  - RSI14: {sec3["rsi14"]}
  - MACD Line: {sec3["macd_line"]} | Signal: {sec3["macd_signal"]} | Hist: {sec3["macd_histogram"]}
  - MACD Histogram Direction: {sec3["macd_histogram_direction"]}
  - Last 20 RSI Values: {json.dumps(sec3["rsi_last_20"], separators=(',', ':'))}
  - Last 20 MACD Histogram Values: {json.dumps(sec3["macd_hist_last_20"], separators=(',', ':'))}

[SECTION E: F&O DATA]
{futures_text}

{options_text}

[SECTION F: SCORING INSTRUCTIONS]
═══════════════════════════════════════════════════
DIMENSION SCORING — MANDATORY CALCULATION FORMAT
═══════════════════════════════════════════════════

For EVERY dimension, you MUST write an
explicit labelled score calculation block
at the END of that dimension's narrative,
BEFORE writing the scoring_breakdown JSON.

The number written in scoring_breakdown
for each dimension MUST exactly match the
SUM written in that dimension's calculation
block. If they differ, recompute before
writing the JSON output.

These calculation blocks are not optional
commentary — they are mandatory arithmetic
anchors that prevent score drift between
your analysis and your JSON output.

───────────────────────────────────────────
DIMENSION 1: PRICE STRUCTURE (max 55 pts)
───────────────────────────────────────────

Sub-components are scored independently.
No deductions or adjustments exist.
Each sub-score is bounded by its maximum.

  S/R Zones (15 pts): EMA dynamic support + horizontal swing levels confluence.
    14-15 = major confluence, 10-13 = clear S/R from 2 sources,
    6-9 = single level, 2-5 = weak, 0-1 = no basis.
  Chart Patterns (13 pts): Completion, textbook shape, mechanical target.
    12-13 = complete/clean, 8-11 = clear but imperfect,
    4-7 = forming, 1-3 = ambiguous, 0 = none.
  Buyer/Seller Analysis (12 pts): Body vs range, close position, last 5 candles control.
    11-12 = clear control, 7-10 = biased, 4-6 = contested,
    1-3 = opposing building, 0 = absorption.
  Candlestick Patterns (8 pts): Named candlestick patterns at key levels.
    7-8 = high significance, 5-6 = medium, 3-4 = low,
    1-2 = conflicting, 0 = none.
  RSI + MACD (4 pts): RSI divergence (only divergence, not overbought/oversold)
    and MACD momentum direction.
  Volume (3 pts): Volume ratio and trend confirming the price movement.

At the END of dimension_1_narrative,
write this block exactly:

  "DIMENSION 1 FINAL SCORE:
   S/R Zones:           __/15
   Chart Patterns:      __/13
   Buyer/Seller:        __/12
   Candlestick:         __/8
   RSI + MACD:          __/4
   Volume:              __/3
   ─────────────────────────
   SUM:                 __/55
   Verify each sub-score ≤ its max ✓
   Verify SUM = sum of above six ✓"

Constraints:
  Each sub-score must be ≤ its maximum.
  SUM must equal the arithmetic total of
  all six sub-scores.
  SUM must be ≤ 55.
  No sub-score can be negative.

───────────────────────────────────────────
DIMENSION 2: RISK/REWARD (max 25 pts)
───────────────────────────────────────────

  Stop Loss Quality (10 pts): Invalidation logic clarity and ATR check
    (sweet spot 0.75x-1.5x ATR). 0 = no structural SL (triggers REJECT).
  Target Logic (8 pts): Targets T1/T2 at structural S/R.
    T2 must yield >= 1:1.5 R:R. 0 = R:R < 1:1.5 (triggers REJECT).
  Entry Zone Quality (5 pts): Zone confluence and tightness (< 1% width).
  R:R Ratio Score (2 pts): >= 2.5 R:R = 2 pts, >= 2.0 = 1.5 pts,
    >= 1.5 = 1 pt, < 1.5 = 0 pts (triggers REJECT).

FORCED ZERO RULE — MANDATORY:
  If Gate 2 fires (rr_t2 < 1.5),
  the following sub-scores are
  automatically forced to zero
  regardless of qualitative assessment:
    Target Logic:    0/8  (forced)
    R:R Ratio Score: 0/2  (forced)

  No partial credit on these two
  sub-components when Gate 2 fires.
  Stop Loss Quality and Entry Zone Quality
  are still scored normally.

  This is not a deduction — these
  sub-components are zeroed because
  the R:R failure makes targets invalid.

At the END of dimension_2_narrative,
write this block exactly:

  "DIMENSION 2 FINAL SCORE:
   Stop Loss Quality:   __/10
   Target Logic:        __/8  [0 if Gate 2 fires]
   Entry Zone Quality:  __/5
   R:R Ratio Score:     __/2  [0 if Gate 2 fires]
   ─────────────────────────
   SUM:                 __/25
   Gate 2 status:       FIRED / NOT FIRED
   Verify each sub-score ≤ its max ✓
   Verify SUM = sum of above four ✓"

Constraints:
  If Gate 2 fires:
    Target Logic = 0 (not 1, not 2, not 4 — zero)
    R:R Ratio Score = 0
    SUM can only be SL_score + EZQ_score (max 15)
  If Gate 2 does not fire:
    All sub-scores scored normally
    SUM must be ≤ 25
  No sub-score can be negative.

───────────────────────────────────────────
DIMENSION 3: MARKET + SECTOR (max 15 pts)
───────────────────────────────────────────

  Index Context (8 pts): Mapped from Nifty bias
    (Supportive = 7-8 pts, Neutral = 4-5 pts, Resistant = 1-2 pts).
  Sector Context (7 pts): Tailwind Strong = 6-7 pts,
    Tailwind Moderate = 4-5 pts, Neutral = 3 pts, Headwind = 0-2 pts.

RELATIVE STRENGTH ADJUSTMENT — MANDATORY:
  The Sector Context sub-score has an
  explicit adjustment rule:

  Step 1: Determine base sector score
    Tailwind Strong:    6-7 pts base
    Tailwind Moderate:  4-5 pts base
    Neutral:            3 pts base
    Headwind Moderate:  1-2 pts base
    Headwind Strong:    0 pts base

  Step 2: Compute relative strength
    Compare stock's 20-day return
    against sector's 20-day return

    If stock outperforms sector by ≥ 2%:
      adjustment = +1
    If stock underperforms sector by ≥ 2%:
      adjustment = −1
    If difference is within 2%:
      adjustment = 0

  Step 3: Apply adjustment
    sector_context_adjusted =
      base_sector_score + adjustment

    Floor at 0 (cannot go below 0)
    Cap at 7 (cannot exceed sub-max)

    Show the arithmetic explicitly:
    e.g. "Base: 6 pts + adj: −1 = 5 pts"
         "Base: 4 pts + adj: +1 = 5 pts"
         "Base: 7 pts + adj: +1 = 7 pts (cap)"

At the END of dimension_3_narrative,
write this block exactly:

  "DIMENSION 3 FINAL SCORE:
   Index Context:            __/8
   Sector Context base:      __/7
   Relative strength adj:    +1 / −1 / 0
   Sector Context adjusted:  __/7
     (= base + adj, min 0, max 7)
   ─────────────────────────────────
   SUM:                      __/15
     (= Index Context + Sector Context adjusted)
   Verify Index Context ≤ 8 ✓
   Verify Sector Context adjusted ≤ 7 ✓
   Verify SUM = Index + Sector adjusted ✓"

Constraints:
  Index Context: 0-8, no adjustment
  Sector Context adjusted: 0-7
  SUM = Index Context + Sector Context adjusted
  SUM must be ≤ 15
  Do NOT add the adjustment separately —
  it is already folded into
  Sector Context adjusted

COMMON ERROR TO AVOID:
  ❌ SUM = Index (8) + base (6) + adj (−1)
     = 8 + 6 + (−1) = 13
     WRONG — adj is applied TO base, not added separately

  ✅ SUM = Index (8) + adjusted (6−1=5) = 13
     CORRECT

───────────────────────────────────────────
DIMENSION 4: STOCK F&O (max 5 pts)
───────────────────────────────────────────

  Futures Basis (2 pts): Positive carry = 2 pts, negative carry = 0-1 pts.
  PCR Context (2 pts): Contrarian extreme PCR checks.
  Rollover + DTE (1 pt): DTE < 6 trading days triggers options REJECT.

OI WALL DEDUCTION — MANDATORY:
  If oi_wall_proximity_check.pass == false:
    PCR Context score is reduced by 1 pt
    Minimum PCR Context score after
    deduction = 0 (cannot go negative)

    Show explicitly in narrative:
    "PCR Context: X pts − 1 pt (OI wall
     deduction) = Y pts"

    Use Y (the adjusted value) in the
    final sum — not X (the base value)
    and NOT X + Y (both values)

At the END of dimension_4_narrative,
write this block exactly:

  "DIMENSION 4 FINAL SCORE:
   Futures Basis:              __/2
   PCR Context base:           __/2
   OI wall deduction:          −1 / 0
     (−1 if wall check fails, 0 if passes)
   PCR Context adjusted:       __/2
     (= base − deduction, min 0)
   Rollover + DTE:             __/1
   ─────────────────────────────────────
   SUM:                        __/5
     (= Basis + PCR adjusted + DTE)
   Verify Basis ≤ 2 ✓
   Verify PCR adjusted ≤ 2 ✓
   Verify DTE ≤ 1 ✓
   Verify SUM = Basis + PCR adjusted + DTE ✓
   Verify SUM ≤ 5 ✓"

Constraints:
  Futures Basis: 0-2
  PCR Context base: 0-2
  PCR Context adjusted: 0-2 (after deduction)
  Rollover + DTE: 0-1
  SUM = Basis + PCR_adjusted + DTE
  SUM must be ≤ 5

COMMON ERROR TO AVOID:
  ❌ SUM = Basis(2) + PCR_base(2)
           + deduction(−1) + DTE(1) = 4
     WRONG — deduction is applied TO PCR,
     not added as a separate term

  ❌ SUM = Basis(2) + PCR_base(2)
           + PCR_adjusted(1) + DTE(1) = 6
     WRONG — do not add both base and adjusted

  ✅ SUM = Basis(2) + PCR_adjusted(1) + DTE(1) = 4
     CORRECT

───────────────────────────────────────────
SCORING_BREAKDOWN COMPUTATION
───────────────────────────────────────────

After completing all four dimension
calculation blocks, compute:

  raw_total_score =
    dimension_1 SUM
    + dimension_2 SUM
    + dimension_3 SUM
    + dimension_4 SUM

  adjusted_score =
    raw_total_score × conviction_multiplier
    (currently {sec7["conviction_multiplier"]})

Write this final block at the end of
ALL dimension narratives, immediately
before the JSON output:

  "FINAL SCORE SUMMARY:
   Dimension 1: __/55
   Dimension 2: __/25
   Dimension 3: __/15
   Dimension 4: __/5
   ──────────────────
   Raw Total:   __/100
   Multiplier:  __
   Adjusted:    __ (= raw × multiplier)

   Stage determination:
   adjusted ≥ 72 → TRADE_READY
   adjusted 52-71 → WATCH
   adjusted 35-51 → ON_RADAR
   adjusted < 35  → REJECT (score-based)
   Any hard gate  → REJECT (gate-based)

   Stage: ___________"

The values in scoring_breakdown JSON MUST
exactly match this final summary block.
If any value differs, recompute before
writing the JSON.

Apply thresholds on adjusted_score to set the initial stage:
- TRADE_READY : adjusted_score >= 72
- WATCH       : adjusted_score 52-71
- ON_RADAR    : adjusted_score 35-51
- REJECT      : adjusted_score < 35 OR any hard gate triggered

───────────────────────────────────────────
GENERAL ARITHMETIC RULES FOR ALL DIMENSIONS
───────────────────────────────────────────

1. USE ADJUSTED VALUES IN SUMS
   When a deduction or adjustment exists,
   use the POST-adjustment value in the sum.
   Never add both the base and adjusted value.
   Never add the deduction as a separate term.

2. NO NEGATIVE SCORES
   No sub-score can be negative.
   No dimension total can be negative.
   Floor all values at 0.

3. NO SCORES EXCEEDING MAXIMUMS
   If any sub-score would exceed its maximum,
   cap it at the maximum.
   If dimension SUM would exceed its maximum,
   something is wrong — recheck sub-scores.

4. ONE SOURCE OF TRUTH
   The calculation block in the narrative
   is the source of truth.
   The scoring_breakdown JSON copies from it.
   They must match exactly.
   If they differ, the narrative block wins —
   correct the JSON to match the narrative.

5. INTEGER SCORES ONLY
   All sub-scores and dimension totals
   must be integers.
   Adjusted score may be a decimal when
   conviction_multiplier is not 1.0
   (e.g. 78 × 0.97 = 75.66).

═══════════════════════════════════════════════════
[HARD GATES] — UNCONDITIONAL ENFORCEMENT
═══════════════════════════════════════════════════

Hard gates are binary and unconditional.
When a gate condition is met, you MUST
immediately set stage = REJECT and
hard_gate_triggered = true.

There are NO exceptions.
There is NO reasoning around a gate.
"The setup has merit" is NOT a valid
reason to bypass a gate.
"The gate is technically triggered but..."
is a forbidden construction — if it is
triggered, it is triggered, full stop.

GATE 1: No structural SL identified
  Condition: You cannot identify a clear
    structural price level that, if broken,
    definitively invalidates the trade thesis.
    An arbitrary percentage or ATR-based SL
    with no structural anchor does NOT pass.
  Action:
    stage = "REJECT"
    hard_gate_triggered = true
    hard_gate_reason = "GATE 1 — No structural SL identified"
    rejection_reason = "No structural stop loss level identifiable from price data"
  Then: Stop all further scoring.
    Set all scoring_breakdown scores to
    whatever you computed but mark stage REJECT.
    Skip instrument_decision block entirely
    (use hard gate short-circuit in that block).

GATE 2: R:R < 1:1.5 at Target 2
  Condition: The ratio (target_2 - entry_mid)
    / (entry_mid - stop_loss) is less than 1.5,
    calculated using YOUR OWN target_2,
    entry_mid, and stop_loss values from the
    trade_parameters and key_levels fields.

  SELF-CHECK BEFORE FINALISING:
    Compute this ratio explicitly:
      rr_t2 = (target_2 - entry_mid) /
               (entry_mid - stop_loss)
    If rr_t2 < 1.5 → GATE 2 fires.
    There is no entry adjustment that fixes
    this retroactively. If your chosen entry,
    SL, and T2 produce rr_t2 < 1.5,
    GATE 2 fires for this setup tonight.
    Do NOT re-derive a different entry_mid
    or SL after the fact to try to pass
    the gate — use the values you committed
    to in key_levels and trade_parameters.

  Action:
    stage = "REJECT"
    hard_gate_triggered = true
    hard_gate_reason = "GATE 2 — RR at Target 2 = {{rr_t2:.2f}} < 1.5 minimum"
    rejection_reason = "RR {{rr_t2:.2f}} at T2 below 1.5 minimum threshold"
    rr_t2 field = the actual computed value (e.g. 1.02, not null)
  Then: Stop all further scoring.
    Complete scoring_breakdown with what
    you scored before hitting the gate.
    Skip instrument_decision block entirely
    (use hard gate short-circuit).

  COMMON GATE 2 MISTAKES TO AVOID:
    ❌ "Gate 2 is technically triggered but
        the setup has strong momentum"
    ❌ "Applying the gate strictly while noting
        the setup has merit from a structural
        standpoint"
    ❌ Re-computing entry_mid to a lower value
       after finding rr_t2 < 1.5 in order to
       pass the gate
    ❌ Setting stage = WATCH when rr_t2 < 1.5
    ❌ Setting hard_gate_triggered = false when
       rr_t2 < 1.5
    ✅ stage = REJECT, hard_gate_triggered = true,
       full stop, no additional commentary
       about setup merit

GATE 3: DTE < 6 trading days
  Scope: OPTIONS INSTRUMENT PATH ONLY.

  GATE 3 DOES NOT:
    Set hard_gate_triggered = true
    Change stage from its score-based value
    Block the FUT instrument path
    Prevent TRADE_READY, WATCH, or ON_RADAR

  GATE 3 ONLY DOES:
    Null out all options_setup fields
    Set theta_cost_check = null
      with note "N/A — Gate 3 (DTE < 6)"
    Set liquidity_check = null
      with note "N/A — Gate 3 (DTE < 6)"
    Force instrument_recommendation to
      evaluate FUT path only

  hard_gate_triggered remains false.
  stage is determined solely by
  adjusted_score thresholds.

GATE 4: Chart directly contradicts direction
  Condition: The price data in Section D
    shows a clear, unambiguous structure
    that directly opposes the Turn 2
    preliminary direction, AND you cannot
    identify any alternative valid thesis
    in the same direction.

  IMPORTANT: Gate 4 requires BOTH conditions:
    (a) Chart contradicts the stated direction
    AND
    (b) No alternative valid direction exists

    If the chart is mixed or uncertain,
    Gate 4 does NOT fire — score it low
    in Dimension 1 instead.

    If you can find an alternative valid
    thesis in the same direction, Gate 4
    does NOT fire — note the revision.

    Gate 4 fires ONLY when the chart is
    unambiguously against the direction
    and no valid bull/bear case exists.

  Action:
    stage = "REJECT"
    hard_gate_triggered = true
    hard_gate_reason = "GATE 4 — Chart structure directly contradicts LONG/SHORT direction with no valid alternative thesis"
    rejection_reason = "Price structure contradicts stated direction — [specific evidence e.g. stock in confirmed downtrend with lower highs/lows while LONG is hypothesised]"
  Then: Stop all further scoring.
    Skip instrument_decision block.

═══════════════════════════════════════════════════
GATE SELF-CHECK — RUN BEFORE WRITING ANY OUTPUT
═══════════════════════════════════════════════════

Before writing a single field in the output
JSON, run this internal checklist:

STEP 1: GATE 1 CHECK
  Can I identify a structural SL level?
  If NO → set stage=REJECT, hard_gate_triggered=true
           hard_gate_reason = "GATE 1..."
           Skip to output.
  If YES → continue.

STEP 2: GATE 4 CHECK
  Does the chart unambiguously contradict
  the direction AND no alternative exists?
  If YES → set stage=REJECT, hard_gate_triggered=true
            hard_gate_reason = "GATE 4..."
            Skip to output.
  If NO → continue.

STEP 3: COMPUTE ALL SCORES
  Score Dimensions 1, 2, 3, 4 fully.

STEP 4: GATE 2 CHECK
  Compute rr_t2 = (target_2 - entry_mid)
                  / (entry_mid - stop_loss)
  Is rr_t2 < 1.5?
  If YES → set stage=REJECT, hard_gate_triggered=true
            hard_gate_reason = "GATE 2 — RR = {{value}} < 1.5"
            Skip instrument_decision.
  If NO → continue.

STEP 5: APPLY SCORE THRESHOLDS
  adjusted_score = raw_total * conviction_multiplier
  If adjusted_score >= 72 → TRADE_READY
  If adjusted_score 52-71 → WATCH
  If adjusted_score 35-51 → ON_RADAR
  If adjusted_score < 35  → REJECT
                            hard_gate_triggered = false
                            hard_gate_reason = null
                            (score-based reject, not a gate)

STEP 6: GATE 3 CHECK (independent of above)
  Is near_month_dte < 6?
  If YES → null out options_setup fields only
            FUT path still evaluated normally
            hard_gate_triggered unchanged

STEP 7: PROCEED TO INSTRUMENT_DECISION
  Only if hard_gate_triggered == false.
  If hard_gate_triggered == true from any
  gate above → use hard gate short-circuit
  in instrument_decision block.

═══════════════════════════════════════════════════
FINAL CONSISTENCY CHECK — BEFORE CLOSING JSON
═══════════════════════════════════════════════════

Before closing the JSON object verify:

  IF hard_gate_triggered == true:
    stage MUST be "REJECT" ✅
    hard_gate_reason MUST be non-null ✅
    rejection_reason MUST be non-null ✅
    instrument_recommendation MUST be "NONE" ✅
    actionable_now MUST be false ✅
    All instrument_decision numeric fields
    MUST be null ✅

  IF hard_gate_triggered == false AND
     stage == "REJECT":
    This is a score-based reject (score < 35)
    hard_gate_reason MUST be null ✅
    rejection_reason should explain the
    low score, not cite a gate ✅

  IF stage != "REJECT":
    hard_gate_triggered MUST be false ✅
    hard_gate_reason MUST be null ✅

  IF rr_t2 < 1.5 AND stage != "REJECT":
    THIS IS AN ERROR — go back to Step 4
    Gate 2 must fire. Fix before output.

  IF rr_t2 >= 1.5 AND hard_gate_triggered == true
     AND hard_gate_reason contains "GATE 2":
    THIS IS AN ERROR — Gate 2 misfired.
    Recheck rr_t2 calculation.

[SECTION G: OUTPUT SPECIFICATION]
Provide your analysis ONLY as a single valid JSON object. Do not include any markdown styling, conversational text, introduction, or wrap it in anything other than the JSON format.

[INSTRUMENT_DECISION COMPUTATION — evaluate ONLY when hard_gate_triggered == false]

ORDERING CONSTRAINT: hard_gate_triggered is determined SOLELY by GATES 1, 2, and 4, and by adjusted_score < 35.
It must be finalized BEFORE this block runs. The outcome of this block — including OI wall failures, GATE 3 firing,
or contract selection — must NEVER feed back into hard_gate_triggered or stage. Those fields are frozen at this point.

HARD GATE SHORT-CIRCUIT: If hard_gate_triggered == true (stage == REJECT), skip all checks below.
Set instrument_decision to: {{ "oi_wall_proximity_check": null, "theta_cost_check": null,
  "liquidity_check": null, "criteria_passed_count": null, "instrument_recommendation": "NONE",
  "margin_efficiency_note": null, "none_reason": "Hard gate triggered: <value from hard_gate_reason>" }}.
Set all numeric and string fields inside options_setup and fut_setup to null (keep the object structures, null the values).
Set actionable_now = false, actionable_note = "Rejected — <hard_gate_reason value>".
Then skip directly to writing the output schema.

SELF-CHECK (run before finalizing output): Verify hard_gate_reason cites only GATE 1, GATE 2, GATE 4, or the
adjusted_score threshold (< 35). If you find hard_gate_reason referencing GATE 3 or an OI wall failure,
correct it: set hard_gate_triggered = false, restore the appropriate stage from the adjusted_score thresholds,
and move the GATE 3 / wall information to instrument_decision.none_reason and actionable_now / actionable_note only.

═══════════════════════════════════════════════════
STEP 0: FUTURES CONTRACT SELECTION AND
        CHART ANALYSIS
═══════════════════════════════════════════════════

─────────────────────────────────────────────
STEP 0A: CONTRACT SELECTION
─────────────────────────────────────────────

Determine which futures contract to use
for ALL subsequent analysis, rules, and
fut_setup population.

Read near_month_dte from Section E.

IF near_month_dte >= 6:
  ACTIVE CONTRACT = near_month

  Use futures_30d_near_month series for:
    Chart analysis (Step 0B)
    All fut_setup levels
    All FUT rule evaluations
    fut_setup.expiry = near month expiry date
    fut_setup.days_to_expiry = near_month_dte
    fut_setup.contract_selected = "near_month"
    fut_setup.contract_selection_note = null

  Do NOT analyse or populate next_month levels.
  next_month data is available but not used.

IF near_month_dte < 6:
  ACTIVE CONTRACT = next_month

  Near month is too close to expiry for a
  2-5 day swing trade — settlement risk,
  theta acceleration, and price discovery
  distortion make near month unsuitable.

  Use futures_30d_next_month series for:
    Chart analysis (Step 0B)
    All fut_setup levels (primary recommendation)
    All FUT rule evaluations
    fut_setup.expiry = next month expiry date
    fut_setup.days_to_expiry = next_month_dte
    fut_setup.contract_selected = "next_month"
    fut_setup.contract_selection_note =
      "Near-month has [near_month_dte] DTE —
       below 6-day minimum for 2-5 day hold.
       Using next-month contract for all
       analysis and recommendation."

  ADDITIONALLY when near_month_dte < 6:
    Provide near_month levels as reference
    in a separate block within
    dimension_2_narrative:

    "NEAR-MONTH REFERENCE (not recommended
     — [near_month_dte] DTE remaining):
     Near-month entry zone: [level]
     Near-month SL: [level]
     Near-month T1: [level]
     Near-month T2: [level]
     Near-month fut_rr_t2: [value]
     Note: Near-month levels shown for
     reference only. All rules evaluated
     and fut_setup populated using
     next-month contract."

    This reference is informational only.
    It does NOT affect fut_setup fields.
    It does NOT affect any rule evaluation.
    All instrument rules use next_month data.

SCOPE OF CONTRACT SELECTION:
  Contract selection affects ONLY:
    fut_setup fields
    FUT rule evaluations (Steps 4A-4D)
    fut_rr_t2 computation (Step 1)

  Contract selection does NOT affect:
    hard_gate_triggered
    stage
    underlying_rr_t2 (always spot-based)
    options_setup fields
    OPTIONS rule evaluations
    scoring_breakdown
    key_levels

─────────────────────────────────────────────
STEP 0B: FUTURES CHART ANALYSIS
─────────────────────────────────────────────

Using the ACTIVE CONTRACT series identified
in Step 0A, read the futures OHLCV data
as an independent price chart.

ACTIVE SERIES:
  near_month_dte >= 6 → futures_30d_near_month
  near_month_dte < 6  → futures_30d_next_month

From this series, identify independently:

FUTURES SUPPORT ZONES:
  Recent swing lows in futures prices
  Where futures price found buyers
  High-volume accumulation days on futures
  OI-confirmed demand zones from futures OI data

FUTURES ENTRY ZONE:
  Where to enter the futures position
  Based on futures price structure
  Typically: recent futures consolidation range
  or pullback to futures support zone

FUTURES STOP LOSS:
  The structural futures price level whose
  breach invalidates the trade thesis
  Based on futures swing lows or breakdown
  below futures support structure

FUTURES TARGET 1:
  Nearest meaningful futures resistance
  Recent futures swing high
  Or futures OI wall that creates ceiling

FUTURES TARGET 2:
  Next meaningful futures resistance beyond T1
  Prior futures distribution zone
  Or next major futures swing high

CRITICAL RULES FOR FUTURES CHART ANALYSIS:

  DO NOT use spot_level + basis arithmetic.
  DO NOT copy key_levels into fut_setup.
  DO NOT reference spot OHLCV for fut_setup levels.

  The futures levels will resemble spot levels
  because both track the same underlying asset.
  But they must come from the futures series
  independently — futures has its own:
    Open gaps that act as support/resistance
    OI-weighted price memory at specific levels
    Rollover artifacts visible as volume spikes
    Basis-driven deviations at key sessions

  Where futures chart diverges from spot chart,
  note explicitly in dimension_2_narrative.

POPULATE fut_setup with futures-derived levels:
  entry_low:  futures structural entry low
  entry_high: futures structural entry high
  entry_mid:  (entry_low + entry_high) / 2
  stop_loss:  futures structural SL level
  target_1:   futures structural T1 level
  target_2:   futures structural T2 level

WHEN near_month_dte < 6:
  Analyse futures_30d_next_month as the
  primary chart for fut_setup levels.

  ALSO analyse futures_30d_near_month
  to produce near-month reference levels
  for the reference block in narrative.

  The next_month series may have fewer
  data points and lower volume in early
  sessions — note this in analysis:
  "Next-month series shows [X] sessions
   of data. Earlier sessions have lower
   volume and wider spreads — placing
   more weight on recent [Y] sessions
   for level identification."

─────────────────────────────────────────────
STEP 0C: FUTURES BASIS ASSESSMENT
─────────────────────────────────────────────

Assess basis from the ACTIVE CONTRACT:

near_month_dte >= 6:
  basis_current = last row of
    futures_30d_near_month.basis
  basis_trend: compare last 5 rows of
    futures_30d_near_month.basis
    EXPANDING = basis moving more negative
                or more positive each day
                in the unfavorable direction
    CONTRACTING = basis moving toward zero
                  or in the favorable direction
    STABLE = no clear trend in last 5 sessions

near_month_dte < 6:
  basis_current = last row of
    futures_30d_next_month.basis
  basis_trend: from last 5 rows of
    futures_30d_next_month.basis

  Note: Next-month basis is typically
  more positive (higher contango) than
  near-month due to additional carry cost.
  This is expected and not a bearish signal.

Populate fut_setup.basis_note:
  "Near/Next-month basis: [value] pts
   ([EXPANDING/CONTRACTING/STABLE] trend).
   [One sentence on what this means for
   the trade direction — positive = longs
   willing to carry, negative = shorts
   carrying or dividend expectation]."

═══════════════════════════════════════════════════
STEP 1: THREE RR COMPUTATIONS
═══════════════════════════════════════════════════

Compute all three explicitly before
running any gates or instrument rules.

COMPUTATION 1: UNDERLYING RR (spot-based)
  Purpose: Gate 2 enforcement only
  Source: key_levels (spot OHLCV)

  underlying_entry_mid =
    (key_levels.support_zone_low
     + key_levels.support_zone_high) / 2

  underlying_rr_t2 =
    (key_levels.resistance_2
     - underlying_entry_mid)
    / (underlying_entry_mid
       - key_levels.stop_loss)

  Used for: Gate 2 only.
  NOT used for FUT or OPTIONS rules.

COMPUTATION 2: FUTURES RR
  Purpose: FUT instrument rule (F4)
  Source: fut_setup levels from Step 0B

  Uses ACTIVE CONTRACT levels only.

  fut_entry_mid = (fut_setup.entry_low
                   + fut_setup.entry_high) / 2

  fut_rr_t2 = (fut_setup.target_2
               - fut_entry_mid)
              / (fut_entry_mid
                 - fut_setup.stop_loss)

  Store as: fut_setup.rr_t2
  Used for: FUT rule F4 only.

COMPUTATION 3: PREMIUM RR
  Purpose: OPTIONS instrument rule (O6)
  Source: options_setup premium levels

  entry_premium_mid =
    (options_setup.entry_premium_low
     + options_setup.entry_premium_high) / 2

  rr_premium_t2 =
    (options_setup.target_2_premium
     - entry_premium_mid)
    / (entry_premium_mid
       - options_setup.sl_premium)

  Store as: options_setup.rr_premium_t2
  Used for: OPTIONS rule O6 only.

Write all three explicitly at end of
dimension_2_narrative:

  "RR COMPUTATIONS:

   Underlying RR (Gate 2 only):
     entry_mid: __  sl: __  t2: __
     underlying_rr_t2: __

   Futures RR ([near/next]-month contract):
     fut_entry_mid: __  fut_sl: __  fut_t2: __
     fut_rr_t2: __

   Premium RR (OPTIONS rule only):
     entry_premium_mid: __  sl_premium: __
     target_2_premium: __
     rr_premium_t2: __"

═══════════════════════════════════════════════════
STEP 2: GATE 2 CHECK (underlying RR)
═══════════════════════════════════════════════════

If underlying_rr_t2 < 1.5:
  stage = "REJECT"
  hard_gate_triggered = true
  hard_gate_reason = "GATE 2 — underlying_rr_t2
    = [value] < 1.5 minimum"
  instrument_recommendation = "NONE"
  instrument_reason = "GATE 2 triggered —
    underlying spot RR [value] < 1.5 minimum.
    No instrument recommended.
    Re-evaluate when entry or target levels
    improve to achieve 1.5+ underlying RR."
  Skip Steps 3-6.

If underlying_rr_t2 >= 1.5:
  Continue to Step 3.

═══════════════════════════════════════════════════
STEP 3: OI WALL PROXIMITY CHECK
═══════════════════════════════════════════════════

Always runs before instrument rules.
If wall check fails → NONE immediately.

walls_checked_side:
  direction = LONG → CE walls
  direction = SHORT → PE walls

Filter Option Chain Last Day to
walls_checked_side type.
Keep strikes STRICTLY between
entry_mid (underlying) and target_2
(underlying) in trade direction.

For each candidate strike:
  Find two immediate chain neighbors
  Compute average OI of neighbors
  Strike is OBSTRUCTING if OI > 3× average

nearest_obstructing_wall_strike =
  closest obstructing strike to entry_mid
  null if none qualifies

pass = true if no strike obstructs
pass = false if any strike obstructs

If pass == false:
  instrument_recommendation = "NONE"
  instrument_reason = "[strike] [CE/PE] wall —
    OI [value] (~[ratio]x neighbor average [calc]).
    OPTIONS: suppresses premium expansion toward
    target_2. FUT: gamma-pinning resistance makes
    [target_2] structurally difficult within
    2-5 days. Neither path recommended.
    Re-evaluate when [level] cleared on volume."
  none_reason = "[strike] wall blocks both paths.
    Revisit when cleared on volume."
  actionable_now = false
  Skip Steps 4 and 5.

If pass == true:
  Continue to Step 4.

═══════════════════════════════════════════════════
STEP 4: FUTURES RULES
═══════════════════════════════════════════════════

All four rules evaluated using ACTIVE CONTRACT
data (near or next month per Step 0A).

If ALL four pass → FUT recommended. Stop.
If ANY fails → evaluate OPTIONS in Step 5.

RULE F1: BASIS NOT NEGATIVE AND EXPANDING
  From fut_setup.basis_note (ACTIVE CONTRACT).

  PASS:
    basis_current >= 0
    OR basis_current < 0 AND CONTRACTING

  FAIL:
    basis_current < 0 AND EXPANDING

RULE F2: PRICE-OI REGIME NOT CONSISTENTLY BEARISH
  From price_oi_regime_last_3.
  These are always computed from near_month
  series regardless of active contract —
  near_month has more liquidity and reflects
  current institutional positioning better
  even when near expiry.

  PASS:
    At least 1 of last 3 valid sessions shows
    LONG_BUILDUP or SHORT_COVERING

  FAIL:
    ALL valid sessions show
    SHORT_BUILDUP or LONG_UNWINDING

RULE F3: OI TREND ALIGNED WITH DIRECTION
  From near_month_oi_trend in Section E.
  Always uses near_month OI trend for
  institutional positioning signal
  regardless of active contract.

  LONG setup:
    PASS: near_month_oi_trend = INCREASING or STABLE
    FAIL: near_month_oi_trend = DECREASING

  SHORT setup:
    PASS: near_month_oi_trend = INCREASING or STABLE
    FAIL: near_month_oi_trend = DECREASING

RULE F4: FUTURES RR >= 2.0
  From fut_setup.rr_t2 (ACTIVE CONTRACT).

  PASS: fut_rr_t2 >= 2.0
  FAIL: fut_rr_t2 < 2.0

IF ALL FOUR PASS:
  instrument_recommendation = "FUT"

  fut_setup fields already populated
  from Step 0B using ACTIVE CONTRACT.

  instrument_reason = "FUT ([near/next]-month,
    [DTE] days) — [basis value] basis ([trend]),
    regime shows [bullish session summary],
    OI trend [value], futures RR [fut_rr_t2].
    All four FUT rules passed.
    [If next_month: 'Near-month [near_month_dte]
    DTE too short — using next-month contract.']
    Clean directional exposure — full delta,
    no theta, no IV drag."

  none_reason = null
  actionable_now = true
  actionable_note = null

IF ANY FAIL:
  Note which rules failed.
  Continue to Step 5.

═══════════════════════════════════════════════════
STEP 5: OPTIONS RULES
═══════════════════════════════════════════════════

Only reached if Step 4 had any failure.
ALL eight must pass for OPTIONS.
ANY failure → NONE.

Note: OPTIONS always uses near-month expiry
if DTE >= 6 (Gate 3 has not fired).
If near_month_dte < 6, Gate 3 has already
nulled options_setup. In this case
OPTIONS path is unavailable — skip to NONE.

IF near_month_dte < 6:
  OPTIONS path unavailable (Gate 3).
  instrument_recommendation = "NONE"
  Document that OPTIONS path is blocked
  by Gate 3 in addition to any FUT
  rule failures.
  Skip remaining OPTIONS rules.
  Go directly to NONE outcome below.

IF near_month_dte >= 6:
  Evaluate all eight rules:

RULE O1: STAGE IS TRADE_READY
  PASS: stage == "TRADE_READY"
  FAIL: any other stage

RULE O2: ADJUSTED SCORE >= 80
  PASS: adjusted_score >= 80
  FAIL: adjusted_score < 80

RULE O3: IV IS GENUINELY LOW
  Use iv_used_for_trade (atm_ce_iv if LONG,
  atm_pe_iv if SHORT) from options_setup.

  If iv_source_used = "chain_iv":
    PASS: iv_used_for_trade < 20
    FAIL: iv_used_for_trade >= 20

  If iv_source_used = "vix_proxy":
    PASS: VIX < 13
    FAIL: VIX >= 13

RULE O4: DTE >= 15 TRADING DAYS
  PASS: days_to_expiry >= 15
  FAIL: days_to_expiry < 15
  (Gate 3 handles < 6 — O4 sets higher bar)

RULE O5: STOCK SUFFICIENTLY VOLATILE
  PASS: atr_pct >= 2.5%
  FAIL: atr_pct < 2.5%

RULE O6: PREMIUM RR >= 3.0
  From options_setup.rr_premium_t2 (Step 1).

  PASS: rr_premium_t2 >= 3.0
  FAIL: rr_premium_t2 < 3.0

  Show calculation:
  "rr_premium_t2 = ([target_2_premium] -
   [entry_premium_mid]) / ([entry_premium_mid]
   - [sl_premium]) = [value]"

RULE O7: LIQUIDITY ADEQUATE
  PASS: ATM OI >= 1000 contracts
        AND estimated bid-ask <= 3% of premium
  FAIL: either condition not met

  Set liquidity_check.pass and note.

RULE O8: THETA COST MANAGEABLE
  Estimate total theta over 5-day max hold:

  daily_theta ≈ iv_used_for_trade
    × entry_premium_mid
    × sqrt(1 / days_to_expiry) / 100

  total_theta_5d = daily_theta × 5

  PASS: total_theta_5d < 20% of entry_premium_mid
  FAIL: total_theta_5d >= 20% of entry_premium_mid

  Set theta_cost_check.pass and note with
  explicit calculation shown.

IF ALL EIGHT PASS:
  instrument_recommendation = "OPTIONS"

  instrument_reason = "OPTIONS — all 8 stringent
    criteria met: TRADE_READY [score] >= 80,
    IV [value] < 20 (LOW), DTE [value] >= 15,
    ATR [value]% >= 2.5, premium RR
    [rr_premium_t2] >= 3.0, liquidity
    [ATM OI] adequate, theta [value]%
    manageable over 5-day hold.
    FUT disqualified: [specific rule and value
    that caused FUT failure in Step 4]."

  none_reason = null
  actionable_now = true
  actionable_note = null

IF ANY FAIL:
  instrument_recommendation = "NONE"

  Build complete failure summary:

  instrument_reason = "NONE —
    FUT disqualified (Step 4):
      [For each failed FUT rule:]
      F[n]: [rule name] — [specific value
      that failed, e.g. 'basis -11.5
      expanding' or 'fut_rr_t2 1.8 < 2.0'].
    OPTIONS disqualified (Step 5):
      [For each failed OPTIONS rule:]
      O[n]: [rule name] — [specific value
      that failed, e.g. 'adjusted score
      68 < 80' or 'rr_premium_t2 2.1 < 3.0'].
    Re-evaluate when: [most actionable
    condition — e.g. 'pullback to [level]
    improves fut_rr_t2 to 2.0+' or
    'price clears [level] to set new
    T2 with better RR']."

  none_reason = "FUT: [first failed rule].
    OPTIONS: [first failed rule].
    Revisit when [most actionable condition]."

  margin_efficiency_note = null
  actionable_now = false
  actionable_note = "Neither FUT nor OPTIONS
    passes instrument criteria — [single
    most important reason]."

═══════════════════════════════════════════════════
STEP 6: CRITERIA_PASSED_COUNT
═══════════════════════════════════════════════════

criteria_passed_count = count of checks
where pass == true (null excluded).

Count:
  oi_wall_proximity_check.pass
  theta_cost_check.pass
  liquidity_check.pass

Range: 0-3. Null if hard_gate_triggered.

═══════════════════════════════════════════════════
INSTRUMENT DECISION SELF-CHECK
═══════════════════════════════════════════════════

Before writing instrument_decision JSON:

IF near_month_dte < 6:
  fut_setup.contract_selected = "next_month" ✓
  fut_setup uses next_month levels ✓
  All FUT rules use next_month data ✓
  Near-month reference block in
    dimension_2_narrative ✓
  options_setup all nulled (Gate 3) ✓
  OPTIONS rules skipped ✓
  If FUT also fails → NONE ✓

IF near_month_dte >= 6:
  fut_setup.contract_selected = "near_month" ✓
  fut_setup uses near_month levels ✓

IF instrument_recommendation == "FUT":
  All four FUT rules passed ✓
  OI wall check passed ✓
  instrument_reason cites contract
    (near or next month) and DTE ✓
  instrument_reason cites all four rules ✓
  none_reason == null ✓
  actionable_now == true ✓

IF instrument_recommendation == "OPTIONS":
  All eight OPTIONS rules passed ✓
  OI wall check passed ✓
  At least one FUT rule failed ✓
  near_month_dte >= 6 ✓
  instrument_reason cites all 8 rules ✓
  instrument_reason states FUT failure ✓
  none_reason == null ✓
  actionable_now == true ✓

IF instrument_recommendation == "NONE":
  instrument_reason NEVER null ✓
  instrument_reason lists specific failed
    rules with actual values ✓
  none_reason populated ✓
  actionable_now == false ✓

FORBIDDEN:
  ❌ instrument_reason == null (ever)
  ❌ none_reason == null when NONE chosen
  ❌ Futures levels = spot + basis
  ❌ FUT recommended when any F rule fails
  ❌ OPTIONS recommended when any O rule fails
  ❌ OPTIONS evaluated when near_month_dte < 6
  ❌ FUT recommended when OI wall fails
  ❌ OPTIONS recommended when OI wall fails
  ❌ Next-month contract selected when
     near_month_dte >= 6
  ❌ Near-month contract used when
     near_month_dte < 6

[SETUP_DELTA COMPUTATION — compare against previous_setups from Section B]
Identify the most recent entry in previous_setups (index 0, highest setup_date). If previous_setups is empty or null, set all fields to null and direction_changed = false.
Otherwise:
- previous_direction = previous_setups[0].direction
- previous_score    = previous_setups[0].conviction_score  (may be null for legacy records)
- direction_changed = true if current direction != previous_direction; false otherwise
- score_delta       = (current conviction_score) − previous_score; set null if previous_score is null. When direction_changed == true this is a magnitude comparison across two different directional theses — a positive delta does not mean the thesis improved, only that today's score is numerically higher. Reflect this distinction in justification.
- justification: Required only when direction_changed == true. Cite the specific new data visible in this session's inputs that was absent or contradicted the prior direction (e.g., "PCR contracted from 1.8 to 0.5; CE wall at 24500 present in prior chain is now absent from today's chain"). If you cannot identify specific new evidence from the data, write exactly: "NONE — treat with caution". Write null when direction_changed == false.

[PRICE_OI_REGIME COMPUTATION — uses futures_30d_near_month from Section E: F&O DATA]
Requires at least 4 rows in futures_30d_near_month to produce 3 day-over-day comparisons. If futures_30d_near_month has fewer than 4 rows or futures data is unavailable, set price_oi_regime_last_3 = [].
Otherwise, take the last 4 rows (rows[-4:], ascending by date) and classify the last 3 using the `oi` field (near-month OI):
  price_change_pct = (futures_price[i] − futures_price[i−1]) / futures_price[i−1] × 100  (round to 2 dp)
  oi_change_pct:   if oi[i−1] is zero or null, set oi_change_pct = null and regime = "UNAVAILABLE" for that day and skip classification below; otherwise = (oi[i] − oi[i−1]) / oi[i−1] × 100  (round to 2 dp)

Classification rule:
  price >= prev AND oi >  prev  →  LONG_BUILDUP    (fresh longs added, bullish)
  price >= prev AND oi <= prev  →  SHORT_COVERING  (shorts exiting on price rise, fading strength)
  price <  prev AND oi >  prev  →  SHORT_BUILDUP   (fresh shorts added, bearish)
  price <  prev AND oi <= prev  →  LONG_UNWINDING  (longs exiting on price fall, bearish)
  NOTE: zero price change is treated as non-negative (groups with LONG_BUILDUP or SHORT_COVERING); zero OI change is treated as non-positive (groups with SHORT_COVERING or LONG_UNWINDING). Deliberate tie-break, not an oversight.

Output the 3 classified sessions most-recent-first in price_oi_regime_last_3.

Your JSON output must match this exact schema:
{{
  "symbol": "{symbol}",
  "direction": "LONG | SHORT",
  "stage": "TRADE_READY | WATCH | ON_RADAR | REJECT",
  "conviction_score": <raw_total_score_0_to_100>,
  "adjusted_score": <adjusted_score_raw_times_multiplier>,
  "conviction_multiplier_applied": {sec7["conviction_multiplier"]},
  "setup_summary": {{
    "pattern_name": "<Pattern name, e.g. Bull Flag or None>",
    "pattern_status": "COMPLETE | FORMING | NONE",
    "key_candle": "<Key candle description, e.g. Hammer or Inside Bar>",
    "key_candle_location": "AT_SUPPORT | AT_RESISTANCE | MID_RANGE | NONE",
    "key_candle_significance": "HIGH | MEDIUM | LOW | NONE"
  }},
  "setup_delta_vs_previous": {{
    "previous_direction": "LONG | SHORT | null",
    "previous_score": <number_or_null>,
    "score_delta": <number_or_null — current conviction_score minus previous; magnitude only when direction_changed == true>,
    "direction_changed": <true_or_false>,
    "justification": "<specific new evidence justifying direction flip; 'NONE — treat with caution' if none; null if direction_changed == false>"
  }},
  "key_levels": {{
    "support_zone_low": <number>,
    "support_zone_high": <number>,
    "support_basis": "<basis for support, e.g. EMA20 + Swing Low>",
    "resistance_1": <number>,
    "resistance_1_basis": "<basis, e.g. prior swing high date>",
    "resistance_2": <number>,
    "resistance_2_basis": "<basis>",
    "stop_loss": <number>,
    "stop_loss_basis": "<basis>"
  }},
  "trade_parameters": {{
    "entry_low": <number>,
    "entry_high": <number>,
    "entry_mid": <number>,
    "target_1": <number>,
    "target_2": <number>,
    "rr_t1": <number_ratio>,
    "rr_t2": <number_ratio>
  }},
  "options_setup": {{
    "strike": <number_or_null>,
    "option_type": "CE | PE | null",
    "expiry": "<YYYY-MM-DD_or_null>",
    "days_to_expiry": <number_or_null>,
    "entry_premium_low": <number_or_null>,
    "entry_premium_high": <number_or_null>,
    "sl_pct": <number_or_null>,
    "sl_premium": <number_or_null>,
    "target_1_premium": <number_or_null>,
    "target_2_premium": <number_or_null>,
    "atm_ce_iv": <number_or_null — CE IV at ATM strike; null unless iv_source_used = "chain_iv">,
    "atm_pe_iv": <number_or_null — PE IV at ATM strike; null unless iv_source_used = "chain_iv">,
    "atm_iv_skew": <number_or_null — atm_ce_iv minus atm_pe_iv; null if either is null>,
    "iv_used_for_trade": <number_or_null — atm_ce_iv when LONG, atm_pe_iv when SHORT; null when chain_iv unavailable>,
    "iv_note": "<State atm_ce_iv and atm_pe_iv, which was used for this trade (iv_used_for_trade) and why, IV level assessment (LOW/MEDIUM/HIGH), source used, and flag the skew if unusually wide (>5 pts) with both possible explanations (market skew vs. stale quote)>",
    "iv_source_used": "chain_iv | summary_atm_iv | vix_proxy"
  }},
  "fut_setup": {{
    "expiry": "<YYYY-MM-DD_or_null — expiry of the selected contract (near or next month per contract_selected)>",
    "days_to_expiry": <number_or_null — DTE of the selected contract>,
    "entry_low": <number_or_null>,
    "entry_high": <number_or_null>,
    "sl_pct": <number_or_null — percentage distance from entry_mid to stop_loss>,
    "stop_loss": <number_or_null>,
    "target_1": <number_or_null>,
    "target_2": <number_or_null>,
    "basis_note": "<basis value and trend for the selected contract; positive = contango/bullish carry, negative = backwardation>",
    "contract_selected": "near_month | next_month",
    "contract_selection_note": "<one-line reason for choosing next-month; null when near_month is used by default>"
  }},
  "price_oi_regime_last_3": [
    {{
      "date": "<YYYY-MM-DD — most recent session first>",
      "price_change_pct": <number — rounded to 2 dp>,
      "oi_change_pct": <number_or_null — null when prior-day OI is zero or missing>,
      "regime": "LONG_BUILDUP | SHORT_BUILDUP | LONG_UNWINDING | SHORT_COVERING | UNAVAILABLE"
    }}
  ],
  "instrument_decision": {{
    "oi_wall_proximity_check": {{
      "walls_checked_side": "<CE if direction=LONG, PE if direction=SHORT — derived from direction, not chosen freely>",
      "walls_between_entry_and_target2": [ {{ "strike": <number>, "oi": <number> }} ],
      "nearest_obstructing_wall_strike": <number_or_null>,
      "nearest_obstructing_wall_oi": <number_or_null>,
      "wall_oi_vs_neighbors_ratio": <number_or_null>,
      "pass": <true_or_false>,
      "note": "<one line: obstructing wall strike and OI vs neighbors ratio and impact on premium expansion or underlying pinning; or 'no obstructing wall between entry and T2'>"
    }},
    "theta_cost_check": {{
      "pass": <true_or_false_or_null>,
      "note": "<theta decay assessment using iv_used_for_trade (the IV of the side actually being traded — CE for LONG, PE for SHORT) vs premium paid over the 2–5 day holding period; or 'N/A — Gate 3 (DTE < 6)' if Gate 3 triggered>"
    }},
    "liquidity_check": {{
      "pass": <true_or_false_or_null>,
      "note": "<ATM OI and estimated bid-ask spread assessment; or 'N/A — FUT path' if Gate 3 triggered>"
    }},
    "criteria_passed_count": <0_to_3 — count_of_checks_where_pass_is_true_excluding_nulls>,
    "instrument_recommendation": "OPTIONS | FUT | NONE",
    "margin_efficiency_note": "<one concise observation on the capital or margin efficiency advantage of the recommended instrument; null if instrument_recommendation = NONE>",
    "none_reason": "<concise explanation of why neither OPTIONS nor FUT is recommended; null if instrument_recommendation != NONE>"
  }},
  "hard_gate_triggered": <true_or_false>,
  "hard_gate_reason": "<name of hard gate triggered or null>",
  "scoring_breakdown": {{
    "dimension_1": {{ "score": <0_to_55>, "max": 55, "pct": <percentage> }},
    "dimension_2": {{ "score": <0_to_25>, "max": 25, "pct": <percentage> }},
    "dimension_3": {{ "score": <0_to_15>, "max": 15, "pct": <percentage> }},
    "dimension_4": {{ "score": <0_to_5>, "max": 5, "pct": <percentage> }},
    "raw_total": <raw_total_score_0_to_100>,
    "adjusted_total": <adjusted_score_raw_times_multiplier>
  }},
  "dimension_1_narrative": "<Meticulous detail. Assess S/R confluence, completed/forming patterns with dates/prices, candle body/wicks close position, candlestick patterns with location context, RSI divergence check, and volume trend. Every number/date must be chart-verifiable. Do not generalize.>",
  "dimension_2_narrative": "<Describe SL structural invalidation logic with ATR validation. Detail T1 and T2 levels and R:R parameters. Describe entry zone confluence basis.>",
  "dimension_3_narrative": "<Connect Nifty regime stance and bias to this trade's execution. Assess sector tailwind/headwind and stock relative strength/performance vs sector.>",
  "dimension_4_narrative": "<REQUIRED OPENING: name each of the last 3 sessions' Price-OI regimes by label with supporting numbers, e.g. 'Jul-16→17: LONG_BUILDUP (price +0.7%, OI +0.9%); Jul-15→16: SHORT_COVERING (price +0.3%, OI −1.2%); Jul-14→15: SHORT_BUILDUP (price −0.5%, OI +2.1%)'. Then assess basis current/trend, PCR contrarian reading with any thin OI warning, and DTE/rollover phase significance. When fut_setup.contract_selected = 'next_month', state the contract switch explicitly before the basis discussion (e.g. 'Using next-month Aug-28 futures contract — near-month Jul-31 has only 4 DTE vs 2–5 day hold requirement') and base the basis/DTE commentary on the next-month contract's values. If instrument_decision.oi_wall_proximity_check.pass = false, explicitly name the obstructing wall strike, its OI vs neighbors ratio, and explain the impact on target_2 achievability (premium compression for options; gamma-pinning resistance for futures/spot) — state this even when instrument_recommendation = FUT.>",
  "mentor_notes": "<Educational swing-trading takeaways taught by this specific setup. Why does it work and what visual cues verify it on the chart.>",
  "why_could_be_wrong": "<Three highly specific bearish scenarios with exact invalidation price levels where the trade goes wrong (e.g. 'If closes below 1828 on high volume'). No generic disclaimers.>",
  "key_thing_to_watch": "<Single, most critical actionable observation for the morning market open (e.g. entry boundary trigger, gap opens).>",
  "spot_price": <underlying_close_price_for_session_date_as_number>,
  "rejection_reason": "<Detail reasons for REJECT or null>",
  "actionable_now": <true_or_false — false when instrument_decision.instrument_recommendation == "NONE", otherwise true>,
  "actionable_note": "<one line explaining why not actionable now; null if actionable_now is true>"
}}
"""
    return prompt


def _build_recommended_trade(analysis: dict, lot_size: int) -> dict:
    """Build a normalised recommended_trade object from the validated analysis."""
    instr_dec = analysis.get("instrument_decision") or {}
    instrument = instr_dec.get("instrument_recommendation") or analysis.get("instrument_recommendation") or "NONE"
    levels = analysis.get("key_levels") or {}

    if instrument == "OPTIONS":
        setup = analysis.get("options_setup") or {}
        entry_mid = setup.get("entry_premium_mid")
        sl = setup.get("sl_premium")
        if entry_mid and sl:
            lots = analysis.get("lots", 1) or 1
            risk_per_lot = (float(entry_mid) - float(sl)) * lot_size
            return {
                "instrument": "OPTIONS",
                "action": "BUY",
                "strike": setup.get("strike"),
                "option_type": setup.get("option_type"),
                "expiry": setup.get("expiry"),
                "entry_premium_low": setup.get("entry_premium_low"),
                "entry_premium_high": setup.get("entry_premium_high"),
                "entry_premium_mid": entry_mid,
                "sl_premium": sl,
                "sl_pct": setup.get("sl_pct"),
                "target_1_premium": setup.get("target_1_premium"),
                "target_2_premium": setup.get("target_2_premium"),
                "rr_premium_t1": setup.get("rr_premium_t1"),
                "rr_premium_t2": setup.get("rr_premium_t2"),
                "underlying_entry_low": levels.get("support_zone_low"),
                "underlying_entry_high": levels.get("support_zone_high"),
                "underlying_sl": levels.get("stop_loss"),
                "underlying_t1": levels.get("resistance_1"),
                "underlying_t2": levels.get("resistance_2"),
                "lot_size": lot_size,
                "lots": lots,
                "capital_required": round(float(entry_mid) * lot_size * lots, 0),
                "max_loss_inr": round(risk_per_lot * lots, 0),
            }

    elif instrument == "FUT":
        setup = analysis.get("fut_setup") or {}
        entry_mid = setup.get("entry_mid")
        sl = setup.get("stop_loss")
        if entry_mid and sl:
            lots = analysis.get("lots", 1) or 1
            risk_per_lot = abs(float(entry_mid) - float(sl)) * lot_size
            return {
                "instrument": "FUT",
                "action": "BUY" if analysis.get("direction") == "LONG" else "SELL",
                "expiry": setup.get("expiry"),
                "entry_low": setup.get("entry_low"),
                "entry_high": setup.get("entry_high"),
                "entry_mid": entry_mid,
                "stop_loss": sl,
                "sl_pct": setup.get("sl_pct"),
                "target_1": setup.get("target_1"),
                "target_2": setup.get("target_2"),
                "rr_t1": setup.get("rr_t1"),
                "rr_t2": setup.get("rr_t2"),
                "lot_size": lot_size,
                "lots": lots,
                "margin_required": None,
                "max_loss_inr": round(risk_per_lot * lots, 0),
            }

    return {
        "instrument": "NONE",
        "reason": instr_dec.get("instrument_reason") or instr_dec.get("none_reason") or instr_dec.get("margin_efficiency_note"),
        "reference_entry_low": levels.get("support_zone_low"),
        "reference_entry_high": levels.get("support_zone_high"),
        "reference_sl": levels.get("stop_loss"),
        "reference_t1": levels.get("resistance_1"),
        "reference_t2": levels.get("resistance_2"),
    }


def _validate_position_sizing_turn3(analysis: dict, config: dict) -> dict:
    """
    Validates position sizing for Turn 3 deep analysis (supporting Options vs Futures recommendation).
    """
    capital = float(config.get("claude_capital_inr", 500000.0))
    max_risk_pct = 0.025
    max_risk_inr = capital * max_risk_pct
    
    symbol = analysis.get("symbol")
    # instrument_recommendation now lives inside instrument_decision; fall back to flat key for backward compat
    instr_dec = analysis.get("instrument_decision") or {}
    rec = instr_dec.get("instrument_recommendation") or analysis.get("instrument_recommendation") or "NONE"
    
    from database.queries import get_all_lot_sizes
    lot_sizes = get_all_lot_sizes()
    lot_size = lot_sizes.get(symbol, 1)
    
    analysis["lot_size"] = lot_size
    
    # Flatten basic setup fields based on recommendation
    if rec == "OPTIONS":
        opt = analysis.get("options_setup", {})
        if not opt:
            opt = {}
            analysis["options_setup"] = opt
            
        opt["days_to_expiry"] = analysis.get("days_to_expiry") or opt.get("days_to_expiry")
        
        entry_low = opt.get("entry_premium_low")
        entry_high = opt.get("entry_premium_high")
        stop_loss = opt.get("sl_premium")
        target_1 = opt.get("target_1_premium")
        target_2 = opt.get("target_2_premium")
        
        # Map to flat keys for DB compatibility
        analysis["strike"] = opt.get("strike")
        analysis["option_type"] = opt.get("option_type")
        analysis["expiry_date"] = opt.get("expiry")
        analysis["entry_premium_low"] = entry_low
        analysis["entry_premium_high"] = entry_high
        analysis["stop_loss_premium"] = stop_loss
        analysis["target_1_premium"] = target_1
        analysis["target_2_premium"] = target_2
        
        if all(x is not None for x in [entry_low, entry_high, stop_loss, target_2]):
            entry_mid = (float(entry_low) + float(entry_high)) / 2.0
            risk_per_lot = (entry_mid - float(stop_loss)) * lot_size
            
            if risk_per_lot <= 0 or risk_per_lot > max_risk_inr:
                lots = 0
                actual_risk = 0.0
                actual_rr = 0.0
            else:
                lots = max(1, int(max_risk_inr / risk_per_lot))
                actual_risk = risk_per_lot * lots
                actual_rr = (float(target_2) - entry_mid) / (entry_mid - float(stop_loss))
                
            analysis["lots"] = lots
            analysis["max_risk_inr"] = round(actual_risk, 0)
            analysis["risk_pct_capital"] = round((actual_risk / capital) * 100, 2)
            analysis["risk_reward"] = round(actual_rr, 2)
            
            # Write back to options_setup
            opt["lots"] = lots
            opt["risk_inr"] = round(actual_risk, 0)
            opt["risk_pct_capital"] = round((actual_risk / capital) * 100, 2)
            opt["entry_premium_mid"] = round(entry_mid, 2)
            opt["rr_premium_t1"] = round((float(target_1) - entry_mid) / (entry_mid - float(stop_loss)), 2) if target_1 else 0.0
            opt["rr_premium_t2"] = round(actual_rr, 2)
            
    elif rec == "FUT":
        fut = analysis.get("fut_setup", {})
        if not fut:
            fut = {}
            analysis["fut_setup"] = fut
            
        entry_low = fut.get("entry_low")
        entry_high = fut.get("entry_high")
        stop_loss = fut.get("stop_loss")
        target_1 = fut.get("target_1")
        target_2 = fut.get("target_2")
        
        # Map to flat keys for DB compatibility
        analysis["strike"] = None
        analysis["option_type"] = "FUT"
        analysis["expiry_date"] = None
        analysis["entry_premium_low"] = entry_low
        analysis["entry_premium_high"] = entry_high
        analysis["stop_loss_premium"] = stop_loss
        analysis["target_1_premium"] = target_1
        analysis["target_2_premium"] = target_2
        
        if all(x is not None for x in [entry_low, entry_high, stop_loss, target_2]):
            entry_mid = (float(entry_low) + float(entry_high)) / 2.0
            risk_per_lot = (entry_mid - float(stop_loss)) * lot_size
            
            if risk_per_lot <= 0 or risk_per_lot > max_risk_inr:
                lots = 0
                actual_risk = 0.0
                actual_rr = 0.0
            else:
                lots = max(1, int(max_risk_inr / risk_per_lot))
                actual_risk = risk_per_lot * lots
                actual_rr = (float(target_2) - entry_mid) / (entry_mid - float(stop_loss))
                
            analysis["lots"] = lots
            analysis["max_risk_inr"] = round(actual_risk, 0)
            analysis["risk_pct_capital"] = round((actual_risk / capital) * 100, 2)
            analysis["risk_reward"] = round(actual_rr, 2)
            
            # Write back to fut_setup
            fut["lots"] = lots
            fut["lot_size"] = lot_size
            fut["risk_inr"] = round(actual_risk, 0)
            fut["risk_pct_capital"] = round((actual_risk / capital) * 100, 2)
            fut["entry_mid"] = round(entry_mid, 2)
            fut["rr_t1"] = round((float(target_1) - entry_mid) / (entry_mid - float(stop_loss)), 2) if target_1 else 0.0
            fut["rr_t2"] = round(actual_rr, 2)
            
    else:
        # None recommended or skip
        analysis["lots"] = 0
        analysis["max_risk_inr"] = 0.0
        analysis["risk_pct_capital"] = 0.0
        analysis["risk_reward"] = 0.0

    # Lift nested instrument_decision fields to flat keys for DB / frontend backward compat
    analysis["instrument_recommendation"] = rec
    analysis.setdefault("instrument_reason", instr_dec.get("margin_efficiency_note", ""))

    # Gate 2 Python validation — catch any RR < 1.5 that Claude missed
    if rec == "OPTIONS":
        rr_t2_check = (analysis.get("options_setup") or {}).get("rr_premium_t2")
    elif rec == "FUT":
        rr_t2_check = (analysis.get("fut_setup") or {}).get("rr_t2")
    else:
        rr_t2_check = None

    if rr_t2_check is not None and rr_t2_check < 1.5:
        if not analysis.get("hard_gate_triggered"):
            logger.warning(
                f"Gate 2 missed by Claude for {symbol} — rr_t2={rr_t2_check} — forcing REJECT in Python"
            )
            analysis["stage"] = "REJECT"
            analysis["hard_gate_triggered"] = True
            analysis["hard_gate_reason"] = (
                f"GATE 2 — rr_t2 = {rr_t2_check:.2f} < 1.5 (caught by Python validation)"
            )

    analysis["recommended_trade"] = _build_recommended_trade(analysis, lot_size)

    return analysis


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
    max_tokens: int = 4000,
    turn1_result: dict = None,
    turn2_result: list[dict] = None,
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
        if not turn1_result or not turn2_result:
            from database.queries import get_claude_turn
            if not turn1_result:
                t1_row = get_claude_turn(session_id, 1)
                turn1_result = json.loads(t1_row["output_text"]) if t1_row else {}
            if not turn2_result:
                t2_row = get_claude_turn(session_id, 2)
                if t2_row:
                    turn2_res_raw = json.loads(t2_row["output_text"])
                    if isinstance(turn2_res_raw, dict):
                        turn2_result = turn2_res_raw.get("stock_assessments", []) or turn2_res_raw.get("stocks", []) or []
                    else:
                        turn2_result = turn2_res_raw
                else:
                    turn2_result = []

        stock_pkg = _build_turn3_data(symbol, session_date, turn1_result, turn2_result)
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
        prev_setups = stock_pkg.get("section1", {}).get("previous_setups", [])
        prev_score = prev_setups[0].get("conviction_score", "??") if prev_setups else "??"
        prev_type  = prev_setups[0].get("setup_type", "??") if prev_setups else "??"
        custom_instructions = (
            f"\n\nCONTEXT: This stock has been on Watch for {days_in} days. "
            f"Previous conviction: {prev_score}. Previous setup: {prev_type}. "
            "Re-evaluate with today's data. Has the setup confirmed or broken down?"
        )

    prompt = _build_turn3_prompt(stock_pkg)
    if custom_instructions:
        prompt += custom_instructions

    try:
        analysis, in_tok, out_tok = call_claude_deep(client, prompt, max_tokens=max_tokens)
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
    analysis = _validate_position_sizing_turn3(analysis, config)

    save_claude_turn(session_id, turn_num, "deep_analysis", symbol,
                     in_tok, out_tok, prompt, json.dumps(analysis))

    # Calculate cost per turn
    turn_cost = round(in_tok / 1_000_000 * 3.00 + out_tok / 1_000_000 * 15.00, 6)

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
    # if is_re:
    #     if stage == "TRADE_READY" or (conviction >= 75 and stage != "SKIP"):
    #         update_watchlist_staging(symbol, {"current_stage": "TRADE_READY", "updated_at": datetime.now(IST).isoformat()})
    #         send_loud(f"🚀 <b>{symbol} graduated</b>\nWatch → <b>Trade Ready</b> (Conviction: {conviction})")
    #         logger.info("Watchlist graduation: %s", symbol)
    #     elif conviction >= 55:
    #         # Maintain in watch, increment days
    #         new_days = days_in + 1
    #         if new_days > 10:
    #             update_watchlist_staging(symbol, {"current_stage": "EXPIRED", "updated_at": datetime.now(IST).isoformat()})
    #             send_silent(f"⏰ <b>{symbol} Watch expired</b>\nNo trigger in 10 days. Moved out of Watch.")
    #         else:
    #             update_watchlist_staging(symbol, {"days_in_stage": new_days, "updated_at": datetime.now(IST).isoformat()})
    #             logger.info("Watchlist maintenance: %s (Day %d)", symbol, new_days)
    #     else:
    #         # Conviction dropped
    #         update_watchlist_staging(symbol, {"current_stage": "DEGRADED", "updated_at": datetime.now(IST).isoformat()})
    #         send_silent(f"📉 <b>{symbol} removed from Watch</b>\nSetup broke (Conviction dropped to {conviction}).")
    #         logger.info("Watchlist degradation: %s", symbol)
    # else:
    #     # New discovery — if it's WATCH or TRADE_READY, add to staging
    #     if stage in ("WATCH", "TRADE_READY", "ON_RADAR"):
    #         upsert_watchlist_staging({
    #             "symbol":            symbol,
    #             "current_stage":     stage,
    #             "direction_bias":    analysis.get("direction"),
    #             "days_in_stage":     0,
    #             "first_flagged_date": str(session_date),
    #             "updated_at":        datetime.now(IST).isoformat(),
    #         })
    #         logger.info("New watchlist discovery synced: %s stage=%s", symbol, stage)

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
                "instrument":       analysis.get("instrument_recommendation"),
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

                # Full nested analysis objects (JSONB)
                "options_setup":       analysis.get("options_setup"),
                "fut_setup":           analysis.get("fut_setup"),
                "key_levels":          analysis.get("key_levels"),
                "instrument_decision": analysis.get("instrument_decision"),
                "recommended_trade":   analysis.get("recommended_trade"),
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
    context_bundle:   dict,
    level1_passed:    list[str],
    session_id:       str,
    mandatory_stocks: list[str] | None = None,
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
    turn1_result, t1_cost = _run_turn1(client, session_id, session_date, config)
    turn_costs   = [t1_cost]
    total_input  = t1_cost["input_tokens"]
    total_output = t1_cost["output_tokens"]
    messages     = []  # conversation history for Turn 2 when un-commented

    # Build regime_result for send_phase1_complete and Turn 2 system prompt
    regime_result = {
        "regime":            (
            f"{turn1_result.get('market_trend', 'SIDEWAYS')}_"
            f"{turn1_result.get('market_volatility', 'NORMAL')}_"
            f"{turn1_result.get('market_structure', 'WIDE')}"
        ),
        "market_trend":      turn1_result.get("market_trend", "SIDEWAYS"),
        "market_volatility": turn1_result.get("market_volatility", "NORMAL"),
        "market_structure":  turn1_result.get("market_structure", "WIDE"),
        "execution_bias":    turn1_result.get("execution_bias", "NEUTRAL"),
        "fii_dii_stance":    turn1_result.get("fii_dii_stance", "NEUTRAL"),
        "nifty_close":       (turn1_result.get("index_key_levels") or {}).get("current"),
        "vix":               (turn1_result.get("vix_assessment") or {}).get("current"),
        "sector_weights":    turn1_result.get("sector_pictures", {}),
        "guidance":          turn1_result.get("guidance", {}),
        "index_key_levels":  turn1_result.get("index_key_levels", {}),
        "session_narrative": turn1_result.get("session_narrative", ""),
        "risk_flags":        turn1_result.get("risk_flags", []),
    }



    # Re-build system prompt using the dynamically generated regime context
    context_bundle["regime"] = regime_result
    system_text = build_system_prompt(context_bundle)

    # Token ceiling check before Turn 2
    if total_input + total_output + 25_000 >= _TOKEN_CEILING:
        raise RuntimeError(
            f"Token ceiling ({_TOKEN_CEILING}) would be exceeded entering Turn 2 "
            f"({total_input + total_output} tokens used so far)."
        )


    cost1_usd = round(
        total_input  / 1_000_000 * 3.00 +
        total_output / 1_000_000 * 15.00,
        6,
        )

    # ── Turn 2: Pre-scan ──────────────────────────────────────────────────────
    final_forward_list, turn2_results, t2_cost = _run_turn2(
        client=client,
        session_id=session_id,
        session_date=session_date,
        turn1_result=turn1_result,
        mandatory_stocks=list(mandatory_stocks or []),
    )
    turn_costs.append(t2_cost)
    total_input += t2_cost["input_tokens"]
    total_output += t2_cost["output_tokens"]

    # Calculate cost of pre-scan run
    cost2_usd = round(
        t2_cost["input_tokens"]  / 1_000_000 * 3.00 +
        t2_cost["output_tokens"] / 1_000_000 * 15.00,
        6,
    )

    # Combine pre-scan forwarded stocks with priority watchlist stocks
    final_queue = final_forward_list[:]

    deep_results: list[dict] = []
    trade_ready_list: list[dict] = []

    # Pack the index dimensions explicitly for single stock runs
    index_ctx = {
        "regime":            regime_result.get("regime")      if regime_result else "UNKNOWN",
        "market_trend":      regime_result.get("market_trend") if regime_result else "UNKNOWN",
        "market_volatility":  regime_result.get("market_volatility") if regime_result else "UNKNOWN",
        "market_structure":   regime_result.get("market_structure") if regime_result else "UNKNOWN",
        "execution_bias":     regime_result.get("execution_bias") if regime_result else "UNKNOWN",
        "fii_dii_stance":     regime_result.get("fii_dii_stance") if regime_result else "UNKNOWN",
        "nifty_close":       regime_result.get("nifty_close") if regime_result else None,
        "vix":               regime_result.get("vix")         if regime_result else None,
        "ema20":             regime_result.get("ema20")        if regime_result else None,
        "ema50":             regime_result.get("ema50")        if regime_result else None,
        "ret20d_pct":        regime_result.get("ret20d")       if regime_result else None,
    }

    total_turn3_input = 0
    total_turn3_output = 0

    for i, prescan_stock in enumerate(final_queue):
        symbol    = prescan_stock.get("symbol", "")
        direction = prescan_stock.get("direction", "AUTO")
        is_re     = prescan_stock.get("is_watchlist_reanalysis", False)
        days_in   = prescan_stock.get("days_in_stage", 0)
        turn_num  = 3 + i

        if not symbol:
            continue

        deep_res, deep_cost = run_turn_deep_analysis(
            client=client,
            session_id=session_id,
            session_date=session_date,
            symbol=symbol,
            direction=direction,
            is_re=is_re,
            days_in=days_in,
            index_ctx=index_ctx,
            config=config,
            turn_num=turn_num,
            trade_ready_list=trade_ready_list,
            turn1_result=turn1_result,
            turn2_result=turn2_results,
            max_tokens=16000,
        )
        deep_results.append(deep_res)
        turn_costs.append(deep_cost)

        total_turn3_input += deep_cost["input_tokens"]
        total_turn3_output += deep_cost["output_tokens"]

        total_input += deep_cost["input_tokens"]
        total_output += deep_cost["output_tokens"]

    cost_usd = round(
        total_input  / 1_000_000 * 3.00 +
        total_output / 1_000_000 * 15.00,
        6,
    )

    cost3_usd = round(
        total_turn3_input  / 1_000_000 * 3.00 +
        total_turn3_output / 1_000_000 * 15.00,
        6,
    )

    trade_ready         = sum(1 for d in deep_results if d.get("stage") == "TRADE_READY")
    trade_ready_blocked = sum(1 for d in deep_results if d.get("stage") == "TRADE_READY" and d.get("actionable_now") is False)
    watch               = sum(1 for d in deep_results if d.get("stage") == "WATCH")
    on_radar            = sum(1 for d in deep_results if d.get("stage") == "ON_RADAR")
    skipped             = sum(1 for d in deep_results if d.get("stage") == "SKIP")
    prescan_fwd         = sum(1 for s in turn2_results if s.get("forward_to_deep"))

    try:
        from new_notifications.telegram import send_deep_analysis_complete
        send_deep_analysis_complete(str(session_date), trade_ready, watch, on_radar, skipped, cost3_usd, trade_ready_blocked)
    except Exception as _exc:
        logger.warning("Deep analysis complete notification failed: %s", _exc)



    prescan_fwd = len(final_forward_list)

    # Update session status in DB
    try:
        update_analysis_session(session_id, {
            "claude_tokens_input":  total_input,
            "claude_tokens_output": total_output,
            "claude_cost_usd":      cost_usd,
            "status":               "ANALYSIS_COMPLETE",
            "market_regime":        regime_result.get("regime"),
            "forward_list":         final_forward_list,
            "stage_statuses": {
                "claude_turn1":          "COMPLETE",
                "claude_turn2":          "COMPLETE",
                "deep_analysis":         "COMPLETE",
                "prescan_total":         len(turn2_results),
                "prescan_forwarded":     prescan_fwd,
            }
        })
    except Exception as exc:
        logger.warning("Failed to update session status in DB: %s", exc)

    return {
        "turn1_result":        turn1_result,
        "turn2_results":       turn2_results,
        "deep_results":        deep_results,
        "forward_list":        final_forward_list,
        "total_input_tokens":  total_input,
        "total_output_tokens": total_output,
        "cost_usd":            cost_usd,
        "cost1_usd":            cost1_usd,
        "cost2_usd":            cost2_usd,
        "cost3_usd":            cost3_usd,
        "regime_result":       regime_result,
    }
