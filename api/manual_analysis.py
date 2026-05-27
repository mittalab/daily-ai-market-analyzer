"""
POST /api/analyse — on-demand single-stock deep analysis via Claude.

Bypasses Level 1 filter (user explicitly requested the stock) but still
enforces RR gate, position sizing, and expiry selection rules.

Feature gate: system_config.manual_analysis_enabled must be "true".
"""
import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Literal

import anthropic
import pandas as pd
import pytz
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from database.queries import (
    create_trade_setup,
    get_all_lot_sizes,
    get_all_system_config,
    get_continuous_oi,
    get_fii_dii_flows,
    get_futures_series,
    get_options_snapshot,
    get_price_history,
    save_claude_turn,
    upsert_price_history,
    upsert_single_lot_size,
)
from indicators.technical import (
    atr_pct,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    volume_ratio,
)
from integrations.nse_bhavcopy import get_nifty50_symbols, last_trading_day

load_dotenv()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["analysis"])

IST       = pytz.timezone("Asia/Kolkata")
_MODEL    = "claude-sonnet-4-6"
_SECTOR_MAP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "config", "sector_map.json"
)

# ── Pydantic models ────────────────────────────────────────────────────────────

class AnalyseRequest(BaseModel):
    symbol:    str
    direction: Literal["AUTO", "LONG", "SHORT"] = "AUTO"
    save_to_ledger: bool = False

    @field_validator("symbol")
    @classmethod
    def normalise_symbol(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("direction")
    @classmethod
    def normalise_direction(cls, v: str) -> str:
        return v.strip().upper()


class AnalyseResponse(BaseModel):
    symbol:              str
    session_date:        str
    is_nifty50:          bool
    custom_symbol_note:  str | None
    analysis:            dict
    estimated_cost_usd:  float
    data_quality_notes:  list[str]
    duration_seconds:    float
    setup_id:            str | None   # set if save_to_ledger=True


# ── Sector map helper ─────────────────────────────────────────────────────────

def _load_sector_map() -> dict:
    try:
        with open(_SECTOR_MAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"stocks": {}}


def _sector_info(symbol: str) -> tuple[str, str]:
    """Return (sector, index) for a symbol, or ('UNKNOWN', 'UNKNOWN')."""
    data = _load_sector_map()
    entry = data.get("stocks", {}).get(symbol, {})
    return entry.get("sector", "UNKNOWN"), entry.get("index", "UNKNOWN")


# ── Data assembly ─────────────────────────────────────────────────────────────

def _iv_assessment(iv_pct: float | None) -> str:
    if iv_pct is None:
        return "UNKNOWN"
    if iv_pct < 15:
        return "LOW"
    if iv_pct <= 25:
        return "MEDIUM"
    return "HIGH"


def _atm_iv(options: list[dict], spot: float | None) -> float | None:
    """Return IV of the nearest-to-spot strike from today's options snapshot."""
    if not options or spot is None:
        return None
    ce_rows = [r for r in options if r.get("option_type") == "CE" and r.get("implied_volatility")]
    if not ce_rows:
        return None
    closest = min(ce_rows, key=lambda r: abs(float(r["strike"]) - spot))
    iv = closest.get("implied_volatility")
    return round(float(iv), 2) if iv else None


def _oi_walls(options: list[dict], near_expiry_str: str, top_n: int = 5) -> dict:
    """Top-N CE and PE strikes by OI for near expiry (OI walls)."""
    near = [r for r in options if str(r.get("expiry_date", "")) == near_expiry_str]
    ce   = sorted([r for r in near if r["option_type"] == "CE"],
                  key=lambda r: int(r.get("oi") or 0), reverse=True)[:top_n]
    pe   = sorted([r for r in near if r["option_type"] == "PE"],
                  key=lambda r: int(r.get("oi") or 0), reverse=True)[:top_n]
    return {
        "ce_walls": [{"strike": r["strike"], "oi": r.get("oi")} for r in ce],
        "pe_walls": [{"strike": r["strike"], "oi": r.get("oi")} for r in pe],
    }


def _fetch_lot_size_from_kite(symbol: str) -> int | None:
    """
    Fallback: fetch lot size from Kite NFO instruments master, then cache in DB.
    Used when a symbol (e.g. newly added Nifty 50 reconstitution) is missing from lot_sizes.
    Returns None if Kite token is unavailable or symbol has no futures contract.
    """
    try:
        from integrations.kite_oauth import get_authenticated_kite
        kite = get_authenticated_kite()
        instruments = pd.DataFrame(kite.instruments("NFO"))
        fut = instruments[
            (instruments["name"] == symbol) &
            (instruments["instrument_type"] == "FUT")
        ]
        if fut.empty:
            logger.warning("No NFO futures found for %s in Kite instruments", symbol)
            return None
        lot_size = int(fut.iloc[0]["lot_size"])
        upsert_single_lot_size(symbol, lot_size)
        logger.info("Lot size fetched from Kite for %s: %d (cached in DB)", symbol, lot_size)
        return lot_size
    except Exception as exc:
        logger.warning("Kite lot size fallback failed for %s: %s", symbol, exc)
        return None


def _fetch_ohlcv_on_demand(symbol: str) -> bool:
    """
    Fetch 180 days of OHLCV for any NSE equity symbol on-demand and store in price_history.
    Used when a custom (non-Nifty50) symbol has no history in the DB.
    Returns True if data was fetched and stored, False on any failure.
    """
    try:
        from integrations.kite_oauth import get_authenticated_kite
        from integrations.kite_ohlcv import fetch_ohlcv, get_equity_token, ohlcv_to_price_rows
        kite      = get_authenticated_kite()
        token     = get_equity_token(kite, symbol)
        to_date   = date.today()
        from_date = to_date - timedelta(days=250)
        df        = fetch_ohlcv(kite, token, from_date, to_date)
        if df.empty:
            logger.warning("On-demand OHLCV for %s returned empty DataFrame", symbol)
            return False
        rows = ohlcv_to_price_rows(symbol, df)
        upsert_price_history(rows)
        logger.info("On-demand OHLCV fetched for %s: %d rows stored", symbol, len(rows))
        return True
    except Exception as exc:
        logger.warning("On-demand OHLCV fetch failed for %s: %s", symbol, exc)
        return False


def _build_stock_package(
    symbol: str,
    session_date: date,
    quality_notes: list[str],
) -> dict:
    """
    Assemble the full Section 8 data package for one stock.
    Appends data-quality warnings to quality_notes in-place.
    """
    # ── Price history: 6 months ───────────────────────────────────────────────
    price_rows = get_price_history(symbol, days=250)
    if not price_rows:
        logger.info("No price history for %s — attempting on-demand Kite fetch", symbol)
        fetched = _fetch_ohlcv_on_demand(symbol)
        if fetched:
            price_rows = get_price_history(symbol, days=250)
            quality_notes.append(f"Price history fetched on-demand from Kite for {symbol}")
        if not price_rows:
            quality_notes.append(f"No price history for {symbol} — run nightly pipeline first")
            return {}

    df = pd.DataFrame(price_rows)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).reset_index(drop=True)

    if len(df) < 20:
        quality_notes.append(f"Only {len(df)} days of price history — indicators may be unreliable")

    closes = df["close"]
    spot   = float(closes.iloc[-1])

    # ── Technical indicators ──────────────────────────────────────────────────
    ema20_s  = calculate_ema(closes, 20)
    ema50_s  = calculate_ema(closes, 50) if len(df) >= 50 else ema20_s
    ema200_s = calculate_ema(closes, 200) if len(df) >= 200 else None
    rsi_s    = calculate_rsi(closes, 14)
    atrp_s   = atr_pct(df, 14)
    volr_s   = volume_ratio(df["volume"], short=3, long=20)
    macd_l, macd_sig, macd_hist = calculate_macd(closes)

    def _last(s: pd.Series) -> float | None:
        v = s.iloc[-1]
        return round(float(v), 4) if not pd.isna(v) else None

    # ── Lot size ──────────────────────────────────────────────────────────────
    lot_sizes = get_all_lot_sizes()
    lot_size  = lot_sizes.get(symbol)
    if lot_size is None:
        logger.info("Lot size missing for %s — trying Kite instruments master", symbol)
        lot_size = _fetch_lot_size_from_kite(symbol)
    if lot_size is None:
        quality_notes.append(
            f"Lot size unknown for {symbol} — manual position sizing needed. "
            "Check NSE F&O contract specifications."
        )

    # ── Continuous OI series: 30 days ─────────────────────────────────────────
    oi_rows = get_continuous_oi(symbol, days=30)
    if not oi_rows:
        quality_notes.append(f"No OI series data for {symbol}")
    latest_oi     = oi_rows[-1] if oi_rows else {}
    rollover_phase = latest_oi.get("rollover_phase", "UNKNOWN")
    near_expiry_str = latest_oi.get("near_expiry")
    next_expiry_str = latest_oi.get("next_expiry")

    # ── Options snapshot: today ───────────────────────────────────────────────
    options     = []
    atm_iv      = None
    oi_walls    = {}
    if near_expiry_str:
        try:
            near_exp_date = date.fromisoformat(near_expiry_str)
            options       = get_options_snapshot(symbol, session_date, near_exp_date)
        except Exception:
            pass
    if not options:
        # fallback: try yesterday's snapshot
        yesterday = session_date - timedelta(days=1)
        if near_expiry_str:
            try:
                near_exp_date = date.fromisoformat(near_expiry_str)
                options       = get_options_snapshot(symbol, yesterday, near_exp_date)
                if options:
                    quality_notes.append(f"IV data from {yesterday} (yesterday's snapshot)")
            except Exception:
                pass
    if not options:
        quality_notes.append(f"No options snapshot for {symbol} — IV unavailable")
    else:
        atm_iv   = _atm_iv(options, spot)
        oi_walls = _oi_walls(options, near_expiry_str or "")

    # ── Futures series: 30 days ───────────────────────────────────────────────
    fut_rows = get_futures_series(symbol, days=30)
    if not fut_rows:
        quality_notes.append(f"No futures series data for {symbol}")
    latest_fut = fut_rows[-1] if fut_rows else {}

    # ── Sector / index ────────────────────────────────────────────────────────
    sector, sector_index = _sector_info(symbol)
    sector_rows = get_price_history(sector_index, days=10) if sector_index != "UNKNOWN" else []

    # ── OHLCV payload: last 120 days (6 months) ───────────────────────────────
    ohlcv_180 = [
        {"date": r["date"], "open": r["open"], "high": r["high"],
         "low": r["low"], "close": r["close"], "volume": r["volume"]}
        for r in price_rows[-120:]
    ]

    # ── OI series payload ─────────────────────────────────────────────────────
    oi_payload = [
        {"date": r["date"], "near_oi": r.get("near_month_oi"),
         "next_oi": r.get("next_month_oi"), "oi_change": r.get("oi_change"),
         "pcr_near": r.get("pcr_near"), "max_pain": r.get("max_pain"),
         "rollover_pct": r.get("rollover_pct"), "is_expiry_day": r.get("is_expiry_day")}
        for r in oi_rows
    ]

    # ── Futures series payload ────────────────────────────────────────────────
    fut_payload = [
        {"date": r["date"], "futures_price": r.get("futures_price"),
         "near_oi": r.get("near_month_oi"), "next_oi": r.get("next_month_oi"),
         "basis": r.get("basis"), "basis_pct": r.get("basis_pct"),
         "rollover_pct": r.get("rollover_pct")}
        for r in fut_rows
    ]

    # ── Options chain payload (strike-wise) ───────────────────────────────────
    options_payload = [
        {"strike": r["strike"], "type": r["option_type"],
         "oi": r.get("oi"), "iv": r.get("implied_volatility")}
        for r in options
    ] if options else []

    return {
        "symbol":       symbol,
        "sector":       sector,
        "sector_index": sector_index,
        "spot_price":   spot,
        "lot_size":     lot_size,
        # indicators
        "ema20":        round(float(ema20_s.iloc[-1]), 2),
        "ema50":        round(float(ema50_s.iloc[-1]), 2),
        "ema200":       round(float(ema200_s.iloc[-1]), 2) if ema200_s is not None else None,
        "rsi14":        _last(rsi_s),
        "atr_pct14":    _last(atrp_s),
        "vol_ratio":    _last(volr_s),
        "macd":         _last(macd_l),
        "macd_signal":  _last(macd_sig),
        "macd_hist":    _last(macd_hist),
        # OI / options
        "rollover_phase":   rollover_phase,
        "near_expiry":      near_expiry_str,
        "next_expiry":      next_expiry_str,
        "atm_iv_pct":       atm_iv,
        "iv_assessment":    _iv_assessment(atm_iv),
        "oi_walls":         oi_walls,
        # current OI snapshot
        "near_month_oi":    latest_oi.get("near_month_oi"),
        "next_month_oi":    latest_oi.get("next_month_oi"),
        "pcr_near":         latest_oi.get("pcr_near"),
        "max_pain":         latest_oi.get("max_pain"),
        "rollover_pct":     latest_oi.get("rollover_pct"),
        # current futures snapshot
        "futures_price":    latest_fut.get("futures_price"),
        "basis":            latest_fut.get("basis"),
        "basis_pct":        latest_fut.get("basis_pct"),
        # time series
        "ohlcv_120d":       ohlcv_180,
        "oi_series_30d":    oi_payload,
        "futures_series_30d": fut_payload,
        "options_chain":    options_payload,
        # sector context
        "sector_index_5d":  [{"date": r["date"], "close": r["close"]} for r in sector_rows[-5:]],
    }


def _build_index_context(session_date: date) -> dict:
    """Nifty/VIX/FII-DII context block — same as Turn 1 inputs."""
    nifty_rows = get_price_history("NIFTY_50",  days=35)
    vix_rows   = get_price_history("INDIA_VIX", days=32)
    fii_rows   = get_fii_dii_flows(days=30)

    from pipeline.market_regime import run_market_regime
    regime = run_market_regime(session_date)

    return {
        "regime":       regime["regime"],
        "nifty_close":  regime["nifty_close"],
        "vix":          regime["vix"],
        "ema20":        regime["ema20"],
        "ema50":        regime["ema50"],
        "ret20d_pct":   regime["ret20d"],
        "nifty_30d":    [{"date": r["date"], "close": r["close"]} for r in nifty_rows[-30:]],
        "vix_30d":      [{"date": r["date"], "close": r["close"]} for r in vix_rows[-30:]],
        "fii_dii_30d":  [{"date": r["date"], "fii_net_cr": r.get("fii_net_cr"),
                          "dii_net_cr": r.get("dii_net_cr")} for r in fii_rows],
    }


# ── Claude deep analysis prompt ───────────────────────────────────────────────

_DEEP_SYSTEM = (
    "You are an experienced hedge fund manager and swing trading mentor "
    "specialising in Indian F&O markets (Nifty 50 stocks, 2-5 day holds, "
    "stock options only — monthly Tuesday expiry).\n\n"
    "Operating rules:\n"
    "  Capital: ₹5,00,000 | Risk per trade: 2-3% | Min RR: 1:2\n"
    "  Instruments: stock options ONLY | Min DTE: 6 trading days\n"
    "  PCR > 1.3 = contrarian BULLISH | PCR < 0.7 = contrarian BEARISH\n"
    "  Do NOT force setups — SKIP is always valid"
)


def _build_deep_prompt(
    stock_pkg: dict,
    index_ctx: dict,
    direction: str,
    config:    dict,
) -> str:
    direction_instruction = (
        f"\n\nIMPORTANT: Analyse for {direction} setup ONLY. "
        "If no valid setup exists in that direction, return stage=SKIP."
        if direction != "AUTO"
        else ""
    )

    lot_size_instruction = (
        "\n\nIMPORTANT: lot_size is unknown for this symbol. "
        "Set lots=null, lot_size=null, max_risk_inr=null in your response. "
        "Still provide all other fields (entry/SL/target levels, option levels, scoring)."
        if stock_pkg.get("lot_size") is None
        else ""
    )

    payload = {
        "task":          "deep_analysis",
        "index_context": index_ctx,
        "stock":         stock_pkg,
    }

    instructions = (
        "Perform a full deep analysis of this stock using the Section 9 "
        "conviction scoring framework.\n"
        "Respond with ONLY a JSON object:\n"
        "{\n"
        '  "stage": "TRADE_READY | WATCH | ON_RADAR | SKIP",\n'
        '  "direction": "LONG | SHORT",\n'
        '  "conviction_score": 0-100,\n'
        '  "setup_type": "string",\n'
        '  "setup_maturity": "EARLY | DEVELOPING | READY",\n'
        '  "entry_zone_low": number,\n'
        '  "entry_zone_high": number,\n'
        '  "underlying_stop": number,\n'
        '  "underlying_target_1": number,\n'
        '  "underlying_target_2": number,\n'
        '  "option_type": "CE | PE",\n'
        '  "strike": number,\n'
        '  "expiry_date": "YYYY-MM-DD",\n'
        '  "entry_premium_low": number,\n'
        '  "entry_premium_high": number,\n'
        '  "stop_loss_premium": number,\n'
        '  "target_1_premium": number,\n'
        '  "target_2_premium": number,\n'
        '  "lots": integer,\n'
        '  "lot_size": integer,\n'
        '  "max_risk_inr": number,\n'
        '  "risk_reward": number,\n'
        '  "iv_assessment": "LOW | MEDIUM | HIGH | UNKNOWN",\n'
        '  "scoring_breakdown": {\n'
        '    "price_structure": 0-30,\n'
        '    "momentum_volume": 0-25,\n'
        '    "index_fo_context": 0-25,\n'
        '    "stock_fo": 0-10,\n'
        '    "market_context": 0-10\n'
        '  },\n'
        '  "signals_contributing": ["list of key signals"],\n'
        '  "claude_full_rationale": "full paragraph rationale",\n'
        '  "mentor_explanation": "explanation for learning",\n'
        '  "why_could_be_wrong": "key bear case / risk",\n'
        '  "skip_reason": null\n'
        "}"
        + direction_instruction
    )

    return json.dumps(payload, ensure_ascii=False) + "\n\n" + instructions + direction_instruction + lot_size_instruction


def _parse_json_response(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t[t.index("\n") + 1:]
    if t.endswith("```"):
        t = t[:t.rindex("```")]
    return json.loads(t.strip())


def _call_claude_deep(prompt_text: str, max_tokens: int = 3000) -> tuple[dict, int, int]:
    """
    Single deep analysis call. Returns (parsed_json, input_tokens, output_tokens).
    Retries 3× on rate-limit / 5xx with 5s/10s/20s backoff.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")

    client    = anthropic.Anthropic(api_key=api_key, max_retries=0)
    backoff   = [5, 10, 20]
    last_exc  = None

    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=_MODEL,
                max_tokens=max_tokens,
                system=[{
                    "type": "text",
                    "text": _DEEP_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": prompt_text}],
            )
            raw = resp.content[0].text
            parsed = _parse_json_response(raw)
            return parsed, resp.usage.input_tokens, resp.usage.output_tokens
        except anthropic.RateLimitError as exc:
            last_exc = exc
            time.sleep(backoff[min(attempt, 2)])
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                last_exc = exc
                time.sleep(backoff[min(attempt, 2)])
            else:
                raise HTTPException(status_code=502, detail=f"Claude API error: {exc.message}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502,
                                detail=f"Claude returned non-JSON response: {exc}")

    raise HTTPException(status_code=503,
                        detail=f"Claude API unavailable after 3 attempts: {last_exc}")


# ── Main endpoint ─────────────────────────────────────────────────────────────

@router.post("/analyse", response_model=AnalyseResponse)
async def analyse_stock(req: AnalyseRequest) -> AnalyseResponse:
    """
    On-demand deep analysis for a single stock.

    Bypasses Level 1 filter — user explicitly requested this stock.
    Still enforces RR gate, position sizing, and expiry selection rules
    (these are enforced by Claude's operating rules in the system prompt).
    """
    t_start = time.monotonic()
    symbol  = req.symbol

    # ── Feature gate ──────────────────────────────────────────────────────────
    config = get_all_system_config()
    if config.get("manual_analysis_enabled", "true").lower() != "true":
        raise HTTPException(status_code=503,
                            detail="Manual analysis is currently disabled (budget control)")

    # ── Symbol validation ─────────────────────────────────────────────────────
    nifty50_symbols = set(get_nifty50_symbols())
    is_nifty50      = symbol in nifty50_symbols
    custom_note     = (
        "Custom symbol — data availability not guaranteed. "
        "Ensure price history exists in the database."
        if not is_nifty50
        else None
    )
    logger.info("Manual analysis request: symbol=%s direction=%s nifty50=%s",
                symbol, req.direction, is_nifty50)

    # ── Session date ──────────────────────────────────────────────────────────
    session_date  = last_trading_day()
    quality_notes: list[str] = []

    if custom_note:
        quality_notes.append(custom_note)

    # ── Data assembly ─────────────────────────────────────────────────────────
    stock_pkg = _build_stock_package(symbol, session_date, quality_notes)
    if not stock_pkg:
        raise HTTPException(
            status_code=404,
            detail=f"No price history found for {symbol}. "
                   "Run the nightly pipeline first to populate data.",
        )

    index_ctx = _build_index_context(session_date)

    # ── Build and send prompt ─────────────────────────────────────────────────
    prompt = _build_deep_prompt(stock_pkg, index_ctx, req.direction, config)
    logger.info("Calling Claude for deep analysis: %s direction=%s", symbol, req.direction)

    analysis, input_tok, output_tok = _call_claude_deep(prompt, max_tokens=3000)

    # ── Log Turn ──────────────────────────────────────────────────────────────
    try:
        session_id = f"MANUAL_{session_date}_{datetime.now().strftime('%H%M%S')}"
        save_claude_turn(
            session_id=session_id,
            turn_number=1,
            turn_type="manual_deep",
            symbol=symbol,
            input_tokens=input_tok,
            output_tokens=output_tok,
            input_text=prompt,
            output_text=json.dumps(analysis),
        )
    except Exception as exc:
        logger.warning("Could not log manual turn: %s", exc)

    # ── Cost calculation ──────────────────────────────────────────────────────
    cost_usd = round(input_tok / 1_000_000 * 3.00 + output_tok / 1_000_000 * 15.00, 6)
    logger.info("Deep analysis done: %s stage=%s score=%s cost=$%.4f",
                symbol, analysis.get("stage"), analysis.get("conviction_score"), cost_usd)

    # ── Optional: save to trade_setups ───────────────────────────────────────
    setup_id = None
    if req.save_to_ledger and analysis.get("stage") not in ("SKIP", None):
        try:
            setup_id = create_trade_setup({
                "session_id":             f"MANUAL_{session_date}",
                "setup_date":             str(session_date),
                "symbol":                 symbol,
                "direction":              analysis.get("direction"),
                "stage":                  analysis.get("stage"),
                "setup_type":             analysis.get("setup_type"),
                "setup_maturity":         analysis.get("setup_maturity"),
                "conviction_score":       analysis.get("conviction_score"),
                "strike":                 analysis.get("strike"),
                "option_type":            analysis.get("option_type"),
                "expiry_date":            analysis.get("expiry_date"),
                "entry_zone_low":         analysis.get("entry_premium_low"),
                "entry_zone_high":        analysis.get("entry_premium_high"),
                "stop_loss_premium":      analysis.get("stop_loss_premium"),
                "target_1_premium":       analysis.get("target_1_premium"),
                "target_2_premium":       analysis.get("target_2_premium"),
                "underlying_stop":        analysis.get("underlying_stop"),
                "lots":                   analysis.get("lots"),
                "lot_size":               analysis.get("lot_size"),
                "max_risk_inr":           analysis.get("max_risk_inr"),
                "risk_reward":            analysis.get("risk_reward"),
                "iv_assessment":          analysis.get("iv_assessment"),
                "scoring_breakdown":      analysis.get("scoring_breakdown"),
                "signals_contributing":   analysis.get("signals_contributing", []),
                "claude_full_rationale":  analysis.get("claude_full_rationale"),
                "mentor_explanation":     analysis.get("mentor_explanation"),
                "why_could_be_wrong":     analysis.get("why_could_be_wrong"),
                "market_regime":          index_ctx.get("regime"),
                "vix_at_analysis":        index_ctx.get("vix"),
                "rollover_phase":         stock_pkg.get("rollover_phase"),
                "near_month_oi_at_flag":  stock_pkg.get("near_month_oi"),
                "next_month_oi_at_flag":  stock_pkg.get("next_month_oi"),
                "rollover_pct_at_flag":   stock_pkg.get("rollover_pct"),
            })
            logger.info("Setup saved to trade_setups: %s", setup_id)
        except Exception as exc:
            logger.warning("Failed to save setup to ledger: %s", exc)
            quality_notes.append(f"Note: Could not save to ledger — {exc}")

    return AnalyseResponse(
        symbol=symbol,
        session_date=str(session_date),
        is_nifty50=is_nifty50,
        custom_symbol_note=custom_note,
        analysis=analysis,
        estimated_cost_usd=cost_usd,
        data_quality_notes=quality_notes,
        duration_seconds=round(time.monotonic() - t_start, 2),
        setup_id=setup_id,
    )
