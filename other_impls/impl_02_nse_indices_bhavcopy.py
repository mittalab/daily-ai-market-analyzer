"""
NSE Indices Bhavcopy Downloader
=================================
Source  : nsearchives.nseindia.com (open — no login or cookies required)
File    : ind_close_all_{DDMMYYYY}.csv  (plain CSV, ~15 KB)
Latency : Available by ~6:30 PM IST on trading days
Covers  : 147 indices including all sector indices + India VIX

CONFIRMED WORKING: 2026-05-22
URL PATTERN THAT WORKS:
  https://nsearchives.nseindia.com/content/indices/ind_close_all_{DDMMYYYY}.csv

CONFIRMED INDICES PRESENT (from live test):
  Broad market : NIFTY 50, NIFTY NEXT 50, NIFTY 100, NIFTY 500, NIFTY MIDCAP 50
  Sector       : NIFTY BANK, NIFTY IT, NIFTY FMCG, NIFTY AUTO, NIFTY PHARMA,
                 NIFTY METAL, NIFTY REALTY, NIFTY MEDIA, NIFTY ENERGY,
                 NIFTY FINANCIAL SERVICES, NIFTY CONSUMER DURABLES
  Volatility   : India VIX  ← exact field name in "Index Name" column
  Strategy     : NIFTY ALPHA 50, NIFTY QUALITY 30, NIFTY LOW VOLATILITY 50

EXACT COLUMN NAMES (as they appear in the CSV header):
  Index Name              — index identifier string
  Closing Index Value     — closing price / level
  Opening Index Value     — opening level
  High Index Value        — intraday high
  Low Index Value         — intraday low
  Change                  — absolute change from previous close
  % Chg                   — percentage change from previous close
  52 Week High            — 52-week high level
  52 Week Low             — 52-week low level
  365 d % Chg             — 1-year percentage change
  30 d % Chg              — 1-month percentage change

NOTE: India VIX has all the same columns as other indices.
      "Closing Index Value" for India VIX = VIX value (e.g. 13.45).
      VIX does not have meaningful OHLC — only the closing value is used.
"""

import time
from datetime import date, timedelta
from io import StringIO

import pandas as pd
import requests

# ── Constants ──────────────────────────────────────────────────────────────

BASE_URL = (
    "https://nsearchives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

# Sector indices used for market breadth / regime detection
SECTOR_INDICES = [
    "NIFTY BANK",
    "NIFTY IT",
    "NIFTY FMCG",
    "NIFTY AUTO",
    "NIFTY PHARMA",
    "NIFTY METAL",
    "NIFTY REALTY",
    "NIFTY MEDIA",
]

BROAD_INDICES = [
    "NIFTY 50",
    "NIFTY NEXT 50",
    "NIFTY MIDCAP 50",
    "NIFTY SMALLCAP 100",
]


def last_trading_day(ref: date | None = None, max_lookback: int = 7) -> date:
    """Return the most recent weekday on or before ref (defaults to today)."""
    dt = ref or date.today()
    for _ in range(max_lookback):
        if dt.weekday() < 5:
            return dt
        dt -= timedelta(days=1)
    raise ValueError("Could not find a trading day within lookback window")


def download_indices(dt: date | None = None, retries: int = 2) -> pd.DataFrame:
    """
    Download and parse NSE indices bhavcopy for the given trading date.

    Returns a DataFrame with columns:
      Index Name, Closing Index Value, Opening Index Value, High Index Value,
      Low Index Value, Change, % Chg, 52 Week High, 52 Week Low,
      365 d % Chg, 30 d % Chg

    Index Name is set as the DataFrame index for convenient lookup.
    """
    target = last_trading_day(dt)

    for attempt in range(retries + 1):
        url = BASE_URL.format(ddmmyyyy=target.strftime("%d%m%Y"))
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            raise ConnectionError(f"Network error: {e}") from e

        if r.status_code == 200 and len(r.content) > 500:
            df = pd.read_csv(StringIO(r.text))
            df.columns = df.columns.str.strip()
            df["Index Name"] = df["Index Name"].str.strip()
            df = df.set_index("Index Name")
            return df

        if r.status_code == 404 and attempt < retries:
            target = last_trading_day(target - timedelta(days=1))
            time.sleep(2)
            continue

        raise FileNotFoundError(
            f"Indices file not found for {target.isoformat()} — HTTP {r.status_code}"
        )

    raise FileNotFoundError("Indices file unavailable after all retries")


def get_india_vix(df: pd.DataFrame) -> float | None:
    """
    Extract India VIX closing value from the indices DataFrame.
    Returns None if VIX is not in the file (should not happen on trading days).

    Usage in market regime logic:
      vix < 15  → low fear, trend-following setups preferred
      vix 15–20 → moderate, normal swing trading
      vix > 20  → elevated fear, tighten stop-losses or reduce size
      vix > 25  → high fear, consider staying in cash
    """
    try:
        return float(df.loc["India VIX", "Closing Index Value"])
    except KeyError:
        return None


def get_sector_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    """
    Return a filtered DataFrame of sector indices with close + % change.
    Useful for identifying sector rotation and relative strength.
    """
    available = [idx for idx in SECTOR_INDICES if idx in df.index]
    cols = ["Closing Index Value", "% Chg", "30 d % Chg"]
    return df.loc[available, cols].copy()


def get_broad_market_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    """Return broad market indices: NIFTY 50, NIFTY MID, NIFTY SMALL."""
    available = [idx for idx in BROAD_INDICES if idx in df.index]
    cols = ["Closing Index Value", "% Chg", "365 d % Chg"]
    return df.loc[available, cols].copy()


# ── Example usage ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    dt = last_trading_day()
    print(f"Downloading indices for {dt.isoformat()} ...")

    df = download_indices(dt)
    print(f"Total indices : {len(df)}")
    print(f"Columns       : {list(df.columns)}")

    # India VIX
    vix = get_india_vix(df)
    print(f"\nIndia VIX     : {vix}")
    if vix:
        regime = (
            "LOW FEAR"    if vix < 15 else
            "MODERATE"    if vix < 20 else
            "ELEVATED"    if vix < 25 else
            "HIGH FEAR"
        )
        print(f"VIX Regime    : {regime}")

    # Sector snapshot
    print("\nSector indices:")
    print(get_sector_snapshot(df).to_string())

    # Broad market
    print("\nBroad market:")
    print(get_broad_market_snapshot(df).to_string())
