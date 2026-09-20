"""
Daily 8:00 AM IST Mon-Fri job: Option Sell Recommendation Engine.

Reads ACTIVE zones from key_levels, computes Black-Scholes delta and ROI
economics per strike, persists qualifying SELL_PE / SELL_CE recommendations,
and sends a Telegram summary. No LLM involvement.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from database.client import get_client
from database.queries import (
    get_latest_snapshot_date,
    get_options_snapshot,
    get_price_history,
)
from indicators.technical import calculate_atr
from new_data_ingestion.nse_bhavcopy import last_trading_day
from option_sell import config, engine
from option_sell.db import persist_recommendations
from option_sell.notifications import send_option_sell_failed, send_option_sell_summary

logger = logging.getLogger(__name__)


# ── Expiry helper (inlined from pipeline/level1_filter.py) ────────────────────

def _last_tuesday_of_month(ref: date) -> date:
    if ref.month == 12:
        last_day = date(ref.year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(ref.year, ref.month + 1, 1) - timedelta(days=1)
    delta = (last_day.weekday() - 1) % 7  # days back to Tuesday
    return last_day - timedelta(days=delta)


def _get_near_expiry(ref: date) -> date:
    """Near-month expiry = last Tuesday of current month (or next month if today >= expiry)."""
    expiry = _last_tuesday_of_month(ref)
    if ref >= expiry:
        next_month = (
            date(ref.year, ref.month % 12 + 1, 1)
            if ref.month < 12
            else date(ref.year + 1, 1, 1)
        )
        expiry = _last_tuesday_of_month(next_month)
    return expiry


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_symbols_with_active_zones() -> list[str]:
    """Return distinct symbols that have at least one ACTIVE zone in key_levels."""
    res = (
        get_client()
        .table("key_levels")
        .select("symbol")
        .eq("status", "ACTIVE")
        .execute()
    )
    seen: set[str] = set()
    symbols: list[str] = []
    for row in res.data or []:
        s = row.get("symbol")
        if s and s not in seen:
            seen.add(s)
            symbols.append(s)
    return sorted(symbols)


def _get_active_zones_for_symbol(symbol: str) -> list[dict]:
    """Fetch all ACTIVE key_levels rows for a symbol (includes id for zone_id FK)."""
    res = (
        get_client()
        .table("key_levels")
        .select(
            "id,symbol,level_type,zone_low,zone_high,conviction,"
            "touch_count,last_touch_date,confluence_flags,reasoning,analysis_date"
        )
        .eq("symbol", symbol)
        .eq("status", "ACTIVE")
        .execute()
    )
    return res.data or []


# ── Per-symbol processing ─────────────────────────────────────────────────────

def _process_symbol(symbol: str, analysis_date: date, expiry_date: date) -> list[dict]:
    """
    Fetch OHLCV, zones, and options chain for one symbol; run the engine.
    Returns a (possibly empty) list of recommendation dicts.
    """
    # 1. OHLCV → ATR
    raw_rows = get_price_history(symbol, config.OHLCV_LOOKBACK)
    if not raw_rows:
        logger.warning("%s: no OHLCV data — skipping", symbol)
        return []

    df = pd.DataFrame(raw_rows)
    df["high"]  = df["high"].astype(float)
    df["low"]   = df["low"].astype(float)
    df["close"] = df["close"].astype(float)

    atr_series = calculate_atr(df, period=config.ATR_PERIOD)
    atr = float(atr_series.iloc[-1]) if atr_series is not None and len(atr_series) > 0 else 0.0
    if atr <= 0:
        logger.warning("%s: ATR=0 — using 1%% of spot as fallback", symbol)
        atr = float(df["close"].iloc[-1]) * 0.01

    spot = float(df["close"].iloc[-1])

    # 2. Active zones
    zones = _get_active_zones_for_symbol(symbol)
    if not zones:
        logger.info("%s: no active zones", symbol)
        return []

    # 3. Options chain — try analysis_date, fall back to latest snapshot
    chain_rows = get_options_snapshot(symbol, analysis_date, expiry_date)
    if not chain_rows:
        latest_snap = get_latest_snapshot_date(symbol)
        if latest_snap and latest_snap != analysis_date:
            logger.warning(
                "%s: no options snapshot for %s — falling back to %s",
                symbol, analysis_date, latest_snap,
            )
            chain_rows = get_options_snapshot(symbol, latest_snap, expiry_date)
    if not chain_rows:
        logger.warning("%s: options chain empty after fallback — skipping", symbol)
        return []

    # 4. Run engine
    return engine.run_symbol(
        symbol=symbol,
        analysis_date=analysis_date,
        spot=spot,
        zones=zones,
        chain_rows=chain_rows,
        atr=atr,
        expiry_date=expiry_date,
    )


# ── Job entry point ───────────────────────────────────────────────────────────

def run_option_sell_job(analysis_date: date | None = None) -> dict:
    """
    Main job entry point. Runs for every symbol with an ACTIVE key-level zone.

    Parameters
    ----------
    analysis_date : date, optional
        Override the trading date; defaults to last_trading_day().

    Returns
    -------
    dict with keys: analysis_date, symbols_processed, recommendations_count.
    """
    if analysis_date is None:
        analysis_date = last_trading_day()

    expiry_date = _get_near_expiry(analysis_date)
    logger.info(
        "option_sell job: analysis_date=%s, expiry_date=%s", analysis_date, expiry_date
    )

    try:
        symbols = _get_symbols_with_active_zones()
    except Exception as exc:
        msg = f"Failed to load symbols: {exc}"
        logger.error(msg)
        send_option_sell_failed(analysis_date, msg)
        return {"analysis_date": str(analysis_date), "symbols_processed": 0, "recommendations_count": 0}

    all_recs: list[dict] = []
    for symbol in symbols:
        try:
            recs = _process_symbol(symbol, analysis_date, expiry_date)
            if recs:
                persist_recommendations(recs)
                all_recs.extend(recs)
        except Exception as exc:
            logger.error("%s: processing failed — %s", symbol, exc)

    send_option_sell_summary(analysis_date, all_recs)

    result = {
        "analysis_date":          str(analysis_date),
        "symbols_processed":      len(symbols),
        "recommendations_count":  len(all_recs),
    }
    logger.info("option_sell job complete: %s", result)
    return result
