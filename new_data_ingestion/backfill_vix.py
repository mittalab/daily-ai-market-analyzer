"""
NSE India VIX Historical Backfill.

Fetches daily OHLC for India VIX from NSE historicalOR/vixhistory API
and upserts into price_history with symbol='INDIA_VIX'.
"""

import argparse
import logging
import random
import sys
import time
from datetime import date, datetime, timedelta

import requests

from database.queries import upsert_price_history

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_BASE_URL = "https://www.nseindia.com"
_VIX_URL  = _BASE_URL + "/api/historicalOR/vixhistory"
_SYMBOL   = "INDIA_VIX"

_CHUNK_DAYS = 364

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


# ── Session ────────────────────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    logger.info("Warming up NSE session for VIX...")
    s.get(_BASE_URL, timeout=20)
    time.sleep(random.uniform(2.0, 3.0))
    s.headers.update({"Referer": _BASE_URL + "/"})
    s.get(_BASE_URL + "/reports-indices-historical-vix", timeout=20)
    time.sleep(random.uniform(1.5, 2.0))
    s.headers.update({"Referer": _BASE_URL + "/reports-indices-historical-vix"})
    return s


# ── Fetch ──────────────────────────────────────────────────────────────────────

def _fetch_chunk(session: requests.Session, from_date: date, to_date: date) -> list[dict]:
    """Fetch VIX records for a date range. Returns raw records list."""
    params = {
        "from": from_date.strftime("%d-%m-%Y"),
        "to":   to_date.strftime("%d-%m-%Y"),
    }
    logger.debug("Fetching VIX chunk %s → %s", from_date, to_date)

    try:
        r = session.get(_VIX_URL, params=params, timeout=30)
    except requests.RequestException as exc:
        logger.error("Network error for %s→%s: %s", from_date, to_date, exc)
        return []

    if r.status_code != 200:
        logger.warning("HTTP %d for %s→%s", r.status_code, from_date, to_date)
        return []

    try:
        return r.json().get("data", [])
    except Exception as exc:
        logger.error("JSON parse error: %s", exc)
        return []


# ── Parse ──────────────────────────────────────────────────────────────────────

def _parse_records(records: list[dict]) -> list[dict]:
    """Convert raw VIX API records into price_history rows."""
    rows = []
    for rec in records:
        raw_date = rec.get("EOD_TIMESTAMP", "")
        try:
            dt = datetime.strptime(raw_date.strip(), "%d-%b-%Y").date()
        except ValueError:
            logger.warning("Could not parse date: %s", raw_date)
            continue

        open_  = rec.get("EOD_OPEN_INDEX_VAL")
        high   = rec.get("EOD_HIGH_INDEX_VAL")
        low    = rec.get("EOD_LOW_INDEX_VAL")
        close  = rec.get("EOD_CLOSE_INDEX_VAL")

        if close is None:
            continue

        rows.append({
            "symbol": _SYMBOL,
            "date":   str(dt),
            "open":   float(open_) if open_ is not None else None,
            "high":   float(high)  if high  is not None else None,
            "low":    float(low)   if low   is not None else None,
            "close":  float(close),
            "volume": None,
        })
    return rows


# ── Date chunk generator ───────────────────────────────────────────────────────

def _date_chunks(start: date, end: date, chunk_days: int) -> list[tuple[date, date]]:
    chunks = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end)
        chunks.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    return chunks


# ── Main ───────────────────────────────────────────────────────────────────────

def run_backfill(start: date, end: date, dry_run: bool = False) -> dict:
    session  = _make_session()
    chunks   = _date_chunks(start, end, _CHUNK_DAYS)
    total    = 0
    saved    = 0

    logger.info("Fetching India VIX: %s → %s  (%d chunk(s))", start, end, len(chunks))

    for i, (chunk_start, chunk_end) in enumerate(chunks):
        logger.info("[%d/%d] %s → %s", i + 1, len(chunks), chunk_start, chunk_end)

        records = _fetch_chunk(session, chunk_start, chunk_end)
        if not records:
            logger.warning("  No records returned for this chunk")
            continue

        rows = _parse_records(records)
        total += len(rows)
        logger.info("  %d trading days parsed", len(rows))

        if not dry_run:
            upsert_price_history(rows)
            saved += len(rows)
            logger.info("  %d rows upserted", len(rows))
        else:
            logger.info("  %d rows (dry-run, not saved)", len(rows))

        if i < len(chunks) - 1:
            time.sleep(random.uniform(1.5, 2.5))

    return {"total": total, "saved": saved}


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill India VIX into price_history")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--days",      type=int, help="Past N calendar days")
    group.add_argument("--from",      dest="from_date", help="Start date YYYY-MM-DD")
    parser.add_argument("--to",       dest="to_date",   help="End date YYYY-MM-DD")
    parser.add_argument("--dry-run",  action="store_true")
    args = parser.parse_args()

    today = date.today()

    if args.days:
        start = today - timedelta(days=args.days)
        end   = today
    else:
        start = date.fromisoformat(args.from_date)
        end   = date.fromisoformat(args.to_date) if args.to_date else today

    run_backfill(start, end, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
