"""
NSE Option Chain — 3:25 PM IV snapshot.

Uses the same NSE session as nse_fii_dii (one warm-up per run).
Timing: run at 3:25 PM IST — 5 minutes BEFORE market close.
        NOT 3:30 PM — market closes and IV becomes stale.

Critical field name: impliedVolatility (camelCase) — NOT iv / IV.
Filter: impliedVolatility > 0 (deep OTM returns 0 or null — exclude).
Stock URL: /api/option-chain-equities?symbol={SYMBOL}
           NOT option-chain-indices (that's for NIFTY/BANKNIFTY).
"""
import logging
import time
import random
from datetime import date

import requests

logger = logging.getLogger(__name__)

_STOCK_CHAIN_URL = "https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"
_INDEX_CHAIN_URL = "https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"


_STOCK_CHAIN_URL = "https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"
_INDEX_CHAIN_URL = "https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"

_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    "Accept-Encoding": "gzip, deflate", # Removed 'br'
    "Connection": "keep-alive",
    "Referer": "https://www.nseindia.com/option-chain",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin"
}

def fetch_option_chain(session: requests.Session, symbol: str) -> dict:
    """
    Fetch full option chain for a symbol (Stock or Index).
    Uses 'shadow navigation' to sync Referer and Akamai state.
    """
    is_index = symbol.upper() in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")
    symbol_up = symbol.upper()

    if is_index:
        referer = f"https://www.nseindia.com/option-chain?symbol={symbol_up}"
        api_url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol_up}"
    else:
        referer = f"https://www.nseindia.com/get-quotes/derivatives?symbol={symbol_up}"
        api_url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol_up}"

    try:
        # FIX 3: Wait longer and ensure session headers are browser-like
        session.headers.update(_BROWSER_HEADERS)
        
        # Hit referer
        session.get(referer, timeout=15)
        time.sleep(random.uniform(2.0, 3.5))

        r = session.get(api_url, timeout=20)
        
        if r.status_code == 200:
            data = r.json()
            if data.get("records", {}).get("data"):
                return data

        raise ValueError(f"HTTP {r.status_code} for {symbol_up} — chain empty or blocked (size {len(r.content)})")

    except requests.RequestException as exc:
        raise ConnectionError(f"Network error fetching option chain for {symbol_up}: {exc}") from exc

    try:
        data = r.json()
    except Exception:
        raise ValueError(f"Option chain response not JSON for {symbol}. First 200: {r.text[:200]}")

    chain = data.get("records", {}).get("data", [])
    if not chain:
        raise ValueError(
            f"Option chain for {symbol} is empty — "
            "market may be closed or outside 9:15 AM – 3:30 PM IST"
        )

    return data


def parse_snapshot_for_db(
    symbol: str,
    snapshot_date: date,
    data: dict,
) -> list[dict]:
    """
    Parse raw option chain into rows ready for options_snapshots upsert.

    One row per (symbol, snapshot_date, expiry_date, strike, option_type).
    Filters: impliedVolatility > 0 only (spec rule — deep OTM excluded).
    Captures near month + next month expiries (far month excluded).

    Returns list of dicts matching options_snapshots schema.
    """
    records     = data.get("records", {})
    chain       = records.get("data", [])
    expiry_dates = records.get("expiryDates", [])

    # Keep near + next month only (first 2 expiries in list)
    relevant_expiries = set(expiry_dates[:2]) if expiry_dates else set()

    rows = []
    for entry in chain:
        expiry_str = entry.get("expiryDate", "")
        if expiry_str not in relevant_expiries:
            continue

        try:
            expiry_date = _parse_expiry_date(expiry_str)
        except ValueError:
            logger.warning("Could not parse expiry date '%s' for %s", expiry_str, symbol)
            continue

        strike = entry.get("strikePrice")
        if strike is None:
            continue

        for opt_type, side_key in (("CE", "CE"), ("PE", "PE")):
            side = entry.get(side_key, {})
            if not side:
                continue

            iv = side.get("impliedVolatility")   # exact camelCase field name
            if not iv or iv <= 0:
                continue                           # filter zeros — deep OTM

            rows.append({
                "symbol":        symbol,
                "snapshot_date": str(snapshot_date),
                "expiry_date":   str(expiry_date),
                "strike":        float(strike),
                "option_type":   opt_type,
                "oi":            side.get("openInterest"),
                "oi_change":     side.get("changeinOpenInterest"),
                "volume":        side.get("totalTradedVolume"),
                "iv":            float(iv),
                "premium_close": side.get("lastPrice"),
            })

    return rows


def run_snapshot_batch(
    session: requests.Session,
    symbols: list[str],
    snapshot_date: date,
    sleep_secs: float = 0.35,
) -> tuple[list[dict], list[str]]:
    """
    Fetch and parse option chain for all symbols.

    Returns (all_rows, failed_symbols).
    Calls sleep_secs between requests (~3 req/sec — same as Kite rate).
    """
    all_rows:      list[dict] = []
    failed_symbols: list[str] = []

    for symbol in symbols:
        try:
            data = fetch_option_chain(session, symbol)
            rows = parse_snapshot_for_db(symbol, snapshot_date, data)
            all_rows.extend(rows)
            logger.debug("%s: %d option rows parsed", symbol, len(rows))
        except Exception as exc:
            logger.warning("Option chain failed for %s: %s", symbol, exc)
            failed_symbols.append(symbol)

        time.sleep(sleep_secs)

    logger.info(
        "Snapshot batch complete: %d rows from %d/%d symbols",
        len(all_rows),
        len(symbols) - len(failed_symbols),
        len(symbols),
    )
    return all_rows, failed_symbols


def _parse_expiry_date(expiry_str: str) -> date:
    """Parse NSE expiry date string e.g. '26-May-2026' → date(2026, 5, 26)."""
    from datetime import datetime
    return datetime.strptime(expiry_str, "%d-%b-%Y").date()
