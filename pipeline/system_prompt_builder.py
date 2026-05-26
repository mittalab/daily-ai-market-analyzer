"""
System prompt builder — constructs the exact Section 16 system prompt from
the context bundle assembled by context_builder.py.

Call:
    prompt = build_system_prompt(bundle)

Returns a plain string.  The caller (claude_session.py) wraps it in
{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}
so the stable prefix is cached across all turns.
"""
import logging
from datetime import date

logger = logging.getLogger(__name__)


# ── Rollover block (spec Section 16) ─────────────────────────────────────────

_ROLLOVER_BLOCKS: dict[str, str] = {
    "NORMAL": "(No special rollover context — normal expiry week.)",
    "ROLLOVER_WATCH": (
        "Rollover beginning. Near month OI declining partially reflects rolling, "
        "not just direction. Monitor next month OI alongside. "
        "Futures basis direction more meaningful."
    ),
    "TRANSITION": (
        "Next month now dominant. Near month OI collapse expected. "
        "Recommend next month expiry for all new trades."
    ),
    "EXPIRY": (
        "Expiry day. Near month settled — OI data is settlement noise. "
        "Use yesterday's OI as last valid reference. "
        "Weight price structure and futures basis heavily today."
    ),
}


def _rollover_block(rollover_ctx: dict | None) -> str:
    if rollover_ctx is None:
        return "(Rollover data unavailable.)"
    phase     = rollover_ctx.get("rollover_phase", "NORMAL")
    expiry    = rollover_ctx.get("near_expiry", "?")
    rollover  = rollover_ctx.get("rollover_pct")
    text      = _ROLLOVER_BLOCKS.get(phase, _ROLLOVER_BLOCKS["NORMAL"])
    pct_note  = f"  (Near expiry: {expiry} | Rollover %: {rollover:.1f}%)" if rollover else f"  (Near expiry: {expiry})"
    return f"[{phase}]: {text}\n{pct_note}"


def _open_risk(open_positions: list[dict], config: dict) -> tuple[float, float]:
    """Return (open_risk_rupees, open_risk_pct). Phase 1: no real positions yet."""
    capital = float(config.get("capital_inr", 500000))
    risk    = sum(float(p.get("risk_amount", 0) or 0) for p in open_positions)
    pct     = round(risk / capital * 100, 1) if capital else 0.0
    return risk, pct


def _recent_outcomes_block(outcomes: list[dict]) -> str:
    if not outcomes:
        return "(No completed trades in the last 7 days.)"
    lines = []
    for o in outcomes:
        sym     = o.get("symbol", "?")
        dt      = o.get("setup_date", "?")
        outcome = o.get("paper_outcome", "?")
        lines.append(f"  {sym:12} {dt}  → {outcome}")
    return "\n".join(lines)


def _watchlist_block(watchlist: list[dict]) -> str:
    if not watchlist:
        return "(Watchlist empty — first session.)"
    lines = []
    for w in watchlist:
        sym   = w.get("symbol", "?")
        stage = w.get("stage", "?")
        days  = w.get("days_in_stage", 0)
        lines.append(f"  {sym:12} stage={stage} ({days}d)")
    return "\n".join(lines)


def _open_positions_block(open_positions: list[dict]) -> str:
    if not open_positions:
        return "(No open positions.)"
    lines = []
    for p in open_positions:
        sym  = p.get("symbol", "?")
        dir_ = p.get("direction", "?")
        lines.append(f"  {sym:12} {dir_}")
    return "\n".join(lines)


def build_system_prompt(bundle: dict) -> str:
    """
    Build the Section 16 system prompt from the context bundle.
    All placeholder blocks are always filled — no dangling {variables}.
    """
    regime_result   = bundle.get("regime") or {}
    regime          = regime_result.get("regime", "UNKNOWN")
    nifty_close     = regime_result.get("nifty_close") or 0.0
    vix             = regime_result.get("vix") or 0.0
    session_date    = bundle["session_date"]
    available_slots = bundle["available_slots"]
    max_slots       = bundle["max_slots"]
    rollover_ctx    = bundle.get("rollover_context")
    config          = bundle.get("config") or {}
    open_positions  = bundle.get("open_positions") or []
    watchlist       = bundle.get("active_watchlist") or []
    outcomes        = bundle.get("recent_outcomes") or []

    open_risk, open_risk_pct = _open_risk(open_positions, config)
    date_str = session_date.strftime("%A, %d %b %Y") if isinstance(session_date, date) else str(session_date)

    prompt = f"""You are an experienced hedge fund manager and swing trading mentor \
specialising in Indian F&O markets (Nifty 50 stocks, 2-5 day holds, \
stock options only — monthly Tuesday expiry).

━━━━━ TONIGHT'S SESSION CONTEXT ━━━━━
Date          : {date_str}
Market Regime : {regime}  (Nifty {nifty_close:.1f} | VIX {vix:.2f})
Trade Slots   : {available_slots} of {max_slots} available
Capital at Risk: ₹{open_risk:,.0f} ({open_risk_pct:.1f}%)

━━━━━ ROLLOVER CONTEXT ━━━━━
{_rollover_block(rollover_ctx)}

━━━━━ PCR INTERPRETATION GUIDE ━━━━━
PCR is contrarian at extremes:
PCR < 0.7  → contrarian bearish (excessive bullishness)
PCR 0.7-1.1 → neutral
PCR > 1.3  → contrarian bullish (excessive bearishness)
Do NOT interpret high PCR as automatically bearish.

━━━━━ SIGNAL PERFORMANCE ━━━━━
[Phase 1: Signal attribution building — use general judgment.]

━━━━━ RECENT OUTCOMES ━━━━━
{_recent_outcomes_block(outcomes)}

━━━━━ ACTIVE WATCHLIST ━━━━━
{_watchlist_block(watchlist)}

━━━━━ OPEN POSITIONS ━━━━━
{_open_positions_block(open_positions)}

━━━━━ OPERATING RULES ━━━━━
Capital        : ₹5,00,000
Risk per trade : 2-3% (₹10,000-15,000)
Min RR         : 1:2 (hard gate — reject below)
Max setups     : {available_slots} Trade Ready tonight
Min DTE        : 6 trading days
Expiry         : Monthly Tuesday
Instruments    : Stock options ONLY
Sector rule    : No two stocks from same sector + same direction
Do NOT force setups — SKIP is always valid"""

    logger.info(
        "System prompt built: %d chars | regime=%s | slots=%d | rollover=%s",
        len(prompt),
        regime,
        available_slots,
        rollover_ctx.get("rollover_phase") if rollover_ctx else "N/A",
    )

    return prompt
