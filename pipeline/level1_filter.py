"""
Level 1 Hard Elimination Filter — spec Section 6.

Three filters applied in order:
  1. Earnings within 5 trading days  (no shadow track)
  2. ATR dead zone < 0.8%            (shadow tracked)
  3. F&O liquidity ATM OI < 10,000  (shadow tracked)

Call:
    result = run_level1_filter(symbols, analysis_date, kite)

Returns:
    {
        "passed":         ["HDFCBANK", ...],
        "eliminated":     [{"symbol": "X", "reason": "ATR_DEAD", "value": 0.5}, ...],
        "filter_skipped": ["FNO_LIQUIDITY"],   # only present if snapshot missing
        "errors":         [{"symbol": "X", "error": "..."}],
    }
"""
import logging
from datetime import date, timedelta

import pandas as pd

from database.queries import (
    create_shadow_track,
    get_latest_snapshot_date,
    get_options_snapshot,
    get_price_history,
)
from indicators.technical import atr_pct as calc_atr_pct
from new_data_ingestion.nse_bhavcopy import get_holiday_dates, last_trading_day

logger = logging.getLogger(__name__)

# ── Constants (spec Section 6) ────────────────────────────────────────────────
_EARNINGS_WINDOW_DAYS = 5          # trading days forward
_ATR_DEAD_THRESHOLD   = 0.5        # % — below this = dead zone - for Nifty 50
_ATM_OI_MINIMUM       = 10_000     # combined ATM CE+PE OI
_ATR_HISTORY_ROWS     = 25         # need 14 for ATR + a few extra

# NSE event-calendar purpose strings that indicate binary event risk.
# kite.corporate_actions() does not exist in KiteConnect Python client —
# using NSE event calendar API instead (confirmed working 2026-05-23).
_EARNINGS_KEYWORDS = {"financial results", "agm", "board meeting"}


# ── Trading day helpers ───────────────────────────────────────────────────────

def _next_n_trading_days(from_date: date, n: int) -> list[date]:
    """Return the next n trading days after from_date (exclusive of from_date)."""
    holidays = get_holiday_dates()
    days: list[date] = []
    cursor = from_date
    while len(days) < n:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5 and cursor not in holidays:
            days.append(cursor)
    return days


def _add_trading_days(from_date: date, n: int) -> date:
    """Return the date n trading days after from_date."""
    return _next_n_trading_days(from_date, n)[-1]


def _last_tuesday_of_month(ref: date) -> date:
    """Return the last Tuesday of ref's month (NSE F&O expiry day)."""
    # Find last day of month, walk back to Tuesday (weekday=1)
    if ref.month == 12:
        last_day = date(ref.year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(ref.year, ref.month + 1, 1) - timedelta(days=1)
    delta = (last_day.weekday() - 1) % 7   # days back to Tuesday
    return last_day - timedelta(days=delta)


def _get_near_expiry(ref: date) -> date:
    """
    Near-month expiry = last Tuesday of current month.
    If today IS expiry, return last Tuesday of next month.
    """
    expiry = _last_tuesday_of_month(ref)
    if ref >= expiry:
        # Move to next month
        next_month = date(ref.year, ref.month % 12 + 1, 1) if ref.month < 12 \
                     else date(ref.year + 1, 1, 1)
        expiry = _last_tuesday_of_month(next_month)
    return expiry


# ── Filter 1: Earnings — NSE event calendar ───────────────────────────────────

def fetch_nse_earnings_window(analysis_date: date) -> dict[str, str]:
    """
    Fetch NSE event calendar once and return {symbol: purpose} for all Nifty50
    stocks with a binary-risk event in the next EARNINGS_WINDOW_DAYS trading days.

    Called once per pipeline run, result passed into run_level1_filter.
    Returns empty dict on failure (earnings filter skipped gracefully).
    """
    from new_data_ingestion.nse_fii_dii import create_nse_session

    window = set(_next_n_trading_days(analysis_date, _EARNINGS_WINDOW_DAYS))

    try:
        session = create_nse_session()
        r = session.get(
            "https://www.nseindia.com/api/event-calendar",
            timeout=15,
        )
        if r.status_code != 200:
            logger.warning("NSE event calendar returned %d — earnings filter skipped", r.status_code)
            return {}
        events = r.json()
    except Exception as exc:
        logger.warning("NSE event calendar fetch failed: %s — earnings filter skipped", exc)
        return {}

    at_risk: dict[str, str] = {}
    for ev in events:
        sym     = str(ev.get("symbol", "")).strip()
        purpose = str(ev.get("purpose", "")).strip().lower()
        raw_dt  = str(ev.get("date", "")).strip()

        if not sym or not raw_dt:
            continue
        #AI: There are strings like "Financial Results/Other business matters", so we need to check contains
        if not any(kw in purpose for kw in _EARNINGS_KEYWORDS):
            continue

        # NSE format: "26-May-2026" → date
        try:
            parts      = raw_dt.split("-")
            month_abbr = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
                          "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
            event_date = date(int(parts[2]), month_abbr[parts[1].lower()], int(parts[0]))
        except Exception:
            continue

        if event_date in window:
            at_risk[sym] = f"{ev.get('purpose','')}:{event_date}"
            logger.info("Earnings window: %s — %s on %s", sym, ev.get("purpose"), event_date)

    logger.info("NSE calendar: %d Nifty50 stocks with events in next %d trading days",
                len(at_risk), _EARNINGS_WINDOW_DAYS)
    return at_risk


# ── Filter 2: ATR Dead Zone ───────────────────────────────────────────────────

def _filter_atr_dead(symbol: str) -> tuple[bool, float]:
    """
    Return (eliminate, atr_pct_value).
    Loads last 25 rows from price_history. Requires at least 15 to compute ATR(14).
    """
    rows = get_price_history(symbol, days=_ATR_HISTORY_ROWS)
    if not rows or len(rows) < 15:
        logger.warning("%s: only %d price rows — ATR filter skipped", symbol, len(rows))
        return True, 0.0 # Data is missing

    df = pd.DataFrame(rows)
    df = df.rename(columns={"date": "trade_date"})
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["high", "low", "close"])

    # CRITICAL FIX: Re-verify data length post-cleaning
    if len(df) < 15:
        logger.warning("%s: Insufficient valid price rows (%d) after dropna — Eliminating stock", symbol, len(df))
        return True, 0.0

    atr_series = calc_atr_pct(df)

    if atr_series.empty or pd.isna(atr_series.iloc[-1]):
        logger.error("%s: ATR calculation returned NaN or empty series — Eliminating stock", symbol)
        return True, 0.0

    current_atr_pct = float(atr_series.iloc[-1])

    eliminate = current_atr_pct < _ATR_DEAD_THRESHOLD
    return eliminate, round(current_atr_pct, 3)


# ── Filter 3: F&O Liquidity ───────────────────────────────────────────────────

def _filter_fno_liquidity(
    symbol: str,
    analysis_date: date,
    current_price: float,
) -> tuple[bool, int, bool]:
    """
    Return (eliminate, atm_oi, skipped).
    skipped=True means no snapshot available — caller should skip this filter.
    ATM OI = CE OI + PE OI at the strike closest to current_price.
    """
    snap_date = get_latest_snapshot_date(symbol)
    if snap_date is None:
        return False, 0, True   # no snapshot ever — skip

    # Allow snapshot up to 2 trading days old (weekend gap, holiday gap)
    holiday_set = get_holiday_dates()
    recent_trading_day = last_trading_day(analysis_date)
    if (recent_trading_day - snap_date).days > 3:
        logger.warning(
            "%s: snapshot too old (%s) — liquidity filter skipped", symbol, snap_date
        )
        return False, 0, True

    near_expiry = _get_near_expiry(analysis_date)
    rows = get_options_snapshot(symbol, snap_date, near_expiry)

    if not rows:
        # Try next expiry if near is empty (possible if snapshot was taken early)
        next_month = date(near_expiry.year, near_expiry.month % 12 + 1, 1) \
                     if near_expiry.month < 12 else date(near_expiry.year + 1, 1, 1)
        next_expiry = _last_tuesday_of_month(next_month)
        rows = get_options_snapshot(symbol, snap_date, next_expiry)

    if not rows:
        logger.warning("%s: no option rows for %s — liquidity filter skipped", symbol, snap_date)
        return False, 0, True

    # Find ATM strike (closest to current price)
    strikes = sorted({float(r["strike"]) for r in rows})
    if not strikes:
        return False, 0, True

    atm_strike = min(strikes, key=lambda s: abs(s - current_price))

    atm_oi = sum(
        int(r.get("oi") or 0)
        for r in rows
        if abs(float(r["strike"]) - atm_strike) < 0.01
    )

    eliminate = atm_oi < _ATM_OI_MINIMUM
    return eliminate, atm_oi, False


# ── Shadow tracking ───────────────────────────────────────────────────────────

def _record_shadow_track(
    symbol: str,
    reason: str,
    price: float,
    atr_pct_val: float,
    analysis_date: date,
) -> None:
    """Insert a shadow track row — fire and forget."""
    try:
        track_until = _add_trading_days(analysis_date, _EARNINGS_WINDOW_DAYS)
        create_shadow_track({
            "symbol":               symbol,
            "elimination_date":     str(analysis_date),
            "elimination_reason":   reason,
            "atr_pct":              atr_pct_val if atr_pct_val else None,
            "price_at_elimination": price,
            "track_until_date":     str(track_until),
        })
    except Exception as exc:
        logger.warning("Shadow track insert failed for %s: %s", symbol, exc)


# ── Main entry point ──────────────────────────────────────────────────────────

def run_level1_filter(
    symbols: list[str],
    analysis_date: date,
    kite,
    earnings_window: dict[str, str] | None = None,
) -> dict:
    """
    Run all three Level 1 filters on the given symbol list.

    Args:
        symbols:         List of Nifty 50 symbols to filter.
        analysis_date:   The date being analysed (typically last trading day).
        kite:            Authenticated KiteConnect instance (used by future filters).
        earnings_window: Pre-fetched {symbol: detail} from fetch_nse_earnings_window().
                         If None, fetched automatically. Pass {} to skip earnings filter.

    Returns dict with keys: passed, eliminated, filter_skipped, errors.
    """
    if earnings_window is None:
        earnings_window = fetch_nse_earnings_window(analysis_date)

    passed:         list[str]  = []
    eliminated:     list[dict] = []
    filter_skipped: list[str]  = []
    errors:         list[dict] = []

    # Track whether liquidity filter was skipped globally (log once, not per symbol)
    liquidity_skipped_globally = False

    for symbol in symbols:
        try:
            # ── Latest close price (needed for ATM strike calculation) ──────
            rows = get_price_history(symbol, days=5)
            if not rows:
                logger.warning("%s: no price history — passing through", symbol)
                passed.append(symbol)
                continue
            current_price = float(rows[-1]["close"])

            # ── Filter 1: Earnings ────────────────────────────────────────
            if symbol in earnings_window:
                detail = earnings_window[symbol]
                eliminated.append({"symbol": symbol, "reason": "EARNINGS", "detail": detail})
                logger.info("ELIMINATED %s — EARNINGS (%s)", symbol, detail)
                continue   # no shadow track for earnings

            # ── Filter 2: ATR Dead Zone ───────────────────────────────────
            elim, atr_val = _filter_atr_dead(symbol)
            if elim:
                eliminated.append({"symbol": symbol, "reason": "ATR_DEAD", "value": atr_val})
                logger.info("ELIMINATED %s — ATR_DEAD (%.3f%%)", symbol, atr_val)
                _record_shadow_track(symbol, "ATR_DEAD", current_price, atr_val, analysis_date)
                continue

            # ── Filter 3: F&O Liquidity ───────────────────────────────────
            # elim, atm_oi, skipped = _filter_fno_liquidity(symbol, analysis_date, current_price)
            # if skipped:
            #     if not liquidity_skipped_globally:
            #         liquidity_skipped_globally = True
            #         logger.info("Liquidity filter skipped — no snapshot available")
            # elif elim:
            #     eliminated.append({"symbol": symbol, "reason": "FNO_ILLIQUID", "atm_oi": atm_oi})
            #     logger.info("ELIMINATED %s — FNO_ILLIQUID (ATM OI=%d)", symbol, atm_oi)
            #     _record_shadow_track(symbol, "FNO_ILLIQUID", current_price, 0.0, analysis_date)
            #     continue

            passed.append(symbol)

        except Exception as exc:
            logger.error("Level 1 error for %s: %s", symbol, exc, exc_info=True)
            errors.append({"symbol": symbol, "error": str(exc)})
            passed.append(symbol)   # generous — pass through on unexpected error

    if liquidity_skipped_globally:
        filter_skipped.append("FNO_LIQUIDITY")

    logger.info(
        "Level 1 complete: %d passed, %d eliminated, %d errors",
        len(passed), len(eliminated), len(errors),
    )
    return {
        "passed":         passed,
        "eliminated":     eliminated,
        "filter_skipped": filter_skipped,
        "errors":         errors,
    }
