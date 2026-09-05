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
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pytz

# To prevent redundant historical backfills of the same date across different symbols
_COMPLETED_BACKFILL_DATES = set()

from database.queries import (
    get_last_passed_validation_date,
    upsert_validation_state,
    upsert_fii_dii_flow,
    get_latest_fii_dii,
    upsert_price_history,
)
from new_data_ingestion.nse_bhavcopy import (
    get_holiday_dates,
    last_trading_day,
    fetch_indices_bhavcopy,
    indices_to_price_rows,
)
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
    check_options: bool,
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
        ok = expiry in cache.futures.get(check_date, set())
        msg = f"Futures {'present' if ok else 'missing'} for {sym} expiry={expiry} on {check_date}"
        (_ok if ok else _fail)(key, msg)

    def _chk_options(sym, expiry, key, days):
        ok = expiry in cache.options.get(check_date, set())
        msg = f"Options {'present' if ok else 'missing'} for {sym} expiry={expiry} on {check_date}"
        (_ok if ok else _fail)(key, msg)

    if symbol == "INDIA_VIX":
        _chk_ohlcv(symbol, "vix_ohlcv", _OHLCV_DAYS_VIX)

    elif symbol in other_indices:
        _chk_ohlcv(symbol, "index_ohlcv", _OHLCV_DAYS)

    elif symbol in ("NIFTY_50", "NIFTY"):
        _chk_ohlcv("NIFTY_50", "nifty_ohlcv", _OHLCV_DAYS)

        if check_options:
            for exp in get_nifty_expiries(check_date, holidays):
                _chk_options("NIFTY_50", exp, f"nifty_options_{exp}", _NIFTY_OPT_DAYS)

    else:
        _chk_ohlcv(symbol, "stock_ohlcv", _OHLCV_DAYS)

        if check_options:
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
    options_to_heal: bool,
    force_historical: bool = False,
) -> tuple[bool, dict]:
    """
    Trigger ingestion for failed checks, then re-check using point queries.
    Updates cache in-place for any data that lands successfully.
    Returns (passed_after_healing, updated_results).

    force_historical=True skips the Kite live path even when check_date==today,
    using bhavcopy instead. Use this for batch jobs that run after market close
    (e.g. 11 PM bhavcopy job) where Kite is not required.
    """
    logger.info("%s: Healing and check rechecking. Check date: %s, Today: %s", symbol, check_date, today)
    start_time = time.time()
    is_today = (check_date == today) and not force_historical

    needs_ohlcv    = _needs(results, "stock_ohlcv", "nifty_ohlcv", "index_ohlcv", "vix_ohlcv")
    needs_futures  = _needs(results, "stock_futures")
    needs_options  = _needs(results, "stock_options", "nifty_options")

    # ── Heal ──────────────────────────────────────────────────────────────────
    t0 = time.time()
    if is_today:
        # Today: Kite API only — no bhavcopy under any circumstance.
        if symbol == "INDIA_VIX" and needs_ohlcv:
            _safe(lambda: run_vix_backfill(check_date, check_date), "VIX live backfill")
        else:
            if needs_ohlcv or needs_futures:
                _safe(lambda: ingest_today_kite_data(check_date, [symbol]), "Kite live OHLCV+futures")
            if needs_options:
                _safe(lambda: backfill_indices_for_dates([{"date": check_date}]), "nifty values")
                _safe(lambda: ingest_today_options(check_date, [symbol]), "live options")
    else:
        # Historical — use bhavcopy only if it hasn't already been run for this date
        if symbol == "INDIA_VIX" and needs_ohlcv:
            _safe(lambda: run_vix_backfill(check_date, check_date), "VIX historical backfill")
        elif needs_ohlcv or needs_futures or needs_options:
            if check_date in _COMPLETED_BACKFILL_DATES:
                logger.info("Historical backfill for %s already executed in this run. Skipping redundant ingestion.", check_date)
            else:
                try:
                    backfill_historical_date(check_date, options_to_heal=options_to_heal)
                    #_COMPLETED_BACKFILL_DATES.add(check_date)
                except Exception as exc:
                    logger.error("Historical backfill failed for %s: %s", check_date, exc)

    
    heal_time = time.time() - t0

    # ── If F&O bhavcopy is unavailable from NSE (archive limit), accept the gap ─
    # fo_bhavcopy_ran() returns True if ANY options row exists for this date, meaning
    # NSE did serve the bhavcopy. If False after a backfill attempt, the archive is
    # genuinely unavailable — permanently failing on unrecoverable data is wrong.
    # if not is_today and (needs_futures or needs_options) and not fo_bhavcopy_ran(check_date):
    #     logger.warning(
    #         "%s: F&O bhavcopy unavailable for %s (NSE archive limit) — accepting gap",
    #         symbol, check_date,
    #     )
    #     for key, val in results.items():
    #         if not val["ok"] and ("futures" in key or "options" in key):
    #             results[key] = {"ok": True, "msg": f"F&O bhavcopy unavailable for {check_date} — NSE archive limit"}

    # ── Re-check via point queries ────────────────────────────────────────────
    t0 = time.time()
    for key, val in results.items():
        if val["ok"]:
            continue   # already passed, skip

        if "ohlcv" in key or "vix" in key:
            db_sym = "NIFTY_50" if symbol in ("NIFTY", "NIFTY_50") else symbol
            if point_check_ohlcv(db_sym, check_date):
                cache.mark_ohlcv_present(check_date)
                results[key] = {"ok": True, "msg": f"OHLCV healed for {symbol} on {check_date}"}
                logger.info("%s: %s OHLCV healed and point-check passed", symbol, check_date)

        elif "futures" in key:
            expiry = _expiry_from_key(key)
            db_sym = "NIFTY_50" if symbol in ("NIFTY", "NIFTY_50") else symbol
            if expiry and point_check_futures(db_sym, expiry, check_date):
                cache.mark_futures_present(expiry, check_date)
                results[key] = {"ok": True, "msg": f"Futures healed for {symbol} expiry={expiry} on {check_date}"}
                logger.info("%s: %s futures (expiry=%s) healed and point-check passed", symbol, check_date, expiry)

        elif "options" in key:
            expiry = _expiry_from_key(key)
            db_sym = "NIFTY_50" if symbol in ("NIFTY", "NIFTY_50") else symbol
            if expiry and point_check_options(db_sym, expiry, check_date):
                cache.mark_options_present(expiry, check_date)
                results[key] = {"ok": True, "msg": f"Options healed for {symbol} expiry={expiry} on {check_date}"}
                logger.info("%s: %s options (expiry=%s) healed and point-check passed", symbol, check_date, expiry)
                
    recheck_time = time.time() - t0
    logger.debug("%s: heal_and_recheck took %.2fs [heal=%.2fs, recheck=%.2fs]", symbol, time.time() - start_time, heal_time, recheck_time)

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
    force_historical: bool = False,
) -> bool:
    """
    Validate and self-heal a symbol across all gap dates up to today.
    Returns True only if every date in the range passes validation.

    force_historical=True forces the bhavcopy healing path even for today's date.
    """
    start_time = time.time()
    
    t0 = time.time()
    raw_last_passed = get_last_passed_validation_date(symbol)
    logger.info("%s: last passed date = %s", symbol, raw_last_passed or "None (initial run)")

    last_passed_time = time.time() - t0
    
    is_initial      = raw_last_passed is None

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

    t0 = time.time()
    ohlcv_start  = dates_to_check[0]
    fo_end = dates_to_check[-1]
    fo_start = max(ohlcv_start, fo_end - timedelta(days=_FO_DAYS))
    db_symbol = "NIFTY_50" if symbol in ("NIFTY", "NIFTY_50") else symbol
    cache     = SymbolDataCache(db_symbol, ohlcv_start, fo_start, fo_end)
    cache_time = time.time() - t0

    all_passed = True
    checks_time = 0.0
    healing_time = 0.0
    db_update_time = 0.0

    for check_date in dates_to_check:
        t_chk = time.time()
        options_to_check: bool = (today - check_date).days <= _FO_DAYS
        passed, results = check_symbol_on_date(
            symbol, check_date, cache, holidays, other_indices,
            check_options=options_to_check, check_depth=False,
        )
        checks_time += time.time() - t_chk

        if not passed:
            failed_checks = [k for k, v in results.items() if not v["ok"]]
            logger.warning("%s: checks failed on %s — healing... %s. Options to check: %s. Today %s", symbol,
                           check_date, failed_checks, options_to_check, today)
            t_heal = time.time()
            passed, results = heal_and_recheck(
                symbol, check_date, results, cache, today, holidays, other_indices, options_to_check,
                force_historical=force_historical,
            )
            healing_time += time.time() - t_heal

        t_db = time.time()
        status = "PASSED" if passed else "FAILED"
        upsert_validation_state(symbol, check_date, "daily", status, results)
        db_update_time += time.time() - t_db

        if passed:
            logger.debug("%s: %s PASSED", symbol, check_date)
        else:
            failed_checks = [k for k, v in results.items() if not v["ok"]]
            logger.error(
                "%s: %s FAILED after healing — %s",
                symbol, check_date, failed_checks,
            )
            all_passed = False

    total_time = time.time() - start_time
    logger.info(
        "%s validation finished in %.2fs [db_last_passed=%.2fs, cache_load=%.2fs, checks=%.2fs, healing=%.2fs, db_update=%.2fs]",
        symbol, total_time, last_passed_time, cache_time, checks_time, healing_time, db_update_time
    )
    return all_passed

# Running FII/DII data insertion in table
def fii_dii_insertion_validation(target_date: date):
    # FII/DII — daily, as FII/DII historical data is not available, i want to ensure it is available daily
    logger.info("Fetching FII/DII flows for %s", target_date)
    try:
        cached_fii = get_latest_fii_dii(target_date)
        if cached_fii:
            logger.info("FII/DII flows already present for %s — skipping fetch", target_date)
        else:
            session = create_nse_session()
            fii_data = fetch_fii_dii(session)
            parsed_date = datetime.strptime(str(fii_data.get("date")), "%d-%b-%Y").date()
            if parsed_date == target_date:
                upsert_fii_dii_flow(fii_dii_to_db_row(fii_data, target_date))
                logger.info("FII/DII flows stored for %s", target_date)
            else:
                from new_notifications.telegram import send_fii_dii_data_missing
                send_fii_dii_data_missing(str(target_date), str(fii_data.get("date")))
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

# ── Daily run ─────────────────────────────────────────────────────────────────

def run_daily_validation(
    target_date: date,
    symbol: str | None = None,
    include_indexes: bool = True,
    kite_validation_mandatory: bool = True,
) -> tuple[bool, int]:
    """
    Validate symbols with pre-flight checks and (on full runs) FII/DII ingestion.

    symbol=None  → full run: all stocks + NIFTY_50 + INDIA_VIX + sector indices
                   FII/DII ingestion is performed.
    symbol=<str> → targeted run: just that symbol, or symbol + all indexes when
                   include_indexes=True. FII/DII ingestion is skipped.
    """
    global _COMPLETED_BACKFILL_DATES
    _COMPLETED_BACKFILL_DATES.clear()

    start_time = time.time()
    holidays = get_holiday_dates()

    # Pre-flight
    db_ok, db_msg = validate_db_connectivity()
    if not db_ok:
        logger.critical("DB connectivity failed: %s — aborting.", db_msg)
        send_preflight_failed(f"DB Connectivity: {db_msg}", str(target_date))
        return False

    logger.info("DB connectivity OK: %s", db_msg)

    kite_ok, kite_msg = validate_kite_token()
    if not kite_ok:
        logger.warning("Kite token check failed: %s", kite_msg)
        send_token_reminder()
        if kite_validation_mandatory:
            return False

    logger.info("Kite token OK: %s", kite_msg)

    other_indices = get_other_indices()

    # Running FII/DII validation
    fii_dii_insertion_validation(target_date)

    # Symbol universe
    # 1. Standardize core tracking indices into a fixed list
    core_indices = ["INDIA_VIX", "NIFTY_50"] + list(other_indices)
    total_stocks = 0
    if symbol is None:
        stocks_dict = get_stock_list_for_analysis(include_kite_trades=kite_ok)
        # Get a unique, alphabetized list of stocks
        sorted_stocks = sorted(set(stocks_dict.keys()))
        universe = core_indices + sorted_stocks
        total_stocks = len(sorted_stocks)
    elif include_indexes:
        sorted_stocks = sorted({symbol})
        universe = core_indices + sorted_stocks
        total_stocks = len(sorted_stocks)
    else:
        # No indices requested: just a sorted list of the symbols passed
        universe = sorted({symbol})
        total_stocks = len(universe)

    logger.info("Validating %d symbol(s) for %s", len(universe), target_date)

    passed_count, failed_symbols, stocks_passed = 0, [], 0

    for idx, sym in enumerate(universe, 1):
        t_sym = time.time()
        logger.info("[%d/%d] %s", idx, len(universe), sym)
        try:
            ok = validate_and_heal(sym, target_date, holidays, other_indices)
            if ok:
                passed_count += 1
                if sym not in core_indices:
                    stocks_passed += 1
            else:
                failed_symbols.append(sym)
        except Exception as exc:
            logger.error("Unhandled error validating %s: %s", sym, exc)
            failed_symbols.append(sym)
        logger.info("[%d/%d] %s finished in %.2fs", idx, len(universe), sym, time.time() - t_sym)

    logger.info(
        "Validation complete: %d/%d passed (%d stocks). Failed: %s. Total time: %.2fs",
        passed_count, len(universe), stocks_passed, failed_symbols or "none", time.time() - start_time
    )
    return not failed_symbols, stocks_passed


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

def run_validation_now(kite_validation_mandatory: bool = True) -> tuple[bool, int]:
    """
    Determine the correct validation target date and run full validation.

    Target date selection (IST):
      - If it is a trading day AND current time >= 15:40 → today
        (market has closed, today's data is available via Kite API)
      - Otherwise → most recent trading day before today
        (walks backwards from yesterday skipping weekends and holidays)

    Logs are written to  logs/validation.log  (rotating, max 10 MB × 5 files)
    in addition to any console handler already configured by the caller.

    Returns (all_passed, stocks_passed) where stocks_passed is the count of
    non-index symbols that passed validation.
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
    return run_daily_validation(target_date, kite_validation_mandatory=kite_validation_mandatory)


def run_validation_now_for_symbol(symbol: str, include_indexes: bool = True,) -> bool:
    """
    Validate a single symbol, optionally including all indexes.

    Delegates to run_daily_validation with the correct target date.
    Universe:
      include_indexes=True  → symbol + NIFTY_50 + INDIA_VIX + all sector indices
      include_indexes=False → symbol only
    """
    log_file = _ensure_file_logging()

    IST = pytz.timezone("Asia/Kolkata")
    holidays = get_holiday_dates()
    target_date = get_validation_end_date(holidays)

    now_ist = datetime.now(IST)
    logger.info(
        "run_validation_now_for_symbol(%s, include_indexes=%s) at %s IST — target_date=%s  log=%s",
        symbol, include_indexes,
        now_ist.strftime("%Y-%m-%d %H:%M:%S"),
        target_date,
        log_file,
    )
    return run_daily_validation(target_date, symbol=symbol.upper(), include_indexes=include_indexes)


# ── Morning bhavcopy validation ───────────────────────────────────────────────

_FO_VALIDATION_MAX_RETRIES = 3
_FO_VALIDATION_RETRY_DELAY = 10  # seconds between retries


_FO_STOCKS_FALLBACK_PATH = Path(__file__).parent.parent / "config" / "fo_stocks_fallback.json"


def _load_fo_stocks_fallback() -> list[str]:
    """Load the saved F&O stock list from config/fo_stocks_fallback.json."""
    data = json.loads(_FO_STOCKS_FALLBACK_PATH.read_text(encoding="utf-8"))
    return data.get("symbols", [])


def run_fo_stocks_validation(target_date: date) -> tuple[int, int, list[str]]:
    """
    Validate all Kite F&O stocks for target_date (OHLCV + futures + options).
    Each stock is retried up to _FO_VALIDATION_MAX_RETRIES times on failure.
    Called from job_morning_bhavcopy after bhavcopy ingestion.

    Returns (passed_count, total_count, failed_symbols).
    """

    # Running FII/DII validation
    fii_dii_insertion_validation(target_date)

    from new_utils.stock_list import fetch_kite_fo_stocks
    from new_notifications.telegram import send_kite_fo_fetch_failed

    holidays = get_holiday_dates()
    other_indices = get_other_indices()

    kite_error: str | None = None
    symbols: list[str] = []
    try:
        symbols = fetch_kite_fo_stocks()
        if not symbols:
            kite_error = "fetch_kite_fo_stocks returned an empty list"
            logger.warning("run_fo_stocks_validation: Kite returned no F&O stocks")
    except Exception as exc:
        kite_error = str(exc)
        logger.error("run_fo_stocks_validation: Kite fetch failed: %s", exc)

    if kite_error:
        try:
            symbols = _load_fo_stocks_fallback()
            logger.warning(
                "run_fo_stocks_validation: using fallback list (%d symbols from %s)",
                len(symbols), _FO_STOCKS_FALLBACK_PATH.name,
            )
            send_kite_fo_fetch_failed(
                error=kite_error,
                fallback_count=len(symbols),
                trade_date=str(target_date),
            )
        except Exception as fallback_exc:
            logger.error("run_fo_stocks_validation: fallback also failed: %s", fallback_exc)
            return 0, 0, []

    if not symbols:
        logger.error("run_fo_stocks_validation: no symbols available even from fallback")
        return 0, 0, []

    passed_count = 0
    failed_symbols: list[str] = []

    for idx, sym in enumerate(symbols, 1):
        logger.info("[%d/%d] Morning FO validation: %s", idx, len(symbols), sym)
        passed = False

        for attempt in range(1, _FO_VALIDATION_MAX_RETRIES + 1):
            try:
                ok = validate_and_heal(sym, target_date, holidays, other_indices, force_historical=True)
                if ok:
                    if attempt > 1:
                        logger.info("%s: passed on retry %d/%d", sym, attempt, _FO_VALIDATION_MAX_RETRIES)
                    passed = True
                    break
                else:
                    logger.warning(
                        "%s: attempt %d/%d failed (validate_and_heal returned False)",
                        sym, attempt, _FO_VALIDATION_MAX_RETRIES,
                    )
            except Exception as exc:
                logger.warning(
                    "%s: attempt %d/%d raised exception: %s",
                    sym, attempt, _FO_VALIDATION_MAX_RETRIES, exc,
                )

            if attempt < _FO_VALIDATION_MAX_RETRIES:
                logger.info("%s: retrying in %ds…", sym, _FO_VALIDATION_RETRY_DELAY)
                time.sleep(_FO_VALIDATION_RETRY_DELAY)

        if passed:
            passed_count += 1
        else:
            logger.error(
                "%s: FAILED after %d attempt(s) — adding to failed list",
                sym, _FO_VALIDATION_MAX_RETRIES,
            )
            failed_symbols.append(sym)

    logger.info(
        "Morning FO validation complete: %d/%d passed. Failed: %s",
        passed_count, len(symbols), failed_symbols or "none",
    )
    return passed_count, len(symbols), failed_symbols


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


def backfill_indices_for_dates(dates: list[dict]) -> dict:
    """
    Fetch and upsert indices bhavcopy for each date in the list.

    Args:
        dates: list of dicts with a "date" key, e.g. [{"date": "2025-10-02"}, ...]

    Returns:
        {
            "success": [{"date": ..., "rows": n}, ...],
            "failed":  [{"date": ..., "error": "..."}, ...],
        }
    """
    result: dict = {"success": [], "failed": []}

    for entry in dates:
        raw = entry.get("date")
        if not raw:
            logger.warning("backfill_indices_for_dates: skipping entry with no 'date' key: %s", entry)
            continue
        try:
            target_date = date.fromisoformat(str(raw))
            indices, _ = fetch_indices_bhavcopy(target_date)
            index_rows = indices_to_price_rows(indices, target_date)
            n_upserted = upsert_price_history(index_rows)
            logger.info("Indices backfill OK for %s: %d rows upserted", target_date, n_upserted)
            result["success"].append({"date": str(target_date), "rows": n_upserted})
        except Exception as exc:
            logger.error("Indices backfill FAILED for %s: %s", raw, exc)
            result["failed"].append({"date": str(raw), "error": str(exc)})

    logger.info(
        "backfill_indices_for_dates complete: %d succeeded, %d failed",
        len(result["success"]), len(result["failed"]),
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s  %(levelname)-8s %(name)s — %(message)s",
    )
    run_validation_now_for_symbol("PAYTM", False)
    # run_validation_now()
    # result = backfill_indices_for_dates([
    #     {"date": "2026-06-29"},
    #     {"date": "2026-06-25"},
    # ])
    # print(result)
