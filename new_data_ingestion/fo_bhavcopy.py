"""
NSE F&O Bhavcopy Backfill — historical option chain data.

Downloads daily F&O bhavcopy ZIPs from NSE archives and upserts into
options_snapshots and futures_snapshots for ALL symbols present in the bhavcopy.

Source URL:
  Legacy (pre Jul-08-2024):
    https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{YYYY}/{MMM}/fo{DDMMMYYYY}bhav.csv.zip
  UDiFF (Jul-08-2024 onwards):
    https://www.nseindia.com/api/reports?archives=FO_BhavCopy&date={DD-Mmm-YYYY}&type=equity&mode=single
"""

import argparse
import io
import json
import logging
import sys
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import requests
import pandas as pd

from new_data_ingestion.nse_bhavcopy import get_holiday_dates
from database.queries import upsert_options_snapshots, upsert_futures_snapshots

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_LEGACY_URL = (
    "https://nsearchives.nseindia.com/content/historical/DERIVATIVES"
    "/{yyyy}/{mmm}/fo{ddmmmyyyy}bhav.csv.zip"
)

_UDIFF_BASE    = "https://www.nseindia.com"
_UDIFF_ARCHIVE = "FO_BhavCopy"

# July 8, 2024 is the official start of UDiFF formats for FO bhavcopy
_UDIFF_CUTOFF = date(2024, 7, 8)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection":      "keep-alive",
}

# Instrument filters
_LEGACY_OPTION_INSTRUMENTS  = {"OPTSTK", "OPTIDX"}
_LEGACY_FUTURES_INSTRUMENTS = {"FUTSTK", "FUTIDX"}

_UDIFF_OPTION_TYPES  = {"STO", "IDO"}  # Stock Option, Index Option
_UDIFF_FUTURES_TYPES = {"STF", "IDF"}  # Stock Future, Index Future

# Symbol mappings from NSE bhavcopy values to standard DB keys
_SYMBOL_REMAP = {}

_SECTOR_MAP_PATH = Path(__file__).parent.parent / "config" / "sector_map.json"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_symbols() -> set[str]:
    """All symbols from sector_map.json stocks + NIFTY."""
    data = json.loads(_SECTOR_MAP_PATH.read_text())
    symbols = set(data["stocks"].keys())
    symbols.add("NIFTY")
    return symbols


def _is_trading_day(d: date) -> bool:
    """Return True if d is a weekday and not in the holiday list."""
    if d.weekday() >= 5:
        return False
    return d not in get_holiday_dates()


def _trading_days_between(start: date, end: date) -> list[date]:
    """Return trading days in [start, end] inclusive, ascending."""
    days = []
    current = start
    while current <= end:
        if _is_trading_day(current):
            days.append(current)
        current += timedelta(days=1)
    return days


# ── Download & Parse — Legacy (pre Jul-08-2024) ────────────────────────────────

def _legacy_url(d: date) -> str:
    return _LEGACY_URL.format(
        yyyy=d.strftime("%Y"),
        mmm=d.strftime("%b").upper(),
        ddmmmyyyy=d.strftime("%d%b%Y").upper(),
    )


def _download_legacy(d: date) -> pd.DataFrame | None:
    url = _legacy_url(d)
    logger.debug("Legacy download: %s", url)

    try:
        r = requests.get(url, headers=_HEADERS, timeout=30)
    except requests.RequestException as exc:
        logger.error("Network error for %s: %s", d, exc)
        return None

    if r.status_code == 404:
        logger.warning("%s: bhavcopy not found (404) — possibly holiday", d)
        return None
    if r.status_code != 200 or len(r.content) < 500:
        logger.warning("%s: unexpected HTTP %d (size %d)", d, r.status_code, len(r.content))
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            csv_name = zf.namelist()[0]
            with zf.open(csv_name) as f:
                df = pd.read_csv(f)
    except Exception as exc:
        logger.error("%s: failed to unzip/parse bhavcopy: %s", d, exc)
        return None

    df.columns = df.columns.str.strip()
    return df


def _parse_legacy(df: pd.DataFrame, snapshot_date: date) -> list[dict]:
    """Parse legacy F&O bhavcopy into options_snapshots rows."""
    df = df[df["INSTRUMENT"].isin(_LEGACY_OPTION_INSTRUMENTS)].copy()
    df = df[df["OPTION_TYP"].isin(["CE", "PE"])].copy()
    df["SYMBOL"] = df["SYMBOL"].str.strip()
    if df.empty:
        return []

    def _parse_expiry(s: str) -> str | None:
        from datetime import datetime
        for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
            try:
                return str(datetime.strptime(str(s).strip(), fmt).date())
            except ValueError:
                continue
        return None

    df["expiry_date"] = df["EXPIRY_DT"].apply(_parse_expiry)
    df = df[df["expiry_date"].notna()].copy()

    rows = []
    for _, row in df.iterrows():
        strike = float(row["STRIKE_PR"])
        if strike <= 0:
            continue

        settle  = float(row.get("SETTLE_PR", 0) or 0)
        close   = float(row.get("CLOSE", 0) or 0)
        premium = settle if settle > 0 else (close if close > 0 else None)
        oi        = row.get("OPEN_INT")
        oi_change = row.get("CHG_IN_OI")
        volume    = row.get("CONTRACTS")
        raw_symbol = str(row["SYMBOL"]).strip()
        rows.append({
            "symbol":        _SYMBOL_REMAP.get(raw_symbol, raw_symbol),
            "snapshot_date": str(snapshot_date),
            "expiry_date":   row["expiry_date"],
            "strike":        strike,
            "option_type":   str(row["OPTION_TYP"]).strip(),
            "oi":            int(oi) if pd.notna(oi) else None,
            "oi_change":     int(oi_change) if pd.notna(oi_change) else None,
            "volume":        int(volume) if pd.notna(volume) else None,
            "iv":            None,
            "premium_close": premium,
        })
    return rows


# ── Download & Parse — UDiFF (from Jul-08-2024) ───────────────────────────────

def _make_udiff_session() -> requests.Session:
    """Establish headers + dynamic cookie state to query the live API endpoint."""
    s = requests.Session()
    s.headers.update(_HEADERS)
    logger.debug("Warming up NSE session for UDiFF...")
    try:
        s.get(_UDIFF_BASE, timeout=15)
        time.sleep(2)
        s.get(_UDIFF_BASE + "/all-reports", timeout=15)
        time.sleep(1)
        s.headers.update({"Referer": _UDIFF_BASE + "/all-reports"})
    except requests.RequestException as exc:
        logger.warning("NSE UDiFF warm-up issue: %s", exc)
    return s


def _download_udiff(session: requests.Session, d: date) -> pd.DataFrame | None:
    params = {
        "archives": _UDIFF_ARCHIVE,
        "date":     d.strftime("%d-%b-%Y"),
        "type":     "equity",
        "mode":     "single",
    }
    logger.debug("UDiFF download for %s", d)
    try:
        r = session.get(_UDIFF_BASE + "/api/reports", params=params, timeout=30)
    except requests.RequestException as exc:
        logger.error("Network error for %s: %s", d, exc)
        return None

    if r.status_code == 404:
        logger.warning("%s: UDiFF bhavcopy not found (404) — possibly holiday", d)
        return None
    if r.status_code != 200 or len(r.content) < 500:
        logger.warning("%s: UDiFF unexpected HTTP %d (size %d)", d, r.status_code, len(r.content))
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            with zf.open(zf.namelist()[0]) as f:
                df = pd.read_csv(f)
    except Exception as exc:
        logger.error("%s: failed to unzip/parse UDiFF bhavcopy: %s", d, exc)
        return None

    df.columns = df.columns.str.strip()
    return df


def _parse_udiff(df: pd.DataFrame, snapshot_date: date) -> list[dict]:
    """Parse UDiFF options columns into options_snapshots rows."""
    df = df[df["FinInstrmTp"].isin(_UDIFF_OPTION_TYPES)].copy()
    df = df[df["OptnTp"].isin(["CE", "PE"])].copy()
    df["TckrSymb"] = df["TckrSymb"].str.strip()
    if df.empty:
        return []

    rows = []
    for _, row in df.iterrows():
        strike = float(row["StrkPric"])
        if strike <= 0:
            continue

        settle  = float(row.get("SttlmPric", 0) or 0)
        close   = float(row.get("ClsPric", 0) or 0)
        premium = settle if settle > 0 else (close if close > 0 else None)
        oi        = row.get("OpnIntrst")
        oi_change = row.get("ChngInOpnIntrst")
        volume    = row.get("TtlTradgVol")
        raw_symbol = str(row["TckrSymb"]).strip()
        rows.append({
            "symbol":        _SYMBOL_REMAP.get(raw_symbol, raw_symbol),
            "snapshot_date": str(snapshot_date),
            "expiry_date":   str(row["XpryDt"]).strip(),
            "strike":        strike,
            "option_type":   str(row["OptnTp"]).strip(),
            "oi":            int(oi) if pd.notna(oi) else None,
            "oi_change":     int(oi_change) if pd.notna(oi_change) else None,
            "volume":        int(volume) if pd.notna(volume) else None,
            "iv":            None,
            "premium_close": premium,
        })
    return rows


# ── Futures parse — Legacy (pre Jul-08-2024) ──────────────────────────────────

def _parse_legacy_futures(df: pd.DataFrame, snapshot_date: date) -> list[dict]:
    df = df[df["INSTRUMENT"].isin(_LEGACY_FUTURES_INSTRUMENTS)].copy()
    df["SYMBOL"] = df["SYMBOL"].str.strip()
    if df.empty:
        return []

    def _parse_expiry(s: str) -> str | None:
        from datetime import datetime
        for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
            try:
                return str(datetime.strptime(str(s).strip(), fmt).date())
            except ValueError:
                continue
        return None

    df["expiry_date"] = df["EXPIRY_DT"].apply(_parse_expiry)
    df = df[df["expiry_date"].notna()].copy()

    rows = []
    for _, row in df.iterrows():
        settle = float(row.get("SETTLE_PR", 0) or 0)
        close  = float(row.get("CLOSE", 0) or 0)
        raw_symbol = str(row["SYMBOL"]).strip()
        rows.append({
            "symbol":           _SYMBOL_REMAP.get(raw_symbol, raw_symbol),
            "snapshot_date":    str(snapshot_date),
            "expiry_date":      row["expiry_date"],
            "open_price":       float(row["OPEN"]) if pd.notna(row.get("OPEN")) else None,
            "high_price":       float(row["HIGH"]) if pd.notna(row.get("HIGH")) else None,
            "low_price":        float(row["LOW"]) if pd.notna(row.get("LOW")) else None,
            "close_price":      close if close > 0 else None,
            "settle_price":     settle if settle > 0 else None,
            "oi":               int(row["OPEN_INT"]) if pd.notna(row.get("OPEN_INT")) else None,
            "oi_change":        int(row["CHG_IN_OI"]) if pd.notna(row.get("CHG_IN_OI")) else None,
            "volume":           int(row["CONTRACTS"]) if pd.notna(row.get("CONTRACTS")) else None,
            "underlying_price": None,
        })
    return rows


# ── Futures parse — UDiFF (from Jul-08-2024) ──────────────────────────────────

def _parse_udiff_futures(df: pd.DataFrame, snapshot_date: date) -> list[dict]:
    df = df[df["FinInstrmTp"].isin(_UDIFF_FUTURES_TYPES)].copy()
    df["TckrSymb"] = df["TckrSymb"].str.strip()
    if df.empty:
        return []

    rows = []
    for _, row in df.iterrows():
        settle = float(row.get("SttlmPric", 0) or 0)
        close  = float(row.get("ClsPric", 0) or 0)
        raw_symbol = str(row["TckrSymb"]).strip()
        rows.append({
            "symbol":           _SYMBOL_REMAP.get(raw_symbol, raw_symbol),
            "snapshot_date":    str(snapshot_date),
            "expiry_date":      str(row["XpryDt"]).strip(),
            "open_price":       float(row["OpnPric"]) if pd.notna(row.get("OpnPric")) else None,
            "high_price":       float(row["HghPric"]) if pd.notna(row.get("HghPric")) else None,
            "low_price":        float(row["LwPric"]) if pd.notna(row.get("LwPric")) else None,
            "close_price":      close if close > 0 else None,
            "settle_price":     settle if settle > 0 else None,
            "oi":               int(row["OpnIntrst"]) if pd.notna(row.get("OpnIntrst")) else None,
            "oi_change":        int(row["ChngInOpnIntrst"]) if pd.notna(row.get("ChngInOpnIntrst")) else None,
            "volume":           int(row["TtlTradgVol"]) if pd.notna(row.get("TtlTradgVol")) else None,
            "underlying_price": float(row["UndrlygPric"]) if pd.notna(row.get("UndrlygPric")) else None,
        })
    return rows


# ── Main backfill logic ────────────────────────────────────────────────────────

def run_backfill(
    days: list[date],
    dry_run: bool = False,
) -> dict:
    udiff_session: requests.Session | None = None
    if any(d >= _UDIFF_CUTOFF for d in days):
        udiff_session = _make_udiff_session()

    total_rows = 0
    saved_rows = 0
    skipped    = 0
    failed     = []

    for i, d in enumerate(days):
        fmt = "UDiFF" if d >= _UDIFF_CUTOFF else "legacy"
        logger.info("[%d/%d] %s  (%s)", i + 1, len(days), d, fmt)

        if d >= _UDIFF_CUTOFF:
            df = _download_udiff(udiff_session, d)
            opt_parse_fn = _parse_udiff
            fut_parse_fn = _parse_udiff_futures
        else:
            df = _download_legacy(d)
            opt_parse_fn = _parse_legacy
            fut_parse_fn = _parse_legacy_futures

        if df is None:
            skipped += 1
            continue

        opt_rows = opt_parse_fn(df, d)
        fut_rows = fut_parse_fn(df, d)

        if not opt_rows and not fut_rows:
            logger.warning("  %s: 0 rows parsed — symbols may not match", d)
            skipped += 1
            continue

        total_rows += len(opt_rows) + len(fut_rows)
        logger.info("  %s: %d option rows, %d futures rows", d, len(opt_rows), len(fut_rows))

        if not dry_run:
            try:
                if opt_rows:
                    upsert_options_snapshots(opt_rows)
                if fut_rows:
                    upsert_futures_snapshots(fut_rows)
                saved_rows += len(opt_rows) + len(fut_rows)
                logger.info("  %s: %d rows upserted", d, len(opt_rows) + len(fut_rows))
            except Exception as exc:
                logger.error("  %s: DB upsert failed: %s", d, exc)
                failed.append(str(d))
        else:
            logger.info("  %s: %d rows (dry-run, not saved)", d, len(opt_rows) + len(fut_rows))

        if i < len(days) - 1:
            time.sleep(1.0 if d >= _UDIFF_CUTOFF else 0.5)

    return {
        "total_rows": total_rows,
        "saved_rows": saved_rows,
        "skipped":    skipped,
        "failed":     failed,
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill F&O bhavcopy"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--days", type=int,
        help="Number of past trading days to backfill (e.g. 30)"
    )
    group.add_argument(
        "--from", dest="from_date",
        help="Start date YYYY-MM-DD (use with --to)"
    )

    parser.add_argument(
        "--to", dest="to_date", default=None,
        help="End date YYYY-MM-DD (default: today, used with --from)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and report but do NOT write to DB"
    )
    args = parser.parse_args()

    today = date.today()

    if args.days:
        days: list[date] = []
        candidate = today - timedelta(days=1)
        while len(days) < args.days:
            if _is_trading_day(candidate):
                days.append(candidate)
            candidate -= timedelta(days=1)
            if (today - candidate).days > 365:
                break
        days = list(reversed(days))

    else:
        start = date.fromisoformat(args.from_date)
        end   = date.fromisoformat(args.to_date) if args.to_date else today
        days  = _trading_days_between(start, end)

    if not days:
        logger.error("No trading days found in the given range.")
        sys.exit(1)

    result = run_backfill(days, dry_run=args.dry_run)
    logger.info("Done. Stored rows: %d", result["saved_rows"])


if __name__ == "__main__":
    main()
