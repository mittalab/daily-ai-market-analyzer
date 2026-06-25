"""
Kite Connect — historical OI for Nifty 50 futures (near + next month).

OI units: returned in SHARES — always divide by lot_size for lots.
oi=True is MANDATORY.
"""
import logging
import time
from datetime import date, timedelta

import pandas as pd
from kiteconnect import KiteConnect

from new_data_ingestion.kite_ohlcv import get_instruments

logger = logging.getLogger(__name__)


def get_nfo_instruments(kite: KiteConnect) -> pd.DataFrame:
    """NFO instrument master — cached."""
    return get_instruments(kite, "NFO")


def get_futures_contracts(
    kite: KiteConnect,
    symbol: str,
    max_expiries: int = 3,
) -> list[tuple[int, int, date]]:
    """
    Return near + next month futures contracts for a symbol.
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
    """
    raw = kite.historical_data(
        instrument_token=instrument_token,
        from_date=str(from_date),
        to_date=str(to_date),
        interval="day",
        oi=True,
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
    Fetch near + next month futures OI for all target symbols.
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


def futures_oi_to_snapshots_rows(
    symbol: str,
    near_df: pd.DataFrame,
    next_df: pd.DataFrame,
    near_expiry: date,
    next_expiry: date,
) -> list[dict]:
    """
    Convert Kite near + next month futures OI DataFrames into futures_snapshots rows.
    """
    rows = []
    
    if not near_df.empty:
        for _, row in near_df.iterrows():
            dt = row["date"]
            if hasattr(dt, "date"):
                dt = dt.date()
                
            rows.append({
                "symbol": symbol,
                "snapshot_date": str(dt),
                "expiry_date": str(near_expiry),
                "open_price": float(row["open"]) if pd.notna(row.get("open")) else None,
                "high_price": float(row["high"]) if pd.notna(row.get("high")) else None,
                "low_price": float(row["low"]) if pd.notna(row.get("low")) else None,
                "close_price": float(row["close"]) if pd.notna(row.get("close")) else None,
                "settle_price": float(row["close"]) if pd.notna(row.get("close")) else None,
                "oi": int(row["oi"]) if pd.notna(row.get("oi")) else None,
                "oi_change": int(row["oi_change"]) if pd.notna(row.get("oi_change")) else None,
                "volume": int(row["volume"]) if pd.notna(row.get("volume")) else None,
                "underlying_price": None,
            })
            
    if not next_df.empty:
        for _, row in next_df.iterrows():
            dt = row["date"]
            if hasattr(dt, "date"):
                dt = dt.date()
                
            rows.append({
                "symbol": symbol,
                "snapshot_date": str(dt),
                "expiry_date": str(next_expiry),
                "open_price": float(row["open"]) if pd.notna(row.get("open")) else None,
                "high_price": float(row["high"]) if pd.notna(row.get("high")) else None,
                "low_price": float(row["low"]) if pd.notna(row.get("low")) else None,
                "close_price": float(row["close"]) if pd.notna(row.get("close")) else None,
                "settle_price": float(row["close"]) if pd.notna(row.get("close")) else None,
                "oi": int(row["oi"]) if pd.notna(row.get("oi")) else None,
                "oi_change": int(row["oi_change"]) if pd.notna(row.get("oi_change")) else None,
                "volume": int(row["volume"]) if pd.notna(row.get("volume")) else None,
                "underlying_price": None,
            })
            
    return rows
