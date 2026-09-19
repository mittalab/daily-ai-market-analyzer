"""
Key Levels API endpoint — read-only access to the key_levels table.

GET /api/key-levels
  Returns all ACTIVE support/resistance zones grouped by symbol,
  with per-symbol analysis_date = MAX(analysis_date) and overall
  earliest_analysis_date = MIN(analysis_date) across all active symbols.
  Also backfills 120 days of OHLCV per symbol for chart rendering.

Caching:
  In-memory cache keyed by 'key-levels:all', expiring at the next
  Saturday 11:00 AM IST (when the Saturday key-level job is expected
  to have written fresh rows).
"""
import json
import logging
from datetime import date, datetime, timedelta

import pytz
from fastapi import APIRouter

from database.client import get_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["key-levels"])

IST = pytz.timezone("Asia/Kolkata")

# Module-level in-memory cache — same pattern as F&O stocks cache in manual_analysis.py
_cache: dict = {}


def _next_saturday_11am_ist(now: datetime | None = None) -> datetime:
    """
    Returns the next upcoming Saturday 11:00 AM IST as a timezone-aware datetime.
      - If now < this Saturday 11:00 AM IST → returns this Saturday 11:00 AM IST
      - If now >= this Saturday 11:00 AM IST → returns next Saturday 11:00 AM IST

    Accepts an optional `now` for testability; defaults to datetime.now(IST).
    """
    if now is None:
        now = datetime.now(IST)

    dow = now.weekday()  # 0=Mon, 1=Tue, ..., 5=Sat, 6=Sun

    if dow == 5:  # Saturday
        target_today = now.replace(hour=11, minute=0, second=0, microsecond=0)
        if now < target_today:
            return target_today
        days_ahead = 7
    else:
        days_ahead = (5 - dow) % 7  # days until next Saturday (1–6 for Mon–Fri, 6 for Sun)

    return (now + timedelta(days=days_ahead)).replace(hour=11, minute=0, second=0, microsecond=0)


def _parse_confluence_flags(raw_flags: any) -> list[str]:
    """Parse confluence_flags whether stored as JSON string, list, comma-separated string, or null."""
    if isinstance(raw_flags, list):
        return [str(f) for f in raw_flags]
    if isinstance(raw_flags, str):
        cleaned = raw_flags.strip()
        if not cleaned:
            return []
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                return [str(f) for f in parsed]
        except Exception:
            pass
        return [f.strip() for f in cleaned.split(",") if f.strip()]
    return []


@router.get("/key-levels")
async def get_key_levels():
    """
    Returns all ACTIVE key levels grouped by symbol, with OHLCV data
    for chart rendering. Cached until the next Saturday 11:00 AM IST.
    """
    cache_key = "key-levels:all"
    now = datetime.now(IST)

    if cache_key in _cache:
        entry = _cache[cache_key]
        if now < entry["expires_at"]:
            logger.debug("key-levels cache hit")
            return entry["data"]
        del _cache[cache_key]

    try:
        client = get_client()
        res = (
            client
            .table("key_levels")
            .select(
                "symbol,level_type,zone_low,zone_high,conviction,"
                "touch_count,last_touch_date,confluence_flags,"
                "reasoning,breached_at,analysis_date"
            )
            .eq("status", "ACTIVE")
            .execute()
        )
    except Exception as exc:
        logger.error("Failed to fetch key_levels: %s", exc)
        return {"earliest_analysis_date": None, "stocks": []}

    rows = res.data or []

    # Group by symbol, MAX(analysis_date) per symbol
    by_symbol: dict[str, dict] = {}
    for row in rows:
        sym = row["symbol"]
        ad  = row.get("analysis_date") or ""
        if sym not in by_symbol:
            by_symbol[sym] = {
                "symbol":        sym,
                "analysis_date": ad,
                "zones":         [],
                "ohlcv_data":    [],
            }
        elif ad > by_symbol[sym]["analysis_date"]:
            by_symbol[sym]["analysis_date"] = ad

        by_symbol[sym]["zones"].append({
            "level_type":       row.get("level_type"),
            "zone_low":         float(row["zone_low"])  if row.get("zone_low")  is not None else None,
            "zone_high":        float(row["zone_high"]) if row.get("zone_high") is not None else None,
            "conviction":       row.get("conviction"),
            "touch_count":      row.get("touch_count"),
            "last_touch_date":  str(row["last_touch_date"]) if row.get("last_touch_date") else None,
            "confluence_flags": _parse_confluence_flags(row.get("confluence_flags")),
            "reasoning":        row.get("reasoning"),
            "breached_at":      str(row["breached_at"]) if row.get("breached_at") else None,
        })

    stocks = sorted(by_symbol.values(), key=lambda s: s["symbol"])

    all_dates = [s["analysis_date"] for s in stocks if s.get("analysis_date")]
    earliest_analysis_date = min(all_dates) if all_dates else None

    _backfill_ohlcv(stocks)

    response = {
        "earliest_analysis_date": earliest_analysis_date,
        "stocks": stocks,
    }

    expires_at = _next_saturday_11am_ist(now)
    _cache[cache_key] = {"data": response, "expires_at": expires_at}
    logger.info(
        "key-levels cached %d stocks until %s IST",
        len(stocks),
        expires_at.strftime("%Y-%m-%d %H:%M"),
    )

    return response


def _backfill_ohlcv(stocks: list[dict]) -> None:
    """Fetch up to 120 days of OHLCV per symbol for chart rendering."""
    symbols = [s["symbol"] for s in stocks]
    if not symbols:
        return
    try:
        client   = get_client()
        cutoff   = str(date.today() - timedelta(days=120))
        ohlcv_map: dict[str, list] = {}

        for sym in symbols:
            res = (
                client
                .table("price_history")
                .select("date,open,high,low,close,volume")
                .eq("symbol", sym)
                .gte("date", cutoff)
                .order("date", desc=True)
                .limit(120)
                .execute()
            )
            rows = list(reversed(res.data or []))
            ohlcv_map[sym] = [
                {
                    "date":   r["date"],
                    "open":   float(r["open"]),
                    "high":   float(r["high"]),
                    "low":    float(r["low"]),
                    "close":  float(r["close"]),
                    "volume": int(r.get("volume") or 0),
                }
                for r in rows
                if r.get("open") and r.get("close")
            ]
    except Exception as exc:
        logger.warning("ohlcv backfill for key-levels failed: %s", exc)
        ohlcv_map = {}

    for stock in stocks:
        stock["ohlcv_data"] = ohlcv_map.get(stock["symbol"], [])
