"""
Dashboard API endpoints — read-only queries for the React frontend.

GET  /api/today                       — market context + today's setups
GET  /api/setup/{id}                  — full setup detail
GET  /api/positions                   — live Kite positions
GET  /api/watchlist                   — watchlist_staging entries
GET  /api/system/status               — scheduler, token, DB health
GET  /api/session/today/chat-context  — plain-text context for Claude.ai paste
POST /api/chat                        — in-widget chat with Claude (Sonnet)
"""
import json
import logging
import os
import time
from datetime import date, datetime, timezone

import anthropic
import pytz
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from database.queries import (
    get_latest_fii_dii,
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

_CHAT_MODEL   = "claude-sonnet-4-6"
_CHAT_BACKOFF = [5, 10, 20]          # seconds between retries


class ChatRequest(BaseModel):
    messages:   list[dict]       # [{"role": "user"|"assistant", "content": str}]
    session_id: str | None = None


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
        
        # Sort by conviction score DESC
        setups.sort(key=lambda s: s.get("conviction_score") or 0, reverse=True)
        
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
            .select("turn_number,turn_type,symbol,output_text,completed_at")
            .eq("session_id", session_id)
            .in_("turn_type", ["market_context", "deep_analysis"])
            .order("turn_number")
            .execute()
        )
        
        turns = []
        for row in res.data:
            text = row["output_text"].strip()
            analysis = {}
            try:
                # Robust parsing for Markdown JSON blocks
                clean_text = text
                if clean_text.startswith("```"):
                    # Remove opening ```json or ```
                    first_newline = clean_text.find("\n")
                    if first_newline != -1:
                        clean_text = clean_text[first_newline+1:]
                    else:
                        clean_text = clean_text[3:]
                        
                if clean_text.endswith("```"):
                    clean_text = clean_text[:-3]
                    
                analysis = json.loads(clean_text.strip())
            except Exception as e:
                logger.warning("JSON parse failure for turn %s: %s", row["turn_number"], e)
                analysis = {"error": "JSON parse failure", "raw": text}
                
            turns.append({
                "turn_number":  row["turn_number"],
                "turn_type":    row["turn_type"],
                "symbol":       row["symbol"],
                "completed_at": row["completed_at"],
                "analysis":     analysis
            })
            
        return {"turns": turns, "session_id": session_id, "session_date": str(session["session_date"])}
    except Exception as exc:
        logger.error("Failed to fetch deep analysis turns: %s", exc)
        return {"turns": [], "error": str(exc)}


@router.get("/active-trades")
async def get_active_trades():
    """
    Fetches active Zerodha Kite holdings and F&O positions,
    and returns them merged with their latest deep analysis turn/setup.
    """
    # 1. Fetch live holdings and positions from Kite
    holdings_dict = {}
    positions_dict = {}
    
    try:
        from new_integration.kite_holdings import fetch_holdings
        h_data = fetch_holdings()
        # Merge NSE and BSE holdings
        for exch in ["NSE", "BSE"]:
            for sym, items in h_data.get(exch, {}).items():
                if items:
                    holdings_dict[sym] = items[0]
    except Exception as exc:
        logger.warning("Active Trades: failed to fetch Kite holdings: %s", exc)
        
    try:
        from new_integration.kite_positions import fetch_fo_positions
        p_data = fetch_fo_positions()
        for exch in ["NFO", "MCX"]:
            for sym, items in p_data.get(exch, {}).items():
                if items:
                    positions_dict[sym] = items[0]
    except Exception as exc:
        logger.warning("Active Trades: failed to fetch Kite positions: %s", exc)
        
    # All active symbols
    active_symbols = set(holdings_dict.keys()) | set(positions_dict.keys())
    
    if not active_symbols:
        return {"turns": [], "holdings": {}, "positions": {}}
        
    # 2. Fetch latest Deep Analysis or Trade Setup details for these symbols
    session = get_latest_session()
    session_id = session.get("session_id") if session else None
    
    today_turns = {}
    if session_id:
        try:
            from database.client import get_client
            res = (
                get_client()
                .table("session_claude_turns")
                .select("turn_number,turn_type,symbol,output_text,completed_at")
                .eq("session_id", session_id)
                .eq("turn_type", "deep_analysis")
                .in_("symbol", list(active_symbols))
                .execute()
            )
            for row in res.data:
                today_turns[row["symbol"]] = row
        except Exception as exc:
            logger.warning("Active Trades: failed to fetch today's turns: %s", exc)
            
    # For symbols not in today's turns, fetch latest trade setup from DB
    missing_symbols = active_symbols - set(today_turns.keys())
    setups_dict = {}
    if missing_symbols:
        try:
            from database.client import get_client
            for sym in missing_symbols:
                setup_res = (
                    get_client()
                    .table("trade_setups")
                    .select("*")
                    .eq("symbol", sym)
                    .order("setup_date", desc=True)
                    .limit(1)
                    .execute()
                )
                if setup_res.data:
                    setups_dict[sym] = setup_res.data[0]
        except Exception as exc:
            logger.warning("Active Trades: failed to fetch setups: %s", exc)
            
    # 3. Assemble turns list
    turns = []
    import json
    
    for sym in active_symbols:
        turn_row = today_turns.get(sym)
        setup_row = setups_dict.get(sym)
        
        analysis = {}
        completed_at = None
        turn_num = 999
        
        if turn_row:
            completed_at = turn_row["completed_at"]
            turn_num = turn_row["turn_number"]
            text = turn_row["output_text"].strip()
            try:
                clean_text = text
                if clean_text.startswith("```"):
                    first_newline = clean_text.find("\n")
                    clean_text = clean_text[first_newline+1:] if first_newline != -1 else clean_text[3:]
                if clean_text.endswith("```"):
                    clean_text = clean_text[:-3]
                analysis = json.loads(clean_text.strip())
            except Exception:
                analysis = {"error": "JSON parse failure", "symbol": sym, "stage": "WATCH"}
        elif setup_row:
            completed_at = setup_row.get("created_at")
            analysis = {
                "symbol": sym,
                "direction": setup_row.get("direction"),
                "stage": setup_row.get("stage") or "WATCH",
                "conviction_score": setup_row.get("conviction_score"),
                "adjusted_score": setup_row.get("adjusted_score"),
                "conviction_multiplier_applied": setup_row.get("conviction_multiplier"),
                "instrument_recommendation": setup_row.get("instrument") or "OPTIONS",
                "instrument_reason": setup_row.get("instrument_reason"),
                "hard_gate_triggered": setup_row.get("hard_gate_triggered"),
                "hard_gate_reason": setup_row.get("hard_gate_reason"),
                "setup_summary": {
                    "pattern_name": setup_row.get("pattern_name"),
                    "pattern_status": setup_row.get("pattern_status"),
                    "key_candle": setup_row.get("key_candle")
                },
                "key_levels": {
                    "support_zone_low": setup_row.get("spot_support_low"),
                    "support_zone_high": setup_row.get("spot_support_high"),
                    "support_basis": setup_row.get("support_basis"),
                    "stop_loss": setup_row.get("underlying_stop"),
                    "stop_loss_basis": setup_row.get("stop_loss_premium")
                },
                "trade_parameters": {
                    "entry_low": setup_row.get("spot_entry_low"),
                    "entry_high": setup_row.get("spot_entry_high"),
                    "target_1": setup_row.get("spot_target_1"),
                    "target_2": setup_row.get("spot_target_2"),
                    "rr_t2": setup_row.get("risk_reward")
                },
                "options_setup": {
                    "strike": setup_row.get("strike"),
                    "option_type": setup_row.get("option_type"),
                    "expiry": setup_row.get("expiry_date"),
                    "entry_premium_low": setup_row.get("entry_zone_low"),
                    "entry_premium_high": setup_row.get("entry_zone_high"),
                    "sl_premium": setup_row.get("stop_loss_premium"),
                    "target_1_premium": setup_row.get("target_1_premium"),
                    "target_2_premium": setup_row.get("target_2_premium")
                },
                "lots": setup_row.get("lots"),
                "lot_size": setup_row.get("lot_size"),
                "max_risk_inr": setup_row.get("max_risk_inr"),
                "risk_pct_capital": setup_row.get("risk_pct_capital"),
                "dimension_1_narrative": setup_row.get("claude_full_rationale"),
                "mentor_explanation": setup_row.get("mentor_explanation"),
                "why_could_be_wrong": setup_row.get("why_could_be_wrong"),
                "key_thing_to_watch": setup_row.get("key_learning_today")
            }
        else:
            analysis = {
                "symbol": sym,
                "direction": "LONG",
                "stage": "WATCH",
                "conviction_score": 50,
                "instrument_recommendation": "NONE"
            }
            
        turns.append({
            "turn_number": turn_num,
            "turn_type": "deep_analysis",
            "symbol": sym,
            "completed_at": completed_at,
            "analysis": analysis
        })
        
    return {
        "turns": turns,
        "holdings": holdings_dict,
        "positions": positions_dict,
        "session_id": session_id,
        "session_date": str(session["session_date"]) if session else None
    }


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


# ── GET /api/session/today/chat-context ──────────────────────────────────────

def _strip_json_fences(text: str) -> str:
    """Remove markdown ```json ... ``` fences — same logic as get_deep_analysis_turns."""
    t = text.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        t  = t[nl + 1:] if nl != -1 else t[3:]
    if t.endswith("```"):
        t = t[:-3]
    return t.strip()


def _fmt_val(v, prefix: str = "") -> str:
    """Return a display string for a value that may be None/null."""
    if v is None:
        return "Not available"
    return f"{prefix}{v}"


def _fmt_date(d) -> str:
    """Format a date or date-string as '07 Jun 2026'."""
    try:
        if isinstance(d, str):
            d = date.fromisoformat(d)
        return d.strftime("%d %b %Y")
    except Exception:
        return str(d) if d else "Not available"


def _format_chat_context(
    session: dict,
    turn1: dict | None,
    turn2: list | None,
    setups: list[dict],
    fii_row: dict | None,
) -> str:
    session_date_str = _fmt_date(session.get("session_date"))
    generated_at     = datetime.now(IST).strftime("%H:%M")

    # ── Header ────────────────────────────────────────────────────────────────
    lines: list[str] = [
        "═══════════════════════════════════════════════════",
        f"SWING TRADING ANALYSIS — {session_date_str}",
        "Complete context for Claude.ai discussion",
        f"Generated: {generated_at} IST",
        "═══════════════════════════════════════════════════",
        "",
        "INSTRUCTIONS FOR CLAUDE:",
        "You are the AI analyst who performed tonight's",
        "swing trading analysis for Indian F&O markets.",
        "The user wants to discuss, question, or challenge",
        "your recommendations. You have complete knowledge",
        "of all data and reasoning shown below.",
        "",
        "Be direct and honest. If the user raises a valid",
        "point that challenges your analysis, acknowledge",
        "it. If they provide new information (news, intraday",
        "data), factor it into your response.",
        "",
        "You are analysing Nifty 50 stocks for swing trades",
        "using stock options (CE for long, PE for short).",
        "Capital: Rs 5,00,000 | Risk per trade: 2-3%",
        "Min R:R: 1:2 | Max concurrent trades: 3",
        "",
    ]

    # ── Market context ────────────────────────────────────────────────────────
    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "MARKET CONTEXT TONIGHT",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Date        : {session_date_str}",
        f"Regime      : {_fmt_val(session.get('market_regime'))}",
        f"Nifty Close : {_fmt_val(session.get('nifty_close'))}",
        f"VIX         : {_fmt_val(session.get('vix_close'))}",
        f"FII Flow    : {_fmt_val(fii_row.get('fii_net_cr') if fii_row else None)} Cr",
        f"DII Flow    : {_fmt_val(fii_row.get('dii_net_cr') if fii_row else None)} Cr",
        "",
    ]

    if turn1:
        narrative = turn1.get("session_narrative") or ""
        risk_flags = turn1.get("risk_flags") or []
        favourable = turn1.get("favourable_setups") or "Not available"
        levels     = turn1.get("index_key_levels") or {}
        support    = levels.get("support",    "Not available")
        resistance = levels.get("resistance", "Not available")

        lines.append(f"Your Market Narrative:")
        lines.append(narrative if narrative else "Not available")
        lines.append("")
        lines.append("Risk Flags You Identified:")
        if risk_flags:
            for flag in risk_flags:
                lines.append(f"  • {flag}")
        else:
            lines.append("  Not available")
        lines.append("")
        lines.append(f"Favourable Setups : {favourable}")
        lines.append(f"Key Support       : {support}")
        lines.append(f"Key Resistance    : {resistance}")
    else:
        lines.append("Market narrative not available.")

    lines.append("")

    # ── Setups ────────────────────────────────────────────────────────────────
    total = len(setups)
    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"TONIGHT'S RECOMMENDATIONS ({total} setups)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    for s in setups:
        symbol    = s.get("symbol") or "UNKNOWN"
        direction = s.get("direction") or "?"
        stage     = s.get("stage") or "?"

        lines.append(f"─── {symbol} | {direction} | {stage} ───────────")
        lines.append(f"Conviction : {_fmt_val(s.get('conviction_score'))}/100")
        lines.append(f"Setup      : {_fmt_val(s.get('setup_type'))} ({_fmt_val(s.get('setup_maturity'))})")
        lines.append(
            f"Instrument : {_fmt_val(s.get('option_type'))} {_fmt_val(s.get('strike'))}"
            f" expiry {_fmt_date(s.get('expiry_date'))}"
            f" ({_fmt_val(s.get('days_to_expiry_at_flag'))} trading days)"
        )
        lines.append(f"IV Context : {_fmt_val(s.get('iv_assessment'))}")
        lines.append(f"Rollover   : {_fmt_val(s.get('rollover_phase'))} at time of analysis")
        lines.append("")
        lines.append(f"Entry Zone : Rs {_fmt_val(s.get('entry_zone_low'))} to Rs {_fmt_val(s.get('entry_zone_high'))}")
        lines.append(f"Stop Loss  : Rs {_fmt_val(s.get('stop_loss_premium'))}")
        lines.append(f"Target 1   : Rs {_fmt_val(s.get('target_1_premium'))} (50% exit)")
        lines.append(f"Target 2   : Rs {_fmt_val(s.get('target_2_premium'))} (full exit)")
        lines.append(f"Underlying SL : Rs {_fmt_val(s.get('underlying_stop'))}")
        lines.append("")
        lines.append(f"Position   : {_fmt_val(s.get('lots'))} lots x {_fmt_val(s.get('lot_size'))}")
        lines.append(f"Max Risk   : Rs {_fmt_val(s.get('max_risk_inr'))} ({_fmt_val(s.get('risk_pct_capital'))}% of capital)")
        lines.append(f"R:R Ratio  : 1:{_fmt_val(s.get('risk_reward'))}")
        lines.append("")

        # Scoring breakdown
        sb = s.get("scoring_breakdown") or {}
        if isinstance(sb, str):
            try:
                sb = json.loads(sb)
            except Exception:
                sb = {}
        lines.append("Scoring:")
        lines.append(f"  Price Structure    {sb.get('price_structure', 'N/A')}/30")
        lines.append(f"  Momentum/Volume    {sb.get('momentum_volume', 'N/A')}/25")
        lines.append(f"  Index F&O Context  {sb.get('index_fo_context', 'N/A')}/25")
        lines.append(f"  Stock F&O          {sb.get('stock_fo', 'N/A')}/10")
        lines.append(f"  Market Context     {sb.get('market_context', 'N/A')}/10")
        lines.append(f"  TOTAL              {_fmt_val(s.get('conviction_score'))}/100")
        lines.append("")

        # Signals
        signals = s.get("signals_contributing") or []
        lines.append("Signals That Contributed:")
        if signals:
            for sig in signals:
                lines.append(f"  • {sig}")
        else:
            lines.append("  Not available")
        lines.append("")

        lines.append("Your Full Analysis:")
        lines.append(s.get("claude_full_rationale") or "Not available")
        lines.append("")
        lines.append("Mentor Explanation:")
        lines.append(s.get("mentor_explanation") or "Not available")
        lines.append("")
        lines.append("Why This Could Be Wrong:")
        lines.append(s.get("why_could_be_wrong") or "Not available")
        lines.append("")
        lines.append("Key Learning:")
        lines.append(s.get("key_learning_today") or "Not available")
        lines.append("")
        paper = s.get("paper_outcome") or "Monitoring"
        lines.append(f"Paper Trade Status: {paper}")
        lines.append("")

    # ── Pre-scan summary ──────────────────────────────────────────────────────
    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "PRE-SCAN SUMMARY",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Stocks Level 1 passed : {_fmt_val(session.get('stocks_level1_passed'))}",
    ]

    forwarded: list[dict] = []
    skipped:   list[dict] = []
    if turn2:
        for stock in turn2:
            if stock.get("forward_to_deep"):
                forwarded.append(stock)
            else:
                skipped.append(stock)
    deep_count = len([s for s in setups])  # one setup per deep-analysed symbol

    lines.append(f"Forwarded for deep    : {len(forwarded)}")
    lines.append(f"Deep analysed         : {deep_count}")
    lines.append("")

    lines.append("Forwarded stocks:")
    if forwarded:
        for stock in forwarded:
            sym  = stock.get("symbol", "?")
            dire = stock.get("direction", "?")
            pri  = stock.get("priority", "?")
            lines.append(f"  {sym} - {dire} - {pri}")
    else:
        lines.append("  Not available")
    lines.append("")

    lines.append("Notable skips:")
    if skipped:
        for stock in skipped[:10]:
            sym    = stock.get("symbol", "?")
            reason = stock.get("pre_scan_reasoning") or "No reason given"
            # Keep to one line
            reason = reason.replace("\n", " ")[:120]
            lines.append(f"  {sym}: {reason}")
    else:
        lines.append("  Not available")
    lines.append("")

    # ── Session info ──────────────────────────────────────────────────────────
    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "SESSION INFO",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Session ID   : {_fmt_val(session.get('session_id'))}",
        f"Session Date : {session_date_str}",
        f"Analysis Cost: ${_fmt_val(session.get('claude_cost_usd'))}",
        "",
        "═══════════════════════════════════════════════════",
        f"End of analysis context — {session_date_str}",
        "═══════════════════════════════════════════════════",
    ]

    return "\n".join(lines)


@router.get("/session/today/chat-context", response_class=PlainTextResponse)
async def get_chat_context(response: Response):
    """
    Returns plain-text analysis context formatted for pasting into Claude.ai.
    Frontend uses response headers (X-Session-Date, X-Session-Id, X-Generated-At)
    for staleness detection — HEAD requests work without downloading the body.
    """
    from database.client import get_client

    # 1. Most recent ANALYSIS_COMPLETE session
    try:
        res = (
            get_client()
            .table("analysis_sessions")
            .select("*")
            .eq("status", "ANALYSIS_COMPLETE")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("chat-context: DB error fetching session: %s", exc)
        raise HTTPException(status_code=500, detail="Database error")

    if not res.data:
        raise HTTPException(
            status_code=404,
            detail={"error": "no_session", "message": "No completed analysis found. Pipeline runs at 10 PM tonight."},
        )
    session    = res.data[0]
    session_id = session["session_id"]
    session_date = date.fromisoformat(str(session["session_date"]))

    # 2. Fetch market_context + prescan turns
    turn1_data: dict | None = None
    turn2_data: list | None = None
    try:
        turns_res = (
            get_client()
            .table("session_claude_turns")
            .select("turn_type,output_text")
            .eq("session_id", session_id)
            .in_("turn_type", ["market_context", "prescan"])
            .execute()
        )
        for row in turns_res.data:
            try:
                parsed = json.loads(_strip_json_fences(row["output_text"] or ""))
                if row["turn_type"] == "market_context":
                    turn1_data = parsed if isinstance(parsed, dict) else None
                elif row["turn_type"] == "prescan":
                    turn2_data = parsed if isinstance(parsed, list) else None
            except Exception as parse_exc:
                logger.warning("chat-context: parse failure for turn_type=%s: %s", row["turn_type"], parse_exc)
    except Exception as exc:
        logger.warning("chat-context: failed to fetch turns: %s", exc)

    # 3. Trade setups ordered by conviction DESC
    setups = get_trade_setups_by_date(session_date)
    setups.sort(key=lambda s: s.get("conviction_score") or 0, reverse=True)

    # 4. FII/DII
    fii_row: dict | None = None
    try:
        fii_row = get_latest_fii_dii()
    except Exception as exc:
        logger.warning("chat-context: FII fetch failed: %s", exc)

    # 5. Format
    text = _format_chat_context(session, turn1_data, turn2_data, setups, fii_row)

    # 6. Set headers so frontend can check session availability via HEAD
    now_ist = datetime.now(IST)
    response.headers["X-Session-Date"]  = str(session["session_date"])
    response.headers["X-Session-Id"]    = session_id
    response.headers["X-Generated-At"]  = now_ist.isoformat()

    return PlainTextResponse(content=text, headers=dict(response.headers))


# ── POST /api/chat ────────────────────────────────────────────────────────────

@router.post("/chat")
async def chat(body: ChatRequest):
    """
    In-widget chat endpoint. Accepts a message history, prepends the full analysis
    context as a cached system prompt, and returns Claude Sonnet's reply.

    Stateless — caller is responsible for sending the full conversation history.
    History is capped at 20 exchanges to bound token use.
    """
    from database.client import get_client

    # ── Validate messages ─────────────────────────────────────────────────────
    messages = body.messages
    if not messages:
        raise HTTPException(status_code=422, detail="messages must not be empty")
    if messages[-1].get("role") != "user":
        raise HTTPException(status_code=422, detail="last message must have role=user")

    # Cap at 20 exchanges (40 messages) — keep most recent
    if len(messages) > 40:
        messages = messages[-40:]

    # Sanitise: only allow role/content keys, block anything else
    clean_messages = [
        {"role": m["role"], "content": str(m["content"])}
        for m in messages
        if m.get("role") in ("user", "assistant")
    ]
    if not clean_messages:
        raise HTTPException(status_code=422, detail="no valid messages after sanitisation")

    # ── Build system prompt (reuse chat-context data pipeline) ────────────────
    try:
        # Find the target session
        if body.session_id:
            res = (
                get_client()
                .table("analysis_sessions")
                .select("*")
                .eq("session_id", body.session_id)
                .limit(1)
                .execute()
            )
        else:
            res = (
                get_client()
                .table("analysis_sessions")
                .select("*")
                .eq("status", "ANALYSIS_COMPLETE")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
    except Exception as exc:
        logger.error("chat: DB error fetching session: %s", exc)
        raise HTTPException(status_code=500, detail="Database error")

    if not res.data:
        raise HTTPException(
            status_code=404,
            detail={"error": "no_session", "message": "No completed analysis found."},
        )
    session      = res.data[0]
    session_id   = session["session_id"]
    session_date = date.fromisoformat(str(session["session_date"]))

    # Fetch turns, setups, FII/DII (same as get_chat_context)
    turn1_data: dict | None = None
    turn2_data: list | None = None
    try:
        turns_res = (
            get_client()
            .table("session_claude_turns")
            .select("turn_type,output_text")
            .eq("session_id", session_id)
            .in_("turn_type", ["market_context", "prescan"])
            .execute()
        )
        for row in turns_res.data:
            try:
                parsed = json.loads(_strip_json_fences(row["output_text"] or ""))
                if row["turn_type"] == "market_context":
                    turn1_data = parsed if isinstance(parsed, dict) else None
                elif row["turn_type"] == "prescan":
                    turn2_data = parsed if isinstance(parsed, list) else None
            except Exception:
                pass
    except Exception as exc:
        logger.warning("chat: turn fetch failed: %s", exc)

    setups = get_trade_setups_by_date(session_date)
    setups.sort(key=lambda s: s.get("conviction_score") or 0, reverse=True)

    fii_row: dict | None = None
    try:
        fii_row = get_latest_fii_dii()
    except Exception:
        pass

    system_prompt = _format_chat_context(session, turn1_data, turn2_data, setups, fii_row)

    # ── Call Claude with retry ────────────────────────────────────────────────
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    client  = anthropic.Anthropic(api_key=api_key, max_retries=0)
    last_exc: Exception | None = None

    for attempt, backoff in enumerate([0] + _CHAT_BACKOFF):
        if backoff:
            time.sleep(backoff)
        try:
            response = client.messages.create(
                model=_CHAT_MODEL,
                max_tokens=1024,
                system=[{
                    "type":          "text",
                    "text":          system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=clean_messages,
            )
            break
        except anthropic.RateLimitError as exc:
            last_exc = exc
            logger.warning("chat: rate-limit attempt %d: %s", attempt + 1, exc)
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                last_exc = exc
                logger.warning("chat: API %d attempt %d: %s", exc.status_code, attempt + 1, exc)
            else:
                raise HTTPException(status_code=exc.status_code, detail=str(exc))
        except anthropic.APIConnectionError as exc:
            last_exc = exc
            logger.warning("chat: connection error attempt %d: %s", attempt + 1, exc)
    else:
        logger.error("chat: all retries exhausted: %s", last_exc)
        raise HTTPException(status_code=503, detail="Claude API unavailable after retries")

    reply        = response.content[0].text
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    # Sonnet pricing: $3/1M input, $15/1M output
    cost_usd     = round(input_tokens / 1_000_000 * 3.0 + output_tokens / 1_000_000 * 15.0, 6)

    return {
        "reply":         reply,
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "cost_usd":      cost_usd,
        "session_id":    session_id,
    }


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


@router.get("/validate/indicators", tags=["validation"])
def validate_indicators(symbol: str, date: str | None = None):
    """
    Validation endpoint to compute stock indicators for a given date
    to compare system computations side-by-side with TradingView.
    """
    try:
        from indicators.validation import validate_indicators_vs_manual
        return validate_indicators_vs_manual(symbol, date)
    except Exception as exc:
        logger.error("Indicator validation failed for %s on %s: %s", symbol, date, exc)
        raise HTTPException(status_code=400, detail=str(exc))

