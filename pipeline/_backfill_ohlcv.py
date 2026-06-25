"""
Backfill 250 days of OHLCV for all Nifty 50 equity symbols into price_history.

Uses Kite Connect historical_data API (requires valid token).
Skips index symbols (NIFTY_*, INDIA_VIX) — those are handled by _backfill_sector_indices.py.
Adds 0.35s delay between calls (safe Kite rate limit).
"""

import logging
import sys
import time
from datetime import date, timedelta

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

from database.queries import get_client, upsert_price_history
from integrations.kite_oauth import get_authenticated_kite, validate_token
from config.constants import SYMBOL_INDIA_VIX
from integrations.kite_ohlcv import fetch_ohlcv, get_equity_token, ohlcv_to_price_rows
from integrations.nse_bhavcopy import get_nifty50_symbols

LOOKBACK_DAYS = 250
SLEEP_SECS    = 0.35


def get_distinct_equity_symbols() -> list[str]:
    """Get all distinct non-index symbols from price_history, plus all Nifty 50."""
    client  = get_client()
    # Get Nifty50 symbols from config (canonical list)
    nifty50 = sorted(get_nifty50_symbols())
    # Also check what's already in price_history (might include other equities)
    resp = client.table("price_history").select("symbol").execute()
    db_syms = {r["symbol"] for r in resp.data}
    # Exclude index symbols
    equity_syms = {s for s in db_syms if not s.startswith("NIFTY_") and s != SYMBOL_INDIA_VIX}
    # Union with Nifty50
    all_syms = sorted(set(nifty50) | equity_syms)
    return all_syms


def get_existing_date_range(symbol: str) -> tuple[str | None, str | None, int]:
    """Return (first_date, last_date, row_count) for a symbol in price_history."""
    client = get_client()
    resp = (
        client.table("price_history")
        .select("date")
        .eq("symbol", symbol)
        .order("date")
        .execute()
    )
    if not resp.data:
        return None, None, 0
    dates = [r["date"] for r in resp.data]
    return dates[0], dates[-1], len(dates)


def main():
    # Step 1: Validate Kite token
    logger.info("Checking Kite token validity...")
    try:
        kite = get_authenticated_kite()
        if not validate_token():
            logger.error("Kite token is EXPIRED or INVALID. Cannot run backfill.")
            logger.error("Please refresh token at https://api.abhishekmittal.in/kite/refresh")
            sys.exit(1)
        logger.info("Kite token is valid.")
    except Exception as e:
        logger.error("Failed to get Kite auth: %s", e)
        sys.exit(1)

    # Step 2: Get symbols to backfill
    symbols = get_distinct_equity_symbols()
    logger.info("Symbols to backfill: %d", len(symbols))

    today     = date.today()
    from_date = today - timedelta(days=LOOKBACK_DAYS)
    logger.info("Fetching OHLCV from %s to %s", from_date, today)

    # Load instruments master once (cached inside kite_ohlcv module)
    logger.info("Loading NFO/NSE instruments master (cached for session)...")

    stored_total = 0
    skipped      = 0
    errors       = 0

    for i, symbol in enumerate(symbols):
        # Check current state
        first, last, count = get_existing_date_range(symbol)
        target_first = str(from_date)

        # If we already have sufficient history, skip
        if first is not None and first <= target_first and count >= 200:
            logger.info("[%d/%d] %s: already has %d rows from %s — SKIP",
                        i + 1, len(symbols), symbol, count, first)
            skipped += 1
            continue

        logger.info("[%d/%d] %s: currently %d rows (from %s) — fetching 250d...",
                    i + 1, len(symbols), symbol, count, first or "none")

        try:
            token_id = get_equity_token(kite, symbol)
            df       = fetch_ohlcv(kite, token_id, from_date, today)
            if df.empty:
                logger.warning("  %s: empty response from Kite", symbol)
                errors += 1
                time.sleep(SLEEP_SECS)
                continue

            rows = ohlcv_to_price_rows(symbol, df)
            n    = upsert_price_history(rows)
            stored_total += n
            logger.info("  %s: %d rows fetched, %d stored", symbol, len(df), n)

        except Exception as exc:
            logger.error("  %s: FAILED — %s", symbol, exc)
            errors += 1

        time.sleep(SLEEP_SECS)

    logger.info(
        "\nBackfill complete: %d rows stored, %d symbols skipped, %d errors",
        stored_total, skipped, errors,
    )

    # Step 3: Before/after audit for HDFCBANK
    logger.info("\nAudit for HDFCBANK:")
    client = get_client()
    resp = (
        client.table("price_history")
        .select("date")
        .eq("symbol", "HDFCBANK")
        .order("date")
        .execute()
    )
    rows = resp.data
    print(f"HDFCBANK AFTER: {len(rows)} rows, "
          f"first={rows[0]['date'] if rows else 'none'}, "
          f"last={rows[-1]['date'] if rows else 'none'}")


if __name__ == "__main__":
    main()
