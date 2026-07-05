"""
Morning Brief generator — Telegram LOUD message sent at 7 AM.

Reads yesterday's pipeline output from Supabase and formats two messages:
  Message 1 (LOUD): Market context + TRADE_READY setups
  Message 2 (SILENT): WATCH setups + dashboard link

Called by the scheduler job registered dynamically at end of pipeline.
"""
import logging
from datetime import date, datetime

import pytz
from pandas import bdate_range

from database.client import get_client
from database.queries import (
    get_dashboard_url, 
    get_trade_setups_by_date, 
    get_latest_fii_dii,
    get_watchlist,
    get_recent_setups_for_symbol
)
from new_notifications.telegram import send_loud, send_silent

logger = logging.getLogger(__name__)

IST     = pytz.timezone("Asia/Kolkata")
DIVIDER = "━━━━━━━━━━━━━━━━━━"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _trading_days(start: date, end: date) -> int:
    """Approximate trading days between two dates (weekdays only — no holiday calendar)."""
    if end <= start:
        return 0
    return len(bdate_range(start=start, end=end, inclusive="right"))


def _fmt_inr(v) -> str:
    if v is None:
        return "N/A"
    return f"₹{int(v):,}"


def _fmt_price(v) -> str:
    """Format premium price — two decimals if fractional, else integer."""
    if v is None:
        return "N/A"
    f = float(v)
    return f"{f:.0f}" if f == int(f) else f"{f:.1f}"


def _vix_emoji(vix) -> str:
    if vix is None:
        return ""
    v = float(vix)
    if v < 15:
        return "🟢"
    if v <= 20:
        return "🟡"
    return "🔴"


def _iv_emoji(assessment: str | None) -> str:
    return {"LOW": "✅", "MEDIUM": "⚠️", "HIGH": "🔴"}.get(assessment or "", "")


def _fii_fmt(v) -> str:
    if v is None:
        return "N/A"
    cr = float(v)
    sign = "+" if cr >= 0 else "-"
    return f"{sign}₹{abs(cr):,.0f} Cr"


def _get_session_for_date(session_date: date) -> dict | None:
    """Fetch the analysis session closest to session_date (latest before or on that date)."""
    resp = (
        get_client()
        .table("analysis_sessions")
        .select("market_trend,market_volatility,market_structure,nifty_close,vix_close,fii_net_flow_cr,session_date,status")
        .lte("session_date", str(session_date))
        .order("session_date", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


# ── Message builders ──────────────────────────────────────────────────────────


def _build_setup_block_crisp(n: int, s: dict) -> str:
    """Format a single TRADE_READY setup with full details per Turn 3+ spec."""
    symbol     = s.get("symbol", "?")
    direction  = s.get("direction", "")
    score      = s.get("conviction_score", "?")
    setup_type = s.get("setup_type", "Setup")
    instrument = s.get("instrument") or "OPTIONS"
    
    entry_low  = s.get("entry_zone_low")
    entry_high = s.get("entry_zone_high")
    sl         = s.get("stop_loss_premium")
    t1         = s.get("target_1_premium")
    t2         = s.get("target_2_premium")
    lots       = s.get("lots")
    max_risk   = s.get("max_risk_inr")
    capital    = 500000.0
    risk_pct   = round((float(max_risk) / capital) * 100, 2) if max_risk else 0.0

    # Calculate R:R ratios
    rr_t1_str = ""
    rr_t2_str = ""
    if entry_low is not None and entry_high is not None and sl is not None:
        entry_mid = (float(entry_low) + float(entry_high)) / 2.0
        risk_unit = entry_mid - float(sl)
        if risk_unit > 0:
            if t1 is not None:
                rr_t1 = (float(t1) - entry_mid) / risk_unit
                rr_t1_str = f" (RR 1:{rr_t1:.1f})"
            if t2 is not None:
                rr_t2 = (float(t2) - entry_mid) / risk_unit
                rr_t2_str = f" (RR 1:{rr_t2:.1f})"

    mentor_note = s.get("mentor_explanation") or ""
    if mentor_note:
        mentor_note = mentor_note.split(".")[0] + "."
        if len(mentor_note) > 120:
            mentor_note = mentor_note[:117] + "..."
            
    from new_notifications.telegram import safe_html
    
    lines = [
        f"<b>{n}. {safe_html(symbol)} — {safe_html(direction)} — {score}/100</b>",
    ]
    
    if mentor_note:
        lines.append(f"Setup: {safe_html(mentor_note)}")
    else:
        lines.append(f"Setup: {safe_html(setup_type)}")
        
    entry_low_str  = _fmt_price(entry_low)
    entry_high_str = _fmt_price(entry_high)
    sl_str         = _fmt_price(sl)
    t1_str         = _fmt_price(t1)
    t2_str         = _fmt_price(t2)
    
    if instrument == "OPTIONS":
        strike   = s.get("strike")
        opt_type = s.get("option_type")
        expiry   = s.get("expiry_date")
        
        strike_val   = f"{int(strike)}" if strike else ""
        opt_type_val = f"{opt_type}" if opt_type else ""
        expiry_val   = f" {expiry}" if expiry else ""
        
        lines.append(f"Entry: <code>₹{entry_low_str}–{entry_high_str}</code> | SL: <code>₹{sl_str}</code>")
        lines.append(f"T1: <code>₹{t1_str}</code>{rr_t1_str} | T2: <code>₹{t2_str}</code>{rr_t2_str}")
        lines.append(f"OPTIONS: <code>{strike_val} {opt_type_val}{expiry_val} | ₹{entry_low_str}-{entry_high_str}</code>")
        if max_risk:
            lines.append(f"Risk: {safe_html(_fmt_inr(max_risk))} ({risk_pct}% capital) | {lots} lots")
        lines.append(f"Instrument: OPTIONS — Defined premium risk")
    else:
        lines.append(f"Entry: <code>₹{entry_low_str}–{entry_high_str}</code> | SL: <code>₹{sl_str}</code>")
        lines.append(f"T1: <code>₹{t1_str}</code>{rr_t1_str} | T2: <code>₹{t2_str}</code>{rr_t2_str}")
        if max_risk:
            lines.append(f"Risk: {safe_html(_fmt_inr(max_risk))} ({risk_pct}% capital) | {lots} lots")
        lines.append(f"Instrument: FUTURES — Margin defined position")
        
    return "\n".join(lines)


def generate_morning_brief(session_date: date) -> tuple[str, str]:
    """
    Build the consolidated morning brief message. Returns (msg1, "") as a tuple.
    Does NOT send — caller decides whether to send or just preview.
    """
    brief_date = datetime.now(IST).date()  # today when the 7 AM job fires
    day_str = brief_date.strftime("%a, %d %b %Y")

    setups     = get_trade_setups_by_date(session_date)
    # Sort trade ready setups by conviction score descending
    trade_ready = sorted([s for s in setups if s.get("stage") == "TRADE_READY"], 
                         key=lambda x: x.get("conviction_score") or 0, reverse=True)
    watch       = sorted([s for s in setups if s.get("stage") == "WATCH"],
                         key=lambda x: x.get("conviction_score") or 0, reverse=True)

    session = _get_session_for_date(session_date)
    fii_row = get_latest_fii_dii()

    lines = [f"🌅 <b>Morning Brief — {day_str}</b>", ""]

    # ── Market context ────────────────────────────────────────────────────────
    lines.append("<b>📊 Market Context</b>")
    if session:
        nifty   = session.get("nifty_close")
        vix     = session.get("vix_close")
        fii_cr  = session.get("fii_net_flow_cr") or (fii_row.get("fii_net_cr") if fii_row else None)
        
        trend   = session.get("market_trend")
        vol     = session.get("market_volatility")
        struct  = session.get("market_structure")
        
        nifty_s = f"{float(nifty):,.0f}" if nifty else "N/A"
        vix_s   = f"{float(vix):.1f}" if vix else "N/A"
        
        context_parts = []
        if trend: context_parts.append(trend.title())
        if vol: context_parts.append(vol.title())
        if struct: context_parts.append(struct.title())
        context_str = " | ".join(context_parts) if context_parts else "N/A"
        
        lines.append(f"Nifty: <code>{nifty_s}</code> | {context_str}\n")
        lines.append(f"VIX: <code>{vix_s}</code> {_vix_emoji(vix)} | FII: <code>{_fii_fmt(fii_cr)}</code>")
    else:
        fii_cr = fii_row.get("fii_net_cr") if fii_row else None
        lines.append(f"Session data unavailable | FII: <code>{_fii_fmt(fii_cr)}</code>")

    lines.append(f"<b>{DIVIDER}</b>")

    # ── Trade ready ───────────────────────────────────────────────────────────
    if trade_ready:
        lines.append(f"<b>🟢 TRADE READY ({len(trade_ready)})</b>")
        for i, s in enumerate(trade_ready, 1):
            lines.append("")
            lines.append(_build_setup_block_crisp(i, s))
            if i < len(trade_ready):
                lines.append(f"<b>{DIVIDER}</b>")
    else:
        lines.append("<b>🔴 No Trade Ready setups today</b>")

    lines.append(f"<b>{DIVIDER}</b>")

    # Fetch metadata for watch list re-analysis indicators
    try:
        wl_meta = {r["symbol"]: r for r in get_watchlist()}
    except Exception:
        wl_meta = {}

    # ── Watch list ────────────────────────────────────────────────────────────
    if watch:
        lines.append(f"<b>🟡 WATCHING ({len(watch)})</b>")
        for s in watch[:5]:
            symbol    = s.get("symbol", "?")
            direction = s.get("direction", "")
            score     = s.get("conviction_score", 0)
            reason    = s.get("setup_type") or "Setup under review"
            
            # Determine days in watch
            meta = wl_meta.get(symbol, {})
            days_in = meta.get("days_in_stage", 0)
            day_str = f" (D{days_in})" if days_in > 0 else ""
            
            from new_notifications.telegram import safe_html
            lines.append(f"• <code>{safe_html(symbol)}</code> ({safe_html(direction)}, Conv: <code>{score}</code>){day_str} — {safe_html(reason)}")

        if len(watch) > 5:
            lines.append(f"...and <code>{len(watch) - 5}</code> more on dashboard")

        lines.append("")
        lines.append(f"<b>{DIVIDER}</b>")

    # Accumulate and show total session cost and counts
    try:
        # Fetch the token stats from current session to display costs
        s_id = session_date.strftime("Session_%Y%m%d")
        resp = get_client().table("analysis_sessions").select("claude_cost_usd").eq("session_id", s_id).execute()
        cost_val = resp.data[0].get("claude_cost_usd") if resp.data else None
    except Exception:
        cost_val = None
        
    cost_str = f"${float(cost_val):.2f}" if cost_val else "N/A"
    lines.append(f"📊 Session cost: {cost_str} | {len(setups)} stocks analysed")
    lines.append(f'📱 Dashboard: <a href="{get_dashboard_url()}">trading.abhishekmittal.in</a>')

    msg = "\n".join(lines)
    logger.info(
        "Morning brief built: %d TRADE_READY, %d WATCH | msg=%d chars",
        len(trade_ready), len(watch), len(msg),
    )
    return msg, ""


def send_morning_brief(session_date: date) -> None:
    """Generate and send the consolidated morning brief message to Telegram."""
    msg, _ = generate_morning_brief(session_date)
    send_loud(msg)
