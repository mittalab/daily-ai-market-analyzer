"""
Option Chain Snapshot — batch fetch and persist to options_snapshots.

Fetches NIFTY + all provided stock symbols using a single NSE session.
Persists to options_snapshots table via upsert.

Usage:
    python run_option_chain_snapshot.py RELIANCE TCS INFY HDFC
    python run_option_chain_snapshot.py --file symbols.txt
    python run_option_chain_snapshot.py RELIANCE --expiry "26-Jun-2026"
    python run_option_chain_snapshot.py RELIANCE --all-expiries

symbols.txt: one symbol per line, blank lines and # comments ignored.
"""

import argparse
import json
import logging
import random
import sys
import time
from datetime import date, datetime

import requests

from database.queries import upsert_options_snapshots

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

BASE_URL      = "https://www.nseindia.com"
SESSION_REFRESH_EVERY = 50   # safety fallback only — bm_sv lasts 100+ min, batch takes ~2.5 min

_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",   # NO br — Brotli not handled by requests
    "Connection":      "keep-alive",
    "sec-ch-ua":        '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest":  "empty",
    "Sec-Fetch-Mode":  "cors",
    "Sec-Fetch-Site":  "same-origin",
}


# ── Session helpers ────────────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    return s


def _chain_type(symbol: str) -> str:
    return "Indices" if symbol.upper() in _INDEX_SYMBOLS else "Equity"


def _referrer_page(symbol: str) -> str:
    from urllib.parse import urlencode
    if symbol in _INDEX_SYMBOLS:
        return f"{BASE_URL}/option-chain?" + urlencode({"symbol": symbol})
    return f"{BASE_URL}/get-quotes/derivatives?" + urlencode({"symbol": symbol})


def _warm_up(session: requests.Session) -> None:
    """Hit homepage to get initial Akamai bot-detection cookies."""
    logger.info("Warming up session — hitting homepage")
    session.headers.update({"Referer": ""})
    session.get(BASE_URL, timeout=20)
    time.sleep(random.uniform(2.0, 3.0))


def _refresh_session(session: requests.Session) -> None:
    """Periodic re-hit of homepage to keep bm_sv cookie alive."""
    logger.info("Refreshing session cookies")
    session.get(BASE_URL, timeout=20)
    time.sleep(random.uniform(1.5, 2.5))


# ── NSE API calls ──────────────────────────────────────────────────────────────

def _hit_referrer(session: requests.Session, symbol: str) -> None:
    """Visit the symbol's option chain page to set Referer + refresh bm_sv."""
    url = _referrer_page(symbol)
    session.headers.update({"Referer": BASE_URL + "/"})
    session.get(url, timeout=20)
    session.headers.update({"Referer": url})
    time.sleep(random.uniform(1.2, 2.0))


def _fetch_contract_info(session: requests.Session, symbol: str) -> dict:
    r = session.get(
        f"{BASE_URL}/api/option-chain-contract-info",
        params={"symbol": symbol},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def _fetch_v2_metadata(session: requests.Session) -> None:
    """Mirrors the browser's option-chain-v2.json call; 304 is expected."""
    url = f"{BASE_URL}/json/option-chain/option-chain-v2.json"
    session.get(url, timeout=20)


def _fetch_chain_v3(
    session: requests.Session,
    symbol: str,
    chain_type: str,
    expiry: str,
) -> dict:
    r = session.get(
        f"{BASE_URL}/api/option-chain-v3",
        params={"type": chain_type, "symbol": symbol, "expiry": expiry},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


# ── Parse chain into DB rows ───────────────────────────────────────────────────

def _parse_expiry_date(expiry_str: str) -> date:
    return datetime.strptime(expiry_str, "%d-%b-%Y").date()


def _parse_chain(symbol: str, snapshot_date: date, data: dict, target_expiry: str | None) -> list[dict]:
    """
    Parse option-chain-v3 response into options_snapshots rows.

    Filters: impliedVolatility > 0 (deep OTM / illiquid strikes return 0).
    If target_expiry is None, all expiries in the response are included.
    """
    records      = data.get("records", {})
    chain        = records.get("data", [])
    all_expiries = set(records.get("expiryDates", []))

    if target_expiry:
        keep_expiries = {target_expiry}
    else:
        keep_expiries = all_expiries

    rows = []
    for entry in chain:
        expiry_str = entry.get("expiryDates", "")   # v3 API uses plural key
        if expiry_str not in keep_expiries:
            continue

        try:
            expiry_date = _parse_expiry_date(expiry_str)
        except ValueError:
            logger.warning("%s: could not parse expiry '%s'", symbol, expiry_str)
            continue

        strike = entry.get("strikePrice")
        if strike is None:
            continue

        for opt_type in ("CE", "PE"):
            side = entry.get(opt_type)
            if not side:
                continue

            iv = side.get("impliedVolatility")

            rows.append({
                "symbol":        symbol,
                "snapshot_date": str(snapshot_date),
                "expiry_date":   str(expiry_date),
                "strike":        float(strike),
                "option_type":   opt_type,
                "oi":            int(side["openInterest"]) if side.get("openInterest") is not None else None,
                "oi_change":     int(side["changeinOpenInterest"]) if side.get("changeinOpenInterest") is not None else None,
                "volume":        int(side["totalTradedVolume"]) if side.get("totalTradedVolume") is not None else None,
                "iv":            float(iv) if iv else None,
                "premium_close": side.get("lastPrice"),
            })

    return rows


# ── Per-symbol fetch ───────────────────────────────────────────────────────────

def _fetch_symbol(
    session: requests.Session,
    symbol: str,
    snapshot_date: date,
    num_expiries: int = 3,
    forced_expiry: str | None = None,
) -> list[dict]:
    """
    Fetch option chain for one symbol across up to num_expiries expiries.

    Flow per symbol (mirrors browser exactly):
      0. Hit referrer page   — refreshes bm_sv, sets Referer header
      1. contract-info       — get sorted expiry list
      2. v2 metadata         — keeps session warm (304 expected)
      3. option-chain-v3 ×N  — one call per expiry, short delay between each

    Returns all parsed DB rows across all fetched expiries.
    """
    ctype = _chain_type(symbol)

    # Step 0: referrer page
    _hit_referrer(session, symbol)

    # Step 1: contract-info → expiry list (already in ascending date order from NSE)
    contract     = _fetch_contract_info(session, symbol)
    expiry_dates = contract.get("expiryDates", [])
    if not expiry_dates:
        raise ValueError("No expiry dates returned — session may be blocked")

    if forced_expiry:
        fetch_expiries = [forced_expiry]
    else:
        fetch_expiries = expiry_dates[:num_expiries]   # nearest N in ascending order

    logger.info(
        "  %s (%s) — fetching %d expiries: %s  [%d available]",
        symbol, ctype,
        len(fetch_expiries),
        ", ".join(fetch_expiries),
        len(expiry_dates),
    )

    time.sleep(random.uniform(0.5, 1.0))

    # Step 2: v2 metadata once per symbol (mirrors browser, 304 expected)
    _fetch_v2_metadata(session)
    time.sleep(random.uniform(0.4, 0.8))

    # Step 3: one v3 call per expiry with a short human-like delay between
    all_rows: list[dict] = []
    for j, expiry in enumerate(fetch_expiries):
        data       = _fetch_chain_v3(session, symbol, ctype, expiry)
        underlying = data.get("records", {}).get("underlyingValue")
        rows       = _parse_chain(symbol, snapshot_date, data, expiry)
        all_rows.extend(rows)
        logger.info("    expiry %s — spot: %s  rows: %d", expiry, underlying, len(rows))

        # Short delay between expiry calls — avoids burst pattern within same symbol
        if j < len(fetch_expiries) - 1:
            time.sleep(random.uniform(0.8, 1.5))

    return all_rows


# ── Batch runner ───────────────────────────────────────────────────────────────

def run_batch(
    symbols: list[str],
    snapshot_date: date,
    num_expiries: int = 3,
    forced_expiry: str | None = None,
    dry_run: bool = False,
    max_retries: int = 2,
) -> dict:
    """
    Fetch option chain for all symbols and upsert to options_snapshots.

    Returns summary dict: {total_rows, saved_rows, failed_symbols, elapsed_secs}.
    """
    session = _make_session()
    _warm_up(session)

    total_rows  = 0
    saved_rows  = 0
    failed: list[str] = []
    t_start = time.monotonic()

    for i, symbol in enumerate(symbols):
        # Periodic session refresh every N symbols
        if i > 0 and i % SESSION_REFRESH_EVERY == 0:
            _refresh_session(session)

        logger.info("[%d/%d] Fetching %s", i + 1, len(symbols), symbol)

        rows = None
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                rows = _fetch_symbol(session, symbol, snapshot_date, num_expiries, forced_expiry)
                break
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries:
                    wait = attempt * random.uniform(3.0, 5.0)
                    logger.warning("  %s attempt %d failed: %s — retrying in %.1fs", symbol, attempt, exc, wait)
                    time.sleep(wait)

        if rows is None:
            logger.error("  %s FAILED after %d attempts: %s", symbol, max_retries, last_exc)
            failed.append(symbol)
        elif not rows:
            logger.warning("  %s: 0 rows parsed (empty chain)", symbol)
        else:
            total_rows += len(rows)
            if not dry_run:
                upsert_options_snapshots(rows)
                saved_rows += len(rows)
                logger.info("  %s: %d rows upserted", symbol, len(rows))
            else:
                logger.info("  %s: %d rows (dry-run, not saved)", symbol, len(rows))

        # Polite delay between symbols (random to avoid pattern detection)
        if i < len(symbols) - 1:
            time.sleep(random.uniform(1.5, 3.0))

    elapsed = time.monotonic() - t_start
    return {
        "total_rows":     total_rows,
        "saved_rows":     saved_rows,
        "failed_symbols": failed,
        "elapsed_secs":   elapsed,
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def _load_symbols_from_file(path: str) -> list[str]:
    with open(path) as f:
        lines = f.readlines()
    return [
        line.strip().upper()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch NSE option chains and persist to options_snapshots"
    )
    parser.add_argument(
        "symbols", nargs="*",
        help="Stock symbols e.g. RELIANCE TCS INFY (NIFTY always added)"
    )
    parser.add_argument(
        "--file", "-f",
        help="Text file with one symbol per line"
    )
    parser.add_argument(
        "--expiry",
        default=None,
        help="Force a specific single expiry e.g. '26-Jun-2026'"
    )
    parser.add_argument(
        "--expiries", type=int, default=3,
        help="Number of expiries to fetch per symbol in ascending order (default: 3)"
    )
    parser.add_argument(
        "--date",
        default=None,
        help="snapshot_date override in YYYY-MM-DD (default: today)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and parse but do NOT write to DB"
    )
    args = parser.parse_args()

    # Build symbol list
    symbols: list[str] = []

    if args.file:
        symbols.extend(_load_symbols_from_file(args.file))

    symbols.extend([s.upper() for s in args.symbols])

    # Deduplicate while preserving order; always include NIFTY first
    seen = set()
    ordered: list[str] = []
    for s in ["NIFTY"] + symbols:
        if s not in seen:
            seen.add(s)
            ordered.append(s)

    if len(ordered) == 0:
        parser.error("Provide at least one symbol or --file")

    snapshot_date = (
        date.fromisoformat(args.date) if args.date else date.today()
    )

    logger.info("=" * 55)
    logger.info("Option Chain Snapshot")
    logger.info("  Date     : %s", snapshot_date)
    logger.info("  Symbols  : %d  (%s)", len(ordered), ", ".join(ordered[:5]) + (" ..." if len(ordered) > 5 else ""))
    logger.info("  Expiries : %s", args.expiry or f"nearest {args.expiries}")
    logger.info("  Dry-run  : %s", args.dry_run)
    logger.info("=" * 55)

    result = run_batch(
        symbols=ordered,
        snapshot_date=snapshot_date,
        num_expiries=args.expiries,
        forced_expiry=args.expiry,
        dry_run=args.dry_run,
    )

    elapsed = result["elapsed_secs"]
    logger.info("=" * 55)
    logger.info("Done in %dm %ds.", int(elapsed // 60), int(elapsed % 60))
    logger.info("  Total rows parsed : %d", result["total_rows"])
    logger.info("  Rows saved to DB  : %d", result["saved_rows"])
    if result["failed_symbols"]:
        logger.warning("  Failed symbols    : %s", ", ".join(result["failed_symbols"]))
        sys.exit(1)


if __name__ == "__main__":
    main()
