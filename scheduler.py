"""
APScheduler job definitions — all times in IST.

Schedule:
  06:00 daily        — Supabase keepalive
  06:30 Mon-Fri      — Morning bhavcopy (equity + FO) for last trading day, no overwrite
  07:00 daily        — Morning brief + Kite token check (loud if invalid)
  09:00 Mon-Fri      — Kite token check (notify only if invalid, trading days only)
  13:00 Mon-Fri      — Kite token check (notify only if invalid, trading days only)
  16:00 Mon-Fri      — Full validation + Claude analysis pipeline
"""
import logging
from datetime import date, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


# ── Trading day helper ─────────────────────────────────────────────────────────

def is_trading_day(for_date: date) -> bool:
    """Return True if for_date is an NSE trading day."""
    if for_date.weekday() >= 5:
        return False
    try:
        import json as _json
        import os as _os
        _map_path = _os.path.join(_os.path.dirname(__file__), "config", "sector_map.json")
        with open(_map_path, encoding="utf-8") as _f:
            _data = _json.load(_f)
        holidays = set(_data.get("holidays", []))
        return str(for_date) not in holidays
    except Exception:
        return True  # fail open


# ── Job 1: Supabase keepalive ──────────────────────────────────────────────────

def job_keepalive() -> None:
    """06:00 daily — ping Supabase to keep the free-tier connection alive."""
    from database.queries import keepalive
    ok = keepalive()
    if ok:
        logger.info("Keepalive: Supabase connection alive")
    else:
        logger.warning("Keepalive: Supabase ping failed")


# ── Job 2: Morning bhavcopy ────────────────────────────────────────────────────

def job_morning_bhavcopy() -> None:
    """
    06:30 Mon-Fri (trading day) — download equity + FO bhavcopy for the last
    trading day without overwriting rows already written by the 4 PM Kite analysis.
    Purpose: persist data for ALL listed stocks, not just the Nifty 50 subset
    that gets analysed each day.

    Telegram notifications (in order):
      1. Validation started          (send_validation_start)
      2. Validation complete + fails (send_validation_complete)
    """
    today = date.today()
    if not is_trading_day(today):
        logger.info("Morning bhavcopy skipped — %s is not a trading day", today)
        return

    from new_data_ingestion.nse_bhavcopy import last_trading_day
    from new_notifications.telegram import send_validation_start, send_validation_complete

    target_date = last_trading_day(today - timedelta(days=1))
    logger.info("Morning bhavcopy: fetching equity + FO data for %s", target_date)

    send_validation_start(str(target_date))

    # Equity + indices bhavcopy — no_overwrite preserves yesterday's Kite data
    try:
        from new_data_ingestion.ingestion_utils import ingest_today_bhavcopy
        summary = ingest_today_bhavcopy(target_date, no_overwrite=True)
        if summary["ok"]:
            logger.info(
                "Morning equity bhavcopy OK: equity=%d index=%d for %s",
                summary["equity_rows"], summary["index_rows"], target_date,
            )
        else:
            logger.warning("Morning equity bhavcopy partial errors: %s", summary.get("errors"))
    except Exception as exc:
        logger.error("Morning equity bhavcopy failed: %s", exc)

    # FO bhavcopy (options + futures for all symbols) — overwrite is fine here
    try:
        from new_data_ingestion.fo_bhavcopy import run_backfill as run_fo_backfill
        fo_summary = run_fo_backfill([target_date])
        if target_date in fo_summary.get("failed", []):
            logger.warning("Morning FO bhavcopy failed for %s", target_date)
        else:
            logger.info("Morning FO bhavcopy OK for %s", target_date)
    except Exception as exc:
        logger.error("Morning FO bhavcopy failed: %s", exc)

    # Post-ingestion validation: confirm all F&O stocks landed correctly
    try:
        from new_validation.run_validation import run_fo_stocks_validation
        passed, total, failed = run_fo_stocks_validation(target_date)
        send_validation_complete(str(target_date), passed, total, failed)
    except Exception as exc:
        logger.error("Morning FO stocks validation failed: %s", exc)
        send_validation_complete(str(target_date), 0, 0, [f"ERROR: {str(exc)[:100]}"])


# ── Job 3: Morning brief + Kite token check ────────────────────────────────────

def job_morning_brief_and_kite_check() -> None:
    """
    07:00 daily — send the morning brief for the latest session, then check
    the Kite token. Sends a LOUD reminder if the token is missing or expired.
    """
    # Morning brief
    try:
        from database.queries import get_latest_session
        from pipeline.morning_brief import send_morning_brief
        latest = get_latest_session()
        if latest:
            ref_date = date.fromisoformat(str(latest["session_date"]))
            logger.info("Morning brief for session: %s", ref_date)
            send_morning_brief(ref_date)
        else:
            logger.info("Morning brief: no completed sessions found — skipping brief")
    except Exception as exc:
        logger.error("Morning brief failed: %s", exc)

    # Kite token check
    try:
        from new_validation.data_validator import validate_kite_token
        from new_notifications.telegram import send_token_reminder
        ok, msg = validate_kite_token()
        if ok:
            logger.info("7 AM Kite check: token valid — %s", msg)
        else:
            logger.warning("7 AM Kite check: token INVALID — %s", msg)
            send_token_reminder()
    except Exception as exc:
        logger.error("7 AM Kite token check error: %s", exc)


# ── Job 4 & 5: Intraday Kite token checks ─────────────────────────────────────

def job_kite_check() -> None:
    """
    09:00 and 13:00 Mon-Fri (trading day) — validate Kite token and send
    a LOUD Telegram reminder ONLY if the token is invalid. Silent on success.
    """
    today = date.today()
    if not is_trading_day(today):
        return

    try:
        from new_validation.data_validator import validate_kite_token
        from new_notifications.telegram import send_token_reminder
        ok, msg = validate_kite_token()
        if ok:
            logger.info("Kite token check OK: %s", msg)
        else:
            logger.warning("Kite token check FAILED: %s — sending reminder", msg)
            send_token_reminder()
    except Exception as exc:
        logger.error("Kite token check error: %s", exc)


# ── Job 6: 4 PM analysis pipeline ─────────────────────────────────────────────

def job_analysis_pipeline() -> None:
    """
    16:00 Mon-Fri (trading day) — run full validation + Claude analysis.

    Telegram notifications (in order):
      1. Validation started          (send_validation_start — in orchestrator)
      2. Validation complete + fails (send_validation_complete — in orchestrator)
      3. Phase 1 / market regime     (send_phase1_complete — in claude_session)
      4. Pre-scan complete           (send_prescan_complete — in claude_session)
      5. Per-stock deep cost         (send_claude_cost — in claude_session, existing)
      6. Deep analysis complete      (send_deep_analysis_complete — in claude_session)
      7. Full analysis summary       (send_pipeline_complete — in orchestrator, existing)
    """
    from datetime import datetime
    import pytz

    IST = pytz.timezone("Asia/Kolkata")
    today = datetime.now(IST).date()

    if not is_trading_day(today):
        logger.info("Analysis pipeline skipped — %s is not a trading day", today)
        return

    # Paper trade engine runs after the main pipeline
    try:
        from pipeline.orchestrator import run_pipeline
        run_pipeline(today)
    except Exception as exc:
        logger.error("Analysis pipeline failed: %s", exc)
        try:
            from new_notifications.telegram import send_loud
            send_loud(
                f"🚨 <b>Pipeline Failed — {today}</b>\n"
                f"<code>{str(exc)[:300]}</code>"
            )
        except Exception:
            pass
        return

    try:
        from pipeline.paper_trade_engine import run_paper_trade_engine
        summary = run_paper_trade_engine(today)
        logger.info("Paper trade engine: %s", summary)
    except Exception as exc:
        logger.error("Paper trade engine failed: %s", exc)


# ── Registration ───────────────────────────────────────────────────────────────

def register_jobs(scheduler: AsyncIOScheduler) -> None:
    """
    Add all cron jobs to the provided scheduler.
    Call once at startup, after scheduler.start().
    """
    ist = {"timezone": "Asia/Kolkata"}

    # 06:00 daily — keepalive
    scheduler.add_job(
        job_keepalive,
        CronTrigger(hour=6, minute=0, **ist),
        id="keepalive",
        name="Supabase keepalive",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # 06:30 Mon-Fri — morning bhavcopy (no overwrite)
    scheduler.add_job(
        job_morning_bhavcopy,
        CronTrigger(day_of_week="mon-fri", hour=6, minute=30, **ist),
        id="morning_bhavcopy",
        name="Morning bhavcopy (equity + FO)",
        replace_existing=True,
        misfire_grace_time=1800,
    )

    # 07:00 daily — morning brief + Kite token check
    scheduler.add_job(
        job_morning_brief_and_kite_check,
        CronTrigger(hour=7, minute=0, **ist),
        id="morning_brief",
        name="Morning brief + Kite check",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # 09:00 Mon-Fri — Kite token check (loud only if invalid)
    scheduler.add_job(
        job_kite_check,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0, **ist),
        id="kite_check_9am",
        name="Kite token check 9 AM",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # 13:00 Mon-Fri — Kite token check (loud only if invalid)
    scheduler.add_job(
        job_kite_check,
        CronTrigger(day_of_week="mon-fri", hour=13, minute=0, **ist),
        id="kite_check_1pm",
        name="Kite token check 1 PM",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # 16:00 Mon-Fri — full validation + Claude analysis
    scheduler.add_job(
        job_analysis_pipeline,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=0, **ist),
        id="analysis_pipeline",
        name="Validation + Claude analysis pipeline",
        replace_existing=True,
        misfire_grace_time=1800,
    )

    logger.info(
        "Scheduler: 6 jobs registered "
        "(keepalive, morning_bhavcopy, morning_brief, kite_check×2, analysis_pipeline)"
    )
