"""
Main pipeline orchestrator — wires all stages in sequence.

Stages:
  2.5  OI Continuous Series Builder  (pipeline/oi_series_builder.py)
  2.6  Market Regime Detection        (pipeline/market_regime.py)
  3    Level 1 Filter                 (pipeline/level1_filter.py)
  4    Context Bundle Assembly        (pipeline/context_builder.py)
  5    Claude Multi-Turn Session      (pipeline/claude_session.py)

Call:
    result = run_pipeline(session_date)
"""
import logging
from datetime import date, datetime

import pytz

from database.queries import (
    create_analysis_session,
    get_analysis_session,
    get_watchlist,
    update_analysis_session,
    update_watchlist_staging,
)
from new_notifications.telegram import (
    send_pipeline_complete,
    send_validation_start,
    send_validation_complete,
)
from new_utils.stock_list import get_stock_list_for_analysis
from pipeline.claude_session import run_claude_session
from pipeline.context_builder import build_context_bundle
from pipeline.level1_filter import fetch_nse_earnings_window, run_level1_filter

from pipeline.oi_series_builder import run_oi_series_builder

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


def run_pipeline(session_date: date) -> dict:
    """
    Run the full nightly analysis pipeline for session_date.

    Returns a summary dict. Raises on critical failures (DB, missing API key).
    Non-critical stage errors (OI builder, single-stock data) are logged and
    the pipeline continues.
    """
    session_id = f"SESSION_{session_date.strftime('%Y%m%d')}"
    started_at = datetime.now(IST).isoformat()
    # Resolve target symbols (Nifty 50 + active watchlist)
    symbols = get_stock_list_for_analysis()

    all_symbol_count = len(symbols)
    logger.info("Pipeline start: %s | %d symbols", session_id, all_symbol_count)

    # ── Validation start notification ─────────────────────────────────────────
    send_validation_start(str(session_date))

    # ── Run Data Validation & Self-Healing first ──────────────────────────────
    from new_validation.run_validation import run_validation_now
    from database.queries import get_validation_state
    logger.info("Running pre-flight data validation and self-healing for all symbols...")
    try:
        run_validation_now()
    except Exception as exc:
        logger.error("Error during daily validation execution: %s", exc)

    failed_validation = []
    for symbol in symbols:
        try:
            state = get_validation_state(symbol, session_date)
            if not state or state.get("status") != "PASSED":
                failed_validation.append(symbol)
        except Exception as e:
            logger.error("Error checking validation state for %s: %s", symbol, e)
            failed_validation.append(symbol)

    symbols = [s for s in symbols if s not in failed_validation]

    # ── Validation complete notification ──────────────────────────────────────
    send_validation_complete(str(session_date), len(symbols), all_symbol_count, failed_validation)

    if failed_validation:
        logger.warning("Validation failed (and could not be healed) for symbols: %s", failed_validation)

    if not symbols:
        logger.error("All target symbols failed validation. Aborting pipeline.")
        update_analysis_session(session_id, {"status": "ABORTED"})
        return {"error": "All target symbols failed validation", "session_id": session_id}

    # ── Create / reuse session record ─────────────────────────────────────────
    try:
        create_analysis_session(session_id, session_date)
        logger.info("Created session %s", session_id)
    except Exception:
        logger.info("Session %s already exists — continuing", session_id)

    update_analysis_session(session_id, {
        "status":     "RUNNING",
        "started_at": started_at,
        "stage_statuses": {"data_validation": "COMPLETE", "started_ist": started_at},
    })

    # ── Kite token check (best-effort) ────────────────────────────────────────
    kite = None
    try:
        from new_data_ingestion.kite_oauth import get_authenticated_kite
        kite = get_authenticated_kite()
        token_ok = True
    except Exception as exc:
        logger.warning("Kite token unavailable: %s", exc)
        token_ok = False

    # ── Stage 2.5: OI Series Builder ─────────────────────────────────────────
    logger.info("Stage 2.5: OI Continuous Series Builder...")
    oi_series_symbols: list[str] = ["NIFTY_50"] + symbols
    oi_result = run_oi_series_builder(oi_series_symbols, session_date)
    logger.info(
        "OI builder: stored=%d no_futures=%d no_options=%d errors=%d",
        oi_result["stored"], len(oi_result["no_futures"]),
        len(oi_result["no_options"]), len(oi_result["errors"]),
    )
    update_analysis_session(session_id, {
        "stage_statuses": {
            "data_ingestion": "COMPLETE",
            "oi_series": "COMPLETE" if not oi_result["errors"] else "PARTIAL",
            "oi_stored":  oi_result["stored"],
        }
    })

    # ── Stage 3: Level 1 Filter ───────────────────────────────────────────────
    logger.info("Stage 3: Level 1 Filter...")
    earnings_window = fetch_nse_earnings_window(session_date)
    l1_result       = run_level1_filter(symbols, session_date, kite, earnings_window)
    level1_passed   = l1_result["passed"]
    logger.info(
        "Level 1: passed=%d eliminated=%d errors=%d",
        len(level1_passed), len(l1_result["eliminated"]), len(l1_result["errors"]),
    )
    update_analysis_session(session_id, {
        "stocks_level1_passed": len(level1_passed),
        "status": "PRE_PROCESSING_COMPLETE",
        "stage_statuses": {
            "data_ingestion": "COMPLETE",
            "oi_series":      "COMPLETE" if not oi_result["errors"] else "PARTIAL",
            "level1_filter":  "COMPLETE" if not l1_result["errors"] else "PARTIAL",
            "l1_passed":      len(level1_passed),
            "l1_eliminated":  len(l1_result["eliminated"]),
        }
    })

    if not level1_passed:
        logger.error("Level 1 passed 0 stocks — aborting pipeline")
        update_analysis_session(session_id, {"status": "ABORTED"})
        return {"error": "Level 1 passed 0 stocks", "session_id": session_id}

    # ── Stage 4: Context Bundle ───────────────────────────────────────────────
    logger.info("Stage 4: Building context bundle...")
    context_bundle = build_context_bundle(session_date, session_id, regime_result=None)

    # ── Stage 4.5: Watchlist Priority ─────────────────────────────────────────
    # Fetch active watchlist stocks and prioritize for deep analysis
    watchlist_stocks = []
    try:
        active_wl = get_watchlist()
        # Filter for active ones (WATCH/ON_RADAR) within 10 days
        watchlist_stocks = [
            {
                "symbol": r["symbol"],
                "direction": r.get("direction_bias", "AUTO"),
                "priority": "HIGH",
                "forward_to_deep": True,
                "is_watchlist_reanalysis": True,
                "days_in_stage": r.get("days_in_stage", 0)
            }
            for r in active_wl
            if r.get("current_stage") in ("WATCH", "ON_RADAR", "TRADE_READY", "MANUAL_ADD") and r.get("days_in_stage", 0) <= 10
        ]
        if watchlist_stocks:
            logger.info("Watchlist re-analysis: %d stocks prioritized", len(watchlist_stocks))
    except Exception as exc:
        logger.warning("Failed to fetch watchlist for re-analysis: %s", exc)

    # ── Stage 5: Claude Session ───────────────────────────────────────────────
    logger.info("Stage 5: Claude multi-turn session (%d stocks)...", len(level1_passed))
    claude_result = run_claude_session(context_bundle, level1_passed, session_id, watchlist_priority=watchlist_stocks)
    regime_result = claude_result["regime_result"]
    print(regime_result)

    # # ── Pipeline complete ─────────────────────────────────────────────────────
    # # FIX: Ground-truth DB validation before final notification
    # from database.queries import get_row_count
    # # Verify setups were created AFTER the pipeline started
    # started_dt = datetime.fromisoformat(started_at)
    # actual_setups = get_row_count(
    #     "trade_setups",
    #     {"setup_date": session_date},
    #     created_after=started_dt
    # )
    # trade_ready   = claude_result.get("trade_ready", 0)
    # watch         = claude_result.get("watch", 0)
    #
    # if actual_setups < (trade_ready + watch):
    #     logger.warning(
    #         "DB VALIDATION WARNING: Pipeline reported %d setups but only %d NEW setups found in DB",
    #         trade_ready + watch, actual_setups
    #     )
    # else:
    #     logger.info("DB VALIDATION OK: %d NEW setups verified in database", actual_setups)
    #
    # elapsed_min = int((datetime.now(IST) - started_dt).total_seconds() / 60)
    #
    # # Gather cost info and context warnings for notification
    # monthly_spent      = 0.0
    # budget_usd         = 50.0
    # usd_to_inr         = 84.0
    # sessions_remaining = 0
    # context_warnings: list[str] = []
    # try:
    #     from database.queries import get_monthly_claude_spend, get_all_system_config as _cfg
    #     _config    = _cfg()
    #     budget_usd = float(_config.get("claude_monthly_budget_usd", 50.0))
    #     usd_to_inr = float(_config.get("usd_to_inr_rate", 84.0))
    #     monthly_spent = get_monthly_claude_spend()
    #     remaining  = max(0.0, budget_usd - monthly_spent)
    #     sess_cost  = claude_result["cost_usd"]
    #     sessions_remaining = int(remaining / sess_cost) if sess_cost > 0 else 0
    #
    #     for dr in claude_result.get("deep_results", []):
    #         sym = dr.get("symbol", "")
    #         for note in dr.get("quality_notes", []):
    #             if any(w in note.lower() for w in ("unavailable", "no options snapshot", "no oi", "no futures")):
    #                 context_warnings.append(f"{sym}: {note[:40]}")
    # except Exception:
    #     pass
    #
    # send_pipeline_complete(
    #     trade_date=str(session_date),
    #     trade_ready=trade_ready,
    #     watch=watch,
    #     duration_mins=elapsed_min,
    #     cost_usd=claude_result["cost_usd"],
    #     monthly_spent_usd=monthly_spent,
    #     budget_usd=budget_usd,
    #     usd_to_inr=usd_to_inr,
    #     sessions_remaining=sessions_remaining,
    #     context_warnings=context_warnings[:3] or None,
    #     verified_in_db=actual_setups
    # )
    #
    # forwarded = sum(1 for s in claude_result["turn2_results"] if s.get("forward_to_deep"))
    # logger.info(
    #     "Pipeline complete: %s | cost=$%.4f | forwarded=%d",
    #     session_id, claude_result["cost_usd"], forwarded,
    # )
    #
    # return {
    #     "session_id":          session_id,
    #     "regime":              regime_result["regime"],
    #     "level1_passed":       len(level1_passed),
    #     "prescan_forwarded":   forwarded,
    #     "trade_ready":         trade_ready,
    #     "watch":               watch,
    #     "deep_results":        claude_result.get("deep_results", []),
    #     "turn1_result":        claude_result["turn1_result"],
    #     "turn2_results":       claude_result["turn2_results"],
    #     "total_input_tokens":  claude_result["total_input_tokens"],
    #     "total_output_tokens": claude_result["total_output_tokens"],
    #     "cost_usd":            claude_result["cost_usd"],
    # }
    return {}


def run_oi_series_for_indices() -> dict:
    """
    Run OI series builder for all sector indices + NIFTY_50 using last trading day.

    Intended as a standalone utility — independent of the main analysis pipeline.
    """
    from new_data_ingestion.nse_bhavcopy import last_trading_day

    session_date = last_trading_day(date.today())
    symbols: list[str] = ["NIFTY_50"]

    logger.info(
        "OI series builder — %d indices, session_date=%s",
        len(symbols), session_date,
    )

    result = run_oi_series_builder(symbols, session_date)

    logger.info(
        "OI series (indices): stored=%d no_futures=%d no_options=%d errors=%d",
        result["stored"], len(result["no_futures"]),
        len(result["no_options"]), len(result["errors"]),
    )
    return result

if __name__ == "__main__":
    run_oi_series_for_indices()