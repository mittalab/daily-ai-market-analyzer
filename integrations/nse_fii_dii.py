"""
NSE FII/DII data fetcher — requires Akamai session cookies.

Session is shared with nse_option_chain (one warm-up per pipeline run).
Critical field: netValue (NOT netPurchasesSales — that key does not exist).
Values are already in Crores — do NOT divide.
"""
import logging
import time
from datetime import date

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://www.nseindia.com/api/fiidiiTradeReact"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language":           "en-US,en;q=0.9",
    "Accept-Encoding":           "gzip, deflate",   # NOT br
    "Connection":                "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

_API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection":      "keep-alive",
    "Referer":         "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
}


def create_nse_session() -> requests.Session:
    """
    Establish NSE Akamai session.

    Homepage returns HTTP 403 — that is expected and normal.
    Cookies (nsit, _abck, bm_sz) are set regardless.
    Reuse this session for ALL NSE website calls (FII/DII + option chain).
    """
    session = requests.Session()
    session.headers.update(_API_HEADERS)

    try:
        # Step 1: homepage — 403 is normal, cookies still set
        session.get("https://www.nseindia.com", headers=_BROWSER_HEADERS, timeout=15)
        logger.debug("NSE homepage hit (403 is normal)")
        time.sleep(3)   # Akamai challenge window — spec says do not reduce below 2s

        # Step 2: warm-up with a market-data page
        session.get(
            "https://www.nseindia.com/market-data/live-equity-market",
            headers=_BROWSER_HEADERS,
            timeout=15,
        )
        time.sleep(2)
        logger.debug("NSE session warmed up. Cookies: %s", list(session.cookies.keys()))
    except requests.RequestException:
        logger.warning("NSE session warm-up had network error — proceeding anyway")

    return session


def fetch_fii_dii(session: requests.Session) -> dict:
    """
    Fetch FII and DII net buy/sell for the most recent trading day.

    Returns:
    {
        "date": "22-May-2026",
        "FII": {"buyValue": 45234.56, "sellValue": 49674.32, "netValue": -4439.76},
        "DII": {"buyValue": 52341.10, "sellValue": 46338.20, "netValue": 6002.90},
    }

    Raises ConnectionError on network failure.
    Raises ValueError on unexpected API structure.
    """
    try:
        r = session.get(_API_URL, timeout=20)
    except requests.RequestException as exc:
        raise ConnectionError(f"Network error fetching FII/DII: {exc}") from exc

    if r.status_code != 200:
        raise ValueError(f"FII/DII API returned HTTP {r.status_code}")

    try:
        data = r.json()
    except Exception:
        raise ValueError(f"FII/DII response not JSON. First 200 chars: {r.text[:200]}")

    if not isinstance(data, list) or len(data) < 2:
        raise ValueError(f"Unexpected FII/DII response structure: {data}")

    result: dict = {"date": None, "FII": {}, "DII": {}}
    for record in data:
        cat = record.get("category", "").upper().replace("/FPI", "").strip()
        if cat not in ("FII", "DII"):
            continue
        result["date"] = record.get("date")
        result[cat] = {
            "buyValue":  _to_float(record.get("buyValue")),
            "sellValue": _to_float(record.get("sellValue")),
            "netValue":  _to_float(record.get("netValue")),   # PRIMARY — not netPurchasesSales
        }

    logger.info(
        "FII/DII fetched for %s — FII net: %s Cr, DII net: %s Cr",
        result.get("date"),
        result.get("FII", {}).get("netValue"),
        result.get("DII", {}).get("netValue"),
    )
    return result


def fii_dii_to_db_row(data: dict, trade_date: date) -> dict:
    """Convert fetch_fii_dii() result to a fii_dii_flows upsert row."""
    fii = data.get("FII", {})
    dii = data.get("DII", {})
    return {
        "date":        str(trade_date),
        "fii_buy_cr":  fii.get("buyValue"),
        "fii_sell_cr": fii.get("sellValue"),
        "fii_net_cr":  fii.get("netValue"),
        "dii_buy_cr":  dii.get("buyValue"),
        "dii_sell_cr": dii.get("sellValue"),
        "dii_net_cr":  dii.get("netValue"),
        "source":      "LIVE",
    }


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None
