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
                    
    futures_data = {
        "futures_available": futures_available,
        "futures_30d": futures_30d,
        "basis_current": basis_current,
        "basis_trend": basis_trend,
        "rollover_phase": rollover_phase,
        "days_to_expiry": None,
        "near_month_oi_trend": near_month_oi_trend
    }
    
    # ── Section 5: Options Data ───────────────────────────────────────────────
    options_available = False
    pcr_near = None
    max_pain = None
    atm_strike = None
    iv_available = False
    iv_atm = None
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
            if ce_atm and ce_atm[0].get("implied_volatility") is not None:
                iv_atm = float(ce_atm[0]["implied_volatility"])
                iv_available = True
            else:
                iv_available = any(r.get("implied_volatility") is not None for r in options)
                
            if not iv_available:
                options_note = "IV unavailable — Kite fallback used. OI data available but IV null. Use VIX as vol proxy."
                
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
        "iv_available": iv_available,
        "iv_atm": iv_atm,
        "options_note": options_note,
        "ce_walls": ce_walls,
        "pe_walls": pe_walls
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
    futures_compact = json.dumps(sec4["futures_30d"], separators=(',', ':')) if sec4["futures_available"] else "[]"
    options_compact = json.dumps(sec5, separators=(',', ':')) if sec5["options_available"] else "{}"
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

    prompt = f"""[SECTION A: ROLE AND TASK DEFINITION]
You are a highly experienced hedge fund manager and swing trading mentor specializing in the Indian F&O (Futures & Options) markets. Your task is to perform a meticulous deep analysis on {symbol} for the session date {sec1["session_date"]}.

Your goal is to evaluate if there is a valid swing setup (2-5 days hold) on this stock matching the Turn 2 preliminary direction ({direction}).
You must apply the 100-point Conviction Scoring Framework and enforce all operational hard gates to determine the trade readiness of the setup: TRADE_READY, WATCH, ON_RADAR, or REJECT.

[SECTION B: STOCK CONTEXT]
- Symbol: {symbol}
- Preliminary Direction: {direction}
- Preliminary Reason from Pre-Scan: {sec1["preliminary_reason"]}
- Mandatory Stock: {is_mandatory_str}
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

[SECTION D: PRICE DATA]
- 180 Days OHLCV Time Series:
{ohlcv_compact}
- Volume Ratio (20d): {sec2["volume_ratio_20d"]}
- Pre-Computed Indicators:
  - EMA20: {sec3["ema20"]} (Price vs EMA20: {sec3["price_vs_ema20"]})
  - EMA50: {sec3["ema50"]} (Price vs EMA50: {sec3["price_vs_ema50"]})
  - EMA180: {sec3["ema180"]} (Price vs EMA180: {sec3["price_vs_ema180"]})
  - EMA Arrangement: {sec3["ema_arrangement"]}
  - ATR14: {sec3["atr14"]} (ATR% of Price: {sec3["atr_pct"]}%)
  - RSI14: {sec3["rsi14"]}
  - MACD Line: {sec3["macd_line"]} | Signal: {sec3["macd_signal"]} | Hist: {sec3["macd_histogram"]}
  - MACD Histogram Direction: {sec3["macd_histogram_direction"]}
  - Last 20 RSI Values: {json.dumps(sec3["rsi_last_20"], separators=(',', ':'))}
  - Last 20 MACD Histogram Values: {json.dumps(sec3["macd_hist_last_20"], separators=(',', ':'))}

[SECTION E: F&O DATA]
- Futures Available: {sec4["futures_available"]}
- Futures 30 Days series:
{futures_compact}
- Current Futures Basis: {sec4["basis_current"]} (Trend: {sec4["basis_trend"]})
- Rollover Phase: {sec4["rollover_phase"]}
- Days to Expiry (DTE): {sec4["days_to_expiry"]}
- Near Month OI Trend: {sec4["near_month_oi_trend"]}

- Options Data Available: {sec5["options_available"]}
- Options Snapshot Details:
{options_compact}

[SECTION F: SCORING INSTRUCTIONS]
Evaluate the stock setup across 4 dimensions (100 Points Total) using the following rubrics:

1. Dimension 1: Price Structure (55 pts)
   - S/R Zones (15 pts): EMA dynamic support + horizontal swing levels confluence. 14-15 = major confluence, 10-13 = clear S/R from 2 sources, 6-9 = single level, 2-5 = weak, 0-1 = no basis.
   - Chart Patterns (13 pts): Completion, textbook shape, mechanical target. 12-13 = complete/clean, 8-11 = clear but imperfect, 4-7 = forming, 1-3 = ambiguous, 0 = none.
   - Buyer/Seller Analysis (12 pts): Body vs range, close position, last 5 candles control. 11-12 = clear control, 7-10 = biased, 4-6 = contested, 1-3 = opposing building, 0 = absorption.
   - Candlestick Patterns (8 pts): Named candlestick patterns at key levels. 7-8 = high significance, 5-6 = medium, 3-4 = low, 1-2 = conflicting, 0 = none.
   - RSI + MACD (4 pts): RSI divergence (only divergence, not overbought/oversold) and MACD momentum direction.
   - Volume (3 pts): Volume ratio and trend confirming the price movement.

2. Dimension 2: Risk/Reward (25 pts)
   - Stop Loss Quality (10 pts): Invalidation logic clarity and ATR check (sweet spot 0.75x-1.5x ATR). 0 = no structural SL (triggers REJECT).
   - Target Logic (8 pts): Targets T1/T2 at structural S/R. T2 must yield >= 1:1.5 R:R. 0 = R:R < 1:1.5 (triggers REJECT).
   - Entry Zone Quality (5 pts): Zone confluence and tightness (< 1% width).
   - R:R Ratio Score (2 pts): >= 2.5 R:R = 2 pts, >= 2.0 = 1.5 pts, >= 1.5 = 1 pt, < 1.5 = 0 pts (triggers REJECT).

3. Dimension 3: Market + Sector Context (15 pts)
   - Index Context (8 pts): Mapped from Nifty bias (Supportive = 7-8 pts, Neutral = 4-5 pts, Resistant = 1-2 pts).
   - Sector Context (7 pts): Tailwind Strong = 6-7 pts, Tailwind Moderate = 4-5 pts, Neutral = 3 pts, Headwind = 0-2 pts. Adjust for Relative Strength: +1 pt if stock outperforms sector return by 2%+, -1 pt if it underperforms by 2%+.

4. Dimension 4: Stock F&O Context (5 pts)
   - Futures Basis (2 pts): Positive carry = 2 pts, negative carry = 0-1 pts.
   - PCR Context (2 pts): Contrarian extreme PCR checks.
   - Rollover + DTE (1 pt): DTE < 6 trading days triggers options REJECT.

SCORING CALCULATIONS:
Calculate raw_total_score = Sum of Dimension 1 + 2 + 3 + 4.
Calculate adjusted_score = raw_total_score * conviction_multiplier (from Turn 1, currently {sec7["conviction_multiplier"]}).

Apply thresholds on adjusted_score to set the initial stage:
- TRADE_READY : adjusted_score >= 72
- WATCH       : adjusted_score 52-71
- ON_RADAR    : adjusted_score 35-51
- REJECT      : adjusted_score < 35 OR any hard gate triggered

[HARD GATES]
Enforce operational hard gates. Any trigger of these gates forces the stage to REJECT immediately, bypassing the score:
- GATE 1: No structural SL identified -> REJECT
- GATE 2: R:R < 1:1.5 at Target 2 -> REJECT
- GATE 3: DTE < 6 trading days -> Options instruments REJECT (Futures instrument is still allowed)
- GATE 4: Price chart directly contradicts Turn 2 direction hypothesis and no alternative valid direction is found -> REJECT

[SECTION G: OUTPUT SPECIFICATION]
Provide your analysis ONLY as a single valid JSON object. Do not include any markdown styling, conversational text, introduction, or wrap it in anything other than the JSON format.

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
    "strike": <number_or_null_if_fut_only>,
    "option_type": "CE | PE | null",
    "expiry": "<YYYY-MM-DD_or_null>",
    "days_to_expiry": <number_or_null>,
    "entry_premium_low": <number_or_null>,
    "entry_premium_high": <number_or_null>,
    "sl_pct": <number_or_null>,
    "sl_premium": <number_or_null>,
    "target_1_premium": <number_or_null>,
    "target_2_premium": <number_or_null>,
    "iv_note": "<note about IV and VIX proxy>"
  }},
  "fut_setup": {{
    "entry_low": <number_or_null>,
    "entry_high": <number_or_null>,
    "stop_loss": <number_or_null>,
    "target_1": <number_or_null>,
    "target_2": <number_or_null>,
    "lots": null,
    "lot_size": null,
    "risk_inr": null,
    "risk_pct_capital": null
  }},
  "instrument_recommendation": "OPTIONS | FUT | NONE",
  "instrument_reason": "<reasons for recommending options or futures based on IV and index bias>",
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
  "dimension_4_narrative": "<Assess F&O context: basis current/trend, PCR contrarian reading with any thin OI warning, and DTE/rollover phase significance.>",
  "mentor_notes": "<Educational swing-trading takeaways taught by this specific setup. Why does it work and what visual cues verify it on the chart.>",
  "why_could_be_wrong": "<Three highly specific bearish scenarios with exact invalidation price levels where the trade goes wrong (e.g. 'If closes below 1828 on high volume'). No generic disclaimers.>",
  "key_thing_to_watch": "<Single, most critical actionable observation for the morning market open (e.g. entry boundary trigger, gap opens).>",
  "rejection_reason": "<Detail reasons for REJECT or null>"
}}
"""
    return prompt


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

    # Combine pre-scan forwarded stocks with priority watchlist stocks
    final_queue = final_forward_list[:]

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

    # Calculate cost of pre-scan run
    cost2_usd = round(
        t2_cost["input_tokens"]  / 1_000_000 * 3.00 +
        t2_cost["output_tokens"] / 1_000_000 * 15.00,
        6,
    )

    prescan_fwd = len(final_forward_list)

    # Update session status in DB
    try:
        update_analysis_session(session_id, {
            "claude_tokens_input":  total_input,
            "claude_tokens_output": total_output,
            "claude_cost_usd":      cost1_usd + cost2_usd,
            "status":               "ANALYSIS_COMPLETE",
            "market_regime":        regime_result.get("regime"),
            "forward_list":         final_forward_list,
            "stage_statuses": {
                "claude_turn1":          "COMPLETE",
                "claude_turn2":          "COMPLETE",
                "deep_analysis":         "SKIPPED",
                "prescan_total":         len(turn2_results),
                "prescan_forwarded":     prescan_fwd,
            }
        })
    except Exception as exc:
        logger.warning("Failed to update session status in DB: %s", exc)

    return {
        "turn1_result":        turn1_result,
        "turn2_results":       turn2_results,
        "forward_list":        final_forward_list,
        "total_input_tokens":  total_input,
        "total_output_tokens": total_output,
        "cost_usd":            round(cost1_usd + cost2_usd, 6),
        "cost1_usd":            cost1_usd,
        "cost2_usd":            cost2_usd,
        "regime_result":       regime_result,
    }
