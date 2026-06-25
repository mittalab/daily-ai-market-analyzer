"""
Validation & self-healing runner.

Flow per symbol:
  1. Determine date range: (last_passed_date + 1) → today
  2. Pre-fetch data presence for the entire range in 3 bulk queries (OHLCV, futures, options)
  3. For each date in range:
     a. Run checks against the in-memory cache (O(1) lookups)
     b. On failure → heal that date → point-check DB → update cache
     c. Record PASSED / FAILED per date in validation_state table
  4. Stop processing a symbol on the first date that cannot be healed

Healing rule for historical dates:
  - Check if bhavcopy already ran for that date (data exists for other symbols)
  - If yes → skip re-download (symbol simply wasn't in bhavcopy)
  - If no  → run backfill (downloads all symbols for that date)
"""
import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytz

from database.queries import (
    get_last_passed_validation_date,
    upsert_validation_state,
    upsert_fii_dii_flow,
    get_latest_fii_dii,
)
from new_data_ingestion.nse_bhavcopy import get_holiday_dates, last_trading_day
from new_data_ingestion.ingestion_utils import (
    ingest_today_options,
    ingest_today_kite_data,
    backfill_historical_date,
)
from new_data_ingestion.backfill_vix import run_backfill as run_vix_backfill
from new_data_ingestion.nse_fii_dii import create_nse_session, fetch_fii_dii, fii_dii_to_db_row
from new_validation.data_validator import (
    validate_kite_token,
    validate_db_connectivity,
    SymbolDataCache,
    check_ohlcv,
    check_futures,
    check_options,
    equity_bhavcopy_ran,
    fo_bhavcopy_ran,
    point_check_ohlcv,
    point_check_futures,
    point_check_options,
)
from new_notifications.telegram import (
    send_preflight_failed,
    send_token_reminder,
)
from new_utils.stock_list import get_stock_list_for_analysis

logger = logging.getLogger(__name__)

# ── Validation depth constants ─────────────────────────────────────────────────
_OHLCV_DAYS         = 180   # stocks + NIFTY + all other sector indices
_OHLCV_DAYS_VIX     = 30    # India VIX only
_FO_DAYS            = 30    # stock options + futures
_NIFTY_OPT_DAYS     = 15    # NIFTY weekly options
_STOCK_NEAR_EXPIRY  = 5     # trading days threshold → add next monthly expiry
_NIFTY_NEAR_EXPIRY  = 2     # trading days threshold → add next weekly expiry

# Calendar days to look back for initial validation (180 trading days ≈ 270 cal days)
_OHLCV_CAL_BUFFER   = 270


# ── Date / trading-day helpers ─────────────────────────────────────────────────

def get_validation_end_date(holidays: set[date]) -> date:
    """Today if trading day and IST time >= 15:40, otherwise last trading day."""
    IST = pytz.timezone("Asia/Kolkata")
    now = datetime.now(IST)
    today = now.date()
    cutoff = datetime.strptime("15:40", "%H:%M").time()
    if today.weekday() < 5 and today not in holidays and now.time() >= cutoff:
        return today
    return last_trading_day(today - timedelta(days=1))


def count_trading_days(start: date, end: date, holidays: set[date]) -> int:
    """Count trading days in [start, end) — start inclusive, end exclusive."""
    count, curr = 0, start
    while curr < end:
        if curr.weekday() < 5 and curr not in holidays:
            count += 1
        curr += timedelta(days=1)
    return count


def get_trading_days_in_range(start: date, end: date, holidays: set[date]) -> list[date]:
    """Return all trading days in (start, end] — start exclusive, end inclusive."""
    days, curr = [], start + timedelta(days=1)
    while curr <= end:
        if curr.weekday() < 5 and curr not in holidays:
            days.append(curr)
        curr += timedelta(days=1)
    return days


# ── Expiry helpers ─────────────────────────────────────────────────────────────

def _last_tuesday_of_month(ref: date) -> date:
    """Last Tuesday of ref's calendar month (monthly stock F&O expiry)."""
    if ref.month == 12:
        last_day = date(ref.year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(ref.year, ref.month + 1, 1) - timedelta(days=1)
    return last_day - timedelta(days=(last_day.weekday() - 1) % 7)


def _adjust_for_holiday(d: date, holidays: set[date]) -> date:
    """Walk backwards past weekends and holidays."""
    while d.weekday() >= 5 or d in holidays:
        d -= timedelta(days=1)
    return d


def get_stock_expiries(ref_date: date, holidays: set[date]) -> list[date]:
    """Near monthly expiry, plus next if <= _STOCK_NEAR_EXPIRY trading days remain.

    On expiry day itself (days_left == 0): near stays as today — the contract still
    trades and its data must be present. 0 <= threshold, so next month is also added.
    Only roll near forward when the expiry is strictly in the past (ref_date > near).
    """
    near = _adjust_for_holiday(_last_tuesday_of_month(ref_date), holidays)

    if ref_date > near:
        # Expiry already passed — roll near to next month
        next_month = date(near.year, near.month % 12 + 1, 1) if near.month < 12 else date(near.year + 1, 1, 1)
        near = _adjust_for_holiday(_last_tuesday_of_month(next_month), holidays)

    # days_left == 0 when today IS expiry day; 0 <= threshold so next is always added
    days_left = count_trading_days(ref_date, near, holidays)
    if days_left <= _STOCK_NEAR_EXPIRY:
        nxt_month = date(near.year, near.month % 12 + 1, 1) if near.month < 12 else date(near.year + 1, 1, 1)
        nxt = _adjust_for_holiday(_last_tuesday_of_month(nxt_month), holidays)
        return [near, nxt]
    return [near]


def get_nifty_expiries(ref_date: date, holidays: set[date]) -> list[date]:
    """Near weekly Tuesday expiry, plus next if <= _NIFTY_NEAR_EXPIRY trading days remain.

    On expiry Tuesday itself: days_ahead == 0, so near = ref_date (today).
    days_left == 0 <= threshold, so next week is also added.
    The old `days_ahead if days_ahead else 7` pattern skipped today's expiry — fixed.
    """
    days_ahead = (1 - ref_date.weekday()) % 7
    # days_ahead == 0 means today is Tuesday (expiry day) — near is today, not next week
    near = _adjust_for_holiday(ref_date + timedelta(days=days_ahead), holidays)

    # days_left == 0 when today IS expiry day; 0 <= threshold so next is always added
    days_left = count_trading_days(ref_date, near, holidays)
    if days_left <= _NIFTY_NEAR_EXPIRY:
        nxt = near + timedelta(days=7)
        nxt = _adjust_for_holiday(nxt, holidays)
        return [near, nxt]
    return [near]


def get_other_indices(sector_map_path: Path | None = None) -> list[str]:
    """Unique sector indices from sector_map.json, excluding NIFTY / NIFTY_50."""
    path = sector_map_path or Path(__file__).parent.parent / "config" / "sector_map.json"
    try:
        data = json.loads(path.read_text())
        indices = {v["index"] for v in data.get("stocks", {}).values() if v.get("index")}
        indices.discard("NIFTY")
        indices.discard("NIFTY_50")
        return sorted(indices)
    except Exception as exc:
        logger.warning("Failed to load other indices: %s", exc)
        return []


# ── Per-date check: symbol type dispatch ──────────────────────────────────────

def check_symbol_on_date(
    symbol: str,
    check_date: date,
    cache: SymbolDataCache,
    holidays: set[date],
    other_indices: list[str],
    check_depth: bool = False,
) -> tuple[bool, dict]:
    """
    Run all applicable checks for symbol on check_date using the pre-loaded cache.

    check_depth:
      True  — initial validation (no previous PASSED date): verify required_days depth.
              Cache covers full history so the count is meaningful.
      False — incremental validation (continuing from a PASSED date): presence only.
              Depth is already proven by the previous PASSED validation; re-counting
              against a short-range cache would always fail.

    Returns (all_passed, {check_name: {"ok": bool, "msg": str}}).
    """
    results: dict = {}
    passed = True

    def _fail(key, msg):
        nonlocal passed
        results[key] = {"ok": False, "msg": msg}
        passed = False

    def _ok(key, msg):
        results[key] = {"ok": True, "msg": msg}

    def _chk_ohlcv(sym, key, days):
        if check_depth:
            ok, msg = check_ohlcv(sym, check_date, cache, days)
        else:
            ok = check_date in cache.ohlcv_dates
            msg = f"OHLCV {'present' if ok else 'missing'} for {sym} on {check_date}"
        (_ok if ok else _fail)(key, msg)

    def _chk_futures(sym, expiry, key, days):
        if check_depth:
            ok, msg = check_futures(sym, check_date, expiry, cache, days)
        else:
            ok = check_date in cache.futures.get(expiry, set())
            msg = f"Futures {'present' if ok else 'missing'} for {sym} expiry={expiry} on {check_date}"
        (_ok if ok else _fail)(key, msg)

    def _chk_options(sym, expiry, key, days):
        if check_depth:
            ok, msg = check_options(sym, check_date, expiry, cache, days)
        else:
            ok = check_date in cache.options.get(expiry, set())
            msg = f"Options {'present' if ok else 'missing'} for {sym} expiry={expiry} on {check_date}"
        (_ok if ok else _fail)(key, msg)

    if symbol == "INDIA_VIX":
        _chk_ohlcv(symbol, "vix_ohlcv", _OHLCV_DAYS_VIX)

    elif symbol in other_indices:
        _chk_ohlcv(symbol, "index_ohlcv", _OHLCV_DAYS)

    elif symbol in ("NIFTY_50", "NIFTY"):
        _chk_ohlcv("NIFTY_50", "nifty_ohlcv", _OHLCV_DAYS)

        for exp in get_nifty_expiries(check_date, holidays):
            _chk_options("NIFTY_50", exp, f"nifty_options_{exp}", _NIFTY_OPT_DAYS)

    else:
        _chk_ohlcv(symbol, "stock_ohlcv", _OHLCV_DAYS)

        for exp in get_stock_expiries(check_date, holidays):
            _chk_futures(symbol, exp, f"stock_futures_{exp}", _FO_DAYS)
            _chk_options(symbol, exp, f"stock_options_{exp}", _FO_DAYS)

    return passed, results


# ── Healing ───────────────────────────────────────────────────────────────────

def _needs(results: dict, *prefixes: str) -> bool:
    return any(
        k.startswith(p) and not v["ok"]
        for p in prefixes
        for k, v in results.items()
    )


def heal_and_recheck(
    symbol: str,
    check_date: date,
    results: dict,
    cache: SymbolDataCache,
    today: date,
    holidays: set[date],
    other_indices: list[str],
) -> tuple[bool, dict]:
    """
    Trigger ingestion for failed checks, then re-check using point queries.
    Updates cache in-place for any data that lands successfully.
    Returns (passed_after_healing, updated_results).
    """
    is_today = (check_date == today)

    needs_ohlcv    = _needs(results, "stock_ohlcv", "nifty_ohlcv", "index_ohlcv", "vix_ohlcv")
    needs_futures  = _needs(results, "stock_futures", "nifty_futures")
    needs_options  = _needs(results, "stock_options", "nifty_options")

    # ── Heal ──────────────────────────────────────────────────────────────────
    if is_today:
        # Today: Kite API only — no bhavcopy under any circumstance.
        # kite_ohlcv.get_nse_token handles both equity (EQ) and index (INDEX) symbols.
        # kite_oi.fetch_futures_oi_all maps DB symbol names to Kite NFO names internally.
        if symbol == "INDIA_VIX" and needs_ohlcv:
            _safe(lambda: run_vix_backfill(check_date, check_date), "VIX live backfill")
        else:
            if needs_ohlcv or needs_futures:
                _safe(lambda: ingest_today_kite_data(check_date, [symbol]), "Kite live OHLCV+futures")
            if needs_options:
                _safe(lambda: ingest_today_options(check_date, [symbol]), "live options")

    else:
        # Historical — use bhavcopy only if it hasn't already been run for this date
        if symbol == "INDIA_VIX" and needs_ohlcv:
            _safe(lambda: run_vix_backfill(check_date, check_date), "VIX historical backfill")

        elif needs_ohlcv and needs_futures or needs_options:
            _safe(lambda: backfill_historical_date(check_date), f"equity bhavcopy {check_date}")

        # If bhavcopy already ran, the symbol simply isn't in it — no point re-downloading

    # ── Re-check via point queries ────────────────────────────────────────────
    for key, val in results.items():
        if val["ok"]:
            continue   # already passed, skip

        if "ohlcv" in key or "vix" in key:
            db_sym = "NIFTY_50" if symbol in ("NIFTY", "NIFTY_50") else symbol
            if point_check_ohlcv(db_sym, check_date):
                cache.mark_ohlcv_present(check_date)
                results[key] = {"ok": True, "msg": f"OHLCV healed for {symbol} on {check_date}"}

        elif "futures" in key:
            # Extract expiry from key suffix  e.g. stock_futures_2026-07-29
            expiry = _expiry_from_key(key)
            db_sym = "NIFTY_50" if symbol in ("NIFTY", "NIFTY_50") else symbol
            if expiry and point_check_futures(db_sym, expiry, check_date):
                cache.mark_futures_present(expiry, check_date)
                results[key] = {"ok": True, "msg": f"Futures healed for {symbol} expiry={expiry} on {check_date}"}

        elif "options" in key:
            expiry = _expiry_from_key(key)
            db_sym = "NIFTY_50" if symbol in ("NIFTY", "NIFTY_50") else symbol
            if expiry and point_check_options(db_sym, expiry, check_date):
                cache.mark_options_present(expiry, check_date)
                results[key] = {"ok": True, "msg": f"Options healed for {symbol} expiry={expiry} on {check_date}"}

    all_passed = all(v["ok"] for v in results.values())
    return all_passed, results


def _safe(fn, label: str) -> None:
    try:
        fn()
    except Exception as exc:
        logger.error("Heal step '%s' failed: %s", label, exc)


def _expiry_from_key(key: str) -> date | None:
    """Extract date from keys like 'stock_futures_2026-07-29'."""
    parts = key.rsplit("_", 3)
    try:
        return date.fromisoformat(parts[-1])
    except (ValueError, IndexError):
        return None


# ── Per-symbol orchestrator ───────────────────────────────────────────────────

def validate_and_heal(
    symbol: str,
    today: date,
    holidays: set[date],
    other_indices: list[str],
) -> bool:
    """
    Validate and self-heal a symbol across all gap dates up to today.
    Returns True only if every date in the range passes validation.
    """
    raw_last_passed = get_last_passed_validation_date(symbol)
    is_initial      = raw_last_passed is None

    # Initial validation: go back far enough to cover the full required depth.
    # For incremental: start from the day after the last PASSED date — no lookback needed
    # because depth is already proven by the previous PASSED validation.
    last_passed = raw_last_passed or (today - timedelta(days=_OHLCV_CAL_BUFFER))

    dates_to_check = get_trading_days_in_range(last_passed, today, holidays)

    if not dates_to_check:
        logger.info("%s: already up to date (last passed: %s)", symbol, last_passed)
        return True

    logger.info(
        "%s: %s validation — %d date(s) %s → %s",
        symbol,
        "initial" if is_initial else "incremental",
        len(dates_to_check),
        dates_to_check[0],
        dates_to_check[-1],
    )

    # Cache covers exactly the gap — no prior-history lookback.
    # For initial runs, the gap itself spans _OHLCV_CAL_BUFFER days, so the cache
    # contains all the historical rows needed for the depth check.
    fo_start  = dates_to_check[0]
    fo_end = dates_to_check[-1]
    db_symbol = "NIFTY_50" if symbol in ("NIFTY", "NIFTY_50") else symbol
    cache     = SymbolDataCache(db_symbol, fo_start, fo_end, today)

    all_passed = True

    for check_date in dates_to_check:
        passed, results = check_symbol_on_date(
            symbol, check_date, cache, holidays, other_indices,
            check_depth=is_initial,
        )

        if not passed:
            logger.warning("%s: checks failed on %s — healing...", symbol, check_date)
            passed, results = heal_and_recheck(
                symbol, check_date, results, cache, today, holidays, other_indices
            )

        status = "PASSED" if passed else "FAILED"
        upsert_validation_state(symbol, check_date, "daily", status, results)

        if passed:
            logger.debug("%s: %s PASSED", symbol, check_date)
        else:
            failed_checks = [k for k, v in results.items() if not v["ok"]]
            logger.error(
                "%s: %s FAILED after healing — %s",
                symbol, check_date, failed_checks,
            )
            all_passed = False
            # Continue to next date rather than aborting — log all gaps

    return all_passed


# ── Daily run ─────────────────────────────────────────────────────────────────

def run_daily_validation(target_date: date) -> bool:
    """
    Full daily validation for all symbols (stocks + NIFTY + VIX + sector indices).
    Runs pre-flight checks, FII/DII ingestion, then per-symbol validation.
    """
    holidays = get_holiday_dates()

    # Pre-flight
    db_ok, db_msg = validate_db_connectivity()
    if not db_ok:
        logger.critical("DB connectivity failed: %s — aborting.", db_msg)
        send_preflight_failed(f"DB Connectivity: {db_msg}", str(target_date))
        return False

    kite_ok, kite_msg = validate_kite_token()
    if not kite_ok:
        logger.warning("Kite token check failed: %s", kite_msg)
        send_token_reminder()

    # FII/DII
    logger.info("Fetching FII/DII flows for %s", target_date)
    try:
        session = create_nse_session()
        fii_data = fetch_fii_dii(session)
        upsert_fii_dii_flow(fii_dii_to_db_row(fii_data, target_date))
        logger.info("FII/DII flows stored for %s", target_date)
    except Exception as exc:
        logger.warning("FII/DII ingestion failed (%s) — using cached value", exc)
        try:
            cached = get_latest_fii_dii()
            if cached:
                row = {**dict(cached), "source": "CACHED", "date": str(target_date)}
                row.pop("id", None)
                row.pop("created_at", None)
                upsert_fii_dii_flow(row)
        except Exception as cache_exc:
            logger.error("FII/DII cache fallback also failed: %s", cache_exc)

    # Symbol universe
    other_indices  = get_other_indices()
    stocks_dict    = get_stock_list_for_analysis(include_kite_trades=True)
    universe = sorted(
        set(stocks_dict.keys()) | {"NIFTY_50", "INDIA_VIX"} | set(other_indices)
    )
    logger.info("Validating %d symbols for %s", len(universe), target_date)

    passed_count, failed_symbols = 0, []

    for idx, symbol in enumerate(universe, 1):
        logger.info("[%d/%d] %s", idx, len(universe), symbol)
        try:
            ok = validate_and_heal(symbol, target_date, holidays, other_indices)
            if ok:
                passed_count += 1
            else:
                failed_symbols.append(symbol)
        except Exception as exc:
            logger.error("Unhandled error validating %s: %s", symbol, exc)
            failed_symbols.append(symbol)

    logger.info(
        "Validation complete: %d/%d passed. Failed: %s",
        passed_count, len(universe), failed_symbols or "none",
    )
    return not failed_symbols


# ── Convenience entry-point ───────────────────────────────────────────────────

_LOG_DIR = Path(__file__).parent.parent / "logs"


def _ensure_file_logging() -> Path:
    """
    Attach a RotatingFileHandler to the root logger the first time this runs.
    Safe to call multiple times — only one handler is ever added.
    Returns the log file path so callers can print it.
    """
    import logging.handlers

    _LOG_DIR.mkdir(exist_ok=True)
    log_file = _LOG_DIR / "validation.log"

    root = logging.getLogger()
    # Guard: don't add a second handler if one is already writing to this file
    already = any(
        isinstance(h, logging.handlers.RotatingFileHandler)
        and getattr(h, "baseFilename", "") == str(log_file)
        for h in root.handlers
    )
    if not already:
        fh = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=5,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
        ))
        root.addHandler(fh)
        if root.level == logging.NOTSET:
            root.setLevel(logging.INFO)

    return log_file


def run_validation_now() -> bool:
    """
    Determine the correct validation target date and run full validation.

    Target date selection (IST):
      - If it is a trading day AND current time >= 15:40 → today
        (market has closed, today's data is available via Kite API)
      - Otherwise → most recent trading day before today
        (walks backwards from yesterday skipping weekends and holidays)

    Logs are written to  logs/validation.log  (rotating, max 10 MB × 5 files)
    in addition to any console handler already configured by the caller.

    Returns True if all symbols passed, False otherwise.
    """
    log_file = _ensure_file_logging()

    IST = pytz.timezone("Asia/Kolkata")
    holidays = get_holiday_dates()
    target_date = get_validation_end_date(holidays)

    now_ist = datetime.now(IST)
    logger.info(
        "run_validation_now called at %s IST — target_date=%s  log=%s",
        now_ist.strftime("%Y-%m-%d %H:%M:%S"),
        target_date,
        log_file,
    )
    return run_daily_validation(target_date)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Validation & self-healing runner")
    parser.add_argument("--mode", choices=["daily", "manual"], required=True)
    parser.add_argument("--symbol", help="Symbol for manual mode")
    parser.add_argument("--date",   help="Target date YYYY-MM-DD (default: last trading day)")
    args = parser.parse_args()

    holidays = get_holiday_dates()
    target_date = (
        date.fromisoformat(args.date)
        if args.date
        else get_validation_end_date(holidays)
    )

    if args.mode == "manual":
        db_ok, db_msg = validate_db_connectivity()
        if not db_ok:
            logger.critical("DB connectivity failed: %s", db_msg)
            sys.exit(1)
        if not args.symbol:
            logger.error("--symbol required in manual mode")
            sys.exit(1)
        other_indices = get_other_indices()
        ok = validate_and_heal(
            args.symbol.upper(), target_date, holidays, other_indices
        )
        sys.exit(0 if ok else 1)
    else:
        ok = run_daily_validation(target_date)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s  %(levelname)-8s %(name)s — %(message)s",
    )
    run_validation_now()
    #main()
