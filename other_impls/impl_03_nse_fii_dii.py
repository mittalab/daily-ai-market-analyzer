"""
NSE FII/DII Data Fetcher
==========================
Source  : www.nseindia.com/api/fiidiiTradeReact
Auth    : NSE session cookies required (homepage warm-up — see below)
Latency : Updated ~6:00 PM IST on trading days
Covers  : FII and DII net buy/sell for the most recent trading day

CONFIRMED WORKING: 2026-05-22
  DII net: +₹6,003 Cr (net buyer)
  FII net: -₹4,440 Cr (net seller)

EXACT FIELD NAMES returned by the API (list of 2 dicts):
  category   — "FII" or "DII"
  date       — "22-May-2026"  (string, DD-Mon-YYYY format)
  buyValue   — gross buy value in ₹ Crores  (numeric float)
  sellValue  — gross sell value in ₹ Crores (numeric float)
  netValue   — net = buy - sell in ₹ Crores (numeric float, negative = net seller)

  NOTE: The field is "netValue" — NOT "netPurchasesSales" (that key does not exist).

WHY A SESSION IS REQUIRED:
  www.nseindia.com is protected by Akamai bot management.
  The API endpoint returns 401/403 or empty JSON if called directly
  without the cookies set by the homepage. The required cookies are:
    nsit    — NSE session identifier
    _abck   — Akamai bot challenge cookie
    bm_sz   — Akamai bot management size cookie
  The homepage returns HTTP 403 but STILL sets these cookies — that is normal.

URLS THAT DO NOT WORK (404 or wrong data):
  /api/fiidii-live-oi      ← 404
  /api/fii-dii-data        ← 404
"""

import time
from datetime import date

import requests

# ── Constants ──────────────────────────────────────────────────────────────

API_URL = "https://www.nseindia.com/api/fiidiiTradeReact"

# Two sets of headers — browser headers for session warm-up, API headers for the call
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",   # No 'br' — Brotli causes garbled response
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Referer": "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
}


def create_nse_session() -> requests.Session:
    """
    Establish an NSE session by hitting the homepage to acquire Akamai cookies.

    The homepage returns HTTP 403 — this is expected and normal.
    The cookies (nsit, _abck, bm_sz) are set regardless and are what matter.
    The second request to a market-data page reinforces the session.

    Call this once per run — reuse the returned session for all NSE API calls.
    """
    session = requests.Session()
    session.headers.update(API_HEADERS)

    try:
        # Step 1: Homepage — sets Akamai cookies even on 403
        session.get(
            "https://www.nseindia.com",
            headers=BROWSER_HEADERS,
            timeout=15,
        )
        time.sleep(3)   # Akamai challenge window — do not reduce below 2s

        # Step 2: Warm up with a market-data page for a stronger session signal
        session.get(
            "https://www.nseindia.com/market-data/live-equity-market",
            headers=BROWSER_HEADERS,
            timeout=15,
        )
        time.sleep(2)
    except requests.RequestException:
        pass   # Proceed anyway — cookies may still be set

    return session


def fetch_fii_dii(session: requests.Session) -> dict:
    """
    Fetch FII and DII net buy/sell data for the most recent trading day.

    Returns a dict:
    {
        "date": "22-May-2026",
        "FII": {"buyValue": 45234.56, "sellValue": 49674.32, "netValue": -4439.76},
        "DII": {"buyValue": 52341.10, "sellValue": 46338.20, "netValue": 6002.90},
    }

    Raises ValueError if the API returns unexpected structure.
    Raises ConnectionError on network failure.
    """
    try:
        r = session.get(API_URL, timeout=20)
    except requests.RequestException as e:
        raise ConnectionError(f"Network error fetching FII/DII: {e}") from e

    if r.status_code != 200:
        raise ValueError(f"API returned HTTP {r.status_code}")

    try:
        data = r.json()
    except Exception:
        raise ValueError(f"Response is not JSON. First 200 chars: {r.text[:200]}")

    if not isinstance(data, list) or len(data) < 2:
        raise ValueError(f"Unexpected response structure: {data}")

    result = {"date": None, "FII": {}, "DII": {}}
    for record in data:
        cat = record.get("category", "").upper()
        if cat not in ("FII", "DII"):
            continue

        result["date"] = record.get("date")
        result[cat] = {
            "buyValue":  _to_float(record.get("buyValue")),
            "sellValue": _to_float(record.get("sellValue")),
            "netValue":  _to_float(record.get("netValue")),
        }

    return result


def _to_float(value) -> float | None:
    """Parse numeric value that may be int, float, or a comma-formatted string."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def interpret_flow(fii_dii: dict) -> str:
    """
    Classify the day's institutional flow as a market context signal.

    Returns one of: BULLISH / BEARISH / MIXED / NEUTRAL
    """
    fii_net = fii_dii.get("FII", {}).get("netValue") or 0
    dii_net = fii_dii.get("DII", {}).get("netValue") or 0
    combined = fii_net + dii_net

    if fii_net > 0 and dii_net > 0:
        return "BULLISH"       # Both buying
    if fii_net < 0 and dii_net < 0:
        return "BEARISH"       # Both selling
    if combined > 500:
        return "BULLISH"       # Net positive despite one side selling
    if combined < -500:
        return "BEARISH"
    return "MIXED"


# ── Example usage ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Establishing NSE session ...")
    session = create_nse_session()
    print(f"Cookies acquired: {list(session.cookies.keys())}")

    print("\nFetching FII/DII data ...")
    data = fetch_fii_dii(session)

    print(f"\nDate     : {data['date']}")
    print(f"FII Buy  : ₹{data['FII'].get('buyValue'):,.2f} Cr")
    print(f"FII Sell : ₹{data['FII'].get('sellValue'):,.2f} Cr")
    print(f"FII Net  : ₹{data['FII'].get('netValue'):+,.2f} Cr")
    print()
    print(f"DII Buy  : ₹{data['DII'].get('buyValue'):,.2f} Cr")
    print(f"DII Sell : ₹{data['DII'].get('sellValue'):,.2f} Cr")
    print(f"DII Net  : ₹{data['DII'].get('netValue'):+,.2f} Cr")
    print()
    print(f"Flow signal: {interpret_flow(data)}")
