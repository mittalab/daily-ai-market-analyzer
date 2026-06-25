"""
OI Continuous Series Builder — spec Section 27.

Nightly, for each Nifty 50 stock:
  1. Determine rollover phase (NORMAL / ROLLOVER_WATCH / TRANSITION / EXPIRY)
  2. Load futures OI row from futures_continuous_series
  3. Update spot_price + basis using bhavcopy close from price_history
  4. Compute PCR + max pain from options_snapshots (if available)
  5. Write merged row to continuous_oi_series

Rollover phase schedule (spec Section 28):
  T-6+:        NORMAL
  T-5 to T-3:  ROLLOVER_WATCH
  T-2:         TRANSITION
  T-1 / T:     EXPIRY   (T = expiry day itself)

PCR interpretation (contrarian at extremes):
  < 0.7 → excessive bullishness (contrarian bearish signal)
  0.7–1.1 → neutral
  > 1.3 → excessive bearishness (contrarian bullish signal)

Call:
    result = run_oi_series_builder(symbols, analysis_date)

Returns:
    {"stored": int, "no_futures": [str], "no_options": [str], "errors": [...]}
"""
import logging
from collections import defaultdict
from datetime import date, timedelta

from database.queries import (
    get_continuous_oi,
    get_futures_row,
    get_options_by_date,
    get_price_history,
    update_futures_spot,
    upsert_continuous_oi,
)
from integrations.nse_bhavcopy import get_holiday_dates

logger = logging.getLogger(__name__)


# ── Rollover phase ─────────────────────────────────────────────────────────────

def _trading_days_to(from_date: date, to_date: date) -> int:
    """Count trading days from from_date (exclusive) to to_date (inclusive)."""
    holidays = get_holiday_dates()
    count = 0
    cursor = from_date
    while cursor < to_date:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5 and cursor not in holidays:
            count += 1
    return count


def determine_rollover_phase(analysis_date: date, near_expiry: date) -> str:
    """
    Spec Section 28 schedule:
      T=0 / T-1 : EXPIRY        (on expiry day or 1 trading day before)
      T-2       : TRANSITION    (2 trading days before)
      T-3 to T-5: ROLLOVER_WATCH
      T-6+      : NORMAL
    """
    if analysis_date >= near_expiry:
        return "EXPIRY"
    days_left = _trading_days_to(analysis_date, near_expiry)
    if days_left <= 1:
        return "EXPIRY"
    if days_left == 2:
        return "TRANSITION"
    if days_left <= 5:
        return "ROLLOVER_WATCH"
    return "NORMAL"


# ── Options metrics: PCR + max pain ───────────────────────────────────────────

def _pcr_and_oi_from_options(
    rows: list[dict],
    near_expiry_str: str,
) -> tuple[float | None, float | None, int, int]:
    """
    Returns (pcr_near, pcr_total, near_total_oi, next_total_oi).

    pcr = put_oi / call_oi (standard definition).
    near_total_oi = CE + PE OI for near expiry.
    next_total_oi = CE + PE OI for all other expiries.
    """
    near_ce = near_pe = 0
    other_ce = other_pe = 0

    for r in rows:
        oi = int(r.get("oi") or 0)
        is_near = (str(r.get("expiry_date", "")) == near_expiry_str)
        if r["option_type"] == "CE":
            if is_near:
                near_ce += oi
            else:
                other_ce += oi
        else:
            if is_near:
                near_pe += oi
            else:
                other_pe += oi

    pcr_near  = round(near_pe  / near_ce,  4) if near_ce  > 0 else None
    pcr_total = round((near_pe + other_pe) / (near_ce + other_ce), 4) \
                if (near_ce + other_ce) > 0 else None

    near_total_oi = near_ce + near_pe
    next_total_oi = other_ce + other_pe
    return pcr_near, pcr_total, near_total_oi, next_total_oi


def _calc_max_pain(rows: list[dict], near_expiry_str: str) -> float | None:
    """
    Max pain = strike price that causes maximum total loss to option buyers.

    For each candidate settlement S:
      CE buyer loss: sum( (K - S) * CE_OI(K) )  for all K > S
      PE buyer loss: sum( (S - K) * PE_OI(K) )  for all K < S
    Max pain = S maximising total buyer loss.
    """
    near_rows = [r for r in rows if str(r.get("expiry_date", "")) == near_expiry_str]
    if not near_rows:
        return None

    ce_oi: dict[float, int] = defaultdict(int)
    pe_oi: dict[float, int] = defaultdict(int)
    for r in near_rows:
        strike = float(r["strike"])
        oi     = int(r.get("oi") or 0)
        if r["option_type"] == "CE":
            ce_oi[strike] += oi
        else:
            pe_oi[strike] += oi

    strikes = sorted(set(ce_oi.keys()) | set(pe_oi.keys()))
    if not strikes:
        return None

    max_loss  = -1
    pain_strike = None
    for s in strikes:
        loss  = sum((k - s) * ce_oi[k] for k in strikes if k > s)
        loss += sum((s - k) * pe_oi[k] for k in strikes if k < s)
        if loss > max_loss:
            max_loss, pain_strike = loss, s

    return pain_strike


# ── Previous-day OI for delta calculation ─────────────────────────────────────

def _prev_total_oi(symbol: str, analysis_date: date) -> int | None:
    rows = get_continuous_oi(symbol, days=2)
    for r in reversed(rows):
        if str(r["date"]) < str(analysis_date):
            return r.get("total_oi")
    return None


# ── Main entry point ──────────────────────────────────────────────────────────

def run_oi_series_builder(symbols: list[str], analysis_date: date) -> dict:
    """
    Build continuous_oi_series rows for analysis_date.
    Also patches spot_price + basis into futures_continuous_series.

    Always continues — one symbol error never blocks the rest.
    """
    stored     = 0
    no_futures: list[str]  = []
    no_options: list[str]  = []
    errors:     list[dict] = []

    for symbol in symbols:
        try:
            # ── Spot price (bhavcopy close) ────────────────────────────────
            spot_price: float | None = None
            #AI: This assumes that price data is available for today. You can't run the analysis on any other day.
            # instead, use the last day available data and send alert that using which date of data. This is needed
            # only for manual analysis AND NOT DAILY ANLAYSIS.
            for pr in reversed(get_price_history(symbol, days=5)):
                if str(pr["date"]) == str(analysis_date):
                    spot_price = float(pr["close"])
                    break

            # ── Futures series row ─────────────────────────────────────────
            #AI: Check how is futures_continuous_series table populated?
            # also use the same date logic as that of price. May be from top, pass the analysis date as the last run
            # date for manual analysis.
            fut = get_futures_row(symbol, analysis_date)
            if fut is None:
                logger.warning("%s: no futures row for %s — skipping", symbol, analysis_date)
                no_futures.append(symbol)
                continue

            near_expiry_str = fut["near_expiry"]
            next_expiry_str = fut.get("next_expiry")
            near_expiry     = date.fromisoformat(near_expiry_str)

            # ── Update spot_price + basis in futures table ─────────────────
            futures_price = float(fut["futures_price"]) if fut.get("futures_price") else None
            if spot_price is not None and futures_price is not None:
                basis     = round(futures_price - spot_price, 2)
                basis_pct = round(basis / spot_price * 100, 4) if spot_price else None
                update_futures_spot(symbol, analysis_date, spot_price, basis, basis_pct)

            # ── Rollover phase (re-computed correctly) ─────────────────────
            rollover_phase  = determine_rollover_phase(analysis_date, near_expiry)
            in_rollover_wk  = rollover_phase in ("ROLLOVER_WATCH", "TRANSITION", "EXPIRY")
            is_expiry_day   = (analysis_date == near_expiry)

            # ── Options metrics (if snapshot available) ────────────────────
            opt_rows = get_options_by_date(symbol, analysis_date)
            if opt_rows:
                pcr_near, pcr_total, near_oi, next_oi = _pcr_and_oi_from_options(
                    opt_rows, near_expiry_str
                )
                #AI: Can we get max_pain from the KITE or somewhere else
                max_pain    = _calc_max_pain(opt_rows, near_expiry_str)
                total_oi    = near_oi + next_oi
                rollover_pct = round(next_oi / total_oi * 100, 2) if total_oi > 0 else None
            else:
                #AI: Show warning option data is not available. This should never happen
                no_options.append(symbol)
                # Fall back to futures OI when snapshot unavailable
                near_oi      = int(fut.get("near_month_oi") or 0)
                next_oi      = int(fut.get("next_month_oi") or 0)
                total_oi     = near_oi + next_oi
                rollover_pct = fut.get("rollover_pct")
                pcr_near = pcr_total = max_pain = None

            # ── OI change vs previous day ──────────────────────────────────
            prev = _prev_total_oi(symbol, analysis_date)
            #AI: Should oi_change be computed for each strike individually as well
            oi_change = (total_oi - prev) if prev is not None else None

            upsert_continuous_oi({
                "symbol":          symbol,
                "date":            str(analysis_date),
                "rollover_phase":  rollover_phase,
                "near_expiry":     near_expiry_str,
                "next_expiry":     next_expiry_str,
                "near_month_oi":   near_oi,
                "next_month_oi":   next_oi,
                "total_oi":        total_oi,
                "oi_change":       oi_change,
                "in_rollover_week": in_rollover_wk,
                "is_expiry_day":   is_expiry_day,
                "rollover_pct":    rollover_pct,
                "pcr_near":        pcr_near,
                "pcr_total":       pcr_total,
                "max_pain":        max_pain,
            })
            stored += 1
            logger.debug(
                "%s: stored (phase=%s, near_oi=%d, pcr_near=%s, max_pain=%s)",
                symbol, rollover_phase, near_oi, pcr_near, max_pain,
            )

        except Exception as exc:
            logger.error("OI builder error for %s: %s", symbol, exc, exc_info=True)
            errors.append({"symbol": symbol, "error": str(exc)})

    logger.info(
        "OI series builder: %d stored, %d no-futures, %d no-options, %d errors",
        stored, len(no_futures), len(no_options), len(errors),
    )
    return {
        "stored":     stored,
        "no_futures": no_futures,
        "no_options": no_options,
        "errors":     errors,
    }
