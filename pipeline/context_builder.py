"""
Context bundle builder — assembles all inputs Claude needs for a session.

Queries: system_config, watchlist_staging, open trade_setups, recent outcomes,
         rollover context (continuous_oi_series).

Call:
    bundle = build_context_bundle(session_date, session_id, regime_result)

Returns a dict with keys:
    session_date, session_id, config, regime,
    system_memory, active_watchlist, open_positions, recent_outcomes,
    active_directives, available_slots, max_slots, rollover_context
"""
import logging
from datetime import date

from database.queries import (
    get_all_system_config,
    get_open_trade_setups,
    get_recent_outcomes,
    get_rollover_context,
    get_watchlist,
)

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TRADES = 3


def build_context_bundle(
    session_date: date,
    session_id: str,
    regime_result: dict | None = None,
) -> dict:
    """
    Assemble everything Claude needs for tonight's session.

    regime_result is the dict returned by run_market_regime(); pass None
    during early testing — the system prompt builder handles None gracefully.
    """
    config          = get_all_system_config()
    max_slots       = int(config.get("max_concurrent_trades", _DEFAULT_MAX_TRADES))
    open_positions  = get_open_trade_setups()
    available_slots = max(0, max_slots - len(open_positions))
    watchlist       = get_watchlist()
    recent_outcomes = get_recent_outcomes(days=7)
    rollover_ctx    = get_rollover_context(session_date)

    bundle = {
        "session_date":      session_date,
        "session_id":        session_id,
        "config":            config,
        "regime":            regime_result,
        # Phase 2 placeholders (empty in Phase 1)
        "system_memory":     [],
        "active_directives": [],
        # Live data
        "active_watchlist":  watchlist,
        "open_positions":    open_positions,
        "recent_outcomes":   recent_outcomes,
        # Slot calculation
        "available_slots":   available_slots,
        "max_slots":         max_slots,
        # Rollover / expiry context
        "rollover_context":  rollover_ctx,
    }

    logger.info(
        "Context bundle built: regime=%s slots=%d/%d rollover=%s watchlist=%d outcomes=%d",
        regime_result.get("regime") if regime_result else "N/A",
        available_slots,
        max_slots,
        rollover_ctx.get("rollover_phase") if rollover_ctx else "N/A",
        len(watchlist),
        len(recent_outcomes),
    )

    return bundle
