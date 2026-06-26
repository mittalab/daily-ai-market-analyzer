"""
Data presence validators for the self-healing validation loop.

Design principle: bulk range queries per symbol, not per-date round-trips.
Indices used:
  price_history       → idx_price_history_validation   (symbol, date DESC)
  futures_snapshots   → idx_futures_snapshots_validation (symbol, expiry_date, snapshot_date)
  options_snapshots   → idx_options_snapshots_validation  (symbol, expiry_date, snapshot_date)
"""
import logging
from datetime import date, timedelta

from database.queries import get_client, get_kite_token
from new_data_ingestion.kite_oauth import get_authenticated_kite

logger = logging.getLogger(__name__)


# ── Infrastructure checks ──────────────────────────────────────────────────────

def validate_kite_token() -> tuple[bool, str]:
    """Validate Kite session is active via profile API."""
    try:
        token_row = get_kite_token()
        if not token_row:
            return False, "Kite access token missing in database"
        kite = get_authenticated_kite()
        profile = kite.profile()
        return True, f"Kite token valid (User: {profile.get('user_id', 'Unknown')})"
    except Exception as exc:
        return False, f"Kite token validation failed: {exc}"


def validate_db_connectivity() -> tuple[bool, str]:
    """Validate DB client connectivity to Supabase."""
    try:
        get_client().table("system_config").select("key").limit(1).execute()
        return True, "Database connectivity OK"
    except Exception as exc:
        return False, f"Database connectivity failed: {exc}"


# ── "Already ran" sentinels ────────────────────────────────────────────────────
# These prevent re-downloading a bhavcopy that was already ingested for a date.
# If ANY row exists for that date in the table, the download already happened.

def equity_bhavcopy_ran(check_date: date) -> bool:
    """True if equity bhavcopy was already ingested for this date (any symbol in price_history)."""
    try:
        resp = (
            get_client()
            .table("price_history")
            .select("date")
            .eq("date", str(check_date))
            .neq("symbol", "INDIA_VIX")   # VIX is separate, not an equity bhavcopy symbol
            .limit(1)
            .execute()
        )
        return bool(resp.data)
    except Exception:
        return False


def fo_bhavcopy_ran(check_date: date) -> bool:
    """True if F&O bhavcopy was already ingested for this date (any row in options_snapshots)."""
    try:
        resp = (
            get_client()
            .table("options_snapshots")
            .select("snapshot_date")
            .eq("snapshot_date", str(check_date))
            .limit(1)
            .execute()
        )
        return bool(resp.data)
    except Exception:
        return False


# ── Bulk range cache ───────────────────────────────────────────────────────────

class SymbolDataCache:
    """
    Pre-fetches data presence for a symbol over a date range in 3 bulk queries.
    All per-date checks then run as O(1) set lookups.
    """

    def __init__(self, symbol: str, ohlcv_start: date, fo_start: date, end: date):
        self.symbol = symbol
        self.end = end

        # price_history: set of dates where the symbol has data
        self.ohlcv_dates: set[date] = self._fetch_ohlcv_dates(ohlcv_start, end)

        # futures_snapshots: {expiry_date → set[snapshot_date]}
        self.futures: dict[date, set[date]] = self._fetch_fo_dates(
            "futures_snapshots", "snapshot_date", fo_start, end
        )

        # options_snapshots: {expiry_date → set[snapshot_date]}
        self.options: dict[date, set[date]] = self._fetch_fo_dates(
            "options_snapshots", "snapshot_date", fo_start, end
        )

    # ── Loaders ───────────────────────────────────────────────────────────────

    def _fetch_ohlcv_dates(self, start: date, end: date) -> set[date]:
        try:
            resp = (
                get_client()
                .table("price_history")
                .select("date")
                .eq("symbol", self.symbol)
                .gte("date", str(start))
                .lte("date", str(end))
                .execute()
            )
            return {date.fromisoformat(r["date"]) for r in resp.data}
        except Exception as exc:
            logger.warning("OHLCV bulk fetch failed for %s: %s", self.symbol, exc)
            return set()

    def _fetch_fo_dates(self, table: str, date_col: str, start: date, end: date) -> dict[date, set[date]]:
        """Single query for all expiries in range → {expiry: set[snapshot_dates]}."""
        try:
            q = (
                get_client()
                .table(table)
                .select(f"{date_col},expiry_date")
                .eq("symbol", self.symbol)
                .gte(date_col, str(start))
                .lte(date_col, str(end))
            )
            if table == "options_snapshots":
                # Find a sample strike price for this symbol to limit returned rows and prevent pagination limits
                sample = (
                    get_client()
                    .table("options_snapshots")
                    .select("strike")
                    .eq("symbol", self.symbol)
                    .limit(1)
                    .execute()
                )
                if sample.data:
                    strike_val = sample.data[0]["strike"]
                    q = q.eq("strike", strike_val).eq("option_type", "CE")
                else:
                    return {}

            resp = q.execute()
            result: dict[date, set[date]] = {}
            for r in resp.data:
                exp  = date.fromisoformat(r["expiry_date"])
                snap = date.fromisoformat(r[date_col])
                result.setdefault(exp, set()).add(snap)
            return result
        except Exception as exc:
            logger.warning("%s bulk fetch failed for %s: %s", table, self.symbol, exc)
            return {}

    # ── Targeted refresh after healing ────────────────────────────────────────
    # After ingesting data for one date, add it to the in-memory cache rather
    # than re-querying the whole range.

    def mark_ohlcv_present(self, d: date) -> None:
        self.ohlcv_dates.add(d)

    def mark_futures_present(self, expiry: date, d: date) -> None:
        self.futures.setdefault(expiry, set()).add(d)

    def mark_options_present(self, expiry: date, d: date) -> None:
        self.options.setdefault(expiry, set()).add(d)


# ── Targeted point-in-time checks (used after healing to re-verify) ───────────

def point_check_ohlcv(symbol: str, check_date: date) -> bool:
    try:
        resp = (
            get_client()
            .table("price_history")
            .select("date")
            .eq("symbol", symbol)
            .eq("date", str(check_date))
            .limit(1)
            .execute()
        )
        return bool(resp.data)
    except Exception:
        return False


def point_check_futures(symbol: str, expiry: date, check_date: date) -> bool:
    try:
        resp = (
            get_client()
            .table("futures_snapshots")
            .select("snapshot_date")
            .eq("symbol", symbol)
            .eq("expiry_date", str(expiry))
            .eq("snapshot_date", str(check_date))
            .limit(1)
            .execute()
        )
        return bool(resp.data)
    except Exception:
        return False


def point_check_options(symbol: str, expiry: date, check_date: date) -> bool:
    try:
        resp = (
            get_client()
            .table("options_snapshots")
            .select("snapshot_date")
            .eq("symbol", symbol)
            .eq("expiry_date", str(expiry))
            .eq("snapshot_date", str(check_date))
            .limit(1)
            .execute()
        )
        return bool(resp.data)
    except Exception:
        return False


# ── Per-date check functions (use cache for O(1) lookups) ─────────────────────

def check_ohlcv(
    symbol: str,
    check_date: date,
    cache: SymbolDataCache,
    required_days: int,
) -> tuple[bool, str]:
    """Check OHLCV presence on check_date and depth of history up to that date."""
    if check_date not in cache.ohlcv_dates:
        return False, f"OHLCV missing for {symbol} on {check_date}"
    depth = sum(1 for d in cache.ohlcv_dates if d <= check_date)
    if depth < required_days:
        return False, f"OHLCV depth {depth}/{required_days} for {symbol} on {check_date}"
    return True, f"OHLCV OK ({depth} rows ≤ {check_date})"


def check_futures(
    symbol: str,
    check_date: date,
    expiry: date,
    cache: SymbolDataCache,
    required_days: int,
) -> tuple[bool, str]:
    """Check futures snapshot presence and depth for a specific expiry."""
    expiry_dates = cache.futures.get(expiry, set())
    if check_date not in expiry_dates:
        return False, f"Futures missing for {symbol} expiry={expiry} on {check_date}"
    depth = sum(1 for d in expiry_dates if d <= check_date)
    if depth < required_days:
        return False, f"Futures depth {depth}/{required_days} for {symbol} expiry={expiry} on {check_date}"
    return True, f"Futures OK ({depth} rows)"


def check_options(
    symbol: str,
    check_date: date,
    expiry: date,
    cache: SymbolDataCache,
    required_days: int,
) -> tuple[bool, str]:
    """Check options snapshot presence and depth for a specific expiry."""
    expiry_dates = cache.options.get(expiry, set())
    if check_date not in expiry_dates:
        return False, f"Options missing for {symbol} expiry={expiry} on {check_date}"
    depth = sum(1 for d in expiry_dates if d <= check_date)
    if depth < required_days:
        return False, f"Options depth {depth}/{required_days} for {symbol} expiry={expiry} on {check_date}"
    return True, f"Options OK ({depth} rows)"
