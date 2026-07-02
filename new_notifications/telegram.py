"""
Telegram notification sender — HTML parse mode only.

Credentials from .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Rate limit: 1 message/second to the same chat — sleep 1.1s between sends.
Max length: 4,096 chars per message — split if longer.
Retry: 3 attempts × 30s wait on failure (Section 25 error handling).
Log: message_id for every sent message.

HTML tags confirmed working: <b>, <i>, <code>
NO nesting — <b><i>text</i></b> is unreliable.
Use <code> for ALL prices and numbers (prevents phone number hyperlinking).
"""
import html
import logging
import os
import time

def safe_html(text: str) -> str:
    """Escape HTML special characters to prevent Telegram API formatting issues."""
    if not text:
        return ""
    return html.escape(str(text))

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_MAX_LENGTH = 4096
_RETRY_COUNT = 3
_RETRY_WAIT  = 30   # seconds between retries (spec Section 25)

_dashboard_url_cache: str | None = None


def _get_dashboard_url() -> str:
    """Read dashboard_url from system_config once per process, then cache."""
    global _dashboard_url_cache
    if _dashboard_url_cache is None:
        try:
            from database.queries import get_dashboard_url
            _dashboard_url_cache = get_dashboard_url()
        except Exception:
            _dashboard_url_cache = "https://trading.abhishekmittal.in"
    return _dashboard_url_cache


def _base_url() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in .env")
    return f"https://api.telegram.org/bot{token}"


def _chat_id() -> int:
    cid = os.getenv("TELEGRAM_CHAT_ID")
    if not cid:
        raise RuntimeError("TELEGRAM_CHAT_ID not set in .env")
    return int(cid)


def _send_once(text: str, silent: bool) -> int:
    """Single send attempt. Returns message_id. Raises on failure."""
    payload = {
        "chat_id":              _chat_id(),
        "text":                 text[:_MAX_LENGTH],
        "parse_mode":           "HTML",
        "disable_notification": silent,
    }
    r = requests.post(f"{_base_url()}/sendMessage", json=payload, timeout=15)
    result = r.json()
    if not result.get("ok"):
        raise ValueError(f"Telegram rejected message: {result.get('description', result)}")
    return result["result"]["message_id"]


def _send_with_retry(text: str, silent: bool) -> int | None:
    """Send with up to 3 retries × 30s wait. Returns message_id or None on total failure."""
    for attempt in range(1, _RETRY_COUNT + 1):
        try:
            mid = _send_once(text, silent)
            logger.info("Telegram message sent (id=%d, silent=%s)", mid, silent)
            return mid
        except Exception as exc:
            logger.warning("Telegram attempt %d/%d failed: %s", attempt, _RETRY_COUNT, exc)
            if attempt < _RETRY_COUNT:
                time.sleep(_RETRY_WAIT)
    logger.error("Telegram: all %d attempts failed — message dropped", _RETRY_COUNT)
    return None


def send_loud(text: str) -> int | None:
    """
    Send a Telegram message WITH sound/banner notification (LOUD).

    Use for: morning brief, token reminder, snapshot failure, critical failures.
    """
    return _send_with_retry(text, silent=False)


def send_silent(text: str) -> int | None:
    """
    Send a Telegram message WITHOUT sound (SILENT).

    Use for: pipeline start/status, bhavcopy checks, cost alerts, night messages.
    """
    return _send_with_retry(text, silent=True)


def send_pipeline_start(trade_date: str, token_ok: bool, snapshot_ok: bool, bhavcopy_ok: bool) -> int | None:
    token_sym    = "✅" if token_ok    else "❌"
    snapshot_sym = "✅" if snapshot_ok else "⚠️"
    bhavcopy_sym = "✅" if bhavcopy_ok else "⚠️"
    text = (
        f"🔄 <b>Analysis Pipeline — {trade_date}</b>\n"
        f"Token {token_sym} | Snapshot {snapshot_sym} | Bhavcopy {bhavcopy_sym}\n"
        f"Starting analysis..."
    )
    return send_silent(text)


def send_snapshot_verified(trade_date: str, rows: int, source: str) -> int | None:
    """Verified snapshot success with source info."""
    text = (
        f"📥 <b>Option snapshot VERIFIED — {trade_date}</b>\n"
        f"New rows: <code>{rows}</code> | Source: <b>{source}</b>"
    )
    return send_silent(text)


def send_claude_cost(symbol: str, input_tok: int, output_tok: int, cost_usd: float) -> int | None:
    """SILENT — notify cost of an individual deep analysis turn."""
    cost_inr = round(cost_usd * 84.0) # Heuristic for quick alert
    text = (
        f"💰 <b>Claude Deep Analysis — {symbol}</b>\n"
        f"In: <code>{input_tok}</code> | Out: <code>{output_tok}</code>\n"
        f"Cost: <code>${cost_usd:.3f}</code> (<code>₹{cost_inr}</code>)"
    )
    return send_silent(text)


def send_pipeline_complete(
    trade_date: str,
    trade_ready_count: int,
    watch_count: int,
    duration_mins: int,
    cost_usd: float,
    market_trend: str,
    market_volatility: str,
    market_structure: str,
    execution_bias: str,
    nifty_close: float | None,
    vix: float | None,
    fii_net_flow_cr: float | None,
    trade_ready_symbols: list[str],
    watch_symbols: list[str],
    monthly_spent_usd: float = 0.0,
    budget_usd: float = 50.0,
    usd_to_inr: float = 84.0,
    sessions_remaining: int = 0,
    context_warnings: list | None = None,
    verified_in_db: int | None = None,
) -> int | None:
    budget_pct  = round(monthly_spent_usd / budget_usd * 100, 1) if budget_usd > 0 else 0
    cost_inr    = round(cost_usd * usd_to_inr)
    url         = _get_dashboard_url()

    # Format FII flow
    fii_str = "N/A"
    if fii_net_flow_cr is not None:
        sign = "+" if fii_net_flow_cr >= 0 else ""
        fii_str = f"{sign}₹{abs(fii_net_flow_cr):,.0f} Cr"

    # Format VIX emoji
    vix_emoji = ""
    if vix is not None:
        vix_emoji = " 🟢" if vix < 15 else " 🟡" if vix <= 20 else " 🔴"

    nifty_str = f"{nifty_close:,.0f}" if nifty_close is not None else "N/A"
    vix_str = f"{vix:.1f}" if vix is not None else "N/A"

    # Format symbol lists (HTML escaped)
    tr_list_str = ", ".join(f"<code>{safe_html(s)}</code>" for s in trade_ready_symbols) if trade_ready_symbols else "None"
    wt_list_str = ", ".join(f"<code>{safe_html(s)}</code>" for s in watch_symbols) if watch_symbols else "None"

    warning_block = ""
    if context_warnings:
        safe_warnings = [safe_html(w) for w in context_warnings[:3]]
        warning_block = f"\n\n⚠️ <i>Warnings: {'; '.join(safe_warnings)}</i>"

    trend_str = market_trend.title() if market_trend else "Unknown"
    vol_str = market_volatility.title() if market_volatility else "Normal"
    struct_str = market_structure.title() if market_structure else "Wide"

    text = (
        f"✅ <b>Analysis Complete — {trade_date}</b>\n"
        f"Trend: <b>{safe_html(trend_str)}</b> | Vol: <code>{safe_html(vol_str)}</code> | Struct: <code>{safe_html(struct_str)}</code>\n"
        f"Bias: <code>{safe_html(execution_bias)}</code> | Nifty: <code>{nifty_str}</code> | VIX: <code>{vix_str}</code>{vix_emoji} | FII: <code>{fii_str}</code>\n"
        f"\n"
        f"🟢 <b>TRADE READY ({trade_ready_count})</b>: {tr_list_str}\n"
        f"🟡 <b>WATCH ({watch_count})</b>: {wt_list_str}\n"
        f"\n"
        f"⏱ Duration: <code>{duration_mins}min</code>\n"
        f"💰 Session Cost: <code>${cost_usd:.2f}</code> (<code>₹{cost_inr}</code>)\n"
        f"📊 MTD Spend: <code>${monthly_spent_usd:.2f}</code> of <code>${budget_usd:.0f}</code> budget (<code>{budget_pct}%</code>)\n"
        f"🔮 Est. Sessions Left: <code>{sessions_remaining}</code>"
        f"{warning_block}\n"
        f"\n"
        f'📱 <a href="{url}">View Dashboard</a>'
    )
    return send_silent(text)


def send_prescan_pipeline_complete(
    trade_date: str,
    forwarded_symbols: list[dict],
    duration_mins: int,
    cost_usd: float,
    market_trend: str,
    market_volatility: str,
    market_structure: str,
    execution_bias: str,
    nifty_close: float | None,
    vix: float | None,
    fii_net_flow_cr: float | None,
    monthly_spent_usd: float = 0.0,
    budget_usd: float = 50.0,
    usd_to_inr: float = 84.0,
    sessions_remaining: int = 0,
    context_warnings: list | None = None,
) -> int | None:
    budget_pct  = round(monthly_spent_usd / budget_usd * 100, 1) if budget_usd > 0 else 0
    cost_inr    = round(cost_usd * usd_to_inr)
    url         = _get_dashboard_url()

    # Format FII flow
    fii_str = "N/A"
    if fii_net_flow_cr is not None:
        sign = "+" if fii_net_flow_cr >= 0 else ""
        fii_str = f"{sign}₹{abs(fii_net_flow_cr):,.0f} Cr"

    # Format VIX emoji
    vix_emoji = ""
    if vix is not None:
        vix_emoji = " 🟢" if vix < 15 else " 🟡" if vix <= 20 else " 🔴"

    nifty_str = f"{nifty_close:,.0f}" if nifty_close is not None else "N/A"
    vix_str = f"{vix:.1f}" if vix is not None else "N/A"

    # Group forwarded symbols by direction
    longs = [s.get("symbol", "") for s in forwarded_symbols if s.get("direction") == "LONG"]
    shorts = [s.get("symbol", "") for s in forwarded_symbols if s.get("direction") == "SHORT"]
    
    long_list_str = ", ".join(f"<code>{safe_html(s)}</code>" for s in longs) if longs else "None"
    short_list_str = ", ".join(f"<code>{safe_html(s)}</code>" for s in shorts) if shorts else "None"

    warning_block = ""
    if context_warnings:
        safe_warnings = [safe_html(w) for w in context_warnings[:3]]
        warning_block = f"\n\n⚠️ <i>Warnings: {'; '.join(safe_warnings)}</i>"

    trend_str = market_trend.title() if market_trend else "Unknown"
    vol_str = market_volatility.title() if market_volatility else "Normal"
    struct_str = market_structure.title() if market_structure else "Wide"

    text = (
        f"✅ <b>Pre-Scan Complete — {trade_date}</b>\n"
        f"Trend: <b>{safe_html(trend_str)}</b> | Vol: <code>{safe_html(vol_str)}</code> | Struct: <code>{safe_html(struct_str)}</code>\n"
        f"Bias: <code>{safe_html(execution_bias)}</code> | Nifty: <code>{nifty_str}</code> | VIX: <code>{vix_str}</code>{vix_emoji} | FII: <code>{fii_str}</code>\n"
        f"\n"
        f"🔍 <b>FORWARDED FOR DEEP SCAN ({len(forwarded_symbols)})</b>:\n"
        f"🟢 <b>LONG ({len(longs)})</b>: {long_list_str}\n"
        f"🔴 <b>SHORT ({len(shorts)})</b>: {short_list_str}\n"
        f"\n"
        f"⏱ Duration: <code>{duration_mins}min</code>\n"
        f"💰 Session Cost: <code>${cost_usd:.2f}</code> (<code>₹{cost_inr}</code>)\n"
        f"📊 MTD Spend: <code>${monthly_spent_usd:.2f}</code> of <code>${budget_usd:.0f}</code> budget (<code>{budget_pct}%</code>)\n"
        f"🔮 Est. Sessions Left: <code>{sessions_remaining}</code>"
        f"{warning_block}\n"
        f"\n"
        f'📱 <a href="{url}">View Dashboard</a>'
    )
    return send_silent(text)


def send_snapshot_failed(trade_date: str) -> int | None:
    text = (
        f"❌ <b>Snapshot FAILED — {trade_date}</b>\n"
        f"IV unavailable for tonight's analysis.\n"
        f"Pipeline will use yesterday's IV values.\n"
        f"Check NSE connectivity if this persists."
    )
    return send_loud(text)


def send_token_reminder() -> int | None:
    """LOUD — token missing or expired, action required before 10 PM pipeline."""
    dashboard_url = _get_dashboard_url()
    text = (
        "🔑 <b>Token needs refresh before 10 PM</b>\n"
        "Kite token is missing or expired.\n"
        "Pipeline starts at <b>10:00 PM tonight</b>.\n"
        '<a href="https://api.abhishekmittal.in/kite/refresh">Refresh Token</a> · '
        f'📱 <a href="{dashboard_url}">Dashboard</a>'
    )
    return send_loud(text)


def send_kite_login_reminder() -> int | None:
    """LOUD — friendly morning reminder to generate today's Kite access token."""
    text = (
        "🌅 <b>Good Morning! Time to log in to Kite</b>\n"
        "Kite token needs renewal for today.\n"
        "Please perform Kite login now to generate a new active token:\n"
        "👉 <a href=\"https://api.abhishekmittal.in/kite/refresh\">Refresh / Log In to Kite</a>"
    )
    return send_loud(text)


def send_token_valid() -> int | None:
    """SILENT — token already valid, no action needed."""
    text = (
        "✅ <b>Kite token valid for tonight's pipeline</b>\n"
        "No action needed — pipeline starts at <b>10:00 PM</b>."
    )
    return send_silent(text)


def send_paper_trade_outcome(
    outcome: str,
    symbol: str,
    direction: str,
    option_type: str,
    strike: int | None,
    exit_price: float,
    pnl_inr: float,
    holding_days: int,
) -> int | None:
    """
    Send SILENT Telegram for all closed paper trade outcomes.
    All paper outcomes are silent — paper trades never trigger loud alerts.
    ENTRY_MISSED produces no notification.
    """
    if outcome not in ("TARGET_HIT", "SL_HIT", "EXPIRED", "CLOSED_BREAKEVEN"):
        return None
    positions_url = f"{_get_dashboard_url()}/positions"
    pnl_sign      = "+" if pnl_inr >= 0 else ""
    strike_str    = f" {strike}" if strike else ""

    if outcome == "TARGET_HIT":
        icon, label = "🎯", "Target Hit"
    elif outcome == "SL_HIT":
        icon, label = "🛑", "Stop Loss Hit"
    elif outcome == "EXPIRED":
        icon, label = "⏰", "Trade Expired"
    else:
        icon, label = "↔️", "Breakeven Exit"

    text = (
        f"{icon} <b>Paper {label} — {symbol}{strike_str} {option_type}</b>\n"
        f"Direction: {direction} | Holding: <code>{holding_days}d</code>\n"
        f"Exit: <code>₹{exit_price:.0f}</code> | P&amp;L: <code>{pnl_sign}₹{abs(pnl_inr):,.0f}</code>\n"
        f'📱 <a href="{positions_url}">View Positions</a>'
    )
    return send_silent(text)


def send_budget_exhausted(spent_usd: float, budget_usd: float, trade_date: str) -> int | None:
    text = (
        f"🚨 <b>Claude Budget Exhausted — {trade_date}</b>\n"
        f"Monthly spend: <code>${spent_usd:.2f}</code> / <code>${budget_usd:.2f}</code>\n"
        f"Pipeline aborted to prevent further spend.\n"
        f"Top up budget in system_config or wait for next month."
    )
    return send_loud(text)


def send_preflight_check_failed(failures: list, trade_date: str) -> int | None:
    failure_lines = "\n".join(f"  ❌ {f}" for f in failures)
    text = (
        f"🚨 <b>Pre-flight Check FAILED — {trade_date}</b>\n"
        f"Pipeline starts in 30 minutes. Fix now:\n"
        f"{failure_lines}"
    )
    return send_loud(text)


def send_preflight_failed(reason: str, trade_date: str) -> int | None:
    text = (
        f"🚨 <b>Pre-flight FAILED — {trade_date}</b>\n"
        f"Pipeline aborted.\n"
        f"Reason: <code>{reason[:200]}</code>"
    )
    return send_loud(text)


def send_pipeline_start(trade_date: str) -> int | None:
    text = (
        f"🚀 <b>Analysis Pipeline Started — {trade_date}</b>\n"
        f"Running nightly market validation, pre-scan checks and deep analysis"
    )
    return send_silent(text)


def send_validation_start(trade_date: str, label: str = "Key Stocks") -> int | None:
    text = (
        f"🔍 <b>Validation Started — {trade_date}</b>\n"
        f"Scope: <b>{label}</b>\n"
        f"Running data checks and self-healing..."
    )
    return send_silent(text)


def send_validation_complete(
    trade_date: str,
    passed: int,
    total: int,
    failed_symbols: list,
    label: str = "Key Stocks",
) -> int | None:
    if failed_symbols:
        cap = failed_symbols[:20]
        syms = ", ".join(f"<code>{s}</code>" for s in cap)
        more = f" (+{len(failed_symbols) - 20} more)" if len(failed_symbols) > 20 else ""
        text = (
            f"⚠️ <b>Validation Complete — {trade_date}</b>\n"
            f"Scope: <b>{label}</b>\n"
            f"Passed: <code>{passed}/{total}</code>\n"
            f"Failed: {syms}{more}"
        )
    else:
        text = (
            f"✅ <b>Validation Complete — {trade_date}</b>\n"
            f"Scope: <b>{label}</b> — all <code>{total}</code> symbols passed."
        )
    return send_silent(text)


def send_phase1_complete(
    trade_date: str,
    market_trend: str,
    market_volatility: str,
    market_structure: str,
    execution_bias: str,
    nifty_close: float | None,
    vix: float | None,
    cost_usd: float,
    mentor_notes: dict | None = None,
) -> int | None:
    # Query FII net flow
    fii_net_flow_cr = None
    try:
        from database.queries import get_latest_fii_dii
        fii_row = get_latest_fii_dii()
        if fii_row:
            fii_net_flow_cr = fii_row.get("fii_net_cr")
    except Exception:
        pass

    # Format FII flow
    fii_str = "N/A"
    if fii_net_flow_cr is not None:
        sign = "+" if fii_net_flow_cr >= 0 else "-"
        fii_str = f"{sign} ₹{abs(fii_net_flow_cr):,.0f} Cr"

    # Format VIX emoji
    vix_emoji = ""
    if vix is not None:
        vix_emoji = " 🟢" if vix < 15 else " 🟡" if vix <= 20 else " 🔴"

    nifty_str = f"{nifty_close:,.0f}" if nifty_close is not None else "N/A"
    vix_str = f"{vix:.1f}" if vix is not None else "N/A"

    trend_str = market_trend.title() if market_trend else "Unknown"
    vol_str = market_volatility.title() if market_volatility else "Normal"
    struct_str = market_structure.title() if market_structure else "Wide"

    # Extract dynamic notes
    mn = mentor_notes or {}
    lesson = mn.get("todays_key_lesson", "")
    pattern = mn.get("pattern_to_watch", "")
    
    lesson_block = ""
    if lesson:
        lesson_block = f"\n📖 <b>Key Lesson</b>: <i>{safe_html(lesson)}</i>"
        
    pattern_block = ""
    if pattern:
        pattern_block = f"\n💡 <b>Pattern to Watch</b>: <i>{safe_html(pattern)}</i>"

    text = (
        f"📝 <b>Market Context Complete — {trade_date}</b>\n"
        f"Trend: {safe_html(trend_str)} | Volatility: <code>{safe_html(vol_str)}</code> | Struct: <code>{safe_html(struct_str)}</code>\n"
        f"Struct: <code>{safe_html(struct_str)}</code>\n"
        f"Bias: <code>{safe_html(execution_bias)}</code> | Nifty: <code>{nifty_str}</code> | VIX: <code>{vix_str}</code>{vix_emoji} | FII: <code>{fii_str}</code>\n"
        f"{lesson_block}"
        f"{pattern_block}\n"
        f"\n"
        f"💰 Cost: <code>${cost_usd:.2f}</code>"
    )
    return send_silent(text[:_MAX_LENGTH])


def send_prescan_complete(
    trade_date: str,
    forwarded_symbols: list[dict],
    cost_usd: float,
) -> int | None:
    url = _get_dashboard_url()

    # Group forwarded symbols by direction
    longs = [s.get("symbol", "") for s in forwarded_symbols if s.get("direction") == "LONG"]
    shorts = [s.get("symbol", "") for s in forwarded_symbols if s.get("direction") == "SHORT"]
    
    long_list_str = ", ".join(f"<code>{safe_html(s)}</code>" for s in longs) if longs else "None"
    short_list_str = ", ".join(f"<code>{safe_html(s)}</code>" for s in shorts) if shorts else "None"

    text = (
        f"🔍 <b>Pre-Scan Complete — {trade_date}</b>\n"
        f"\n"
        f"<b>FORWARDED FOR DEEP SCAN ({len(forwarded_symbols)})</b>:\n"
        f"🟢 <b>LONG ({len(longs)})</b>: {long_list_str}\n"
        f"🔴 <b>SHORT ({len(shorts)})</b>: {short_list_str}\n"
        f"\n"
        f"💰 Cost: <code>${cost_usd:.2f}</code>\n"
        f"\n"
        f'📱 <a href="{url}">View Dashboard</a>'
    )
    return send_silent(text)


def send_deep_analysis_complete(
    trade_date: str,
    trade_ready: int,
    watch: int,
    on_radar: int,
    skipped: int,
) -> int | None:
    text = (
        f"🧠 <b>Deep Analysis Complete — {trade_date}</b>\n"
        f"🟢 Trade Ready: <code>{trade_ready}</code>  "
        f"🟡 Watch: <code>{watch}</code>  "
        f"🔵 On Radar: <code>{on_radar}</code>  "
        f"⚪ Skipped: <code>{skipped}</code>"
    )
    return send_silent(text)


def send_fii_dii_data_missing(target_date: str, found_date: str) -> int | None:
    text = (
        f"❌ <b>FII/DII Data Missing — {target_date}</b>\n"
        f"No data found for <code>{target_date}</code>.\n"
        f"Latest available: <code>{found_date}</code>"
    )
    return send_loud(text)


def verify_bot() -> dict:
    """Confirm bot token is valid. Returns bot info dict."""
    r = requests.get(f"{_base_url()}/getMe", timeout=10)
    result = r.json()
    if not result.get("ok"):
        raise ValueError(f"Invalid bot token: {result}")
    return result["result"]
