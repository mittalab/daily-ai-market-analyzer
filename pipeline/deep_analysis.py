"""
Shared deep analysis logic — used by both nightly pipeline (claude_session.py)
and the manual analysis API (api/manual_analysis.py).

Exports:
  build_stock_package    — 180-day data package for one symbol
  build_deep_prompt      — formats Claude deep analysis prompt
  validate_position_sizing — Python-authoritative position sizing (FIX 2)
  call_claude_deep       — single deep analysis API call
  DEEP_SYSTEM            — system prompt constant
"""
import json
import logging
import os
import time
from datetime import date, timedelta

import anthropic
import pandas as pd

from database.queries import (
    get_all_lot_sizes,
    get_continuous_oi,
    get_futures_series,
    get_options_snapshot,
    get_price_history,
    get_recent_setups_for_symbol,
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

logger = logging.getLogger(__name__)

CAPITAL_INR  = 500_000     # ₹5 lakh
MAX_RISK_PCT = 0.025       # 2.5% per trade
MAX_LOTS     = 5
_MODEL       = "claude-sonnet-4-6"

_SECTOR_MAP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "config", "sector_map.json"
)

DEEP_SYSTEM = (
    "You are an experienced hedge fund manager and swing trading mentor "
    "specialising in Indian F&O markets (Nifty 50 stocks, 2-5 day holds, "
    "stock options only — monthly Tuesday expiry).\n\n"
    "Operating rules:\n"
    "  Capital: ₹5,00,000 | Risk per trade: 2-3% | Min RR: 1:2\n"
    "  Instruments: stock options ONLY | Min DTE: 6 trading days\n"
    "  PCR > 1.3 = contrarian BULLISH | PCR < 0.7 = contrarian BEARISH\n"
    "  Do NOT force setups — SKIP is always valid"
)


# ── Sector map ────────────────────────────────────────────────────────────────

def _load_sector_map() -> dict:
    try:
        with open(_SECTOR_MAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"stocks": {}}


def _sector_info(symbol: str) -> tuple[str, str]:
    entry = _load_sector_map().get("stocks", {}).get(symbol, {})
    return entry.get("sector", "UNKNOWN"), entry.get("index", "UNKNOWN")


# ── Options helpers ───────────────────────────────────────────────────────────

def _iv_assessment(iv_pct: float | None) -> str:
    if iv_pct is None:
        return "UNKNOWN"
    if iv_pct < 15:
        return "LOW"
    if iv_pct <= 25:
        return "MEDIUM"
    return "HIGH"


def _atm_iv(options: list[dict], spot: float | None) -> float | None:
    if not options or spot is None:
        return None
    ce_rows = [r for r in options if r.get("option_type") == "CE" and r.get("implied_volatility")]
    if not ce_rows:
        return None
    closest = min(ce_rows, key=lambda r: abs(float(r["strike"]) - spot))
    iv = closest.get("implied_volatility")
    return round(float(iv), 2) if iv else None


def _oi_walls(options: list[dict], near_expiry_str: str, top_n: int = 5) -> dict:
    near = [r for r in options if str(r.get("expiry_date", "")) == near_expiry_str]
    ce   = sorted([r for r in near if r["option_type"] == "CE"],
                  key=lambda r: int(r.get("oi") or 0), reverse=True)[:top_n]
    pe   = sorted([r for r in near if r["option_type"] == "PE"],
                  key=lambda r: int(r.get("oi") or 0), reverse=True)[:top_n]
    return {
        "ce_walls": [{"strike": r["strike"], "oi": r.get("oi")} for r in ce],
        "pe_walls": [{"strike": r["strike"], "oi": r.get("oi")} for r in pe],
    }


# ── Kite fallbacks ────────────────────────────────────────────────────────────

def _fetch_lot_size_from_kite(symbol: str) -> int | None:
    try:
        from integrations.kite_oauth import get_authenticated_kite
        kite        = get_authenticated_kite()
        instruments = pd.DataFrame(kite.instruments("NFO"))
        fut = instruments[
            (instruments["name"] == symbol) &
            (instruments["instrument_type"] == "FUT")
        ]
        if fut.empty:
            logger.warning("No NFO futures for %s in Kite instruments", symbol)
            return None
        lot_size = int(fut.iloc[0]["lot_size"])
        upsert_single_lot_size(symbol, lot_size)
        logger.info("Lot size fetched from Kite: %s = %d (cached)", symbol, lot_size)
        return lot_size
    except Exception as exc:
        logger.warning("Kite lot size fallback failed for %s: %s", symbol, exc)
        return None


def _fetch_ohlcv_on_demand(symbol: str) -> bool:
    """Fetch 180 days of OHLCV for any NSE equity on-demand and store in price_history."""
    try:
        from integrations.kite_oauth import get_authenticated_kite
        from integrations.kite_ohlcv import fetch_ohlcv, get_equity_token, ohlcv_to_price_rows
        kite      = get_authenticated_kite()
        token     = get_equity_token(kite, symbol)
        to_date   = date.today()
        from_date = to_date - timedelta(days=250)
        df        = fetch_ohlcv(kite, token, from_date, to_date)
        if df.empty:
            return False
        rows = ohlcv_to_price_rows(symbol, df)
        upsert_price_history(rows)
        logger.info("On-demand OHLCV: %s — %d rows stored", symbol, len(rows))
        return True
    except Exception as exc:
        logger.warning("On-demand OHLCV failed for %s: %s", symbol, exc)
        return False


# ── Data package ──────────────────────────────────────────────────────────────

def build_stock_package(symbol: str, session_date: date, quality_notes: list) -> dict:
    """
    Assemble full Section 8 data package for one stock.
    Appends data-quality warnings to quality_notes in-place.
    Returns {} if price history is unavailable.
    """
    # Price history: 6 months
    price_rows = get_price_history(symbol, days=250)
    if not price_rows:
        logger.info("No price history for %s — attempting on-demand Kite fetch", symbol)
        if _fetch_ohlcv_on_demand(symbol):
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

    ema20_s  = calculate_ema(closes, 20)
    ema50_s  = calculate_ema(closes, 50)  if len(df) >= 50  else ema20_s
    ema200_s = calculate_ema(closes, 200) if len(df) >= 200 else None
    rsi_s    = calculate_rsi(closes, 14)
    atrp_s   = atr_pct(df, 14)
    volr_s   = volume_ratio(df["volume"], short=3, long=20)
    macd_l, macd_sig, macd_hist = calculate_macd(closes)

    def _last(s: pd.Series) -> float | None:
        v = s.iloc[-1]
        return round(float(v), 4) if not pd.isna(v) else None

    # Lot size
    lot_sizes = get_all_lot_sizes()
    lot_size  = lot_sizes.get(symbol)
    if lot_size is None:
        logger.info("Lot size missing for %s — trying Kite instruments master", symbol)
        lot_size = _fetch_lot_size_from_kite(symbol)
    if lot_size is None:
        quality_notes.append(f"Lot size unknown for {symbol}")

    # OI series: 30 days
    oi_rows         = get_continuous_oi(symbol, days=30)
    latest_oi       = oi_rows[-1] if oi_rows else {}
    rollover_phase  = latest_oi.get("rollover_phase", "UNKNOWN")
    near_expiry_str = latest_oi.get("near_expiry")
    next_expiry_str = latest_oi.get("next_expiry")
    if not oi_rows:
        quality_notes.append(f"No OI series data for {symbol}")

    # Options snapshot
    options  = []
    atm_iv   = None
    oi_walls = {}
    if near_expiry_str:
        try:
            near_exp_date = date.fromisoformat(near_expiry_str)
            options = get_options_snapshot(symbol, session_date, near_exp_date)
        except Exception:
            pass
    if not options and near_expiry_str:
        yesterday = session_date - timedelta(days=1)
        try:
            near_exp_date = date.fromisoformat(near_expiry_str)
            options = get_options_snapshot(symbol, yesterday, near_exp_date)
            if options:
                quality_notes.append(f"IV data from {yesterday} (yesterday's snapshot)")
        except Exception:
            pass
    if not options:
        quality_notes.append(f"No options snapshot for {symbol} — IV unavailable")
    else:
        atm_iv   = _atm_iv(options, spot)
        oi_walls = _oi_walls(options, near_expiry_str or "")

    # Futures series: 30 days
    fut_rows   = get_futures_series(symbol, days=30)
    latest_fut = fut_rows[-1] if fut_rows else {}
    if not fut_rows:
        quality_notes.append(f"No futures series data for {symbol}")

    # Previous trade setups: last 3 for this symbol
    previous_setups = get_recent_setups_for_symbol(symbol, limit=3)

    # Sector context
    sector, sector_index = _sector_info(symbol)
    sector_rows = get_price_history(sector_index, days=30) if sector_index != "UNKNOWN" else []
    nifty_rows  = get_price_history("NIFTY_50", days=30)

    # Relative performance metrics
    sector_20d_ret = None
    nifty_20d_ret  = None
    sector_vs_nifty = None

    if len(sector_rows) >= 20:
        s_now = float(sector_rows[-1]["close"])
        s_old = float(sector_rows[-20]["close"])
        sector_20d_ret = round((s_now - s_old) / s_old * 100, 2)
    
    if len(nifty_rows) >= 20:
        n_now = float(nifty_rows[-1]["close"])
        n_old = float(nifty_rows[-20]["close"])
        nifty_20d_ret = round((n_now - n_old) / n_old * 100, 2)
        
    if sector_20d_ret is not None and nifty_20d_ret is not None:
        sector_vs_nifty = round(sector_20d_ret - nifty_20d_ret, 2)

    return {
        "symbol":         symbol,
        "sector":         sector,
        "sector_index":   sector_index,
        "spot_price":     spot,
        "lot_size":       lot_size,
        # indicators
        "ema20":          round(float(ema20_s.iloc[-1]), 2),
        "ema50":          round(float(ema50_s.iloc[-1]), 2),
        "ema200":         round(float(ema200_s.iloc[-1]), 2) if ema200_s is not None else None,
        "rsi14":          _last(rsi_s),
        "atr_pct14":      _last(atrp_s),
        "vol_ratio":      _last(volr_s),
        "macd":           _last(macd_l),
        "macd_signal":    _last(macd_sig),
        "macd_hist":      _last(macd_hist),
        # Sector metrics
        "sector_20d_return":  sector_20d_ret,
        "nifty_20d_return":   nifty_20d_ret,
        "sector_vs_nifty":    sector_vs_nifty,
        "sector_status":      "TAILWIND" if (sector_vs_nifty or 0) > 0 else "HEADWIND",
        # OI / options
        "rollover_phase": rollover_phase,
        "near_expiry":    near_expiry_str,
        "next_expiry":    next_expiry_str,
        "atm_iv_pct":     atm_iv,
        "iv_assessment":  _iv_assessment(atm_iv),
        "oi_walls":       oi_walls,
        "near_month_oi":  latest_oi.get("near_month_oi"),
        "pcr_near":       latest_oi.get("pcr_near"),
        "max_pain":       latest_oi.get("max_pain"),
        "rollover_pct":   latest_oi.get("rollover_pct"),
        # futures
        "futures_price":  latest_fut.get("futures_price"),
        "basis":          latest_fut.get("basis"),
        "basis_pct":      latest_fut.get("basis_pct"),
        # time series
        "ohlcv_120d":          [
            {"date": r["date"], "open": r["open"], "high": r["high"],
             "low": r["low"], "close": r["close"], "volume": r["volume"]}
            for r in price_rows[-120:]
        ],
        "oi_series_30d":       [
            {"date": r["date"], "near_oi": r.get("near_month_oi"),
             "next_oi": r.get("next_month_oi"), "oi_change": r.get("oi_change"),
             "pcr_near": r.get("pcr_near"), "max_pain": r.get("max_pain"),
             "rollover_pct": r.get("rollover_pct"), "is_expiry_day": r.get("is_expiry_day")}
            for r in oi_rows
        ],
        "futures_series_30d":  [
            {"date": r["date"], "futures_price": r.get("futures_price"),
             "near_oi": r.get("near_month_oi"), "basis": r.get("basis"),
             "basis_pct": r.get("basis_pct"), "rollover_pct": r.get("rollover_pct")}
            for r in fut_rows
        ],
        "options_chain":       [
            {"strike": r["strike"], "type": r["option_type"],
             "oi": r.get("oi"), "iv": r.get("implied_volatility")}
            for r in options
        ],
        "sector_index_20d":     [
            {"date": r["date"], "close": r["close"]} for r in sector_rows[-20:]
        ],
        "previous_setups":     [
            {
                "setup_date":      r.get("setup_date"),
                "direction":       r.get("direction"),
                "conviction_score": r.get("conviction_score"),
                "stage":           r.get("stage"),
                "paper_outcome":   r.get("paper_outcome"),
                "setup_type":      r.get("setup_type"),
            }
            for r in previous_setups
        ],
    }


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_deep_prompt(stock_pkg: dict, index_ctx: dict, direction: str) -> str:
    direction_instruction = (
        f"\n\nIMPORTANT: Analyse for {direction} setup ONLY. "
        "If no valid setup exists in that direction, return stage=SKIP."
        if direction not in ("AUTO", None, "")
        else ""
    )
    lot_size_instruction = (
        "\n\nIMPORTANT: lot_size is unknown for this symbol. "
        "Set lots=null, lot_size=null, max_risk_inr=null in your response. "
        "Still provide all other fields."
        if stock_pkg.get("lot_size") is None
        else ""
    )
    payload = {"task": "deep_analysis", "index_context": index_ctx, "stock": stock_pkg}
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
    )
    return (
        json.dumps(payload, ensure_ascii=False) + "\n\n"
        + instructions + direction_instruction + lot_size_instruction
    )


# ── Claude call ───────────────────────────────────────────────────────────────

def call_claude_deep(
    client: anthropic.Anthropic,
    prompt_text: str,
    max_tokens: int = 3000,
) -> tuple[dict, int, int]:
    """
    Single deep analysis call using an existing Anthropic client.
    Returns (parsed_json, input_tokens, output_tokens).
    Retries 3× on rate-limit / 5xx.
    """
    backoff  = [5, 10, 20]
    last_exc = None

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
                messages=[{"role": "user", "content": prompt_text}],
            )
            raw = resp.content[0].text
            t   = raw.strip()
            if t.startswith("```"):
                t = t[t.index("\n") + 1:]
            if t.endswith("```"):
                t = t[:t.rindex("```")]
            parsed = json.loads(t.strip())
            return parsed, resp.usage.input_tokens, resp.usage.output_tokens
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            last_exc = exc
            logger.warning("Network error in deep analysis (attempt %d/3): %s", attempt + 1, exc)
            time.sleep(backoff[min(attempt, 2)])
        except anthropic.RateLimitError as exc:
            last_exc = exc
            time.sleep(backoff[min(attempt, 2)])
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                last_exc = exc
                time.sleep(backoff[min(attempt, 2)])
            else:
                raise

    raise RuntimeError(f"Claude deep analysis failed after 3 attempts") from last_exc


# ── Position sizing (FIX 2) ───────────────────────────────────────────────────

def validate_position_sizing(analysis: dict, config: dict) -> dict:
    """
    Python-authoritative position sizing. Overwrites Claude's lots/risk/rr.
    Rejects setup (lots=0) if single-lot risk exceeds 3% of capital.
    """
    capital  = float(config.get("capital_inr", CAPITAL_INR))
    max_risk = capital * MAX_RISK_PCT

    lot_size   = analysis.get("lot_size")
    entry_low  = analysis.get("entry_premium_low")
    entry_high = analysis.get("entry_premium_high")
    stop_loss  = analysis.get("stop_loss_premium")
    target_2   = analysis.get("target_2_premium")

    if not all(x is not None for x in [lot_size, entry_low, entry_high, stop_loss, target_2]):
        logger.info("Position sizing: skipped for %s — missing fields", analysis.get("stage"))
        return analysis

    entry_mid    = (float(entry_low) + float(entry_high)) / 2.0
    risk_per_lot = (entry_mid - float(stop_loss)) * int(lot_size)

    if risk_per_lot <= 0:
        analysis.update({"lots": 0, "max_risk_inr": 0.0, "risk_reward": 0.0})
        logger.warning("Position sizing: risk_per_lot <= 0 — setup rejected")
        return analysis

    # Hard reject if one lot already exceeds 3% of capital
    if risk_per_lot > capital * 0.03:
        analysis.update({"lots": 0, "max_risk_inr": round(risk_per_lot, 0)})
        logger.warning(
            "Position sizing: single-lot risk ₹%.0f > 3%% of capital — setup rejected",
            risk_per_lot,
        )
        return analysis

    lots        = max(1, min(int(max_risk / risk_per_lot), MAX_LOTS))
    actual_risk = risk_per_lot * lots
    actual_rr   = (float(target_2) - entry_mid) / (entry_mid - float(stop_loss))

    analysis["lots"]         = lots
    analysis["max_risk_inr"] = round(actual_risk, 0)
    analysis["risk_reward"]  = round(actual_rr, 2)

    logger.info(
        "Position sizing verified by Python: lots=%d risk=₹%.0f rr=%.2f",
        lots, actual_risk, actual_rr,
    )
    return analysis
