"""
Kite Connect — historical OI for Nifty 50 futures (near + next month).

OI units: returned in SHARES — always divide by lot_size for lots.
oi=True is MANDATORY — without it the OI column is absent, no error raised.
Expiry day OI drops to 0 (settlement) — mark is_expiry_day=True.
"""
import logging
import time
from datetime import date, timedelta

import pandas as pd
from kiteconnect import KiteConnect

from integrations.kite_ohlcv import get_instruments

logger = logging.getLogger(__name__)


def get_nfo_instruments(kite: KiteConnect) -> pd.DataFrame:
    """NFO instrument master — cached. ~46,000 rows covering all F&O contracts."""
    return get_instruments(kite, "NFO")


def get_futures_contracts(
    kite: KiteConnect,
    symbol: str,
    max_expiries: int = 2,
) -> list[tuple[int, int, date]]:
    """
    Return near + next month futures contracts for a symbol.

    Returns list of (instrument_token, lot_size, expiry_date), sorted by expiry.
    max_expiries=2 gives near month + next month (far month excluded per spec).
    """
    df = get_nfo_instruments(kite)
    futs = df[
        (df["name"] == symbol) &
        (df["instrument_type"] == "FUT")
    ].sort_values("expiry")

    if futs.empty:
        raise ValueError(f"No futures found for symbol '{symbol}'")

    result = []
    for _, row in futs.head(max_expiries).iterrows():
        result.append((
            int(row["instrument_token"]),
            int(row["lot_size"]),
            row["expiry"].date() if hasattr(row["expiry"], "date") else row["expiry"],
        ))
    return result


def fetch_futures_oi_series(
    kite: KiteConnect,
    instrument_token: int,
    lot_size: int,
    from_date: date,
    to_date: date,
) -> pd.DataFrame:
    """
    Fetch daily OHLCV + OI for a futures contract.

    Returns DataFrame with: date, open, high, low, close, volume, oi, oi_lots, oi_change.
    oi_lots = oi / lot_size (shares → lots).
    """
    raw = kite.historical_data(
        instrument_token=instrument_token,
        from_date=str(from_date),
        to_date=str(to_date),
        interval="day",
        oi=True,    # MANDATORY — without this flag, OI column is absent
    )
    if not raw:
        return pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "volume", "oi", "oi_lots", "oi_change"]
        )

    df = pd.DataFrame(raw)
    df["oi_lots"]   = df["oi"] / lot_size
    df["oi_change"] = df["oi_lots"].diff().fillna(0)
    return df


def fetch_futures_oi_all(
    kite: KiteConnect,
    symbols: list[str],
    days: int = 30,
) -> dict[str, dict]:
    """
    Fetch near + next month futures OI for all Nifty 50 symbols.

    Returns {symbol: {"near": df, "next": df, "lot_size": int, "near_expiry": date, "next_expiry": date}}.
    Failed symbols are logged and excluded.
    Sleeps 0.35s between calls.
    """
    to_date   = date.today()
    from_date = to_date - timedelta(days=days)
    results: dict[str, dict] = {}

    for symbol in symbols:
        try:
            contracts = get_futures_contracts(kite, symbol, max_expiries=2)
        except ValueError as exc:
            logger.warning("No futures for %s: %s", symbol, exc)
            continue

        entry: dict = {"lot_size": contracts[0][1]}

        for idx, (token, lot_size, expiry) in enumerate(contracts):
            label = "near" if idx == 0 else "next"
            entry[f"{label}_expiry"] = expiry
            try:
                df = fetch_futures_oi_series(kite, token, lot_size, from_date, to_date)
                entry[label] = df
                logger.debug("%s %s expiry %s: %d rows", symbol, label, expiry, len(df))
            except Exception as exc:
                logger.warning("OI fetch failed %s %s: %s", symbol, label, exc)
                entry[label] = pd.DataFrame()
            time.sleep(0.35)

        results[symbol] = entry

    ok = sum(1 for v in results.values() if not v.get("near", pd.DataFrame()).empty)
    logger.info("Futures OI batch complete: %d/%d symbols", ok, len(symbols))
    return results


def futures_oi_to_series_rows(
    symbol: str,
    near_df: pd.DataFrame,
    next_df: pd.DataFrame,
    lot_size: int,
    near_expiry: date,
    next_expiry: date,
    rollover_phase: str,
) -> list[dict]:
    """
    Convert near + next month OI DataFrames to futures_continuous_series rows.

    Marks is_expiry_day=True where OI drops to 0 (settlement noise — never use as signal).
    """
    # Build lookup for next month OI by date
    next_oi_by_date: dict[date, int] = {}
    if not next_df.empty:
        for _, row in next_df.iterrows():
            dt = row["date"]
            if hasattr(dt, "date"):
                dt = dt.date()
            next_oi_by_date[dt] = int(row["oi"])

    rows = []
    for _, row in near_df.iterrows():
        dt = row["date"]
        if hasattr(dt, "date"):
            dt = dt.date()

        near_oi       = int(row["oi"])
        near_oi_lots  = float(row["oi_lots"])
        is_expiry_day = (near_oi == 0)   # OI drops to 0 on expiry — mark it

        next_oi      = next_oi_by_date.get(dt, 0)
        total_oi     = near_oi + next_oi
        rollover_pct = (next_oi / total_oi * 100) if total_oi > 0 else None

        futures_close = float(row["close"])   # futures close used as proxy until bhavcopy loaded
        futures_open  = float(row["open"])  if "open"  in row and row["open"]  is not None else None
        futures_high  = float(row["high"])  if "high"  in row and row["high"]  is not None else None
        futures_low   = float(row["low"])   if "low"   in row and row["low"]   is not None else None

        rows.append({
            "symbol":         symbol,
            "date":           str(dt),
            "rollover_phase": rollover_phase,
            "near_expiry":    str(near_expiry),
            "next_expiry":    str(next_expiry),
            "futures_price":  futures_close,
            "futures_open":   futures_open,
            "futures_high":   futures_high,
            "futures_low":    futures_low,
            "spot_price":     None,   # set later from bhavcopy CLOSE
            "basis":          None,
            "basis_pct":      None,
            "near_month_oi":  near_oi,
            "next_month_oi":  next_oi,
            "oi_change":      int(row["oi_change"]),
            "in_rollover_week": rollover_phase in ("ROLLOVER_WATCH", "TRANSITION"),
            "is_expiry_day":  is_expiry_day,
            "rollover_pct":   rollover_pct,
        })
    return rows
