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
    update_analysis_session,
)
from integrations.nse_bhavcopy import get_nifty50_symbols
from integrations.telegram import send_pipeline_complete, send_pipeline_start
from pipeline.claude_session import run_claude_session
from pipeline.context_builder import build_context_bundle
from pipeline.level1_filter import fetch_nse_earnings_window, run_level1_filter
from pipeline.market_regime import run_market_regime
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
    symbols    = sorted(get_nifty50_symbols())

    logger.info("Pipeline start: %s | %d symbols", session_id, len(symbols))

    # ── Create / reuse session record ─────────────────────────────────────────
    try:
        create_analysis_session(session_id, session_date)
        logger.info("Created session %s", session_id)
    except Exception:
        logger.info("Session %s already exists — continuing", session_id)

    update_analysis_session(session_id, {
        "status":     "RUNNING",
        "started_at": started_at,
        "stage_statuses": {"data_ingestion": "COMPLETE", "started_ist": started_at},
    })

    # ── Kite token check (best-effort) ────────────────────────────────────────
    kite = None
    try:
        from integrations.kite_oauth import get_authenticated_kite
        kite = get_authenticated_kite()
        token_ok = True
    except Exception as exc:
        logger.warning("Kite token unavailable: %s", exc)
        token_ok = False

    send_pipeline_start(
        trade_date=str(session_date),
        token_ok=token_ok,
        snapshot_ok=True,
        bhavcopy_ok=True,
    )

    # ── Stage 2.5: OI Series Builder ─────────────────────────────────────────
    logger.info("Stage 2.5: OI Continuous Series Builder...")
    oi_result = run_oi_series_builder(symbols, session_date)
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

    # ── Stage 2.6: Market Regime ──────────────────────────────────────────────
    logger.info("Stage 2.6: Market Regime Detection...")
    regime_result = run_market_regime(session_date)
    logger.info("Regime: %s | Nifty=%.1f | VIX=%.2f",
                regime_result["regime"], regime_result["nifty_close"] or 0,
                regime_result["vix"] or 0)
    update_analysis_session(session_id, {
        "market_regime": regime_result["regime"],
        "nifty_close":   regime_result["nifty_close"],
        "vix_close":     regime_result["vix"],
        "stage_statuses": {
            "data_ingestion": "COMPLETE",
            "oi_series":      "COMPLETE" if not oi_result["errors"] else "PARTIAL",
            "regime_detect":  "COMPLETE",
            "regime_value":   regime_result["regime"],
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
            "regime_detect":  "COMPLETE",
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
    context_bundle = build_context_bundle(session_date, session_id, regime_result=regime_result)

    # ── Stage 5: Claude Session ───────────────────────────────────────────────
    logger.info("Stage 5: Claude multi-turn session (%d stocks)...", len(level1_passed))
    claude_result = run_claude_session(context_bundle, level1_passed, session_id)

    # ── Pipeline complete ─────────────────────────────────────────────────────
    # FIX: Ground-truth DB validation before final notification
    from database.queries import get_row_count
    # Verify setups were created AFTER the pipeline started
    started_dt = datetime.fromisoformat(started_at)
    actual_setups = get_row_count(
        "trade_setups", 
        {"setup_date": session_date}, 
        created_after=started_dt
    )
    trade_ready   = claude_result.get("trade_ready", 0)
    watch         = claude_result.get("watch", 0)

    if actual_setups < (trade_ready + watch):
        logger.warning(
            "DB VALIDATION WARNING: Pipeline reported %d setups but only %d NEW setups found in DB",
            trade_ready + watch, actual_setups
        )
    else:
        logger.info("DB VALIDATION OK: %d NEW setups verified in database", actual_setups)

    elapsed_min = int((datetime.now(IST) - started_dt).total_seconds() / 60)

    # Gather cost info and context warnings for notification
    monthly_spent      = 0.0
    budget_usd         = 50.0
    usd_to_inr         = 84.0
    sessions_remaining = 0
    context_warnings: list[str] = []
    try:
        from database.queries import get_monthly_claude_spend, get_all_system_config as _cfg
        _config    = _cfg()
        budget_usd = float(_config.get("claude_monthly_budget_usd", 50.0))
        usd_to_inr = float(_config.get("usd_to_inr_rate", 84.0))
        monthly_spent = get_monthly_claude_spend()
        remaining  = max(0.0, budget_usd - monthly_spent)
        sess_cost  = claude_result["cost_usd"]
        sessions_remaining = int(remaining / sess_cost) if sess_cost > 0 else 0

        for dr in claude_result.get("deep_results", []):
            sym = dr.get("symbol", "")
            for note in dr.get("quality_notes", []):
                if any(w in note.lower() for w in ("unavailable", "no options snapshot", "no oi", "no futures")):
                    context_warnings.append(f"{sym}: {note[:40]}")
    except Exception:
        pass

    send_pipeline_complete(
        trade_date=str(session_date),
        trade_ready=trade_ready,
        watch=watch,
        duration_mins=elapsed_min,
        cost_usd=claude_result["cost_usd"],
        monthly_spent_usd=monthly_spent,
        budget_usd=budget_usd,
        usd_to_inr=usd_to_inr,
        sessions_remaining=sessions_remaining,
        context_warnings=context_warnings[:3] or None,
        verified_in_db=actual_setups
    )

    forwarded = sum(1 for s in claude_result["turn2_results"] if s.get("forward_to_deep"))
    logger.info(
        "Pipeline complete: %s | cost=$%.4f | forwarded=%d",
        session_id, claude_result["cost_usd"], forwarded,
    )

    return {
        "session_id":          session_id,
        "regime":              regime_result["regime"],
        "level1_passed":       len(level1_passed),
        "prescan_forwarded":   forwarded,
        "trade_ready":         trade_ready,
        "watch":               watch,
        "deep_results":        claude_result.get("deep_results", []),
        "turn1_result":        claude_result["turn1_result"],
        "turn2_results":       claude_result["turn2_results"],
        "total_input_tokens":  claude_result["total_input_tokens"],
        "total_output_tokens": claude_result["total_output_tokens"],
        "cost_usd":            claude_result["cost_usd"],
    }
