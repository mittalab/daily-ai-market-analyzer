"""
Dashboard API endpoints — read-only queries for the React frontend.

GET /api/today          — market context + today's setups
GET /api/setup/{id}     — full setup detail
GET /api/positions      — live Kite positions
GET /api/watchlist      — watchlist_staging entries
GET /api/system/status  — scheduler, token, DB health
"""
import logging
from datetime import date, datetime, timezone

import pytz
from fastapi import APIRouter, HTTPException

from database.queries import (
    get_latest_session,
    get_open_trade_setups,
    get_trade_setup,
    get_trade_setups_by_date,
    get_watchlist,
    keepalive,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["dashboard"])
IST    = pytz.timezone("Asia/Kolkata")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hours_since(ts_str: str | None) -> float | None:
    """Return hours elapsed since an ISO timestamp string."""
    if not ts_str:
        return None
    try:
        ts  = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return round((now - ts).total_seconds() / 3600, 1)
    except Exception:
        return None


def _is_stale(session: dict | None) -> bool:
    hours = _hours_since(session.get("completed_at") if session else None)
    return hours is None or hours > 24


# ── GET /api/today ────────────────────────────────────────────────────────────

@router.get("/today")
async def get_today():
    """
    Returns the most recent session's market context, TRADE_READY and WATCH setups.
    Includes a stale flag if data is older than 24 hours.
    """
    session = get_latest_session()
    stale   = _is_stale(session)

    market_context = None
    trade_ready: list[dict] = []
    watch:       list[dict] = []
    session_info = None

    if session:
        session_date = date.fromisoformat(str(session["session_date"]))
        setups       = get_trade_setups_by_date(session_date)
        trade_ready  = [s for s in setups if s.get("stage") == "TRADE_READY"]
        watch        = [s for s in setups if s.get("stage") == "WATCH"]

        market_context = {
            "regime":          session.get("market_regime"),
            "nifty_close":     session.get("nifty_close"),
            "vix_close":       session.get("vix_close"),
            "fii_net_flow_cr": session.get("fii_net_flow_cr"),
            "session_date":    str(session_date),
        }
        session_info = {
            "session_id":       session.get("session_id"),
            "status":           session.get("status"),
            "completed_at":     session.get("completed_at"),
            "hours_since_run":  _hours_since(session.get("completed_at")),
            "cost_usd":         session.get("claude_cost_usd"),
            "trade_ready_count": session.get("trade_ready_count"),
            "watch_count":       session.get("watch_count"),
        }

    return {
        "stale":          stale,
        "market_context": market_context,
        "trade_ready":    trade_ready,
        "watch":          watch,
        "session_info":   session_info,
    }


@router.get("/deep-analysis")
async def get_deep_analysis_turns():
    """
    Returns every deep analysis turn from today's session, including SKIPPED stocks.
    Useful for the 'Deep Analysis' tab in the UI.
    """
    session = get_latest_session()
    if not session:
        return {"turns": [], "session_id": None}
    
    session_id = session.get("session_id")
    try:
        from database.client import get_client
        import json
        res = (
            get_client()
            .table("session_claude_turns")
            .select("turn_number,symbol,output_text,completed_at")
            .eq("session_id", session_id)
            .eq("turn_type", "deep_analysis")
            .order("turn_number")
            .execute()
        )
        
        turns = []
        for row in res.data:
            analysis = {}
            try:
                analysis = json.loads(row["output_text"])
            except:
                analysis = {"error": "JSON parse failure", "raw": row["output_text"]}
                
            turns.append({
                "turn_number": row["turn_number"],
                "symbol":      row["symbol"],
                "completed_at": row["completed_at"],
                "analysis":    analysis
            })
            
        return {"turns": turns, "session_id": session_id, "session_date": str(session["session_date"])}
    except Exception as exc:
        logger.error("Failed to fetch deep analysis turns: %s", exc)
        return {"turns": [], "error": str(exc)}


# ── GET /api/setup/{setup_id} ─────────────────────────────────────────────────

@router.get("/setup/{setup_id}")
async def get_setup_detail(setup_id: str):
    """Full setup detail including rationale, scoring, and paper trade status."""
    setup = get_trade_setup(setup_id)
    if not setup:
        raise HTTPException(status_code=404, detail="Setup not found")

    paper_status = None
    if setup.get("entry_triggered"):
        paper_status = {
            "entry_triggered":    setup.get("entry_triggered"),
            "entry_date":         setup.get("entry_date"),
            "actual_entry_price": setup.get("actual_entry_price"),
            "paper_outcome":      setup.get("paper_outcome"),
            "paper_exit_date":    setup.get("paper_exit_date"),
            "paper_exit_price":   setup.get("paper_exit_price"),
            "paper_pnl_inr":      setup.get("paper_pnl_inr"),
            "paper_holding_days": setup.get("paper_holding_days"),
        }

    return {**setup, "paper_status": paper_status}


# ── GET /api/positions ─────────────────────────────────────────────────────────

@router.get("/positions")
async def get_positions():
    """
    Live Kite positions for open paper trades.
    Returns open setups with approximate P&L based on latest price_history close.
    Falls back gracefully if Kite token is unavailable.
    """
    open_setups = get_open_trade_setups()
    positions   = []

    for s in open_setups:
        symbol      = s.get("symbol")
        entry_price = s.get("actual_entry_price")
        lots        = s.get("lots") or 0
        lot_size    = s.get("lot_size") or 0

        # Try to get latest price from price_history (available without Kite token)
        from database.queries import get_price_history
        rows = get_price_history(symbol, days=2)
        current_price = float(rows[-1]["close"]) if rows else None

        pnl_estimate = None
        if current_price and entry_price and lots and lot_size:
            pnl_estimate = round(
                (current_price - float(entry_price)) * lots * lot_size, 2
            )

        positions.append({
            "setup_id":      s["id"],
            "symbol":        symbol,
            "direction":     s.get("direction"),
            "option_type":   s.get("option_type"),
            "strike":        s.get("strike"),
            "expiry_date":   s.get("expiry_date"),
            "lots":          lots,
            "lot_size":      lot_size,
            "entry_date":    s.get("entry_date"),
            "entry_price":   entry_price,
            "current_price": current_price,
            "pnl_estimate":  pnl_estimate,
            "stage":         s.get("stage"),
        })

    return {"positions": positions, "count": len(positions)}


# ── GET /api/watchlist ────────────────────────────────────────────────────────

@router.get("/watchlist")
async def get_watchlist_entries():
    """Watchlist staging entries ordered by days_in_stage descending."""
    entries = get_watchlist()
    return {"watchlist": entries, "count": len(entries)}


# ── POST /api/watchlist ───────────────────────────────────────────────────────

@router.post("/watchlist")
async def add_to_watchlist(body: dict):
    """Add a symbol to watchlist_staging from the manual analysis screen."""
    symbol = str(body.get("symbol", "")).strip().upper()
    if not symbol:
        raise HTTPException(status_code=422, detail="symbol is required")

    from database.queries import upsert_watchlist_staging
    upsert_watchlist_staging({
        "symbol":        symbol,
        "current_stage": "MANUAL_ADD",
        "days_in_stage": 0,
    })
    logger.info("Watchlist: manually added %s", symbol)
    return {"status": "added", "symbol": symbol}


# ── GET /api/system/status ────────────────────────────────────────────────────

@router.get("/system/status")
async def system_status():
    """Health dashboard: last run, next run, token status, scheduler jobs."""
    from main import scheduler

    # DB connectivity
    db_ok = keepalive()

    # Last pipeline session
    session = get_latest_session()

    # Kite token status
    kite_info: dict = {"valid": False, "expires_at": None, "hours_remaining": None}
    try:
        from database.queries import get_kite_token
        row = get_kite_token()
        if row:
            expires_at = row.get("expires_at")
            kite_info["expires_at"] = expires_at
            hours_left = None
            if expires_at:
                exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                hours_left = round((exp - now).total_seconds() / 3600, 1)
            kite_info["valid"]           = (hours_left or 0) > 0
            kite_info["hours_remaining"] = hours_left
    except Exception as exc:
        logger.warning("Kite token check failed: %s", exc)

    # Scheduler jobs
    jobs = []
    try:
        for job in scheduler.get_jobs():
            next_run = job.next_run_time
            jobs.append({
                "id":       job.id,
                "name":     job.name,
                "next_run": next_run.isoformat() if next_run else None,
            })
    except Exception:
        pass

    # Cost summary
    cost_info: dict = {
        "monthly_spent_usd": None,
        "budget_usd":        None,
        "budget_pct":        None,
        "last_session_cost": session.get("claude_cost_usd") if session else None,
        "session_turns":     None,
    }
    try:
        import json as _json, os as _os
        from database.queries import get_monthly_claude_spend, get_all_system_config
        _config    = get_all_system_config()
        budget     = float(_config.get("claude_monthly_budget_usd", 50.0))
        spent      = get_monthly_claude_spend()
        cost_info.update({
            "monthly_spent_usd": round(spent, 4),
            "budget_usd":        budget,
            "budget_pct":        round(spent / budget * 100, 1) if budget > 0 else 0,
        })
        # Load last session cost JSON for turn breakdown
        if session:
            _date_str  = str(session.get("session_date", "")).replace("-", "")
            _cost_file = _os.path.join(
                _os.path.dirname(_os.path.dirname(__file__)),
                "logs", f"session_cost_{_date_str}.json"
            )
            if _os.path.exists(_cost_file):
                with open(_cost_file, encoding="utf-8") as _f:
                    _cost_data = _json.load(_f)
                cost_info["session_turns"]   = _cost_data.get("turns")
                cost_info["session_totals"]  = _cost_data.get("totals")
                cost_info["context_quality"] = _cost_data.get("context_quality")
                cost_info["regime"]          = _cost_data.get("regime")
    except Exception as exc:
        logger.warning("Cost info fetch failed: %s", exc)

    return {
        "database":        {"connected": db_ok},
        "kite_token":      kite_info,
        "last_pipeline":   {
            "session_id":       session.get("session_id") if session else None,
            "session_date":     session.get("session_date") if session else None,
            "status":           session.get("status") if session else None,
            "completed_at":     session.get("completed_at") if session else None,
            "hours_since_run":  _hours_since(session.get("completed_at") if session else None),
            "cost_usd":         session.get("claude_cost_usd") if session else None,
        },
        "cost":            cost_info,
        "scheduler_jobs":  jobs,
        "server_time_ist": datetime.now(IST).isoformat(),
    }
