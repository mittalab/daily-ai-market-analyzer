"""
Core database operations — one function per logical operation.
All functions use the singleton client from client.py.
Callers handle errors; queries let exceptions propagate so the pipeline
can log, alert, and decide whether to abort or continue gracefully.
"""
import logging
from datetime import date, datetime
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
    get_client().table("price_history").upsert(rows).execute()
    return len(rows)


def get_price_history(symbol: str, days: int = 180) -> list[dict]:
    """Fetch last N calendar days of OHLCV for a symbol, ordered ascending."""
    resp = (
        get_client()
        .table("price_history")
        .select("date,open,high,low,close,volume")
        .eq("symbol", symbol)
        .order("date", desc=False)
        .limit(days)
        .execute()
    )
    return resp.data


# ─────────────────────────────────────────────────────────────────────────────
# fii_dii_flows
# ─────────────────────────────────────────────────────────────────────────────

def upsert_fii_dii_flow(data: dict) -> None:
    """
    Upsert a single day's FII/DII flow. Values in Crores — not divided.
    source='LIVE' for fresh data, 'CACHED' when using previous day on fetch failure.
    """
    get_client().table("fii_dii_flows").upsert(data).execute()


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


def get_latest_fii_dii() -> dict | None:
    """Return the most recent FII/DII row. Used as fallback when today's fetch fails."""
    resp = (
        get_client()
        .table("fii_dii_flows")
        .select("*")
        .order("date", desc=True)
        .limit(1)
        .execute()
    )
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
    get_client().table("lot_sizes").upsert(rows).execute()


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


# ─────────────────────────────────────────────────────────────────────────────
# options_snapshots
# ─────────────────────────────────────────────────────────────────────────────

def upsert_options_snapshots(rows: list[dict]) -> int:
    """
    Bulk upsert 3:25 PM options snapshot rows.
    Unique constraint is (symbol, snapshot_date, expiry_date, strike, option_type).
    """
    if not rows:
        return 0
    get_client().table("options_snapshots").upsert(rows).execute()
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
    get_client().table("continuous_oi_series").upsert(data).execute()


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
# futures_continuous_series
# ─────────────────────────────────────────────────────────────────────────────

def upsert_futures_series(data: dict) -> None:
    """Upsert one row of the futures continuous series (one symbol, one date)."""
    get_client().table("futures_continuous_series").upsert(data).execute()


def get_futures_series(symbol: str, days: int = 30) -> list[dict]:
    """Fetch last N rows of futures series for a symbol, ordered ascending."""
    resp = (
        get_client()
        .table("futures_continuous_series")
        .select("*")
        .eq("symbol", symbol)
        .order("date", desc=True)
        .limit(days)
        .execute()
    )
    return list(reversed(resp.data))


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
        "output_text":   output_text,
    }).execute()


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


# ─────────────────────────────────────────────────────────────────────────────
# watchlist_staging
# ─────────────────────────────────────────────────────────────────────────────

def upsert_watchlist_staging(data: dict) -> None:
    """Upsert a stock's watchlist state. Keyed on symbol."""
    get_client().table("watchlist_staging").upsert(data, on_conflict="symbol").execute()


def get_watchlist() -> list[dict]:
    """Return all watchlist entries ordered by days_in_stage descending."""
    resp = (
        get_client()
        .table("watchlist_staging")
        .select("*")
        .order("days_in_stage", desc=True)
        .execute()
    )
    return resp.data


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
