"""
Backfill 250 days of sector index history into price_history.

Targets: NIFTY_50, NIFTY_BANK, NIFTY_IT, NIFTY_AUTO, NIFTY_PHARMA,
         NIFTY_FMCG, NIFTY_METAL, NIFTY_ENERGY, NIFTY_FIN_SERVICE, INDIA_VIX

Uses: fetch_indices_bhavcopy (no auth required — public NSE archives).
Skips days already in price_history for these symbols.
Adds 1-second delay between requests to avoid NSE rate limiting.
"""

import logging
import sys
import time
from datetime import date, timedelta

# Add project root to path
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

from integrations.nse_bhavcopy import (
    fetch_indices_bhavcopy,
    get_holiday_dates,
    indices_to_price_rows,
    last_trading_day,
)
from database.queries import get_client, upsert_price_history

# Target symbols (stored with underscores in price_history)
TARGET_SYMBOLS = {
    "NIFTY_50",
    "NIFTY_BANK",
    "NIFTY_IT",
    "NIFTY_AUTO",
    "NIFTY_PHARMA",
    "NIFTY_FMCG",
    "NIFTY_METAL",
    "NIFTY_ENERGY",
    "NIFTY_FIN_SERVICE",
    "INDIA_VIX",
}

LOOKBACK_DAYS = 250


def get_existing_dates(symbols: set) -> dict[str, set]:
    """Return {symbol: set_of_dates_already_in_db} for all target symbols."""
    client = get_client()
    existing: dict[str, set] = {s: set() for s in symbols}
    for symbol in symbols:
        resp = (
            client.table("price_history")
            .select("date")
            .eq("symbol", symbol)
            .execute()
        )
        existing[symbol] = {r["date"] for r in resp.data}
    return existing


def get_trading_days(start: date, end: date) -> list[date]:
    """Return all trading days (Mon-Fri, not holiday) from start to end inclusive."""
    holidays = get_holiday_dates()
    days = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5 and cursor not in holidays:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def main():
    today = date.today()
    start = today - timedelta(days=LOOKBACK_DAYS)

    logger.info("Backfill sector indices: %s to %s (%d calendar days lookback)",
                start, today, LOOKBACK_DAYS)

    # Step 1: Build list of all trading days in the range
    trading_days = get_trading_days(start, today)
    logger.info("Total trading days to check: %d", len(trading_days))

    # Step 2: Check which dates already exist in DB
    logger.info("Fetching existing dates from price_history...")
    existing = get_existing_dates(TARGET_SYMBOLS)
    for sym, dates in existing.items():
        if dates:
            logger.info("  %s: %d rows already in DB", sym, len(dates))

    # Step 3: Determine which days need to be fetched
    # A day is "missing" if ANY target symbol doesn't have it
    # We group by date and fetch bhavcopy once per date
    missing_days = []
    for td in trading_days:
        date_str = str(td)
        # Check if ALL target symbols have this date
        symbols_missing = [s for s in TARGET_SYMBOLS if date_str not in existing[s]]
        if symbols_missing:
            missing_days.append(td)

    logger.info("Days needing fetch: %d out of %d trading days", len(missing_days), len(trading_days))

    if not missing_days:
        logger.info("All data already present — nothing to do!")
        return

    # Step 4: Fetch bhavcopy for each missing day
    stored_total = 0
    errors = 0

    for i, td in enumerate(missing_days):
        logger.info("[%d/%d] Fetching indices bhavcopy for %s...", i + 1, len(missing_days), td)
        try:
            indices, trade_date = fetch_indices_bhavcopy(td)
            rows = indices_to_price_rows(indices, trade_date)

            # Filter to only rows for target symbols, and only if not already in DB
            date_str = str(trade_date)
            new_rows = [
                r for r in rows
                if r["symbol"] in TARGET_SYMBOLS and date_str not in existing[r["symbol"]]
            ]

            if new_rows:
                n = upsert_price_history(new_rows)
                stored_total += n
                # Update local cache
                for r in new_rows:
                    existing[r["symbol"]].add(date_str)
                logger.info("  Stored %d rows for %s (symbols: %s)",
                            len(new_rows), trade_date,
                            [r["symbol"] for r in new_rows])
            else:
                logger.info("  All symbols already have data for %s — skipped", trade_date)

        except FileNotFoundError as e:
            logger.warning("  Bhavcopy not available for %s: %s", td, e)
            errors += 1
        except Exception as e:
            logger.error("  ERROR for %s: %s", td, e)
            errors += 1

        # Rate limiting: 1 second between requests
        if i < len(missing_days) - 1:
            time.sleep(1.0)

    logger.info(
        "Backfill complete: %d rows stored, %d errors, %d trading days processed",
        stored_total, errors, len(missing_days),
    )

    # Step 5: Final audit
    logger.info("\nFinal row counts per symbol:")
    client = get_client()
    for symbol in sorted(TARGET_SYMBOLS):
        resp = client.table("price_history").select("date").eq("symbol", symbol).execute()
        logger.info("  %s: %d rows", symbol, len(resp.data))


if __name__ == "__main__":
    main()
