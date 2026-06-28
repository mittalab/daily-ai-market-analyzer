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
    "execution_bias", "fii_dii_stance", "vix_assessment", "fii_dii_assessment",
    "session_risk_level", "conviction_multiplier", "min_conviction_override",
    "sector_pictures", "directional_filters", "prescan_guidance",
    "index_key_levels", "risk_flags", "guidance",
]

_TURN1_DEFAULTS = {
    "session_narrative":       "Market context unavailable — treating as neutral session.",
    "market_trend":            "SIDEWAYS",
    "market_volatility":       "NORMAL",
    "market_structure":        "WIDE",
    "execution_bias":          "NEUTRAL",
    "fii_dii_stance":          "NEUTRAL",
    "vix_assessment":          {"current": None, "trend": "STABLE", "character": "", "options_implication": ""},
    "fii_dii_assessment":      {"fii_20d_character": "", "recent_shift": "NO", "shift_description": None,
                                "dii_stance_description": "", "divergence": "NO", "key_insight": ""},
    "session_risk_level":      "MEDIUM",
    "conviction_multiplier":   0.95,
    "min_conviction_override": None,
    "sector_pictures":         {},
    "directional_filters":     {"avoid_longs_in": [], "avoid_shorts_in": [], "caution_sectors": []},
    "prescan_guidance":        {"max_stocks_to_forward": 10, "prefer_directions": ["LONG", "SHORT"],
                                "prioritise_sectors": [], "deprioritise_sectors": [], "special_instructions": None},
    "index_key_levels":        {"strong_support": 0, "support": 0, "current": 0,
                                "resistance": 0, "strong_resistance": 0,
                                "max_pain": None, "pcr_signal": "NEUTRAL", "levels_note": ""},
    "risk_flags":              [],
    "guidance":                {"favour": "No specific guidance.", "caution": "Exercise standard caution."},
}


def _build_turn1_data(session_date: date) -> dict:
    """
    Reads all required data from DB for Turn 1.
    No API calls. No external dependencies.
    Returns structured dict for prompt injection.
    """
    # ── Source 1: Nifty 50 ───────────────────────────────────────────────────
    from new_data_ingestion.nse_bhavcopy import get_holiday_dates
    _holidays_str = {str(d) for d in get_holiday_dates()}

    nifty_rows = get_price_history("NIFTY_50", days=250)
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

    df_60 = df_nifty.tail(60)
    df_60 = df_60[~df_60["date"].astype(str).isin(_holidays_str)]
    df_60 = df_60.tail(60)
    nifty_ohlcv_60d = [
        {
            "date":   str(r["date"]),
            "open":   float(r["open"]),
            "high":   float(r["high"]),
            "low":    float(r["low"]),
            "close":  float(r["close"]),
            "volume": int(r["volume"]) if not pd.isna(r["volume"]) else 0,
        }
        for _, r in df_60.iterrows()
    ]

    nifty_indicators = {
        "current": round(price, 2),
        "ema20":   ema20_val,
        "ema50":   ema50_val,
        "ema180":  ema180_val,
        "ret5d":   ret5d_val,
        "ret20d":  ret20d_val,
        "ret60d":  ret60d_val,
        "atr_pct": atr_pct_val,
    }

    # ── Source 2: India VIX ──────────────────────────────────────────────────
    vix_rows = get_price_history("INDIA_VIX", days=30)
    vix_close_30d = [
        {"date": str(r["date"]), "close": float(r["close"])}
        for r in vix_rows
        if r.get("close") is not None and float(r["close"]) != 0
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
        if len(sec_rows) < 7:
            logger.warning("Insufficient data for sector %s — skipping", symbol)
            continue
        df_sec = pd.DataFrame(sec_rows)
        for col in ("open", "high", "low", "close"):
            df_sec[col] = pd.to_numeric(df_sec[col], errors="coerce")
        df_sec = df_sec.dropna(subset=["close"]).reset_index(drop=True)

        sec_close = df_sec["close"]
        sec_price = float(sec_close.iloc[-1])
        s_ret7d  = round((sec_price / float(sec_close.iloc[-7])  - 1) * 100, 2) if len(df_sec) >= 7  else None
        s_ret20d = round((sec_price / float(sec_close.iloc[-20]) - 1) * 100, 2) if len(df_sec) >= 20 else None
        s_ret60d = round((sec_price / float(sec_close.iloc[-60]) - 1) * 100, 2) if len(df_sec) >= 60 else None
        vs_nifty = round(s_ret20d - ret20d_val, 2) if (s_ret20d is not None and ret20d_val is not None) else None

        df_30 = df_sec.tail(30)
        df_30 = df_30[~df_30["date"].astype(str).isin(_holidays_str)]
        df_30 = df_30.tail(30)
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

    # ── Source 5: Options positioning ────────────────────────────────────────
    options_available = False
    ce_walls: list[dict] = []
    pe_walls: list[dict] = []
    pcr_current = None
    max_pain_val = None

    try:
        n_snap = get_options_by_date("NIFTY_50", session_date)
        if not n_snap:
            n_snap = get_options_by_date("NIFTY_50", session_date - timedelta(days=1))
        if n_snap:
            expiries = sorted(list(set(str(r["expiry_date"]) for r in n_snap)))
            if expiries:
                walls = oi_walls(n_snap, expiries[0], top_n=5)
                ce_walls = [{"strike": int(w["strike"]), "oi": int(w["oi"] or 0)} for w in walls.get("ce_walls", [])]
                pe_walls = [{"strike": int(w["strike"]), "oi": int(w["oi"] or 0)} for w in walls.get("pe_walls", [])]
                options_available = True
    except Exception as exc:
        logger.warning("Turn 1 options walls failed: %s", exc)

    oi_rows = None
    expiry_context = {
        "current_expiry": "Unavailable",
        "next_expiry":    "Unavailable",
        "rollover_phase": "UNKNOWN",
    }
    try:
        oi_rows = get_continuous_oi("NIFTY_50", days=1)
        if oi_rows:
            last_oi = oi_rows[-1]
            pcr_current  = last_oi.get("pcr_near")
            max_pain_val = last_oi.get("max_pain")
            expiry_context = {
                "current_expiry": str(last_oi.get("near_expiry") or "Unavailable"),
                "next_expiry":    str(last_oi.get("next_expiry") or "Unavailable"),
                "rollover_phase": str(last_oi.get("rollover_phase") or "UNKNOWN"),
            }
    except Exception as exc:
        logger.warning("Turn 1 continuous OI fetch failed: %s", exc)

    logger.info(
        "Turn 1 data ready: nifty=%d rows, vix=%d rows, fii=%d rows, sectors=%d available, options=%s",
        len(nifty_ohlcv_60d),
        len(vix_close_30d),
        len(fii_dii_flows_30d),
        len(sectors_data),
        "YES" if options_available else "NO",
    )

    pcr_value = None
    if pcr_current is not None:
        pcr_value = float(pcr_current)

    max_pain_value = None
    if max_pain_val is not None:
        max_pain_value = float(max_pain_val)

    return {
        "session_date": str(session_date),
        "nifty": {
            "indicators": nifty_indicators,
            "ohlcv_60d":  nifty_ohlcv_60d,
        },
        "expiry_context": expiry_context,
        "vix": {
            "close_30d": vix_close_30d,
        },
        "fii_dii": {
            "data_quality": data_quality,
            "flows_30d":    fii_dii_flows_30d,
        },
        "sectors": sectors_data,
        "options": {
            "available":   options_available,
            "ce_walls":    ce_walls,
            "pe_walls":    pe_walls,
            "pcr_current": pcr_value,
            "max_pain":    max_pain_value,
        },
    }


def _build_turn1_prompt(data: dict) -> str:
    """
    Builds the Turn 1 user message from prepared data.
    Returns plain text string ready for Claude.
    """
    _j = lambda arr: json.dumps(arr, separators=(",", ":"))

    ind    = data["nifty"]["indicators"]
    opt    = data["options"]
    fii    = data["fii_dii"]
    vix    = data["vix"]

    ema180_str = str(ind["ema180"]) if ind["ema180"] is not None else "Unavailable"

    expiry         = data["expiry_context"]
    current_expiry = expiry["current_expiry"]
    next_expiry    = expiry["next_expiry"]
    rollover_phase = expiry["rollover_phase"]

    try:
        from pipeline.oi_series_builder import _trading_days_to
        _session_date_obj = date.fromisoformat(data["session_date"])
        _exp_date         = date.fromisoformat(current_expiry)
        days_left         = _trading_days_to(_session_date_obj, _exp_date)
    except Exception:
        days_left = "unknown"

    # Options section
    if opt["available"]:
        ce_lines = "\n".join(f"  Strike {w['strike']}: OI {w['oi']:,}" for w in opt["ce_walls"])
        pe_lines = "\n".join(f"  Strike {w['strike']}: OI {w['oi']:,}" for w in opt["pe_walls"])
        pcr_str  = str(round(opt["pcr_current"], 2)) if opt["pcr_current"] is not None else "Unavailable"
        mp_str   = str(int(opt["max_pain"]))         if opt["max_pain"]    is not None else "Unavailable"
        options_block = f"""Nifty resistance levels (CE OI concentration):
{ce_lines}

Nifty support levels (PE OI concentration):
{pe_lines}

PCR (Put-Call Ratio): {pcr_str}
Max Pain strike     : {mp_str}

PCR interpretation (contrarian indicator):
  PCR < 0.7   -> contrarian bearish signal
  PCR 0.7-1.1 -> neutral positioning
  PCR 1.1-1.3 -> mild protective hedging present
  PCR > 1.3   -> contrarian bullish signal"""
    else:
        options_block = "Options data unavailable for today.\nUse price structure and FII flows for key levels."

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
            f"Returns: 7d={r['ret7d']}% | 20d={r['ret20d']}% | 60d={r['ret60d']}% | vs_nifty={r['vs_nifty_20d']}%\n"
            f"30d OHLCV: {_j(sec['ohlcv_30d'])}"
        )
    sectors_text = "\n\n".join(sector_blocks)

    # Pre-compute EMA comparisons for market_trend guidance (FIX 4)
    price           = ind["current"]
    ema20           = ind["ema20"]
    ema50           = ind["ema50"]
    ema180          = ind["ema180"]
    price_vs_ema20  = "above" if price > ema20  else "below"
    price_vs_ema50  = "above" if price > ema50  else "below"
    price_vs_ema180 = ("above" if price > ema180 else "below") if ema180 is not None else "unavailable"
    ema20_vs_ema50  = ">" if ema20 > ema50 else "<"
    ema_arrangement = "bullish" if ema20 > ema50 else "bearish"

    prompt = f"""You are performing post-market analysis for {data['session_date']}. Market has closed for the day.
All data below reflects today's final values.

Analyse all sections thoroughly.
Think step by step through each data source.
Build a complete market intelligence picture.
Be specific — cite actual numbers in your output.
This picture guides all stock analysis tonight.

════════════════════════════════════════════════════
SECTION 1: NIFTY 50 PRICE ACTION
════════════════════════════════════════════════════

Pre-computed indicators:
  Current price : {ind['current']}
  EMA 20        : {ind['ema20']}
  EMA 50        : {ind['ema50']}
  EMA 180       : {ema180_str}
  5-day return  : {ind['ret5d']}%
  20-day return : {ind['ret20d']}%
  60-day return : {ind['ret60d']}%
  ATR% (14)     : {ind['atr_pct']}%

Last 60 days OHLCV — read the price action:
Note: Today's volume shows 0 — this is expected
as exchange volume data is finalized after market
close and may not yet be available. Ignore volume
for today's candle only. Use all prior days'
volume for momentum and participation assessment.
{_j(data['nifty']['ohlcv_60d'])}

════════════════════════════════════════════════════
SECTION 2: INDIA VIX
════════════════════════════════════════════════════

Last 30 days VIX closing values:
{_j(vix['close_30d'])}

Read this data and determine:
  Current level and what it signals
  Direction over 30 days (falling/rising/choppy)
  Character of the move (gradual or spike-driven)
  Implication for option pricing tonight

════════════════════════════════════════════════════
SECTION 3: FII / DII INSTITUTIONAL FLOWS
════════════════════════════════════════════════════

Data quality: {dq}{dq_warning}
Last 30 days daily institutional flows (Crores):
{_j(fii['flows_30d'])}

Read this data and determine:
  Cumulative FII direction over last 20 days
  Whether behaviour shifted in last 5 days
  Whether selling/buying is consistent or event-driven
  DII stance — absorbing FII or following same direction
  Meaningful divergence between FII and DII

════════════════════════════════════════════════════
SECTION 4: SECTOR ANALYSIS
════════════════════════════════════════════════════

For each sector you have summary returns and 30 days of price action to read.

Determine for each sector:
  Trend direction and momentum character
  Whether outperforming or underperforming Nifty
  Stance for tonight: TAILWIND, NEUTRAL, or HEADWIND
  Strength: STRONG, MODERATE, or WEAK

vs_nifty_20d guide:
  > +3%       : TAILWIND STRONG
  +1 to +3%   : TAILWIND MODERATE
  -1 to +1%   : NEUTRAL
  -1 to -3%   : HEADWIND MODERATE
  < -3%       : HEADWIND STRONG

{sectors_text}

════════════════════════════════════════════════════
SECTION 5: OPTIONS MARKET POSITIONING
════════════════════════════════════════════════════

{options_block}

════════════════════════════════════════════════════
SECTION 6: EXPIRY AND ROLLOVER CONTEXT
════════════════════════════════════════════════════

Current expiry : {current_expiry}
Next expiry    : {next_expiry}
Rollover phase : {rollover_phase}

Rollover phase definitions (based on trading days
remaining before expiry, excluding expiry day itself):

  NORMAL        : More than 5 trading days remaining
                  Normal analysis — no special constraints
                  Full stock universe eligible for near month

  ROLLOVER_WATCH: 3-5 trading days remaining
                  Monitor rollover activity in futures OI
                  Prefer setups that work on next expiry too
                  Near-month setups still valid if DTE >= 6

  TRANSITION    : Exactly 2 trading days remaining
                  Near-month theta decay accelerating fast
                  New near-month entries carry high time risk
                  Prefer next-month expiry for all new setups

  EXPIRY        : 0-1 trading days remaining or expiry day
                  Do not recommend near-month entries at all
                  Only next-month setups are valid tonight
                  Existing near-month positions: manage only

Tonight's phase is: {rollover_phase}

Factor this into your output:
  prescan_guidance.max_stocks_to_forward:
    NORMAL or ROLLOVER_WATCH: use risk_level base
    TRANSITION: reduce base by 20%
    EXPIRY: reduce base by 30%

  prescan_guidance.special_instructions:
    NORMAL: null
    ROLLOVER_WATCH: note preference for setups
                    that work on next expiry too
    TRANSITION: "All new setups must target next expiry
                 ({next_expiry}). Near-month theta
                 decay is severe — avoid new entries
                 on {current_expiry} strikes."
    EXPIRY: "Only next-month expiry ({next_expiry})
             setups valid tonight. No near-month
             entries under any circumstances."

  prescan_guidance.expiry_note:
    NORMAL or ROLLOVER_WATCH: null
    TRANSITION or EXPIRY: specific warning string
      citing current_expiry and days remaining

  risk_flags:
    Always add expiry risk flag if TRANSITION or EXPIRY:
    "{days_left} trading days to {current_expiry} expiry
     — near-month theta risk severe.
     All new setups must use {next_expiry}."

════════════════════════════════════════════════════
REQUIRED OUTPUT
════════════════════════════════════════════════════

SECTOR PICTURE INSTRUCTIONS:
Provide a complete assessment for ALL 11 sectors.
No shortcuts. No placeholders. No ellipsis.
Every sector must have all 7 fields completed.

Required sectors (all 11 mandatory):
  BANKING, IT, AUTO, PHARMA, FMCG,
  METAL, ENERGY, FINSERV, INFRA, CONSUMER, MEDIA

Field value options:
  trend    : UPTREND | DOWNTREND | SIDEWAYS
  momentum : ACCELERATING | DECELERATING | STABLE
  vs_nifty : OUTPERFORMING | UNDERPERFORMING | INLINE
  stance   : TAILWIND | NEUTRAL | HEADWIND
  strength : STRONG | MODERATE | WEAK
  character: 1 sentence describing price action.
             Cite actual price levels seen in data.
             e.g. "Broke out from 54000 to 58177
                   with consistent higher highs"
  trading_note: 1 sentence on stock selection tonight.
             e.g. "Favour LONG setups — strong sector
                   tailwind from Banking outperformance"

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
  //   EMA20  : {ema20} (price {price_vs_ema20})
  //   EMA50  : {ema50} (price {price_vs_ema50})
  //   EMA180 : {ema180} (price {price_vs_ema180})
  //   EMA20 {ema20_vs_ema50} EMA50
  //         ({ema_arrangement} short-term arrangement)

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

  "min_conviction_override": null or 80 or 85,

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
    "BANKING": {{
      "trend": "",
      "momentum": "",
      "vs_nifty": "",
      "stance": "",
      "strength": "",
      "character": "",
      "trading_note": ""
    }},
    "IT": {{
      "trend": "",
      "momentum": "",
      "vs_nifty": "",
      "stance": "",
      "strength": "",
      "character": "",
      "trading_note": ""
    }},
    "AUTO": {{
      "trend": "",
      "momentum": "",
      "vs_nifty": "",
      "stance": "",
      "strength": "",
      "character": "",
      "trading_note": ""
    }},
    "PHARMA": {{
      "trend": "",
      "momentum": "",
      "vs_nifty": "",
      "stance": "",
      "strength": "",
      "character": "",
      "trading_note": ""
    }},
    "FMCG": {{
      "trend": "",
      "momentum": "",
      "vs_nifty": "",
      "stance": "",
      "strength": "",
      "character": "",
      "trading_note": ""
    }},
    "METAL": {{
      "trend": "",
      "momentum": "",
      "vs_nifty": "",
      "stance": "",
      "strength": "",
      "character": "",
      "trading_note": ""
    }},
    "ENERGY": {{
      "trend": "",
      "momentum": "",
      "vs_nifty": "",
      "stance": "",
      "strength": "",
      "character": "",
      "trading_note": ""
    }},
    "FINSERV": {{
      "trend": "",
      "momentum": "",
      "vs_nifty": "",
      "stance": "",
      "strength": "",
      "character": "",
      "trading_note": ""
    }},
    "INFRA": {{
      "trend": "",
      "momentum": "",
      "vs_nifty": "",
      "stance": "",
      "strength": "",
      "character": "",
      "trading_note": ""
    }},
    "CONSUMER": {{
      "trend": "",
      "momentum": "",
      "vs_nifty": "",
      "stance": "",
      "strength": "",
      "character": "",
      "trading_note": ""
    }},
    "MEDIA": {{
      "trend": "",
      "momentum": "",
      "vs_nifty": "",
      "stance": "",
      "strength": "",
      "character": "",
      "trading_note": ""
    }}
  }},

  "directional_filters": {{
    "avoid_longs_in": ["sectors with HEADWIND stance"],
    "avoid_shorts_in": ["sectors with TAILWIND stance"],
    "caution_sectors": ["sectors with conflicting signals"]
  }},

  "prescan_guidance": {{
    "max_stocks_to_forward": integer,
    // Calculate as follows:
    //
    // Step 1 — Base from session_risk_level:
    //   LOW     : 15 stocks
    //   MEDIUM  : 12 stocks
    //   HIGH    :  8 stocks
    //   EXTREME :  5 stocks
    //
    // Step 2 — Apply rollover reduction:
    //   NORMAL or ROLLOVER_WATCH : no reduction
    //   TRANSITION               : multiply by 0.80
    //   EXPIRY                   : multiply by 0.70
    //   Round down to nearest integer
    //
    // Step 3 — Hard minimum: never below 3
    //
    // Example calculations:
    //   MEDIUM risk + NORMAL phase   : 12
    //   MEDIUM risk + TRANSITION     : floor(12×0.80) = 9
    //   MEDIUM risk + EXPIRY         : floor(12×0.70) = 8
    //   HIGH risk   + EXPIRY         : floor(8×0.70)  = 5
    //   LOW risk    + TRANSITION     : floor(15×0.80) = 12

    "prefer_directions": [...],

    "prioritise_sectors": [...],

    "deprioritise_sectors": [...],

    "special_instructions": string or null,
    // Use the phase-specific instruction from Section 6.
    // Include actual dates: {current_expiry}, {next_expiry}
    // NORMAL          : null
    // ROLLOVER_WATCH  : prefer next expiry note
    // TRANSITION      : mandate next-month expiry,
    //                   cite {current_expiry} and
    //                   {next_expiry} explicitly
    // EXPIRY          : no near-month entries at all

    "expiry_note": string or null
    // If TRANSITION or EXPIRY:
    //   Add specific note about expiry risk
    //   e.g. "2 days to expiry — theta decay severe
    //         on near-month options. Flag any setup
    //         targeting near-month strike."
    // If NORMAL or ROLLOVER_WATCH: null
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

  "risk_flags": [],
  // Required: 2 to 4 items.
  // Rule 1: If rollover_phase is TRANSITION or EXPIRY,
  //         first item MUST be the expiry warning:
  //         "{days_left} trading days to {current_expiry}
  //          expiry — near-month theta risk severe.
  //          All new setups must target {next_expiry}."
  // Rule 2: All other flags must cite actual numbers.
  //         GOOD: "VIX at 13.05 near 30-day low of 12.67
  //                — complacency risk if event triggers"
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

    logger.info("Turn 1: calling Claude (max_tokens=6000)...")
    # logger.info ("USER PROMPT: %s" , prompt)

    try:
        response = _call_claude(client, _TURN1_SYSTEM, messages, max_tokens=6000)
    except Exception as exc:
        logger.critical("Turn 1 Claude API failed: %s", exc)
        try:
            send_loud("❌ Turn 1 failed — pipeline cannot continue")
        except Exception:
            pass
        raise

    out_text = response.content[0].text
    print("CALUDE OUTPUT TEST: ", out_text)
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

    # Validate required keys — fill defaults for any missing
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
    market_regime = (
        f"{result.get('market_trend', 'SIDEWAYS')}_"
        f"{result.get('market_volatility', 'NORMAL')}_"
        f"{result.get('market_structure', 'WIDE')}"
    )
    vix_current = (result.get("vix_assessment") or {}).get("current")
    update_analysis_session(session_id, {
        "market_regime":         market_regime,
        "market_trend":          result.get("market_trend"),
        "market_volatility":     result.get("market_volatility"),
        "market_structure":      result.get("market_structure"),
        "execution_bias":        result.get("execution_bias"),
        "fii_dii_stance":        result.get("fii_dii_stance"),
        "session_risk_level":    result.get("session_risk_level"),
        "conviction_multiplier": result.get("conviction_multiplier"),
        "nifty_close":           data["nifty"]["indicators"]["current"],
        "vix_close":             vix_current,
        "stage_statuses":        {"turn1": "COMPLETE"},
    })

    # Telegram silent notification
    try:
        narrative = result.get("session_narrative", "")
        first_sentence = narrative.split(".")[0] + "." if narrative else ""
        vix_trend = (result.get("vix_assessment") or {}).get("trend", "")
        vix_str   = f"{vix_current} ({vix_trend})" if vix_current else "n/a"
        msg = (
            f"📊 Market Context — {session_date}\n"
            f"Trend: {result.get('market_trend')} | Vol: {result.get('market_volatility')}\n"
            f"VIX: {vix_str}\n"
            f"Risk: {result.get('session_risk_level')}\n"
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
