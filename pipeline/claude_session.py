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
)
from indicators.technical import (
    atr_pct,
    calculate_atr,
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
    "volume_note": "", "character": "", "trading_note": "",
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
  volume_note : 1 sentence on volume character citing actual observations
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
      "volume_note": "", "character": "", "trading_note": "" }},
    "IT": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "volume_note": "", "character": "", "trading_note": "" }},
    "AUTO": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "volume_note": "", "character": "", "trading_note": "" }},
    "PHARMA": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "volume_note": "", "character": "", "trading_note": "" }},
    "FMCG": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "volume_note": "", "character": "", "trading_note": "" }},
    "METAL": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "volume_note": "", "character": "", "trading_note": "" }},
    "ENERGY": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "volume_note": "", "character": "", "trading_note": "" }},
    "FINSERV": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "volume_note": "", "character": "", "trading_note": "" }},
    "INFRA": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "volume_note": "", "character": "", "trading_note": "" }},
    "CONSUMER": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "volume_note": "", "character": "", "trading_note": "" }},
    "MEDIA": {{ "trend": "", "momentum": "", "stance": "", "strength": "", "structure": "",
      "key_levels": {{ "support": 0, "resistance": 0, "breakout_above": 0, "breakdown_below": 0 }},
      "volume_note": "", "character": "", "trading_note": "" }}
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
    })

    # Telegram silent notification
    try:
        narrative = result.get("session_narrative", "")
        first_sentence = narrative.split(".")[0] + "." if narrative else ""
        vix_trend = (result.get("vix_assessment") or {}).get("trend", "")
        vix_str   = f"{vix_current} ({vix_trend})" if vix_current else "n/a"
        overall_structure = nps.get("overall_structure", "")
        index_bias = trading_impl.get("index_bias", "")
        msg = (
            f"📊 Market Context — {session_date}\n"
            f"Trend: {result.get('market_trend')} | Vol: {result.get('market_volatility')}\n"
            f"Structure: {overall_structure}\n"
            f"VIX: {vix_str}\n"
            f"Index Bias: {index_bias}\n"
            f"Risk Level: {result.get('session_risk_level')}\n"
            f"{first_sentence}"
        )
        send_silent(msg)
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

    try:
        from new_notifications.telegram import send_phase1_complete
        send_phase1_complete(
            str(session_date),
            str(regime_result.get("regime", "UNKNOWN")),
            str(regime_result.get("execution_bias", "UNKNOWN")),
        )
    except Exception as _exc:
        logger.warning("Phase 1 notification failed: %s", _exc)

    # Re-build system prompt using the dynamically generated regime context
    context_bundle["regime"] = regime_result
    system_text = build_system_prompt(context_bundle)

    # Token ceiling check before Turn 2
    if total_input + total_output + 25_000 >= _TOKEN_CEILING:
        raise RuntimeError(
            f"Token ceiling ({_TOKEN_CEILING}) would be exceeded entering Turn 2 "
            f"({total_input + total_output} tokens used so far)."
        )

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
