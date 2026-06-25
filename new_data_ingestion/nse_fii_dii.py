"""
NSE FII/DII data fetcher — requires Akamai session cookies.

Session is shared with nse_option_chain (one warm-up per pipeline run).
Critical field: netValue (NOT netPurchasesSales — that key does not exist).
Values are already in Crores — do NOT divide.
"""
import logging
import time
from datetime import date
import random

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://www.nseindia.com/api/fiidiiTradeReact"

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

def create_nse_session() -> requests.Session:
    """
    Establish NSE Akamai session with a high-fidelity browser profile.
    Uses a specific sequence to bypass modern 'empty response' anti-bot logic.
    """
    session = requests.Session()
    ua = random.choice(_USER_AGENTS)
    
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Cache-Control": "max-age=0",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    session.headers.update(headers)

    try:
        session.get("https://www.nseindia.com", timeout=15)
        time.sleep(5)
        session.get("https://www.nseindia.com/market-data/option-chain", timeout=15)
        time.sleep(3)
        logger.debug("NSE session baked. Cookies: %s", list(session.cookies.keys()))
    except requests.RequestException as exc:
        logger.warning("NSE session setup failed: %s", exc)

    return session


def fetch_fii_dii(session: requests.Session) -> dict:
    """
    Fetch FII and DII net buy/sell for the most recent trading day.
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
            "netValue":  _to_float(record.get("netValue")),
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

def main():
    session = create_nse_session()
    print(fetch_fii_dii(session))


if __name__ == "__main__":
    main()