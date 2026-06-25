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
    sign = "+" if cr >= 0 else ""
    return f"{sign}₹{abs(cr):,.0f} Cr"


def _get_session_for_date(session_date: date) -> dict | None:
    """Fetch the analysis session closest to session_date (latest before or on that date)."""
    resp = (
        get_client()
        .table("analysis_sessions")
        .select("market_regime,nifty_close,vix_close,fii_net_flow_cr,session_date,status")
        .lte("session_date", str(session_date))
        .order("session_date", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def _regime_short(regime: str | None) -> str:
    """Shorten verbose regime names for Telegram."""
    if not regime:
        return "Unknown"
    return (regime
            .replace("_WIDE", " (Wide)")
            .replace("_TIGHT", " (Tight)")
            .replace("_", " ")
            .title())


# ── Message builders ──────────────────────────────────────────────────────────

def _build_setup_block(n: int, s: dict, brief_date: date) -> str:
    """Format a single TRADE_READY setup as a multi-line Telegram block."""
    symbol     = s.get("symbol", "?")
    strike     = s.get("strike")
    opt_type   = s.get("option_type", "")
    direction  = s.get("direction", "")
    conviction = s.get("conviction_score", "?")
    setup_type = s.get("setup_type", "")
    entry_low  = _fmt_price(s.get("entry_zone_low"))
    entry_high = _fmt_price(s.get("entry_zone_high"))
    sl         = _fmt_price(s.get("stop_loss_premium"))
    t1         = _fmt_price(s.get("target_1_premium"))
    t2         = _fmt_price(s.get("target_2_premium"))
    risk       = _fmt_inr(s.get("max_risk_inr"))
    rr         = s.get("risk_reward")
    expiry     = s.get("expiry_date")
    iv_val     = s.get("iv_at_flag")
    iv_assess  = s.get("iv_assessment", "UNKNOWN")

    rr_str     = f"1:{float(rr):.1f}" if rr else "N/A"
    strike_str = f"{int(strike)}" if strike else "?"
    expiry_str = date.fromisoformat(expiry).strftime("%-d %b") if expiry else "?"
    dte        = _trading_days(brief_date, date.fromisoformat(expiry)) if expiry else "?"
    iv_str     = f"{float(iv_val):.1f}%" if iv_val else "N/A"

    lines = [
        f"<b>{n}. {symbol} {strike_str} {opt_type} | {direction}</b>",
        f"Conviction: <code>{conviction}</code> | {setup_type}",
        f"Entry: <code>₹{entry_low}–{entry_high}</code> | SL: <code>₹{sl}</code>",
        f"T1: <code>₹{t1}</code> | T2: <code>₹{t2}</code>",
        f"Risk: <code>{risk}</code> | RR: <code>{rr_str}</code>",
        f"Expiry: <code>{expiry_str}</code> (<code>{dte}</code> trading days)",
        f"IV: <code>{iv_str}</code> — {iv_assess.title()} {_iv_emoji(iv_assess)}",
    ]
    return "\n".join(lines)


def _build_message1(
    brief_date: date,
    session: dict | None,
    fii_row: dict | None,
    trade_ready: list[dict],
) -> str:
    day_str = brief_date.strftime("%a, %d %b %Y")

    # ── Header ────────────────────────────────────────────────────────────────
    lines = [f"<b>🌅 Morning Brief — {day_str}</b>", ""]

    # ── Market context ────────────────────────────────────────────────────────
    lines.append("<b>📊 Market Context</b>")
    if session:
        nifty   = session.get("nifty_close")
        vix     = session.get("vix_close")
        # fii_net_flow_cr may not be populated in the session row — fall back to flows table
        fii_cr  = session.get("fii_net_flow_cr") or (fii_row.get("fii_net_cr") if fii_row else None)
        regime  = _regime_short(session.get("market_regime"))
        nifty_s = f"{float(nifty):,.0f}" if nifty else "N/A"
        vix_s   = f"{float(vix):.1f}" if vix else "N/A"
        lines.append(f"Nifty: <code>{nifty_s}</code> | Regime: {regime}")
        lines.append(f"VIX: <code>{vix_s}</code> {_vix_emoji(vix)} | FII: <code>{_fii_fmt(fii_cr)}</code>")
    else:
        # Fall back to latest FII row if session missing
        fii_cr = fii_row.get("fii_net_cr") if fii_row else None
        lines.append(f"Session data unavailable | FII: <code>{_fii_fmt(fii_cr)}</code>")

    lines.append(f"<b>{DIVIDER}</b>")

    # ── Trade ready ───────────────────────────────────────────────────────────
    if trade_ready:
        lines.append(f"<b>🟢 TRADE READY ({len(trade_ready)})</b>")
        for i, s in enumerate(trade_ready, 1):
            lines.append("")
            lines.append(_build_setup_block(i, s, brief_date))
            if i < len(trade_ready):
                lines.append(f"<b>{DIVIDER}</b>")
    else:
        lines.append("<b>🔴 No Trade Ready setups today</b>")
        regime_line = _regime_short(session.get("market_regime")) if session else "Unknown regime"
        lines.append(f"Market conditions: {regime_line} — no high-conviction setups from last night's scan.")
        lines.append(f'📱 <a href="{get_dashboard_url()}">See Watch List</a>')

    return "\n".join(lines)


def _build_message2(watch: list[dict]) -> str:
    lines: list[str] = []

    # Fetch metadata for re-analysis indicators
    try:
        from database.queries import get_watchlist, get_recent_setups_for_symbol
        wl_meta = {r["symbol"]: r for r in get_watchlist()}
    except Exception:
        wl_meta = {}

    if watch:
        lines.append(f"<b>🟡 WATCHING ({len(watch)})</b>")
        shown = watch[:5]
        for s in shown:
            symbol    = s.get("symbol", "?")
            direction = s.get("direction", "")
            score     = s.get("conviction_score", 0)
            setup_type = s.get("setup_type") or s.get("setup_maturity") or "Setup developing"
            
            # Determine days in watch and trend
            meta = wl_meta.get(symbol, {})
            days_in = meta.get("days_in_stage", 0)
            
            trend = "→"
            try:
                prev = get_recent_setups_for_symbol(symbol, limit=2)
                if len(prev) >= 2:
                    old_c = prev[1].get("conviction_score", 0)
                    if score > old_c: trend = "↑"
                    elif score < old_c: trend = "↓"
            except Exception:
                pass

            day_str = f" | D{days_in} {trend}" if days_in > 0 else ""
            
            lines.append(f"• {symbol} | {direction} | Conv: <code>{score}</code>{day_str}")
            lines.append(f"  <i>{setup_type}</i>")
        if len(watch) > 5:
            lines.append(f"...and <code>{len(watch) - 5}</code> more on dashboard")
    else:
        lines.append("🟡 No stocks on watch today")

    lines.append("")
    lines.append(f"<b>{DIVIDER}</b>")
    lines.append(f'📱 <a href="{get_dashboard_url()}">Full Analysis</a>')

    return "\n".join(lines)


# ── Public entry point ────────────────────────────────────────────────────────

def generate_morning_brief(session_date: date) -> tuple[str, str]:
    """
    Build both morning brief messages. Returns (msg1, msg2) as strings.
    Does NOT send — caller decides whether to send or just preview.
    """
    brief_date = datetime.now(IST).date()  # today when the 7 AM job fires

    setups     = get_trade_setups_by_date(session_date)
    trade_ready = [s for s in setups if s.get("stage") == "TRADE_READY"]
    watch       = [s for s in setups if s.get("stage") == "WATCH"]

    session = _get_session_for_date(session_date)
    fii_row = get_latest_fii_dii()

    msg1 = _build_message1(brief_date, session, fii_row, trade_ready)
    msg2 = _build_message2(watch)

    logger.info(
        "Morning brief built: %d TRADE_READY, %d WATCH | msg1=%d chars, msg2=%d chars",
        len(trade_ready), len(watch), len(msg1), len(msg2),
    )
    return msg1, msg2


def send_morning_brief(session_date: date) -> None:
    """Generate and send both morning brief messages to Telegram."""
    import time
    msg1, msg2 = generate_morning_brief(session_date)

    send_loud(msg1)    # LOUD — wakes phone
    time.sleep(1.1)
    send_silent(msg2)  # SILENT — no second buzz
