"""
Core option-sell recommendation engine.

run_symbol(symbol, analysis_date, spot, zones, chain_rows, atr) -> list[dict]

For each symbol:
  1. Pick nearest qualifying SUPPORT (SELL_PE) and RESISTANCE (SELL_CE) zones.
  2. Project a strike target using BUFFER_MULTIPLIER * ATR.
  3. Snap to the nearest real strike in the options chain.
  4. Compute Black-Scholes delta and ROI economics.
  5. Filter by delta band, OI, and ROI thresholds.
  6. Return recommendation dicts (0-2 per symbol).
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any

from option_sell.blackscholes import bs_delta
from option_sell import config

logger = logging.getLogger(__name__)


# ── Staleness check ────────────────────────────────────────────────────────────

def _is_stale(zone: dict, ref_date: date) -> bool:
    """
    A zone is stale if its last_touch_date is older than STALENESS_DAYS
    AND touch_count is below the HIGH_TOUCH_OVERRIDE threshold.
    """
    touch_count = zone.get("touch_count") or 0
    if touch_count >= config.HIGH_TOUCH_OVERRIDE:
        return False  # high-conviction by touch count; never stale

    last_touch = zone.get("last_touch_date")
    if not last_touch:
        return True  # no touch date at all → treat as stale

    try:
        if isinstance(last_touch, str):
            last_touch_date = date.fromisoformat(last_touch)
        else:
            last_touch_date = last_touch
    except (ValueError, TypeError):
        return True

    return (ref_date - last_touch_date).days > config.STALENESS_DAYS


# ── Strike snapping ────────────────────────────────────────────────────────────

def _snap_strike(chain_rows: list[dict], target: float, option_type: str) -> dict | None:
    """Return the chain row whose strike is closest to `target`."""
    candidates = [r for r in chain_rows if r.get("option_type") == option_type]
    if not candidates:
        return None
    return min(candidates, key=lambda r: abs(float(r["strike"]) - target))


# ── Per-direction recommendation builder ───────────────────────────────────────

def _build_rec(
    *,
    symbol: str,
    analysis_date: date,
    action: str,
    zone: dict,
    chain_row: dict,
    spot: float,
    expiry_date: date,
) -> dict | None:
    """
    Compute delta + ROI for one candidate. Returns None if any filter fails.
    action is 'SELL_PE' or 'SELL_CE'.
    """
    option_type = "PE" if action == "SELL_PE" else "CE"
    strike = float(chain_row["strike"])
    premium = chain_row.get("premium_close")
    oi = chain_row.get("oi") or 0
    iv_pct = chain_row.get("iv")

    # Guard: missing or unusable data
    if premium is None or premium <= 0:
        logger.debug("%s %s: skipped — no premium (strike=%s)", symbol, action, strike)
        return None
    if iv_pct is None or iv_pct <= 0:
        iv_pct = config.FALLBACK_IV_PCT
        logger.debug("%s %s: IV missing — using fallback %.1f%%", symbol, action, iv_pct)
    if oi < config.MIN_OI:
        logger.debug("%s %s: skipped — OI %d < %d (strike=%s)", symbol, action, oi, config.MIN_OI, strike)
        return None

    days_to_expiry = (expiry_date - analysis_date).days
    if days_to_expiry <= 0:
        logger.debug("%s %s: skipped — expiry is today or past", symbol, action)
        return None

    t_years = days_to_expiry / 365.0
    iv_decimal = iv_pct / 100.0
    delta = bs_delta(spot, strike, iv_decimal, t_years, config.RISK_FREE_RATE, option_type)

    if math.isnan(delta):
        logger.debug("%s %s: skipped — delta is nan (strike=%s, iv=%.2f)", symbol, action, strike, iv_decimal)
        return None

    abs_delta = abs(delta)
    if not (config.DELTA_BAND_LOW <= abs_delta <= config.DELTA_BAND_HIGH):
        logger.debug(
            "%s %s: skipped — |delta|=%.3f not in [%.2f, %.2f]",
            symbol, action, abs_delta, config.DELTA_BAND_LOW, config.DELTA_BAND_HIGH,
        )
        return None

    # ROI: SELL_PE collateral is strike (cash-secured), SELL_CE reference is spot (covered call)
    roi_pct = premium / strike if action == "SELL_PE" else premium / spot
    if roi_pct < config.MIN_ROI_PCT:
        logger.debug("%s %s: skipped — ROI %.4f%% < min %.4f%%", symbol, action, roi_pct * 100, config.MIN_ROI_PCT * 100)
        return None

    annualized_pct = roi_pct * (365.0 / days_to_expiry)

    zone_low  = float(zone.get("zone_low")  or 0)
    zone_high = float(zone.get("zone_high") or 0)
    conviction     = zone.get("conviction") or ""
    level_type     = zone.get("level_type") or ""
    touch_count    = zone.get("touch_count") or 0
    last_touch     = zone.get("last_touch_date") or "unknown"

    rationale = (
        f"SELL {strike:.0f} {option_type} (exp {expiry_date}) — anchored to {conviction} conviction "
        f"{level_type} zone {zone_low:.1f}–{zone_high:.1f} ({touch_count} touches, last {last_touch}). "
        f"Delta {delta:.2f}, premium ₹{premium:.2f}, ROI {roi_pct:.2%} (~{annualized_pct:.1%} ann)."
    )

    return {
        "symbol":          symbol,
        "analysis_date":   str(analysis_date),
        "action":          action,
        "zone_id":         zone.get("id"),
        "spot_price":      round(spot, 2),
        "strike":          round(strike, 2),
        "expiry_date":     str(expiry_date),
        "premium":         round(float(premium), 2),
        "oi":              int(oi),
        "iv":              round(iv_decimal * 100, 4),
        "computed_delta":  round(delta, 4),
        "roi_pct":         round(roi_pct, 6),
        "annualized_pct":  round(annualized_pct, 6),
        "conviction":      conviction,
        "rationale":       rationale,
        "status":          "ACTIVE",
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def run_symbol(
    symbol: str,
    analysis_date: date,
    spot: float,
    zones: list[dict],
    chain_rows: list[dict],
    atr: float,
    expiry_date: date,
) -> list[dict]:
    """
    Evaluate one symbol and return a list of qualifying recommendations (0-2).
    """
    recommendations: list[dict] = []

    # ── SELL_PE: nearest SUPPORT strictly below spot ──────────────────────────
    support_zones = [
        z for z in zones
        if z.get("level_type") == "SUPPORT"
        and (z.get("zone_high") or 0) < spot
        and z.get("conviction") != "LOW"
        and not _is_stale(z, analysis_date)
    ]
    nearest_support = max(support_zones, key=lambda z: float(z.get("zone_high") or 0), default=None)

    if nearest_support:
        pe_target = float(nearest_support.get("zone_low") or 0) - (config.BUFFER_MULTIPLIER * atr)
        pe_row = _snap_strike(chain_rows, pe_target, "PE")
        if pe_row:
            rec = _build_rec(
                symbol=symbol,
                analysis_date=analysis_date,
                action="SELL_PE",
                zone=nearest_support,
                chain_row=pe_row,
                spot=spot,
                expiry_date=expiry_date,
            )
            if rec:
                recommendations.append(rec)

    # ── SELL_CE: nearest RESISTANCE strictly above spot ───────────────────────
    resist_zones = [
        z for z in zones
        if z.get("level_type") == "RESISTANCE"
        and (z.get("zone_low") or 0) > spot
        and z.get("conviction") != "LOW"
        and not _is_stale(z, analysis_date)
    ]
    nearest_resist = min(resist_zones, key=lambda z: float(z.get("zone_low") or 0), default=None)

    if nearest_resist:
        ce_target = float(nearest_resist.get("zone_high") or 0) + (config.BUFFER_MULTIPLIER * atr)
        ce_row = _snap_strike(chain_rows, ce_target, "CE")
        if ce_row:
            rec = _build_rec(
                symbol=symbol,
                analysis_date=analysis_date,
                action="SELL_CE",
                zone=nearest_resist,
                chain_row=ce_row,
                spot=spot,
                expiry_date=expiry_date,
            )
            if rec:
                recommendations.append(rec)

    logger.info(
        "%s: %d recommendation(s) (spot=%.2f, support=%s, resist=%s)",
        symbol,
        len(recommendations),
        spot,
        nearest_support.get("zone_high") if nearest_support else "none",
        nearest_resist.get("zone_low") if nearest_resist else "none",
    )
    return recommendations
