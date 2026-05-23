"""
NSE Equity Bhavcopy Downloader
================================
Source  : nsearchives.nseindia.com (open — no login or cookies required)
File    : sec_bhavdata_full_{DDMMYYYY}.csv  (plain CSV, not zipped)
Latency : Available by ~6:30 PM IST on trading days
Covers  : All NSE-listed securities (EQ, BE, SM, MF, etc.)

CONFIRMED WORKING: 2026-05-22
URL PATTERN THAT WORKS:
  https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv

PATTERNS THAT DO NOT WORK (404):
  /content/historical/EQUITIES/{year}/{mon}/cm{DD}{MON}{YYYY}bhav.csv.zip  ← old ZIP
  /products/content/cm{DD}{MON}{YYYY}bhav.csv.zip                          ← old ZIP
  (NSE removed all ZIP-based equity bhavcopy URLs)
"""

import time
from datetime import date, timedelta
from io import StringIO

import pandas as pd
import requests

# ── Constants ──────────────────────────────────────────────────────────────

BASE_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"

# Minimal headers — archives subdomain does not enforce bot checks
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",  # No 'br' — avoids Brotli decode issues
    "Connection": "keep-alive",
}

# Fields available in the file (confirmed from live data):
# SYMBOL        — NSE ticker (e.g. RELIANCE, TCS)
# SERIES        — Market segment: EQ, BE, SM, MF, GS, etc.
# OPEN          — Opening price
# HIGH          — Day high
# LOW           — Day low
# CLOSE         — Closing price (use this — volume-weighted)
# LAST          — Last traded price (can differ from close)
# PREVCLOSE     — Previous day's close
# TOTTRDQTY     — Total traded quantity (volume in shares)
# TOTTRDVAL     — Total traded value in Rupees (NOT Cr — raw ₹)
# TIMESTAMP     — Trading date (DD-MMM-YYYY format)
# TOTALTRADES   — Number of trades executed
# ISIN          — ISIN code
# DELIV_QTY     — Delivery quantity (shares)
# DELIV_PER     — Delivery % of total traded qty


def last_trading_day(ref: date | None = None, max_lookback: int = 7) -> date:
    """Return the most recent weekday on or before ref (defaults to today)."""
    dt = ref or date.today()
    for _ in range(max_lookback):
        if dt.weekday() < 5:   # 0=Mon … 4=Fri
            return dt
        dt -= timedelta(days=1)
    raise ValueError("Could not find a trading day within lookback window")


def build_url(dt: date) -> str:
    return BASE_URL.format(ddmmyyyy=dt.strftime("%d%m%Y"))


def download_bhavcopy(dt: date | None = None, retries: int = 2) -> pd.DataFrame:
    """
    Download and parse NSE equity bhavcopy for the given trading date.
    Falls back to the previous trading day if the file is not yet published.

    Returns a DataFrame with all columns from the file.
    Raises FileNotFoundError if no file is found after retries.
    """
    target = last_trading_day(dt)

    for attempt in range(retries + 1):
        url = build_url(target)
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            raise ConnectionError(f"Network error fetching bhavcopy: {e}") from e

        if r.status_code == 200 and len(r.content) > 1000:
            df = pd.read_csv(StringIO(r.text))
            df.columns = df.columns.str.strip()   # strip any whitespace from headers
            return df

        if r.status_code == 404 and attempt < retries:
            # File not yet published — try the previous trading day
            target = last_trading_day(target - timedelta(days=1))
            time.sleep(2)
            continue

        raise FileNotFoundError(
            f"Bhavcopy not found for {target.isoformat()} — "
            f"HTTP {r.status_code} at {url}"
        )

    raise FileNotFoundError("Bhavcopy unavailable after all retries")


def filter_fo_universe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply screening filters to extract liquid F&O-eligible stocks.
    Returns EQ series stocks passing all liquidity thresholds.

    Filters (confirmed working in test_04_universe_screening.py):
      - Series == EQ (excludes BE, SM, GS, etc.)
      - Close price ₹50 – ₹50,000
      - Total traded value >= ₹1 Cr/day  (TOTTRDVAL >= 10,000,000)
      - Volume >= 50,000 shares/day
      - Delivery % >= 25%
      - Number of trades >= 500
    """
    eq = df[df["SERIES"].str.strip() == "EQ"].copy()

    mask = (
        (eq["CLOSE"].between(50, 50_000)) &
        (eq["TOTTRDVAL"] >= 10_000_000) &
        (eq["TOTTRDQTY"] >= 50_000) &
        (eq["DELIV_PER"] >= 25) &
        (eq["TOTALTRADES"] >= 500)
    )
    return eq[mask].reset_index(drop=True)


# ── Example usage ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    dt = last_trading_day()
    print(f"Downloading bhavcopy for {dt.isoformat()} ...")

    df = download_bhavcopy(dt)
    print(f"Total rows      : {len(df):,}")
    print(f"Columns         : {list(df.columns)}")
    print(f"Series breakdown:\n{df['SERIES'].value_counts().head(8)}")

    universe = filter_fo_universe(df)
    print(f"\nF&O universe (after filters): {len(universe):,} stocks")
    print(universe[["SYMBOL", "CLOSE", "TOTTRDVAL", "TOTTRDQTY", "DELIV_PER"]].head(10).to_string(index=False))
