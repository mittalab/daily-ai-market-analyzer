"""
CLI runner for data validation and self-healing.
Supports daily checks and manual symbol backfill routing with optimized global backfills.
"""
import argparse
import logging
import sys
import json
from pathlib import Path
from datetime import date, datetime, timedelta
import pytz

from database.queries import (
    get_validation_state,
    get_last_passed_validation_date,
    upsert_validation_state,
    get_client,
    upsert_fii_dii_flow,
    get_latest_fii_dii,
)
from new_data_ingestion.nse_bhavcopy import get_holiday_dates, last_trading_day
from new_data_ingestion.ingestion_utils import (
    ingest_today_bhavcopy,
    ingest_today_options,
    ingest_today_kite_data,
    backfill_historical_date,
)
from new_data_ingestion.backfill_vix import run_backfill as run_vix_backfill
from new_data_ingestion.nse_fii_dii import create_nse_session, fetch_fii_dii, fii_dii_to_db_row
from new_validation.data_validator import (
    validate_kite_token,
    validate_db_connectivity,
    validate_stock_ohlcv,
    validate_stock_options,
    validate_stock_futures,
)
from new_notifications.telegram import (
    send_preflight_check_failed,
    send_preflight_failed,
    send_token_reminder,
    send_silent,
    send_loud,
)
from new_utils.stock_list import get_stock_list_for_analysis

logger = logging.getLogger(__name__)


def get_validation_end_date(holidays: set[date]) -> date:
    """
    Return the validation end date:
    Consider end date as today only if today is a trading day and current time
    in IST is >= 15:40 (3:40 PM). Otherwise, consider last trading day.
    """
    IST = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(IST)
    today_ist = now_ist.date()
    now_time_ist = now_ist.time()
    
    is_today_trading = (today_ist.weekday() < 5) and (today_ist not in holidays)
    market_close_time = datetime.strptime("15:40", "%H:%M").time()
    
    if is_today_trading and now_time_ist >= market_close_time:
        return today_ist
    else:
        return last_trading_day(today_ist - timedelta(days=1))


def _last_tuesday_of_month(ref: date) -> date:
    """Return the last Tuesday of ref's month (standard F&O stock options expiry day)."""
    if ref.month == 12:
        last_day = date(ref.year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(ref.year, ref.month + 1, 1) - timedelta(days=1)
    delta = (last_day.weekday() - 1) % 7  # days back to Tuesday (weekday=1)
    return last_day - timedelta(days=delta)


def get_stock_expiries(ref_date: date, holidays: set[date]) -> list[date]:
    """
    Resolve near (+ next if close) monthly stock expiries.
    If remaining trading days to near expiry <= 5, return [near, next], else [near].
    """
    near = _last_tuesday_of_month(ref_date)
    # Adjust near if it falls on holiday (walk back)
    while near in holidays or near.weekday() >= 5:
        near -= timedelta(days=1)
        
    if ref_date >= near:
        # Near has already passed, near becomes next month
        next_month = date(ref_date.year, ref_date.month % 12 + 1, 1) if ref_date.month < 12 else date(ref_date.year + 1, 1, 1)
        near = _last_tuesday_of_month(next_month)
        while near in holidays or near.weekday() >= 5:
            near -= timedelta(days=1)

    # Calculate days remaining to near expiry
    days_remaining = count_trading_days(ref_date, near, holidays)
    
    if days_remaining <= 5:
        # Resolve next month
        next_month_ref = date(near.year, near.month % 12 + 1, 1) if near.month < 12 else date(near.year + 1, 1, 1)
        nxt = _last_tuesday_of_month(next_month_ref)
        while nxt in holidays or nxt.weekday() >= 5:
            nxt -= timedelta(days=1)
        return [near, nxt]
    return [near]


def get_index_expiries(ref_date: date, holidays: set[date]) -> list[date]:
    """
    Resolve NIFTY weekly expiries (every Tuesday, weekday=1).
    If remaining trading days to Tuesday <= 2, return [near, next], else [near].
    """
    # Find Tuesday of current week
    near = ref_date + timedelta(days=(1 - ref_date.weekday()) % 7)
    
    if ref_date >= near:
        near = near + timedelta(days=7)
        
    while near in holidays or near.weekday() >= 5:
        near -= timedelta(days=1)
        
    days_remaining = count_trading_days(ref_date, near, holidays)
    
    if days_remaining <= 2:
        nxt = near + timedelta(days=7)
        while nxt in holidays or nxt.weekday() >= 5:
            nxt -= timedelta(days=1)
        return [near, nxt]
    return [near]


def count_trading_days(start: date, end: date, holidays: set[date]) -> int:
    """Count number of trading days in [start, end) excluding weekends and holidays."""
    count = 0
    curr = start
    while curr < end:
        if curr.weekday() < 5 and curr not in holidays:
            count += 1
        curr += timedelta(days=1)
    return count


def get_trading_day_gaps(start_date: date, end_date: date, holidays: set[date]) -> list[date]:
    """Return all trading days in (start_date, end_date] inclusive."""
    gaps = []
    curr = start_date + timedelta(days=1)
    while curr <= end_date:
        if curr.weekday() < 5 and curr not in holidays:
            gaps.append(curr)
        curr += timedelta(days=1)
    return gaps


def get_other_indices() -> list[str]:
    """Parse other unique indices from config/sector_map.json."""
    path = Path(__file__).parent.parent / "config" / "sector_map.json"
    try:
        data = json.loads(path.read_text())
        stocks = data.get("stocks", {})
        indices = {v["index"] for v in stocks.values() if v.get("index")}
        indices.discard("NIFTY")
        indices.discard("NIFTY_50")
        return sorted(list(indices))
    except Exception as exc:
        logger.warning("Failed to load other indices from sector_map: %s", exc)
        return []


def run_checks_for_symbol(symbol: str, target_date: date, holidays: set[date], other_indices: list[str]) -> tuple[bool, dict]:
    """Run validation checks specific to the symbol type on target_date."""
    results = {}
    passed = True

    if symbol == "NIFTY":
        # NIFTY Index Checks
        # OHLCV: 180 trading sessions
        ohlcv_ok, ohlcv_msg = validate_stock_ohlcv("NIFTY", target_date, days=180)
        results["nifty_ohlcv"] = {"ok": ohlcv_ok, "msg": ohlcv_msg}
        if not ohlcv_ok:
            passed = False
            
        # Options: 15 days, near + next weekly if <= 2 trading days from Tuesday
        idx_expiries = get_index_expiries(target_date, holidays)
        results["nifty_options"] = []
        for exp in idx_expiries:
            opt_ok, opt_msg = validate_stock_options("NIFTY", exp, target_date, days=15)
            results["nifty_options"].append({"expiry": str(exp), "ok": opt_ok, "msg": opt_msg})
            if not opt_ok:
                passed = False
                
        # Futures: 30 days, near monthly expiry contract
        stock_expiries = get_stock_expiries(target_date, holidays)
        results["nifty_futures"] = []
        if stock_expiries:
            near_exp = stock_expiries[0]
            fut_ok, fut_msg = validate_stock_futures("NIFTY", near_exp, target_date, days=30)
            results["nifty_futures"].append({"expiry": str(near_exp), "ok": fut_ok, "msg": fut_msg})
            if not fut_ok:
                passed = False

    elif symbol == "INDIA_VIX":
        # India VIX: 30 trading days OHLCV
        ohlcv_ok, ohlcv_msg = validate_stock_ohlcv("INDIA_VIX", target_date, days=30)
        results["vix_ohlcv"] = {"ok": ohlcv_ok, "msg": ohlcv_msg}
        if not ohlcv_ok:
            passed = False

    elif symbol in other_indices:
        # Other Sector Indices: 120 trading days OHLCV
        ohlcv_ok, ohlcv_msg = validate_stock_ohlcv(symbol, target_date, days=120)
        results["index_ohlcv"] = {"ok": ohlcv_ok, "msg": ohlcv_msg}
        if not ohlcv_ok:
            passed = False

    else:
        # Regular Stock Symbol Checks
        # OHLCV: 180 trading sessions
        ohlcv_ok, ohlcv_msg = validate_stock_ohlcv(symbol, target_date, days=180)
        results["stock_ohlcv"] = {"ok": ohlcv_ok, "msg": ohlcv_msg}
        if not ohlcv_ok:
            passed = False
            
        # Options & Futures: 30 days, near + next monthly if <= 5 trading days from last Tuesday
        stock_expiries = get_stock_expiries(target_date, holidays)
        results["stock_options"] = []
        results["stock_futures"] = []
        for exp in stock_expiries:
            opt_ok, opt_msg = validate_stock_options(symbol, exp, target_date, days=30)
            results["stock_options"].append({"expiry": str(exp), "ok": opt_ok, "msg": opt_msg})
            if not opt_ok:
                passed = False
                
            fut_ok, fut_msg = validate_stock_futures(symbol, exp, target_date, days=30)
            results["stock_futures"].append({"expiry": str(exp), "ok": fut_ok, "msg": fut_msg})
            if not fut_ok:
                passed = False

    return passed, results


def  validate_and_heal(symbol: str, target_date: date, holidays: set[date], other_indices: list[str], force: bool = False) -> bool:
    """Validate data status, run chronological historical backfills for gaps, and heal today if needed."""
    logger.info("Starting validation for %s on %s", symbol, target_date)
    
    # 1. Cache lookup
    if not force:
        cached = get_validation_state(symbol, target_date)
        if cached and cached.get("status") == "PASSED":
            logger.info("Validation already cached as PASSED for %s on %s", symbol, target_date)
            return True

    # 2. Retrieve last successful validation date
    last_passed_date = get_last_passed_validation_date(symbol)
    
    # Resolve the chronological dates to backfill
    today = get_validation_end_date(holidays)

    if last_passed_date is None:
        last_passed_date = date.today() - timedelta(days=250)

    # Find all trading days between last_passed_date + 1 and target_date - 1
    gap_dates = get_trading_day_gaps(last_passed_date, target_date - timedelta(days=1), holidays)

    # 3. Gap chronological backfill
    if gap_dates and symbol == "INDIA_VIX":
        run_vix_backfill(last_passed_date, today)
    elif gap_dates:
        logger.info("%s: Found %d gap trading day(s) since last successful validation (%s). Starting gap backfills.", symbol, len(gap_dates), last_passed_date)
        for gap in gap_dates:
            logger.info("%s: Backfilling gap date: %s", symbol, gap)
            try:
                if symbol in other_indices:
                    ingest_today_bhavcopy(gap)  # Index bhavcopy fetcher handles gap
                elif symbol == "NIFTY":
                    backfill_historical_date(gap)
                else:
                    backfill_historical_date(gap, symbol)
            except Exception as e:
                logger.error("%s: Failed to backfill gap date %s: %s", symbol, gap, e)

    # 4. Run validation checks for today (target_date)
    passed, results = run_checks_for_symbol(symbol, target_date, holidays, other_indices)
    if passed:
        logger.info("All validation checks PASSED for %s on %s", symbol, target_date)
        upsert_validation_state(symbol, target_date, "daily", "PASSED", results)
        return True

    logger.warning("Validation FAILED initially for %s on %s. Triggering healing.", symbol, target_date)

    # 5. Target-specific healing
    failed_checks = []
    if symbol == "NIFTY":
        if not results.get("nifty_ohlcv", {}).get("ok"): failed_checks.append("nifty_ohlcv")
        if any(not o["ok"] for o in results.get("nifty_options", [])): failed_checks.append("nifty_options")
        if any(not f["ok"] for f in results.get("nifty_futures", [])): failed_checks.append("nifty_futures")
    elif symbol == "INDIA_VIX":
        if not results.get("vix_ohlcv", {}).get("ok"): failed_checks.append("vix_ohlcv")
    elif symbol in other_indices:
        if not results.get("index_ohlcv", {}).get("ok"): failed_checks.append("index_ohlcv")
    else:
        if not results.get("stock_ohlcv", {}).get("ok"): failed_checks.append("stock_ohlcv")
        if any(not o["ok"] for o in results.get("stock_options", [])): failed_checks.append("stock_options")
        if any(not f["ok"] for f in results.get("stock_futures", [])): failed_checks.append("stock_futures")

    # Healing logic depending on whether target_date is today or historical
    for check in failed_checks:
        logger.info("%s: Healing failed check '%s' for date %s", symbol, check, target_date)
        try:
            if target_date == today:
                # Live fetch for today's missing data
                if check in ("stock_ohlcv", "nifty_ohlcv", "stock_futures", "nifty_futures"):
                    ingest_today_kite_data(target_date, [symbol])
                    if symbol == "NIFTY":
                        ingest_today_bhavcopy(target_date)
                elif check in ("stock_options", "nifty_options"):
                    ingest_today_options(target_date, [symbol])
                elif check == "index_ohlcv":
                    ingest_today_bhavcopy(target_date)
                elif check == "vix_ohlcv":
                    run_vix_backfill(target_date, target_date)
            else:
                # Historical backfill for past dates
                if symbol == "INDIA_VIX":
                    run_vix_backfill(target_date, target_date)
                elif symbol in other_indices:
                    ingest_today_bhavcopy(target_date)
                elif symbol == "NIFTY":
                    backfill_historical_date(target_date)
                else:
                    backfill_historical_date(target_date, symbol)
        except Exception as exc:
            logger.error("%s: Failed to heal check '%s': %s", symbol, check, exc)

    # Re-run checks after healing
    passed, results = run_checks_for_symbol(symbol, target_date, holidays, other_indices)
    if passed:
        logger.info("Validation checks PASSED for %s on %s after healing", symbol, target_date)
        upsert_validation_state(symbol, target_date, "daily", "PASSED", results)
        return True

    # Marks as FAILED
    logger.error("Validation failed to resolve for %s on %s", symbol, target_date)
    upsert_validation_state(symbol, target_date, "daily", "FAILED", results, "Validation checks failed after healing retry.")
    return False


def run_daily_validation(target_date: date, force: bool = False) -> bool:
    """
    Run the complete validation & self-healing process for all symbols
    (Nifty 50 stocks, NIFTY index, INDIA_VIX, other sector indices) on the target_date.
    Also handles FII/DII flows ingestion and pre-flight checks.
    
    Returns True if all symbols pass successfully, False otherwise.
    """
    holidays = get_holiday_dates()
    
    # 1. Pre-flight infrastructure validation
    db_ok, db_msg = validate_db_connectivity()
    if not db_ok:
        logger.critical("Database connectivity failed: %s. Aborting validation.", db_msg)
        send_preflight_failed(f"DB Connectivity: {db_msg}", str(target_date))
        return False
        
    kite_ok, kite_msg = validate_kite_token()
    if not kite_ok:
        logger.warning("Kite token check failed: %s", kite_msg)
        send_token_reminder()

    # 2. Ingest FII/DII flows for target_date as first step
    logger.info("Fetching FII/DII flows for date: %s", target_date)
    try:
        session = create_nse_session()
        fii_data = fetch_fii_dii(session)
        db_row = fii_dii_to_db_row(fii_data, target_date)
        upsert_fii_dii_flow(db_row)
        logger.info("FII/DII flows successfully stored for %s", target_date)
    except Exception as exc:
        logger.warning("FII/DII flows ingestion failed: %s. Attempting fallback to cache.", exc)
        try:
            cached = get_latest_fii_dii()
            if cached:
                cached_row = dict(cached)
                cached_row.pop("id", None)
                cached_row.pop("created_at", None)
                cached_row["source"] = "CACHED"
                cached_row["date"] = str(target_date)
                upsert_fii_dii_flow(cached_row)
                logger.info("FII/DII using cached value from %s", cached.get("date"))
        except Exception as cache_exc:
            logger.error("FII/DII fallback to cache failed: %s", cache_exc)

    # 3. Resolve symbols universe
    other_indices = get_other_indices()
    stocks_dict = get_stock_list_for_analysis(include_kite_trades=True)
    stock_symbols = list(stocks_dict.keys())
    
    symbols_universe = sorted(list(set(stock_symbols) | {"NIFTY", "INDIA_VIX"} | set(other_indices)))
    logger.info("Running daily validation checks for %d symbols on %s", len(symbols_universe), target_date)

    success_count = 0
    failed_symbols = []
    
    for idx, symbol in enumerate(symbols_universe):
        logger.info("[%d/%d] Validating %s", idx + 1, len(symbols_universe), symbol)
        try:
            ok = validate_and_heal(symbol, target_date, holidays, other_indices, force=force)
            if ok:
                success_count += 1
            else:
                failed_symbols.append(symbol)
        except Exception as e:
            logger.error("Exception during validation of %s: %s", symbol, e)
            failed_symbols.append(symbol)

    logger.info("Daily validation run finished. Passed: %d/%d. Failed: %s", success_count, len(symbols_universe), failed_symbols)
    if failed_symbols:
        logger.warning("Validation failed for some symbols. Self-healing was unable to resolve: %s", failed_symbols)
        return False
        
    logger.info("All active symbols validation passed!")
    return True


def main():
    parser = argparse.ArgumentParser(description="System Validation & Self-Healing Utility")
    parser.add_argument("--mode", choices=["daily", "manual"], required=True, help="daily validates all watchlist+indices; manual validates one symbol")
    parser.add_argument("--symbol", help="Target symbol (required for manual mode)")
    parser.add_argument("--date", help="Target date YYYY-MM-DD (defaults to last trading day)")
    parser.add_argument("--force", action="store_true", help="Bypass cache and force check execution")
    args = parser.parse_args()

    # Load holidays
    holidays = get_holiday_dates()
    
    # Resolve target date
    if args.date:
        target_date = date.fromisoformat(args.date)
    else:
        target_date = get_validation_end_date(holidays)

    if args.mode == "manual":
        # Pre-flight infrastructure validation
        db_ok, db_msg = validate_db_connectivity()
        if not db_ok:
            logger.critical("Database connectivity failed: %s. Aborting validation.", db_msg)
            sys.exit(1)
            
        other_indices = get_other_indices()
        if not args.symbol:
            logger.error("Error: --symbol is required in manual mode")
            sys.exit(1)
        success = validate_and_heal(args.symbol.upper(), target_date, holidays, other_indices, force=args.force)
        if not success:
            sys.exit(1)
    else:
        # Daily mode: use the new utility function
        success = run_daily_validation(target_date, force=args.force)
        if not success:
            sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    main()
