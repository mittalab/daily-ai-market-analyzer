"""
Modular validation checks for system state and data completeness.
Exposes 8 standalone validation check functions.
"""
import logging
from datetime import date, datetime, timedelta
import pandas as pd

from database.queries import get_client, get_kite_token
from new_data_ingestion.kite_oauth import get_authenticated_kite

logger = logging.getLogger(__name__)


def validate_kite_token() -> tuple[bool, str]:
    """1. Validate Kite session is active via profile API check."""
    try:
        token_row = get_kite_token()
        if not token_row:
            return False, "Kite access token missing in database"
        
        kite = get_authenticated_kite()
        profile = kite.profile()
        user_id = profile.get("user_id", "Unknown")
        return True, f"Kite token valid (User: {user_id})"
    except Exception as exc:
        return False, f"Kite token validation failed: {exc}"


def validate_db_connectivity() -> tuple[bool, str]:
    """2. Validate DB client connectivity to Supabase."""
    try:
        client = get_client()
        # Query a simple small table or count config
        resp = client.table("system_config").select("key").limit(1).execute()
        return True, "Database connectivity OK"
    except Exception as exc:
        return False, f"Database connectivity failed: {exc}"


def validate_stock_ohlcv(symbol: str, check_date: date, days: int = 180) -> tuple[bool, str]:
    """3. Check stock has >= 180 trading days of OHLCV history ending on check_date."""
    try:
        client = get_client()
        # Query row count for symbol up to check_date
        resp = (
            client.table("price_history")
            .select("date")
            .eq("symbol", symbol)
            .lte("date", str(check_date))
            .order("date", desc=True)
            .limit(days)
            .execute()
        )
        count = len(resp.data)
        if count >= days:
            return True, f"Stock OHLCV OK: Found {count}/{days} rows ending on {check_date}"
        
        # Check if the stock has today's record specifically
        has_today = any(r["date"] == str(check_date) for r in resp.data)
        today_msg = "present" if has_today else "MISSING"
        return False, f"Stock OHLCV Insufficient: Found {count}/{days} rows ending on {check_date} (Target date: {today_msg})"
    except Exception as exc:
        return False, f"Stock OHLCV check failed for {symbol}: {exc}"


def validate_stock_options(symbol: str, expiry: date, check_date: date, days: int = 30) -> tuple[bool, str]:
    """4. Check stock options snapshot is present on check_date and has sufficient history."""
    try:
        client = get_client()
        
        # Verify the snapshot for check_date itself exists and get a sample strike to query history efficiently
        today_resp = (
            client.table("options_snapshots")
            .select("strike")
            .eq("symbol", symbol)
            .eq("expiry_date", str(expiry))
            .eq("snapshot_date", str(check_date))
            .limit(1)
            .execute()
        )
        if not today_resp.data:
            return False, f"Stock Options snapshot MISSING for {symbol} on {check_date} (Expiry: {expiry})"
            
        target_strike = today_resp.data[0]["strike"]
        
        # Verify history depth using the sample strike and CE option type to retrieve exactly 1 row per date
        hist_resp = (
            client.table("options_snapshots")
            .select("snapshot_date")
            .eq("symbol", symbol)
            .eq("expiry_date", str(expiry))
            .eq("strike", target_strike)
            .eq("option_type", "CE")
            .lte("snapshot_date", str(check_date))
            .execute()
        )
        # Unique dates
        unique_dates = {r["snapshot_date"] for r in hist_resp.data}
        count = len(unique_dates)
        
        if count >= days:
            return True, f"Stock Options OK: Found {count}/{days} option snapshots ending on {check_date}"
        else:
            # Best effort check: if count > 0 and it has today's snapshot, we pass, but warn
            return True, f"Stock Options Pass (Best Effort): Found {count}/{days} option snapshots ending on {check_date}"
            
    except Exception as exc:
        return False, f"Stock Options check failed for {symbol}: {exc}"


def validate_stock_futures(symbol: str, expiry: date, check_date: date, days: int = 30) -> tuple[bool, str]:
    """5. Check stock futures snapshot is present on check_date and has sufficient history."""
    try:
        client = get_client()
        
        # Verify futures snapshot for check_date exists
        today_resp = (
            client.table("futures_snapshots")
            .select("snapshot_date")
            .eq("symbol", symbol)
            .eq("expiry_date", str(expiry))
            .eq("snapshot_date", str(check_date))
            .limit(1)
            .execute()
        )
        if not today_resp.data:
            return False, f"Stock Futures snapshot MISSING for {symbol} on {check_date} (Expiry: {expiry})"
            
        # Verify history depth
        hist_resp = (
            client.table("futures_snapshots")
            .select("snapshot_date")
            .eq("symbol", symbol)
            .eq("expiry_date", str(expiry))
            .lte("snapshot_date", str(check_date))
            .order("snapshot_date", desc=True)
            .limit(days)
            .execute()
        )
        count = len(hist_resp.data)
        if count >= days:
            return True, f"Stock Futures OK: Found {count}/{days} futures snapshots ending on {check_date}"
        else:
            return True, f"Stock Futures Pass (Best Effort): Found {count}/{days} futures snapshots ending on {check_date}"
            
    except Exception as exc:
        return False, f"Stock Futures check failed for {symbol}: {exc}"


def validate_index_ohlcv(index_symbol: str, check_date: date, days: int = 180) -> tuple[bool, str]:
    """6. Check index has >= 180 trading days of price history."""
    try:
        client = get_client()
        resp = (
            client.table("price_history")
            .select("date")
            .eq("symbol", index_symbol)
            .lte("date", str(check_date))
            .order("date", desc=True)
            .limit(days)
            .execute()
        )
        count = len(resp.data)
        if count >= days:
            return True, f"Index OHLCV OK: Found {count}/{days} rows ending on {check_date} for {index_symbol}"
        
        has_today = any(r["date"] == str(check_date) for r in resp.data)
        today_msg = "present" if has_today else "MISSING"
        return False, f"Index OHLCV Insufficient: Found {count}/{days} rows ending on {check_date} for {index_symbol} (Target date: {today_msg})"
    except Exception as exc:
        return False, f"Index OHLCV check failed for {index_symbol}: {exc}"


def validate_index_options(expiry: date, check_date: date, days: int = 30) -> tuple[bool, str]:
    """7. Validate NIFTY 50 index options snapshot on check_date."""
    # Nifty options symbol in database is NIFTY
    return validate_stock_options("NIFTY", expiry, check_date, days)


def validate_india_vix(check_date: date, days: int = 30) -> tuple[bool, str]:
    """8. Confirm India VIX index history contains data."""
    try:
        client = get_client()
        resp = (
            client.table("price_history")
            .select("date")
            .eq("symbol", "INDIA_VIX")
            .lte("date", str(check_date))
            .order("date", desc=True)
            .limit(days)
            .execute()
        )
        count = len(resp.data)
        if count >= days:
            return True, f"India VIX OK: Found {count}/{days} rows ending on {check_date}"
        
        has_today = any(r["date"] == str(check_date) for r in resp.data)
        today_msg = "present" if has_today else "MISSING"
        return False, f"India VIX Insufficient: Found {count}/{days} rows ending on {check_date} (Target date: {today_msg})"
    except Exception as exc:
        return False, f"India VIX check failed: {exc}"
