"""
NSE Bhavcopy — equity + sector indices (no auth required).

Combines impl_01 (equity) and impl_02 (indices) into one production module.

NOTE on equity URL: the spec lists /content/cm/sec_bhavdata_full_{DDMMYYYY}.csv
but the validated working URL (confirmed 2026-05-22) uses /products/content/.
We use the validated URL.
"""
import json
import logging
import time
from datetime import date, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from config.constants import (
    SYMBOL_NIFTY_50,
    SYMBOL_NIFTY_BANK,
    SYMBOL_NIFTY_IT,
    SYMBOL_NIFTY_AUTO,
    SYMBOL_NIFTY_PHARMA,
    SYMBOL_NIFTY_FMCG,
    SYMBOL_NIFTY_METAL,
    SYMBOL_NIFTY_ENERGY,
    SYMBOL_NIFTY_FIN_SERVICE,
    SYMBOL_INDIA_VIX,
)

logger = logging.getLogger(__name__)

# ── URLs ───────────────────────────────────────────────────────────────────────
# Equity: /products/content/ — validated working path (spec has /content/cm/ — typo)
_EQUITY_URL  = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
_INDICES_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",   # NOT br — Brotli causes garbled bytes
    "Connection":      "keep-alive",
}

# Indices whose closing values we track in price_history
# Names are EXACTLY as they appear in the NSE ind_close_all CSV (title case)
_TRACKED_INDICES = [
    "Nifty 50",
    "Nifty Bank",
    "Nifty IT",
    "Nifty Auto",
    "Nifty Pharma",
    "Nifty FMCG",
    "Nifty Metal",
    "Nifty Energy",
    "Nifty Financial Services",
    "Nifty India Consumption",
    "Nifty Infrastructure",
    "Nifty Media",
]

# Normalised symbol names stored in price_history (spaces → underscores)
_INDEX_SYMBOL_MAP = {
    "Nifty 50":                 SYMBOL_NIFTY_50,
    "Nifty Bank":               SYMBOL_NIFTY_BANK,
    "Nifty IT":                 SYMBOL_NIFTY_IT,
    "Nifty Auto":               SYMBOL_NIFTY_AUTO,
    "Nifty Pharma":             SYMBOL_NIFTY_PHARMA,
    "Nifty FMCG":               SYMBOL_NIFTY_FMCG,
    "Nifty Metal":              SYMBOL_NIFTY_METAL,
    "Nifty Energy":             SYMBOL_NIFTY_ENERGY,
    "Nifty Financial Services": SYMBOL_NIFTY_FIN_SERVICE,
    "Nifty India Consumption":  "NIFTY_CONSUMPTION",
    "Nifty Infrastructure":     "NIFTY_INFRA",
    "Nifty Media":              "NIFTY_MEDIA",
}

_SECTOR_MAP_PATH = Path(__file__).parent.parent / "config" / "sector_map.json"
_TMP_DIR = Path(__file__).parent.parent / "tmp"
_TMP_DIR.mkdir(parents=True, exist_ok=True)
_sector_map_cache: dict | None = None


def _load_sector_map() -> dict:
    global _sector_map_cache
    if _sector_map_cache is None:
        _sector_map_cache = json.loads(_SECTOR_MAP_PATH.read_text())
    return _sector_map_cache


def get_nifty50_symbols() -> set[str]:
    """Return the Nifty 50 symbol set from config/sector_map.json."""
    return set(_load_sector_map()["stocks"].keys())


def get_holiday_dates() -> set[date]:
    """Return the 2026 market holiday set from config/sector_map.json."""
    raw = _load_sector_map().get("holidays_2026", [])
    return {date.fromisoformat(d) for d in raw}


def last_trading_day(ref: date | None = None, max_lookback: int = 10) -> date:
    """Return the most recent trading day (weekday, not a holiday) on or before ref."""
    dt       = ref or date.today()
    holidays = get_holiday_dates()
    for _ in range(max_lookback):
        if dt.weekday() < 5 and dt not in holidays:
            return dt
        dt -= timedelta(days=1)
    raise ValueError("No trading day found within lookback window")


# ── Equity bhavcopy ────────────────────────────────────────────────────────────

def fetch_equity_bhavcopy(for_date: date | None = None) -> tuple[pd.DataFrame, date]:
    """
    Download NSE equity bhavcopy and return Nifty 50 EQ rows.

    Returns (df, trade_date) where df has:
      SYMBOL, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, DELIV_PER

    TOTTRDVAL is raw rupees (divide by 10,000,000 for Crores).
    DELIV_PER is already a percentage — do NOT divide.

    Raises FileNotFoundError if file is unavailable after retries.
    Raises ConnectionError on network failure.
    """
    target  = last_trading_day(for_date)

    for attempt in range(3):
        filename = f"sec_bhavdata_full_{target.strftime('%d%m%Y')}.csv"
        local_path = _TMP_DIR / filename

        if local_path.exists():
            logger.info("Found equity bhavcopy in tmp folder: %s", local_path)
        else:
            url = _EQUITY_URL.format(ddmmyyyy=target.strftime("%d%m%Y"))
            logger.info("Equity bhavcopy not found in tmp folder, downloading: %s", url)
            logger.debug("Equity bhavcopy attempt %d: %s", attempt + 1, url)

            try:
                r = requests.get(url, headers=_HEADERS, timeout=30)
            except requests.RequestException as exc:
                raise ConnectionError(f"Network error fetching equity bhavcopy: {exc}") from exc

            if r.status_code == 200 and len(r.content) > 1000:
                local_path.write_bytes(r.content)
                logger.info("Equity bhavcopy downloaded and saved to tmp: %s", local_path)
            elif r.status_code == 404 and attempt < 2:
                logger.warning("Bhavcopy 404 for %s — trying previous day", target)
                target = last_trading_day(target - timedelta(days=1))
                time.sleep(2)
                continue
            else:
                raise FileNotFoundError(
                    f"Equity bhavcopy unavailable for {target}: HTTP {r.status_code}"
                )

        df = pd.read_csv(local_path)
        df.columns = df.columns.str.strip()     # spec: always strip after read
        df = df[
            (df["SERIES"].str.strip() == "EQ")
        ].copy()
        logger.info("Equity bhavcopy processed: %d Nifty 50 rows for %s", len(df), target)
        return df, target

    raise FileNotFoundError("Equity bhavcopy unavailable after all retries")


def equity_bhavcopy_to_price_rows(df: pd.DataFrame, trade_date: date) -> list[dict]:
    """Convert equity bhavcopy DataFrame to price_history upsert rows."""
    rows = []
    for _, row in df.iterrows():
        rows.append({
            "symbol": str(row["SYMBOL"]).strip(),
            "date":   str(trade_date),
            "open":   float(row["OPEN_PRICE"]),
            "high":   float(row["HIGH_PRICE"]),
            "low":    float(row["LOW_PRICE"]),
            "close":  float(row["CLOSE_PRICE"]),
            "volume": int(row["TTL_TRD_QNTY"]),
        })
    return rows


# ── Indices bhavcopy ───────────────────────────────────────────────────────────

def fetch_indices_bhavcopy(for_date: date | None = None) -> tuple[dict, date]:
    """
    Download NSE indices bhavcopy and return closing values for tracked indices.

    Returns ({index_name: close_value, ...}, trade_date).

    India VIX: use CLOSING VALUE ONLY — Open/High/Low are unreliable (spec rule).
    "India VIX" exact name (case-sensitive, one space) — strip() after read.
    """
    target = last_trading_day(for_date)

    for attempt in range(3):
        filename = f"ind_close_all_{target.strftime('%d%m%Y')}.csv"
        local_path = _TMP_DIR / filename

        if local_path.exists():
            logger.info("Found indices bhavcopy in tmp folder: %s", local_path)
        else:
            url = _INDICES_URL.format(ddmmyyyy=target.strftime("%d%m%Y"))
            logger.info("Indices bhavcopy not found in tmp folder, downloading: %s", url)
            logger.debug("Indices bhavcopy attempt %d: %s", attempt + 1, url)

            try:
                r = requests.get(url, headers=_HEADERS, timeout=30)
            except requests.RequestException as exc:
                raise ConnectionError(f"Network error fetching indices bhavcopy: {exc}") from exc

            if r.status_code == 200 and len(r.content) > 500:
                local_path.write_bytes(r.content)
                logger.info("Indices bhavcopy downloaded and saved to tmp: %s", local_path)
            elif r.status_code == 404 and attempt < 2:
                logger.warning("Indices 404 for %s — trying previous day", target)
                target = last_trading_day(target - timedelta(days=1))
                time.sleep(2)
                continue
            else:
                raise FileNotFoundError(
                    f"Indices bhavcopy unavailable for {target}: HTTP {r.status_code}"
                )

        df = pd.read_csv(local_path)
        df.columns = df.columns.str.strip()          # spec: always strip
        df["Index Name"] = df["Index Name"].str.strip()
        df = df.set_index("Index Name")

        result: dict[str, float] = {}
        for idx_name in _TRACKED_INDICES:
            if idx_name in df.index:
                result[idx_name] = float(df.loc[idx_name, "Closing Index Value"])
            else:
                logger.warning("Index '%s' not found in bhavcopy", idx_name)

        return result, target

    raise FileNotFoundError("Indices bhavcopy unavailable after all retries")


def indices_to_price_rows(indices: dict, trade_date: date) -> list[dict]:
    """
    Convert indices closing values to price_history rows.
    Uses normalised symbol names (INDIA_VIX, NIFTY_50, etc.) so index history
    accumulates in price_history alongside equity data.
    """
    rows = []
    for idx_name, close_val in indices.items():
        symbol = _INDEX_SYMBOL_MAP.get(idx_name)
        if not symbol:
            continue
        rows.append({
            "symbol": symbol,
            "date":   str(trade_date),
            "open":   None,   # indices bhavcopy OHLC not reliable for VIX
            "high":   None,
            "low":    None,
            "close":  close_val,
            "volume": None,
        })
    return rows
