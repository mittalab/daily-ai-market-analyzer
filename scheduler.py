"""
APScheduler job definitions — all times in IST.

Jobs registered here; scheduler instance lives in main.py.
Call register_jobs(scheduler) once at startup after scheduler.start().

Schedule (spec Section 6):
  06:00 daily        — Supabase keepalive (prevent free-tier connection drop)
  19:00 Mon-Fri      — Telegram token reminder (trading days only)
  15:25 Mon-Fri      — Option chain snapshot (IV capture 5 min before close)
  18:30 Mon-Fri      — NSE bhavcopy + FII/DII download
  22:00 Mon-Fri      — Main analysis pipeline (stub — Week 3+)
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


# ── Individual job functions ───────────────────────────────────────────────────

def job_keepalive() -> None:
    """06:00 daily — ping Supabase to keep the free-tier connection alive."""
    from database.queries import keepalive
    ok = keepalive()
    if ok:
        logger.info("Keepalive: Supabase connection alive")
    else:
        logger.warning("Keepalive: Supabase ping failed")


def is_trading_day(for_date) -> bool:
    """
    Return True if for_date is an NSE trading day.
    FIX 4: Uses holidays_2026 list from config/sector_map.json instead of DB lookup.
    Weekends always False. Holidays checked against sector_map. Fails open on read error.
    """
    if for_date.weekday() >= 5:  # Saturday / Sunday
        return False
    try:
        import json as _json
        import os as _os
        _map_path = _os.path.join(_os.path.dirname(__file__), "config", "sector_map.json")
        with open(_map_path, encoding="utf-8") as _f:
            _data = _json.load(_f)
        holidays = set(_data.get("holidays_2026", []))
        return str(for_date) not in holidays
    except Exception:
        return True  # fail open


def job_token_reminder() -> None:
    """19:00 Mon-Fri — check Kite token validity and notify accordingly."""
    from datetime import date, datetime
    import pytz

    today = date.today()
    if not is_trading_day(today):
        logger.info("Token reminder skipped — %s is not a trading day", today)
        return

    IST = pytz.timezone("Asia/Kolkata")
    midnight_tonight = datetime.now(IST).replace(hour=23, minute=59, second=59, microsecond=0)

    token_valid = False
    try:
        from database.queries import get_kite_token
        token = get_kite_token()
        if token and token.get("expires_at"):
            expires_at = datetime.fromisoformat(token["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = IST.localize(expires_at)
            token_valid = expires_at > midnight_tonight
            logger.info("Token expires_at=%s | valid_for_tonight=%s", expires_at, token_valid)
    except Exception as exc:
        logger.warning("Token validity check failed: %s — defaulting to LOUD reminder", exc)

    from integrations.telegram import send_token_reminder, send_token_valid
    if token_valid:
        mid = send_token_valid()
        if mid:
            logger.info("Token valid — silent confirmation sent (message_id=%d)", mid)
    else:
        mid = send_token_reminder()
        if mid:
            logger.info("Token needs refresh — LOUD reminder sent (message_id=%d)", mid)
        else:
            logger.warning("Token reminder: Telegram send failed")


def job_option_snapshot() -> None:
    """15:20 Mon-Fri — fetch and store option chain IV snapshot for all Nifty 50 stocks."""
    from pipeline.data_ingestion import run_snapshot_job
    from integrations.telegram import send_snapshot_failed
    from database.queries import get_row_count
    from datetime import date, datetime
    import pytz

    today = date.today()
    # Record start time for 'freshness' check
    job_start = datetime.now(pytz.utc) 
    
    logger.info("Option snapshot job starting for %s", today)
    summary = run_snapshot_job(today)

    # ── DB Validation ─────────────────────────────────────────────────────────
    # Verify rows exist for TODAY and were created AFTER the job started.
    actual_rows = get_row_count(
        "options_snapshots", 
        {"snapshot_date": today}, 
        created_after=job_start
    )

    if actual_rows > 0:
        logger.info(
            "Option snapshot VERIFIED: %d NEW rows in DB (process reported %d) Source: %s",
            actual_rows, summary.get("rows_stored", 0), summary.get("source", "NSE")
        )
        from integrations.telegram import send_snapshot_verified
        send_snapshot_verified(str(today), actual_rows, summary.get("source", "NSE"))
    else:
        logger.error("Option snapshot VERIFIED FAILURE: 0 NEW rows found in DB for %s", today)
        send_snapshot_failed(str(today))


def job_bhavcopy() -> None:
    """18:30 Mon-Fri — download NSE equity + indices bhavcopy and FII/DII flows."""
    from pipeline.data_ingestion import run_bhavcopy_job
    from database.queries import get_row_count
    from integrations.telegram import send_silent, send_loud
    from datetime import datetime
    import pytz

    today = __import__('datetime').date.today()
    if not is_trading_day(today):
        logger.info("Bhavcopy job skipped — not a trading day")
        return

    job_start = datetime.now(pytz.utc)
    logger.info("Bhavcopy job starting")
    summary = run_bhavcopy_job()

    # ── DB Validation ─────────────────────────────────────────────────────────
    # Verify rows exist for TODAY and were created AFTER the job started.
    actual_rows = get_row_count(
        "price_history", 
        {"date": today}, 
        created_after=job_start
    )

    if actual_rows >= 50:
        vix  = summary.get("vix") or 0.0
        fii_ok = summary.get("fii_ok")
        logger.info("Bhavcopy VERIFIED: %d NEW rows in DB. VIX=%.2f, FII OK=%s",
                    actual_rows, vix, fii_ok)
        fii_sym = "✅" if fii_ok else "⚠️"
        fii_msg = "LIVE" if fii_ok else "CACHED"
        send_silent(
            f"📥 <b>Data verified — {today}</b>\n"
            f"Verified NEW rows: <code>{actual_rows}</code> | VIX: <code>{vix:.2f}</code>\n"
            f"FII/DII: <b>{fii_msg}</b> {fii_sym}"
        )
    else:
        logger.error("Bhavcopy VERIFIED FAILURE: Only %d NEW rows found in DB (expected >50)", actual_rows)
        send_loud(f"❌ <b>Bhavcopy FAILED — {today}</b>\nOnly <code>{actual_rows}</code> NEW rows found in database.")


def job_bhavcopy_retry() -> None:
    """FIX 5: 19:00 / 19:30 / 20:00 retry — skip if today's data already downloaded."""
    from datetime import date as _date
    today = _date.today()
    if not is_trading_day(today):
        return
    try:
        from database.queries import get_latest_fii_dii
        latest = get_latest_fii_dii()
        if latest and str(latest.get("date", "")) == str(today):
            logger.info("Bhavcopy retry: data for %s already present — skipping", today)
            return
    except Exception:
        pass
    logger.info("Bhavcopy retry starting for %s", today)
    from pipeline.data_ingestion import run_bhavcopy_job
    summary = run_bhavcopy_job()
    if summary["ok"]:
        logger.info("Bhavcopy retry OK: equity=%d rows", summary.get("equity_rows", 0))
    else:
        logger.warning("Bhavcopy retry failed: %s", summary.get("errors", []))


def job_preflight_check() -> None:
    """FIX 6: 21:30 Mon-Fri — validate all systems 30 min before pipeline fires."""
    from datetime import date as _date, datetime as _datetime
    import pytz as _pytz

    today = _date.today()
    if not is_trading_day(today):
        logger.info("Pre-flight check skipped — not a trading day")
        return

    IST      = _pytz.timezone("Asia/Kolkata")
    failures = []

    # Check 1: Kite token valid tonight
    try:
        from database.queries import get_kite_token
        token    = get_kite_token()
        midnight = _datetime.now(IST).replace(hour=23, minute=59, second=59, microsecond=0)
        if not token or not token.get("expires_at"):
            failures.append("Kite token missing — refresh at api.abhishekmittal.in/kite/refresh")
        else:
            expires_at = _datetime.fromisoformat(token["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = IST.localize(expires_at)
            if expires_at <= midnight:
                failures.append("Kite token expired — refresh at api.abhishekmittal.in/kite/refresh")
    except Exception as exc:
        failures.append(f"Kite token check error: {exc}")

    # Check 2: DB reachable
    try:
        from database.queries import keepalive
        if not keepalive():
            failures.append("Database unreachable")
    except Exception as exc:
        failures.append(f"Database check error: {exc}")

    # Check 3: Data Freshness (Bhavcopy + Snapshots)
    try:
        from database.queries import get_row_count
        # Bhavcopy (Expected after 18:30)
        bhav_start = _datetime.combine(today, _datetime.min.time()).replace(hour=18, minute=30).astimezone(_pytz.utc)
        count_eq = get_row_count("price_history", {"date": today}, created_after=bhav_start)
        
        # Option Snapshot (Expected after 15:20)
        snap_start = _datetime.combine(today, _datetime.min.time()).replace(hour=15, minute=20).astimezone(_pytz.utc)
        count_opt = get_row_count("options_snapshots", {"snapshot_date": today}, created_after=snap_start)
        
        # FII/DII (Check date column)
        from database.queries import get_latest_fii_dii
        fii = get_latest_fii_dii()
        fii_date = str(fii.get("date", "")) if fii else ""

        if count_eq < 50:
            failures.append(f"Bhavcopy incomplete (Verified NEW rows: {count_eq})")
        if count_opt == 0:
            failures.append("Option snapshot missing for today")
        if fii_date != str(today):
            failures.append(f"FII/DII data stale (Latest: {fii_date})")
            
    except Exception as exc:
        failures.append(f"Data freshness check error: {exc}")

    if failures:
        from integrations.telegram import send_preflight_check_failed
        send_preflight_check_failed(failures, str(today))
        logger.warning("Pre-flight FAILED: %s", failures)
    else:
        from integrations.telegram import send_silent
        send_silent(f"✅ <b>Pre-flight OK — {today}</b>\nAll systems ready. Pipeline fires at <b>22:00</b>.")
        logger.info("Pre-flight check OK — all systems ready for 22:00 pipeline")


def job_main_pipeline() -> None:
    """22:00 Mon-Fri — main analysis pipeline + paper trade engine + schedule morning brief."""
    from datetime import date
    from apscheduler.triggers.date import DateTrigger
    import pytz

    today = date.today()
    IST   = pytz.timezone("Asia/Kolkata")

    # ── Run main pipeline ─────────────────────────────────────────────────────
    try:
        from pipeline.orchestrator import run_pipeline
        run_pipeline(today)
    except Exception as exc:
        logger.error("Main pipeline failed: %s", exc)
        from integrations.telegram import send_preflight_failed
        send_preflight_failed(str(exc)[:200], str(today))
        return

    # ── Run paper trade engine ────────────────────────────────────────────────
    try:
        from pipeline.paper_trade_engine import run_paper_trade_engine
        summary = run_paper_trade_engine(today)
        logger.info("Paper trade engine: %s", summary)
    except Exception as exc:
        logger.error("Paper trade engine failed: %s", exc)

    # ── Schedule morning brief for 7 AM next morning ──────────────────────────
    try:
        from datetime import datetime, timedelta
        brief_time = datetime.now(IST).replace(hour=7, minute=0, second=0, microsecond=0)
        if brief_time <= datetime.now(IST):
            brief_time += timedelta(days=1)

        from main import scheduler as _sched
        _sched.add_job(
            job_morning_brief,
            DateTrigger(run_date=brief_time, timezone=IST),
            id="morning_brief_next",
            name="Morning brief",
            replace_existing=True,
            kwargs={"session_date_str": str(today)},
        )
        logger.info("Morning brief scheduled for %s IST", brief_time.strftime("%Y-%m-%d %H:%M"))
    except Exception as exc:
        logger.error("Failed to schedule morning brief: %s", exc)


def job_morning_brief(session_date_str: str) -> None:
    """07:00 — send morning brief to Telegram (scheduled dynamically by pipeline)."""
    from datetime import date
    from pipeline.morning_brief import send_morning_brief
    send_morning_brief(date.fromisoformat(session_date_str))


# ── Registration ───────────────────────────────────────────────────────────────

def register_jobs(scheduler: AsyncIOScheduler) -> None:
    """
    Add all cron jobs to the provided scheduler.

    Call once at application startup, after scheduler.start().
    Day-of-week: 'mon-fri' maps to APScheduler day_of_week='0-4'.
    """
    ist_kwargs = {"timezone": "Asia/Kolkata"}

    # 06:00 daily — keepalive
    scheduler.add_job(
        job_keepalive,
        CronTrigger(hour=6, minute=0, **ist_kwargs),
        id="keepalive",
        name="Supabase keepalive",
        replace_existing=True,
        misfire_grace_time=300,  # tolerate up to 5-min late fire
    )

    # 19:00 Mon-Fri — token reminder (7:00 PM IST, before 10 PM pipeline)
    scheduler.add_job(
        job_token_reminder,
        CronTrigger(day_of_week="mon-fri", hour=19, minute=0, **ist_kwargs),
        id="token_reminder",
        name="Kite token reminder",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # 15:20 Mon-Fri — option chain snapshot
    scheduler.add_job(
        job_option_snapshot,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=20, **ist_kwargs),
        id="option_snapshot",
        name="Option chain IV snapshot",
        replace_existing=True,
        misfire_grace_time=120,  # strict — market closes at 15:30
    )

    # 18:30 Mon-Fri — bhavcopy + FII/DII
    scheduler.add_job(
        job_bhavcopy,
        CronTrigger(day_of_week="mon-fri", hour=18, minute=30, **ist_kwargs),
        id="bhavcopy",
        name="NSE bhavcopy + FII/DII",
        replace_existing=True,
        misfire_grace_time=1800,  # NSE server sometimes slow — allow 30 min
    )

    # 19:00 / 19:30 / 20:00 Mon-Fri — bhavcopy retries (FIX 5)
    for _hour, _minute, _jid in [(19, 0, "bhavcopy_retry_1"), (19, 30, "bhavcopy_retry_2"), (20, 0, "bhavcopy_retry_3")]:
        scheduler.add_job(
            job_bhavcopy_retry,
            CronTrigger(day_of_week="mon-fri", hour=_hour, minute=_minute, **ist_kwargs),
            id=_jid,
            name=f"Bhavcopy retry {_hour}:{_minute:02d}",
            replace_existing=True,
            misfire_grace_time=600,
        )

    # 21:30 Mon-Fri — pre-flight check (FIX 6)
    scheduler.add_job(
        job_preflight_check,
        CronTrigger(day_of_week="mon-fri", hour=21, minute=30, **ist_kwargs),
        id="preflight_check",
        name="Pre-flight system check",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # 22:00 Mon-Fri — main pipeline
    scheduler.add_job(
        job_main_pipeline,
        CronTrigger(day_of_week="mon-fri", hour=22, minute=0, **ist_kwargs),
        id="main_pipeline",
        name="Main analysis pipeline",
        replace_existing=True,
        misfire_grace_time=1800,
    )

    logger.info(
        "Scheduler: %d jobs registered (keepalive, token_reminder, option_snapshot, "
        "bhavcopy, 3x bhavcopy_retry, preflight_check, main_pipeline)",
        10,
    )
