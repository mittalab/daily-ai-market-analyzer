"""
Core database operations — one function per logical operation.
All functions use the singleton client from client.py.
Callers handle errors; queries let exceptions propagate so the pipeline
can log, alert, and decide whether to abort or continue gracefully.
"""
import logging
from datetime import date, datetime, timedelta
from typing import Any

from database.client import get_client

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Keepalive
# ─────────────────────────────────────────────────────────────────────────────

def keepalive() -> bool:
    """Ping the database. Used by the 6 AM job to prevent Supabase free-tier pausing."""
    try:
        get_client().table("system_config").select("key").limit(1).execute()
        return True
    except Exception as exc:
        logger.error("Keepalive failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# system_config
# ─────────────────────────────────────────────────────────────────────────────

def get_system_config(key: str) -> str | None:
    """Fetch a single config value by key. Returns None if key not found."""
    resp = get_client().table("system_config").select("value").eq("key", key).execute()
    if resp.data:
        return resp.data[0]["value"]
    return None


def get_all_system_config() -> dict[str, str]:
    """Fetch all config as {key: value} dict. Pipeline calls this once at startup."""
    resp = get_client().table("system_config").select("key,value").execute()
    return {row["key"]: row["value"] for row in resp.data}


def get_dashboard_url() -> str:
    """Return the dashboard URL from system_config. Fallback ensures notifications always send."""
    url = get_system_config("dashboard_url")
    return url or "https://trading.abhishekmittal.in"


def get_interested_stocks() -> list[str]:
    """Return extra symbols from system_config 'interested_stocks' (comma-separated)."""
    raw = get_system_config("interested_stocks") or ""
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def set_system_config(key: str, value: str) -> None:
    """Upsert a single system_config entry."""
    get_client().table("system_config").upsert(
        {"key": key, "value": value}, on_conflict="key"
    ).execute()


# ─────────────────────────────────────────────────────────────────────────────
# kite_tokens
# ─────────────────────────────────────────────────────────────────────────────

def upsert_kite_token(access_token: str, generated_at: datetime, expires_at: datetime) -> None:
    """Store the daily Kite access token. Overwrites the single 'primary' row."""
    get_client().table("kite_tokens").upsert({
        "user_id":       "primary",
        "access_token":  access_token,
        "generated_at":  generated_at.isoformat(),
        "expires_at":    expires_at.isoformat(),
    }).execute()


def get_kite_token() -> dict | None:
    """Return the current token row, or None if no token stored yet."""
    resp = get_client().table("kite_tokens").select("*").eq("user_id", "primary").execute()
    return resp.data[0] if resp.data else None


# ─────────────────────────────────────────────────────────────────────────────
# price_history
# ─────────────────────────────────────────────────────────────────────────────

def upsert_price_history(rows: list[dict]) -> int:
    """
    Bulk upsert OHLCV rows. Returns count of rows processed.
    ON CONFLICT (symbol, date) overwrites existing — Kite data is authoritative.
    """
    if not rows:
        return 0
    get_client().table("price_history").upsert(rows, on_conflict="symbol,date").execute()
    return len(rows)


def upsert_price_history_new_only(rows: list[dict]) -> int:
    """
    Insert OHLCV rows only for (symbol, date) pairs not already present.
    ON CONFLICT DO NOTHING — existing rows (e.g. from Kite analysis) are preserved.
    """
    if not rows:
        return 0
    get_client().table("price_history").upsert(
        rows, on_conflict="symbol,date", ignore_duplicates=True
    ).execute()
    return len(rows)


def get_price_history(symbol: str, days: int = 180) -> list[dict]:
    """Fetch the most recent N rows of OHLCV for a symbol, ordered ascending."""
    resp = (
        get_client()
        .table("price_history")
        .select("date,open,high,low,close,volume")
        .eq("symbol", symbol)
        .order("date", desc=True)
        .limit(days)
        .execute()
    )
    return list(reversed(resp.data))


def get_row_count(table: str, filters: dict | None = None, created_after: datetime | None = None) -> int:
    """
    Return total row count for a table, filtered by column values and/or creation time.
    Used for verifying that a scheduled job actually inserted NEW data.
    """
    query = get_client().table(table).select("id", count="exact")
    if filters:
        for key, val in filters.items():
            query = query.eq(key, val)
    if created_after:
        # Supabase/PostgREST uses ISO strings for timestamp comparisons
        query = query.gte("created_at", created_after.isoformat())
    
    resp = query.limit(1).execute()
    return resp.count if resp.count is not None else 0


# ─────────────────────────────────────────────────────────────────────────────
# fii_dii_flows
# ─────────────────────────────────────────────────────────────────────────────

def upsert_fii_dii_flow(data: dict) -> None:
    """
    Upsert a single day's FII/DII flow. Values in Crores — not divided.
    source='LIVE' for fresh data, 'CACHED' when using previous day on fetch failure.
    """
    get_client().table("fii_dii_flows").upsert(data, on_conflict="date").execute()


def get_fii_dii_flows(days: int = 30) -> list[dict]:
    """Fetch last N days of FII/DII flows, ordered ascending."""
    resp = (
        get_client()
        .table("fii_dii_flows")
        .select("*")
        .order("date", desc=True)
        .limit(days)
        .execute()
    )
    return list(reversed(resp.data))


def get_latest_fii_dii(target_date: date | None = None) -> dict[str, Any] | None:
    """Return the FII/DII row for a specific date, or the most recent row if no date is passed."""
    query = get_client().table("fii_dii_flows").select("*")

    if target_date:
        # If a specific date is provided, look for an exact match
        resp = query.eq("date", str(target_date)).execute()
    else:
        # Fallback: get the most recent record
        resp = query.order("date", desc=True).limit(1).execute()

    return resp.data[0] if resp.data else None


# ─────────────────────────────────────────────────────────────────────────────
# lot_sizes
# ─────────────────────────────────────────────────────────────────────────────

def upsert_lot_sizes(rows: list[dict]) -> None:
    """
    Bulk upsert lot sizes from Kite instruments master.
    previous_lot is set to the existing value before overwrite so changes
    are detectable for the lot-size-change Telegram alert.
    """
    if not rows:
        return
    get_client().table("lot_sizes").upsert(rows, on_conflict="symbol").execute()


def get_lot_size(symbol: str) -> int | None:
    """Return lot size for a single symbol, or None if not yet loaded."""
    resp = (
        get_client()
        .table("lot_sizes")
        .select("lot_size")
        .eq("symbol", symbol)
        .execute()
    )
    return resp.data[0]["lot_size"] if resp.data else None


def get_all_lot_sizes() -> dict[str, int]:
    """Return {symbol: lot_size} for all stored symbols. Loaded into memory at pipeline start."""
    resp = get_client().table("lot_sizes").select("symbol,lot_size").execute()
    return {row["symbol"]: row["lot_size"] for row in resp.data}


def upsert_single_lot_size(symbol: str, lot_size: int) -> None:
    """Cache a single lot size fetched on-demand (e.g. from Kite instruments master)."""
    get_client().table("lot_sizes").upsert(
        {"symbol": symbol, "lot_size": lot_size}, on_conflict="symbol"
    ).execute()


# ─────────────────────────────────────────────────────────────────────────────
# options_snapshots
# ─────────────────────────────────────────────────────────────────────────────

def upsert_options_snapshots(rows: list[dict], batch_size: int = 2000) -> int:
    """
    Bulk upsert options snapshot rows in batches to avoid Supabase response size limits.
    Unique constraint is (symbol, snapshot_date, expiry_date, strike, option_type).
    """
    if not rows:
        return 0
    for i in range(0, len(rows), batch_size):
        get_client().table("options_snapshots").upsert(
            rows[i : i + batch_size],
            # This instructs Postgres to use 'ON CONFLICT DO NOTHING'
            # against your table's primary/unique keys automatically
            on_conflict="symbol,snapshot_date,expiry_date,strike,option_type",
            ignore_duplicates=True
        ).execute()
    return len(rows)


def get_options_snapshot(symbol: str, snapshot_date: date, expiry_date: date) -> list[dict]:
    """Fetch all strikes for a symbol on a given snapshot date and expiry."""
    resp = (
        get_client()
        .table("options_snapshots")
        .select("*")
        .eq("symbol", symbol)
        .eq("snapshot_date", str(snapshot_date))
        .eq("expiry_date", str(expiry_date))
        .order("strike", desc=False)
        .execute()
    )
    return resp.data


def get_latest_snapshot_date(symbol: str) -> date | None:
    """Return the most recent snapshot date for a symbol. Used for fallback logic."""
    resp = (
        get_client()
        .table("options_snapshots")
        .select("snapshot_date")
        .eq("symbol", symbol)
        .order("snapshot_date", desc=True)
        .limit(1)
        .execute()
    )
    if resp.data:
        return date.fromisoformat(resp.data[0]["snapshot_date"])
    return None


# ─────────────────────────────────────────────────────────────────────────────
# continuous_oi_series
# ─────────────────────────────────────────────────────────────────────────────

def upsert_continuous_oi(data: dict) -> None:
    """Upsert one row of the continuous OI series (one symbol, one date)."""
    get_client().table("continuous_oi_series").upsert(data, on_conflict="symbol,date").execute()


def get_continuous_oi(symbol: str, days: int = 30) -> list[dict]:
    """Fetch last N rows of continuous OI for a symbol, ordered ascending."""
    resp = (
        get_client()
        .table("continuous_oi_series")
        .select("*")
        .eq("symbol", symbol)
        .order("date", desc=True)
        .limit(days)
        .execute()
    )
    return list(reversed(resp.data))


# ─────────────────────────────────────────────────────────────────────────────
# options_snapshots (additional queries for oi_series_builder)
# ─────────────────────────────────────────────────────────────────────────────

def get_options_by_date(symbol: str, snapshot_date: date) -> list[dict]:
    """Fetch all option rows for a symbol on a given snapshot date (all expiries)."""
    resp = (
        get_client()
        .table("options_snapshots")
        .select("*")
        .eq("symbol", symbol)
        .eq("snapshot_date", str(snapshot_date))
        .order("expiry_date", desc=False)
        .order("strike", desc=False)
        .execute()
    )
    return resp.data


# ─────────────────────────────────────────────────────────────────────────────
# futures_snapshots  (raw per-expiry, from bhavcopy)
# ─────────────────────────────────────────────────────────────────────────────

def upsert_futures_snapshots(rows: list[dict], batch_size: int = 2000) -> int:
    """
    Bulk upsert per-expiry futures snapshot rows in batches.
    Unique constraint is (symbol, snapshot_date, expiry_date).
    """
    if not rows:
        return 0
    for i in range(0, len(rows), batch_size):
        get_client().table("futures_snapshots").upsert(
            rows[i : i + batch_size],
            on_conflict="symbol,snapshot_date,expiry_date",
            ignore_duplicates=True
        ).execute()
    return len(rows)


# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# futures_continuous_series (dropped: redirected to futures_snapshots)
# ─────────────────────────────────────────────────────────────────────────────

def upsert_futures_series(data: dict) -> None:
    """No-op: Continuous series table dropped in favor of raw snapshots."""
    pass


def get_futures_row(symbol: str, date_: date) -> dict | None:
    """Fetch the continuous-like futures row for a symbol and date, derived dynamically."""
    resp = (
        get_client()
        .table("futures_snapshots")
        .select("*")
        .eq("symbol", symbol)
        .eq("snapshot_date", str(date_))
        .execute()
    )
    if not resp.data:
        return None
        
    contracts = sorted(resp.data, key=lambda x: x["expiry_date"])
    near_contract = contracts[0]
    next_contract = contracts[1] if len(contracts) > 1 else None
    
    near_oi = int(near_contract.get("oi") or 0)
    next_oi = int(next_contract.get("oi") or 0) if next_contract else 0
    total_oi = near_oi + next_oi
    
    rollover_pct = None
    if total_oi > 0:
        rollover_pct = round(next_oi / total_oi * 100, 4)
        
    futures_price = float(near_contract["settle_price"] or near_contract["close_price"] or 0)
    
    return {
        "symbol": symbol,
        "date": str(date_),
        "futures_price": futures_price,
        "near_expiry": near_contract["expiry_date"],
        "next_expiry": next_contract["expiry_date"] if next_contract else None,
        "near_month_oi": near_oi,
        "next_month_oi": next_oi,
        "rollover_pct": rollover_pct,
        "futures_open": float(near_contract["open_price"]) if near_contract.get("open_price") is not None else None,
        "futures_high": float(near_contract["high_price"]) if near_contract.get("high_price") is not None else None,
        "futures_low": float(near_contract["low_price"]) if near_contract.get("low_price") is not None else None,
        "futures_volume": int(near_contract["volume"]) if near_contract.get("volume") is not None else None,
    }


def update_futures_spot(
    symbol: str, date_: date, spot_price: float, basis: float, basis_pct: float
) -> None:
    """No-op: spot/basis values are computed dynamically in get_futures_series."""
    pass


def get_futures_series(symbol: str, days: int = 30) -> list[dict]:
    """Dynamically compile continuous futures series from futures_snapshots and price_history."""
    resp = (
        get_client()
        .table("futures_snapshots")
        .select("*")
        .eq("symbol", symbol)
        .order("snapshot_date", desc=True)
        .limit(days * 4)
        .execute()
    )
    if not resp.data:
        return []

    from collections import defaultdict
    by_date = defaultdict(list)
    for row in resp.data:
        by_date[row["snapshot_date"]].append(row)

    sorted_dates = sorted(by_date.keys(), reverse=True)[:days]

    spot_resp = (
        get_client()
        .table("price_history")
        .select("date,close")
        .eq("symbol", symbol)
        .in_("date", sorted_dates)
        .execute()
    )
    spot_by_date = {row["date"]: float(row["close"]) for row in spot_resp.data if row.get("close") is not None}

    compiled_rows = []
    total_oi_by_date = {}
    ascending_dates = sorted(sorted_dates)
    
    import json
    from pathlib import Path
    try:
        sector_map = json.loads(Path(r"C:\Users\29abh\Projects\Trading\daily-ai-market-analyzer\config\sector_map.json").read_text())
        holidays = {datetime.strptime(d, "%Y-%m-%d").date() for d in sector_map.get("holidays", [])}
    except Exception:
        holidays = set()

    for idx, snap_date in enumerate(ascending_dates):
        contracts = by_date[snap_date]
        contracts_sorted = sorted(contracts, key=lambda x: x["expiry_date"])
        if not contracts_sorted:
            continue
            
        near_contract = contracts_sorted[0]
        next_contract = contracts_sorted[1] if len(contracts_sorted) > 1 else None
        
        near_expiry = near_contract["expiry_date"]
        next_expiry = next_contract["expiry_date"] if next_contract else None
        
        near_oi = int(near_contract.get("oi") or 0)
        next_oi = int(next_contract.get("oi") or 0) if next_contract else 0
        total_oi = near_oi + next_oi
        total_oi_by_date[snap_date] = total_oi
        
        prev_date = ascending_dates[idx - 1] if idx > 0 else None
        prev_total = total_oi_by_date.get(prev_date) if prev_date else None
        oi_change = (total_oi - prev_total) if prev_total is not None else 0
        
        futures_price = float(near_contract["settle_price"] or near_contract["close_price"] or 0)
        spot_price = spot_by_date.get(snap_date)
        
        basis = None
        basis_pct = None
        if spot_price is not None and futures_price > 0:
            basis = round(futures_price - spot_price, 2)
            basis_pct = round(basis / spot_price * 100, 4)
            
        rollover_pct = None
        if total_oi > 0:
            rollover_pct = round(next_oi / total_oi * 100, 4)

        d_start = datetime.strptime(snap_date, "%Y-%m-%d").date()
        d_end = datetime.strptime(near_expiry, "%Y-%m-%d").date()
        
        trading_days = 0
        curr = d_start
        while curr < d_end:
            curr += timedelta(days=1)
            if curr.weekday() < 5 and curr not in holidays:
                trading_days += 1
                
        if trading_days <= 1:
            rollover_phase = "EXPIRY"
        elif trading_days == 2:
            rollover_phase = "TRANSITION"
        elif trading_days <= 5:
            rollover_phase = "ROLLOVER_WATCH"
        else:
            rollover_phase = "NORMAL"

        compiled_rows.append({
            "symbol": symbol,
            "date": snap_date,
            "futures_price": futures_price,
            "spot_price": spot_price,
            "basis": basis,
            "basis_pct": basis_pct,
            "near_month_oi": near_oi,
            "next_month_oi": next_oi,
            "oi_change": int(oi_change),
            "in_rollover_week": rollover_phase in ("ROLLOVER_WATCH", "TRANSITION", "EXPIRY"),
            "is_expiry_day": (snap_date == near_expiry),
            "rollover_pct": rollover_pct,
            "rollover_phase": rollover_phase,
            "futures_open": float(near_contract["open_price"]) if near_contract.get("open_price") is not None else None,
            "futures_high": float(near_contract["high_price"]) if near_contract.get("high_price") is not None else None,
            "futures_low": float(near_contract["low_price"]) if near_contract.get("low_price") is not None else None,
            "futures_volume": int(near_contract["volume"]) if near_contract.get("volume") is not None else None,
        })
        
    return compiled_rows


# ─────────────────────────────────────────────────────────────────────────────
# validation_states
# ─────────────────────────────────────────────────────────────────────────────

def get_validation_state(symbol: str, validation_date: date) -> dict | None:
    """Fetch the cached validation state for a symbol on a specific date."""
    resp = (
        get_client()
        .table("validation_states")
        .select("*")
        .eq("symbol", symbol)
        .eq("validation_date", str(validation_date))
        .execute()
    )
    return resp.data[0] if resp.data else None


def get_last_passed_validation_date(symbol: str) -> date | None:
    """Fetch the maximum validation date where status is PASSED."""
    resp = (
        get_client()
        .table("validation_states")
        .select("validation_date")
        .eq("symbol", symbol)
        .eq("status", "PASSED")
        .order("validation_date", desc=True)
        .limit(1)
        .execute()
    )
    if resp.data:
        return date.fromisoformat(resp.data[0]["validation_date"])
    return None


def upsert_validation_state(
    symbol: str,
    validation_date: date,
    run_type: str,
    status: str,
    check_results: dict,
    error_message: str | None = None
) -> None:
    """Insert or update the validation state for a symbol and date."""
    get_client().table("validation_states").upsert({
        "symbol": symbol,
        "validation_date": str(validation_date),
        "run_type": run_type,
        "status": status,
        "check_results": check_results,
        "error_message": error_message,
        "updated_at": datetime.utcnow().isoformat()
    }, on_conflict="symbol,validation_date").execute()



def get_option_history_window(
    symbol: str, 
    expiry_date: date, 
    min_strike: float, 
    max_strike: float, 
    days: int = 10
) -> list[dict]:
    """
    Fetch historical daily snapshots (3:25 PM) for a specific strike range.
    Useful for 'Centered Window' analysis of premiums and OI shifts.
    """
    resp = (
        get_client()
        .table("options_snapshots")
        .select("snapshot_date,strike,option_type,premium_close,oi,iv")
        .eq("symbol", symbol)
        .eq("expiry_date", str(expiry_date))
        .gte("strike", min_strike)
        .lte("strike", max_strike)
        .order("snapshot_date", desc=True)
        .limit(1000) # Safety limit for large windows
        .execute()
    )
    # Group by date for easier AI consumption
    return resp.data


# ─────────────────────────────────────────────────────────────────────────────
# analysis_sessions
# ─────────────────────────────────────────────────────────────────────────────

def create_analysis_session(session_id: str, session_date: date) -> None:
    """Create a new session record at pipeline start. Status='RUNNING'."""
    get_client().table("analysis_sessions").insert({
        "session_id":   session_id,
        "session_date": str(session_date),
        "status":       "RUNNING",
        "started_at":   datetime.utcnow().isoformat(),
    }).execute()


def update_analysis_session(session_id: str, updates: dict) -> None:
    """Patch a session row. Called at each stage completion and on pipeline end."""
    get_client().table("analysis_sessions").update(updates).eq("session_id", session_id).execute()


def get_analysis_session(session_id: str) -> dict | None:
    """Fetch a session by its ID."""
    resp = (
        get_client()
        .table("analysis_sessions")
        .select("*")
        .eq("session_id", session_id)
        .execute()
    )
    return resp.data[0] if resp.data else None


def get_monthly_claude_spend() -> float:
    """
    Return total Claude spend in USD for the current calendar month.
    Sums from session_claude_turns to include both nightly pipeline and manual runs.
    """
    month_start = date.today().replace(day=1)
    resp = (
        get_client()
        .table("session_claude_turns")
        .select("input_tokens,output_tokens")
        .gte("completed_at", str(month_start))
        .execute()
    )
    
    total_cost = 0.0
    for turn in resp.data:
        in_t  = turn.get("input_tokens") or 0
        out_t = turn.get("output_tokens") or 0
        cost  = (in_t / 1_000_000 * 3.00) + (out_t / 1_000_000 * 15.00)
        total_cost += cost
        
    return round(total_cost, 4)


def get_latest_session() -> dict | None:
    """Return the most recent session row. Used at startup to check for missed runs."""
    resp = (
        get_client()
        .table("analysis_sessions")
        .select("*")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


# ─────────────────────────────────────────────────────────────────────────────
# session_claude_turns
# ─────────────────────────────────────────────────────────────────────────────

def save_claude_turn(
    session_id: str,
    turn_number: int,
    turn_type: str,
    symbol: str | None,
    input_tokens: int,
    output_tokens: int,
    input_text: str,
    output_text: str,
) -> None:
    """
    Persist a Claude turn immediately after it completes.
    On restart, pipeline reconstructs conversation from these rows
    and resumes from the last saved turn — no token waste.
    """
    get_client().table("session_claude_turns").upsert({
        "session_id":    session_id,
        "turn_number":   turn_number,
        "turn_type":     turn_type,
        "symbol":        symbol,
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "input_text":    input_text,
        "output_text":   output_text,
    }, on_conflict="session_id,turn_number").execute()


def get_claude_turns(session_id: str) -> list[dict]:
    """Fetch all Claude turns for a session, ordered by turn_number."""
    resp = (
        get_client()
        .table("session_claude_turns")
        .select("*")
        .eq("session_id", session_id)
        .order("turn_number", desc=False)
        .execute()
    )
    return resp.data


# ─────────────────────────────────────────────────────────────────────────────
# trade_setups
# ─────────────────────────────────────────────────────────────────────────────

def create_trade_setup(data: dict) -> str:
    """Insert a new trade setup. Returns the generated UUID."""
    resp = get_client().table("trade_setups").insert(data).execute()
    return resp.data[0]["id"]


def update_trade_setup(setup_id: str, updates: dict) -> None:
    """Patch a trade setup (e.g., paper trade outcome, user response)."""
    updates["updated_at"] = datetime.utcnow().isoformat()
    get_client().table("trade_setups").update(updates).eq("id", setup_id).execute()


def get_trade_setups_by_date(setup_date: date) -> list[dict]:
    """Return all setups flagged on a given date, ordered by conviction descending."""
    resp = (
        get_client()
        .table("trade_setups")
        .select("*")
        .eq("setup_date", str(setup_date))
        .order("conviction_score", desc=True)
        .execute()
    )
    return resp.data


def get_trade_setup(setup_id: str) -> dict | None:
    """Fetch a single setup by ID."""
    resp = (
        get_client()
        .table("trade_setups")
        .select("*")
        .eq("id", setup_id)
        .execute()
    )
    return resp.data[0] if resp.data else None


def get_open_trade_setups() -> list[dict]:
    """
    Return setups where entry_triggered=TRUE and paper_outcome is NULL.
    Used to count available trade slots before Claude selection turn.
    """
    resp = (
        get_client()
        .table("trade_setups")
        .select("*")
        .eq("entry_triggered", True)
        .is_("paper_outcome", "null")
        .execute()
    )
    return resp.data


def get_recent_setups_for_symbol(symbol: str, limit: int = 3) -> list[dict]:
    """
    Return the most recent trade setups for a given symbol.
    Fields: setup_date, direction, conviction_score, stage, paper_outcome, setup_type.
    Used in build_stock_package to give Claude historical context on this stock.
    """
    resp = (
        get_client()
        .table("trade_setups")
        .select("setup_date,direction,conviction_score,stage,paper_outcome,setup_type")
        .eq("symbol", symbol)
        .order("setup_date", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data


# ─────────────────────────────────────────────────────────────────────────────
# watchlist_staging
# ─────────────────────────────────────────────────────────────────────────────

def upsert_watchlist_staging(data: dict) -> None:
    """Upsert a stock's watchlist state. Keyed on symbol."""
    get_client().table("watchlist_staging").upsert(data, on_conflict="symbol").execute()


def update_watchlist_staging(symbol: str, updates: dict) -> None:
    """Patch a watchlist entry (e.g., update stage or days_in_stage)."""
    get_client().table("watchlist_staging").update(updates).eq("symbol", symbol).execute()


def get_watchlist() -> list[dict]:
    """Return all watchlist entries ordered by days_in_stage descending, including lot_size."""
    resp = (
        get_client()
        .table("watchlist_staging")
        .select("*")
        .order("days_in_stage", desc=True)
        .execute()
    )
    entries = resp.data
    
    # Merge lot sizes
    lot_sizes = get_all_lot_sizes()
    for e in entries:
        e["lot_size"] = lot_sizes.get(e["symbol"])
        
    return entries


# ─────────────────────────────────────────────────────────────────────────────
# level1_shadow_tracks
# ─────────────────────────────────────────────────────────────────────────────

def create_shadow_track(data: dict) -> None:
    """Record a stock eliminated by Level 1 filter for later outcome checking."""
    get_client().table("level1_shadow_tracks").insert(data).execute()


def get_pending_shadow_tracks() -> list[dict]:
    """Return shadow tracks past their track_until_date but not yet reconciled."""
    today = str(date.today())
    resp = (
        get_client()
        .table("level1_shadow_tracks")
        .select("*")
        .lte("track_until_date", today)
        .is_("reconciled_at", "null")
        .execute()
    )
    return resp.data


def update_shadow_track(track_id: str, updates: dict) -> None:
    """Write 5-day outcome back to a shadow track row."""
    get_client().table("level1_shadow_tracks").update(updates).eq("id", track_id).execute()


# ─────────────────────────────────────────────────────────────────────────────
# Context builder helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_recent_outcomes(days: int = 7) -> list[dict]:
    """Return trade setups with a paper outcome recorded in the last N days."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    resp = (
        get_client()
        .table("trade_setups")
        .select("symbol,setup_date,direction,conviction_score,paper_outcome")
        .not_.is_("paper_outcome", "null")
        .gte("setup_date", cutoff)
        .order("setup_date", desc=True)
        .execute()
    )
    return resp.data


def get_rollover_context(analysis_date: date) -> dict | None:
    """
    Return rollover phase info for analysis_date from one symbol's continuous_oi_series row.
    All symbols share the same rollover_phase on a given date — any row works.
    """
    resp = (
        get_client()
        .table("continuous_oi_series")
        .select("rollover_phase,near_expiry,next_expiry,near_month_oi,next_month_oi,rollover_pct,pcr_near,pcr_total,max_pain")
        .eq("date", str(analysis_date))
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None
