"""
Chart data helpers — the single source of OHLCV for every stock chart in the UI.

Used by Deep / Active (dashboard.py), Analyse (manual_analysis.py) and
Key Levels (key_levels.py) so all screens show the same history window
and apply the same row-cleaning rules.

Chart data is best-effort: failures are logged and the symbol simply gets
an empty series (the frontend renders a "no data" state) rather than
failing the whole request.
"""
import logging
from datetime import date, datetime, timedelta

import pytz

from database.client import get_client

logger = logging.getLogger(__name__)
IST    = pytz.timezone("Asia/Kolkata")

# Calendar-day lookback for spot charts (~135 trading days: enough warm-up for
# EMA50 plus the 60-candle default visible range).
CHART_LOOKBACK_DAYS = 200

# Futures snapshots older than this are irrelevant to the two active contracts.
FUTURES_LOOKBACK_DAYS = 90


def _clean_rows(rows: list[dict], keys: tuple[str, str, str, str, str, str]) -> list[dict]:
    """
    Map DB rows to the frontend OHLCVRow shape, skipping rows with missing prices.
    `keys` = (date, open, high, low, close, volume) column names.
    """
    k_date, k_open, k_high, k_low, k_close, k_vol = keys
    return [
        {
            "date":   r[k_date],
            "open":   float(r[k_open]),
            "high":   float(r[k_high]),
            "low":    float(r[k_low]),
            "close":  float(r[k_close]),
            "volume": int(r.get(k_vol) or 0),
        }
        for r in rows
        if all(r.get(k) is not None for k in (k_open, k_high, k_low, k_close))
    ]


def fetch_ohlcv_map(
    symbols: list[str],
    as_of: date,
    days: int = CHART_LOOKBACK_DAYS,
) -> dict[str, list[dict]]:
    """
    Return {symbol: [OHLCVRow, ...]} in chronological order for each symbol,
    starting `days` calendar days before `as_of`. No upper bound — candles
    after `as_of` (e.g. for an older session) are still shown.

    One query per symbol so PostgREST's default 1000-row cap can't silently
    truncate a batched query. Symbols that fail or have no data map to [].
    """
    client = get_client()
    cutoff = str(as_of - timedelta(days=days))
    out: dict[str, list[dict]] = {}

    for sym in dict.fromkeys(s for s in symbols if s):
        try:
            res = (
                client
                .table("price_history")
                .select("date,open,high,low,close,volume")
                .eq("symbol", sym)
                .gte("date", cutoff)
                .order("date", desc=False)
                .execute()
            )
            out[sym] = _clean_rows(
                res.data or [], ("date", "open", "high", "low", "close", "volume")
            )
        except Exception as exc:
            logger.warning("chart ohlcv fetch failed for %s: %s", sym, exc)
            out[sym] = []

    return out


def fetch_futures_map(symbols: list[str], as_of: date) -> dict[str, dict]:
    """
    Return {symbol: {...}} with the two active futures contracts per symbol:
      near_futures_expiry / near_futures_ohlcv
      next_futures_expiry / next_futures_ohlcv   (only if a second contract exists)

    A contract is active through its expiry day; from the next day near/next
    roll forward. "Today" (IST) is used rather than `as_of` so the tabs roll
    even while the latest session predates the expiry.

    Also returns `_session_expiries`: the [near, next] contracts as of `as_of`,
    i.e. what Claude's `near_month` / `next_month` referred to in that session.
    attach_chart_data() uses it to pin Claude's levels to the right contract
    and strips it before returning.

    Symbols with no futures data (non-F&O, or fetch failure) are omitted.
    """
    client = get_client()
    cutoff = str(as_of - timedelta(days=FUTURES_LOOKBACK_DAYS))
    active_from  = str(max(as_of, datetime.now(IST).date()))
    session_from = str(as_of)
    keys = ("snapshot_date", "open_price", "high_price", "low_price", "close_price", "volume")
    out: dict[str, dict] = {}

    for sym in dict.fromkeys(s for s in symbols if s):
        try:
            res = (
                client
                .table("futures_snapshots")
                .select("snapshot_date,expiry_date,open_price,high_price,low_price,close_price,volume")
                .eq("symbol", sym)
                .gte("snapshot_date", cutoff)
                .order("snapshot_date", desc=False)
                .execute()
            )
        except Exception as exc:
            logger.warning("chart futures fetch failed for %s: %s", sym, exc)
            continue

        rows = res.data or []
        all_expiries = sorted({r["expiry_date"] for r in rows})
        expiries = [e for e in all_expiries if e >= active_from][:2]
        if not expiries:
            continue

        entry: dict = {
            "_session_expiries": [e for e in all_expiries if e >= session_from][:2],
        }
        for prefix, expiry in zip(("near", "next"), expiries):
            entry[f"{prefix}_futures_expiry"] = expiry
            entry[f"{prefix}_futures_ohlcv"]  = _clean_rows(
                [r for r in rows if r["expiry_date"] == expiry], keys
            )
        out[sym] = entry

    return out


def attach_chart_data(
    targets: list[tuple[str, dict]],
    as_of: date,
    include_futures: bool = True,
) -> None:
    """
    Fetch chart data for every (symbol, target_dict) pair and write it into the
    target dict in place: `ohlcv_data`, plus the futures keys when available.

    If the target has a Claude `fut_setup.contract_selected`, also sets
    `fut_setup_expiry` — the actual expiry date Claude analysed — so the
    frontend draws its levels on that contract even after near/next roll.
    """
    symbols = [sym for sym, _ in targets if sym]
    if not symbols:
        return

    ohlcv_map   = fetch_ohlcv_map(symbols, as_of)
    futures_map = fetch_futures_map(symbols, as_of) if include_futures else {}

    for sym, target in targets:
        if not sym:
            continue
        target["ohlcv_data"] = ohlcv_map.get(sym, [])

        fut = dict(futures_map.get(sym, {}))
        session_expiries = fut.pop("_session_expiries", [])
        target.update(fut)

        selected = (target.get("fut_setup") or {}).get("contract_selected")
        idx = {"near_month": 0, "next_month": 1}.get(selected)
        if idx is not None and idx < len(session_expiries):
            target["fut_setup_expiry"] = session_expiries[idx]
