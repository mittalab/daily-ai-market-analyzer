"""
NSE Option Chain — 3:20 PM IV snapshot (Unified v3 API).

Uses browser emulation to bypass Akamai:
  1. Hitting homepage + landing page (sets cookies).
  2. Calling /api/option-chain-contract-info to resolve near/next month expiries.
  3. Querying /api/option-chain-v3?type={type}&symbol={symbol}&expiry={expiry} for each expiry.
"""
import logging
import time
import random
from datetime import date, datetime

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.nseindia.com"

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
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}


def _chain_type(symbol: str) -> str:
    """Return 'Indices' for index symbols, 'Equity' for stocks (singular)."""
    return "Indices" if symbol.upper() in _INDEX_SYMBOLS else "Equity"


def make_nse_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    return s


def warm_up_session(session: requests.Session, symbol: str, chain_type: str) -> None:
    """Visit homepage + option-chain page to bypass Akamai WAF."""
    logger.debug("Warming up NSE session for %s...", symbol)
    session.get(BASE_URL, timeout=15)
    time.sleep(random.uniform(1.5, 2.5))

    if chain_type == "Indices":
        page_url = f"{BASE_URL}/option-chain?symbol={symbol.upper()}"
    else:
        page_url = f"{BASE_URL}/get-quotes/derivatives?symbol={symbol.upper()}"

    session.headers.update({"Referer": BASE_URL + "/"})
    session.get(page_url, timeout=15)
    time.sleep(random.uniform(1.5, 2.5))
    session.headers.update({"Referer": page_url})


def fetch_contract_info(session: requests.Session, symbol: str) -> dict:
    """Get active contract expiries and strike info."""
    url = f"{BASE_URL}/api/option-chain-contract-info?symbol={symbol.upper()}"
    r = session.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_option_chain_v3(
    session: requests.Session,
    symbol: str,
    chain_type: str,
    expiry: str,
) -> dict:
    """Query the unified option-chain-v3 API for a single expiry."""
    url = f"{BASE_URL}/api/option-chain-v3?type={chain_type}&symbol={symbol.upper()}&expiry={expiry}"
    logger.info("GET %s", url)
    r = session.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def parse_snapshot_for_db(
    symbol: str,
    snapshot_date: date,
    expiry_str: str,
    data: dict,
) -> list[dict]:
    """Parse raw v3 API response data into options_snapshots table rows."""
    records = data.get("records", {})
    chain = records.get("data", [])
    if not chain and "data" in data:
        # handle case where raw json has data key at root
        chain = data.get("data", [])

    logger.debug("%s (%s): chain has %d entries; top-level keys=%s", symbol, expiry_str, len(chain), list(data.keys()))

    rows = []

    # Parse expiry format (e.g. '30-Jun-2026' -> YYYY-MM-DD)
    try:
        expiry_date = datetime.strptime(expiry_str.strip(), "%d-%b-%Y").date()
    except ValueError:
        logger.warning("Could not parse expiry date string: %s", expiry_str)
        return []

    # The API is queried per-expiry so entries may not carry an expiryDate field.
    # Only apply the guard when the field is actually present in the response.
    has_expiry_field = any("expiryDate" in e for e in chain[:5])

    expiry_matched = 0
    iv_filtered = 0
    for entry in chain:
        if has_expiry_field and entry.get("expiryDate") != expiry_str:
            continue
        expiry_matched += 1

        strike = entry.get("strikePrice")
        if strike is None:
            continue

        for opt_type, side_key in (("CE", "CE"), ("PE", "PE")):
            side = entry.get(side_key, {})
            if not side:
                continue

            iv = side.get("impliedVolatility")
            if iv is None or float(iv) <= 0:
                iv_filtered += 1
                continue  # Filter out deep OTM zeroes

            final_symbol = symbol.replace('%26', '&')
            rows.append({
                "symbol":        final_symbol.upper(),
                "snapshot_date": str(snapshot_date),
                "expiry_date":   str(expiry_date),
                "strike":        float(strike),
                "option_type":   opt_type,
                "oi":            int(side.get("openInterest") or 0),
                "oi_change":     int(side.get("changeinOpenInterest") or 0),
                "volume":        int(side.get("totalTradedVolume") or 0),
                "iv":            float(iv),
                "premium_close": float(side.get("lastPrice") or 0),
            })

    logger.debug(
        "%s (%s): %d chain entries, has_expiry_field=%s, expiry_matched=%d, rows=%d, iv_filtered=%d",
        symbol, expiry_str, len(chain), has_expiry_field, expiry_matched, len(rows), iv_filtered,
    )
    if not rows:
        all_expiries = sorted({e.get("expiryDate") for e in chain})
        logger.warning(
            "%s (%s): 0 rows — chain=%d, has_expiry_field=%s, expiry_matched=%d, iv_filtered=%d, all expiryDates in response=%s",
            symbol, expiry_str, len(chain), has_expiry_field, expiry_matched, iv_filtered, all_expiries,
        )

    return rows


def fetch_option_chain_symbol(
    session: requests.Session,
    symbol: str,
    snapshot_date: date,
) -> list[dict]:
    """
    Scrape and parse near and next month options data for a single symbol.
    """
    symbol_up = symbol.upper()
    
    # NIFTY_50 symbol is used in DB, but NSE expects NIFTY
    nse_symbol = "NIFTY" if symbol_up == "NIFTY_50" else symbol_up
    chain_type = _chain_type(nse_symbol)

    warm_up_session(session, nse_symbol, chain_type)

    contract_info = fetch_contract_info(session, nse_symbol)
    expiries = contract_info.get("expiryDates", [])
    if not expiries:
        raise ValueError(f"No expiries returned for {nse_symbol}")

    # Keep near and next month expiries
    target_expiries = expiries[:2]
    logger.info("%s: fetching expiries %s", symbol_up, target_expiries)

    parsed_rows = []
    for expiry in target_expiries:
        try:
            logger.debug("Querying v3 chain for %s (%s)...", symbol_up, expiry)
            data = fetch_option_chain_v3(session, nse_symbol, chain_type, expiry)
            
            # Map symbol back to DB symbol (NIFTY_50)
            rows = parse_snapshot_for_db(symbol_up, snapshot_date, expiry, data)
            parsed_rows.extend(rows)
            
            time.sleep(random.uniform(1.0, 2.0))
        except Exception as exc:
            logger.warning("Failed to fetch v3 chain for %s (%s): %s", symbol_up, expiry, exc)

    return parsed_rows


def run_snapshot_batch(
    session: requests.Session,
    symbols: list[str],
    snapshot_date: date,
    sleep_secs: float = 2.0,
) -> tuple[list[dict], list[str]]:
    """
    Fetch and parse options data for all symbols.
    """
    all_rows = []
    failed_symbols = []

    for sym in symbols:
        try:
            symbol = sym.replace('&', '%26')
            rows = fetch_option_chain_symbol(session, symbol, snapshot_date)
            if not rows:
                logger.warning("%s: parsed 0 option rows", symbol)
                failed_symbols.append(symbol)
            else:
                print(rows)
                all_rows.extend(rows)
                logger.info("%s: %d option rows parsed", symbol, len(rows))
        except Exception as exc:
            logger.warning("Option chain scraper failed for %s: %s", sym, exc)
            failed_symbols.append(sym)

        time.sleep(sleep_secs)

    return all_rows, failed_symbols
