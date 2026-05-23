"""
NSE Option Chain IV Fetcher — 3:25 PM Snapshot
================================================
Source   : www.nseindia.com  (NSE live option chain API)
Auth     : NSE session cookies required (same warm-up as impl_03)
Timing   : Market hours ONLY — 9:15 AM to 3:30 PM IST
           Run snapshot at 3:25 PM IST for end-of-day IV capture
           Returns empty/stale data outside market hours

CONFIRMED WORKING: 2026-05-23 (during market hours)
  URL (indices) : https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY
  URL (stocks)  : https://www.nseindia.com/api/option-chain-equities?symbol=RELIANCE

EXACT RESPONSE STRUCTURE:
  Top level keys: filtered, records

  records:
    underlyingValue  — float   current spot price of the underlying
    expiryDates      — list    all available expiry dates as strings ("26-Jun-2026")
    data             — list    one entry per strike (contains both CE and PE)

  Each entry in records.data:
    strikePrice      — float   the strike (e.g. 23700.0)
    expiryDate       — string  e.g. "26-May-2026"
    CE               — dict    call option data  (may be absent for far OTM puts)
    PE               — dict    put option data   (may be absent for far OTM calls)

  Each CE / PE dict contains:
    impliedVolatility      — float   IV in % (e.g. 12.45 means 12.45% annualised)  ← USE THIS
    openInterest           — int     current OI in lots
    changeinOpenInterest   — int     OI change from previous day
    pchangeinOpenInterest  — float   OI change %
    totalTradedVolume      — int     volume in lots
    lastPrice              — float   LTP of this option
    change                 — float   price change from previous close
    pChange                — float   price change %
    totalBuyQuantity       — int     pending buy orders
    totalSellQuantity      — int     pending sell orders
    bidQty                 — int     best bid quantity
    bidprice               — float   best bid price
    askQty                 — int     best ask quantity
    askPrice               — float   best ask price
    underlyingValue        — float   spot price (repeated inside CE/PE dict)
    strikePrice            — float   strike (repeated inside CE/PE dict)
    expiryDate             — string  expiry (repeated inside CE/PE dict)

EXACT IV FIELD NAME: "impliedVolatility"  (camelCase, no spaces)
IV is annualised, in percent. e.g. 18.5 = 18.5% annualised volatility.

SYMBOLS:
  Indices: "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"
  Stocks : exact NSE ticker, e.g. "RELIANCE", "TCS", "HDFCBANK"
"""

import time
from datetime import date, datetime

import pandas as pd
import requests

# ── Session setup (identical to impl_03) ──────────────────────────────────

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
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
    Establish NSE session. Homepage returns 403 — that is expected and normal.
    Cookies (nsit, _abck, bm_sz) are set regardless.
    """
    session = requests.Session()
    session.headers.update(API_HEADERS)
    try:
        session.get("https://www.nseindia.com", headers=BROWSER_HEADERS, timeout=15)
        time.sleep(3)
        session.get(
            "https://www.nseindia.com/market-data/equity-derivatives-watch",
            headers=BROWSER_HEADERS,
            timeout=15,
        )
        time.sleep(2)
    except requests.RequestException:
        pass
    return session


# ── Option chain fetch ─────────────────────────────────────────────────────

def fetch_option_chain(session: requests.Session, symbol: str) -> dict:
    """
    Fetch the full option chain for a symbol.

    Args:
        symbol : "NIFTY", "BANKNIFTY" for indices
                 "RELIANCE", "TCS", etc. for stocks

    Returns the raw API response dict with keys: filtered, records
    Raises ValueError if market is closed or response is empty.
    """
    # Indices and stocks use different endpoints
    index_symbols = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}
    if symbol.upper() in index_symbols:
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    else:
        url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"

    try:
        r = session.get(url, timeout=20)
    except requests.RequestException as e:
        raise ConnectionError(f"Network error: {e}") from e

    if r.status_code != 200:
        raise ValueError(f"HTTP {r.status_code} for {symbol} option chain")

    try:
        data = r.json()
    except Exception:
        raise ValueError(f"Response not JSON. First 200 chars: {r.text[:200]}")

    records = data.get("records", {})
    chain   = records.get("data", [])

    if not chain:
        raise ValueError(
            f"Option chain for {symbol} is empty. "
            f"Market may be closed (run 9:15–3:30 IST on trading days)."
        )

    return data


def parse_iv_snapshot(data: dict, expiry_filter: str | None = None) -> pd.DataFrame:
    """
    Parse the option chain response into a flat DataFrame of IV per strike.

    Args:
        data          : raw dict from fetch_option_chain()
        expiry_filter : filter to a specific expiry string e.g. "26-May-2026"
                        Pass None to get all expiries (large DataFrame)

    Returns DataFrame with columns:
        expiry, strike, ce_iv, pe_iv, ce_oi, pe_oi,
        ce_ltp, pe_ltp, ce_volume, pe_volume, underlying_spot
    """
    records = data.get("records", {})
    chain   = records.get("data", [])
    spot    = records.get("underlyingValue", None)

    rows = []
    for entry in chain:
        expiry = entry.get("expiryDate", "")
        if expiry_filter and expiry != expiry_filter:
            continue

        strike = entry.get("strikePrice")
        ce     = entry.get("CE", {})
        pe     = entry.get("PE", {})

        rows.append({
            "expiry":          expiry,
            "strike":          strike,
            "ce_iv":           ce.get("impliedVolatility"),   # exact field name
            "pe_iv":           pe.get("impliedVolatility"),
            "ce_oi":           ce.get("openInterest"),
            "pe_oi":           pe.get("openInterest"),
            "ce_oi_change":    ce.get("changeinOpenInterest"),
            "pe_oi_change":    pe.get("changeinOpenInterest"),
            "ce_ltp":          ce.get("lastPrice"),
            "pe_ltp":          pe.get("lastPrice"),
            "ce_volume":       ce.get("totalTradedVolume"),
            "pe_volume":       pe.get("totalTradedVolume"),
            "underlying_spot": spot,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("strike").reset_index(drop=True)
    return df


def get_atm_iv(df: pd.DataFrame) -> dict:
    """
    Find the ATM (at-the-money) strike and return its CE + PE IV.

    ATM is the strike closest to the current spot price.
    Average of CE IV and PE IV at ATM gives the 'ATM IV' used for
    market sentiment gauge and to seed Black-Scholes for other strikes.

    Returns:
    {
        "atm_strike": 23700.0,
        "spot":       23744.0,
        "atm_ce_iv":  14.35,    # in percent, annualised
        "atm_pe_iv":  14.12,
        "atm_iv_avg": 14.24,    # average — use this as the single IV figure
    }
    """
    if df.empty or "underlying_spot" not in df.columns:
        return {}

    spot = df["underlying_spot"].iloc[0]
    if not spot:
        return {}

    # Strike closest to spot
    df = df.copy()
    df["dist"] = (df["strike"] - spot).abs()
    atm_row = df.loc[df["dist"].idxmin()]

    ce_iv = atm_row.get("ce_iv")
    pe_iv = atm_row.get("pe_iv")
    avg   = (
        (ce_iv + pe_iv) / 2
        if ce_iv is not None and pe_iv is not None
        else ce_iv or pe_iv
    )

    return {
        "atm_strike": atm_row["strike"],
        "spot":       spot,
        "atm_ce_iv":  ce_iv,
        "atm_pe_iv":  pe_iv,
        "atm_iv_avg": round(avg, 2) if avg else None,
    }


def get_pcr(df: pd.DataFrame) -> float | None:
    """
    Compute Put-Call Ratio (PCR) by OI across all strikes for one expiry.
    PCR > 1.2 = bearish hedging dominant (contrarian bullish signal)
    PCR < 0.8 = call writing dominant (contrarian bearish signal)
    """
    total_ce_oi = df["ce_oi"].sum()
    total_pe_oi = df["pe_oi"].sum()
    if not total_ce_oi:
        return None
    return round(total_pe_oi / total_ce_oi, 3)


# ── Example usage ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Creating NSE session ...")
    session = create_nse_session()

    print("\nFetching NIFTY option chain ...")
    try:
        data = fetch_option_chain(session, "NIFTY")

        records      = data["records"]
        expiry_dates = records.get("expiryDates", [])
        spot         = records.get("underlyingValue")
        print(f"  Spot          : {spot}")
        print(f"  Expiry dates  : {expiry_dates[:5]}")

        # Parse nearest expiry
        nearest = expiry_dates[0] if expiry_dates else None
        df = parse_iv_snapshot(data, expiry_filter=nearest)
        print(f"\n  Strikes parsed: {len(df)}")
        print(f"  Expiry filter : {nearest}")

        # ATM IV
        atm = get_atm_iv(df)
        print(f"\n  ATM Strike    : {atm.get('atm_strike')}")
        print(f"  ATM CE IV     : {atm.get('atm_ce_iv')} %")
        print(f"  ATM PE IV     : {atm.get('atm_pe_iv')} %")
        print(f"  ATM IV (avg)  : {atm.get('atm_iv_avg')} %")

        # PCR
        pcr = get_pcr(df)
        print(f"  PCR (by OI)   : {pcr}")

        # Sample of chain around ATM
        spot_val = atm.get("spot", 0)
        near = df[(df["strike"] >= spot_val - 300) & (df["strike"] <= spot_val + 300)]
        cols = ["strike", "ce_iv", "ce_oi", "ce_ltp", "pe_iv", "pe_oi", "pe_ltp"]
        print(f"\n  Chain around ATM (±300 points):")
        print(near[cols].to_string(index=False))

    except ValueError as e:
        print(f"\n  {e}")
        print("  Re-run on a weekday between 9:15 AM and 3:30 PM IST.")
