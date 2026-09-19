"""
Phase 5 — Saturday orchestrator for weekly key-level analysis.

Entry point: run_weekly_key_levels_job(analysis_date=None) -> dict

Flow per batch (~5 stocks):
  1. Fetch OHLCV + option chain for each stock.
  2. Run Phase-1 build_stage1_output() per stock.
  3. Build and send the multimodal Claude API request (Phase 3).
  4. Upsert the response into key_levels table (Phase 4).
  Failed batches are retried once; unresolved failures are logged + notified.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date
from typing import Any

from database.client import get_client
from database.queries import get_claude_analysis_settings, get_latest_snapshot_date, get_price_history
from key_levels.api_client import call_claude_batch
from key_levels.db import upsert_key_levels
from key_levels.stage1 import build_stage1_output
from new_utils.stock_list import get_stock_list_for_analysis

logger = logging.getLogger(__name__)

_BATCH_SIZE = 5
_RETRY_DELAY_SECS = 30
_SECTOR_MAP_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "sector_map.json")


# ── Telegram helpers (best-effort — never raise) ───────────────────────────────

def _tg_silent(text: str) -> None:
    try:
        from new_notifications.telegram import send_silent
        send_silent(text)
    except Exception as exc:
        logger.warning("Telegram silent send failed: %s", exc)


def _tg_loud(text: str) -> None:
    try:
        from new_notifications.telegram import send_loud
        send_loud(text)
    except Exception as exc:
        logger.warning("Telegram loud send failed: %s", exc)


def _notify_job_start(analysis_date: date, total_stocks: int, total_batches: int) -> None:
    _tg_loud(
        f"📊 <b>Key Levels Analysis Starting — {analysis_date}</b>\n"
        f"Universe: <code>{total_stocks}</code> stocks | "
        f"Batches: <code>{total_batches}</code> × ~{_BATCH_SIZE}"
    )


def _notify_batch_done(
    batch_num: int,
    total_batches: int,
    symbols: list[str],
    updated: list[str],
    failed: list[str],
) -> None:
    status = "✅" if not failed else "⚠️"
    sym_str = ", ".join(f"<code>{s}</code>" for s in symbols)
    msg = (
        f"{status} <b>Batch {batch_num}/{total_batches} done</b>\n"
        f"Symbols: {sym_str}\n"
        f"Updated: <code>{len(updated)}</code>"
    )
    if failed:
        fail_str = ", ".join(f"<code>{s}</code>" for s in failed)
        msg += f" | Failed: <code>{len(failed)}</code> ({fail_str})"
    _tg_silent(msg)


def _notify_batch_retry(batch_num: int, total_batches: int, symbols: list[str], error: str) -> None:
    sym_str = ", ".join(f"<code>{s}</code>" for s in symbols)
    _tg_silent(
        f"⚠️ <b>Batch {batch_num}/{total_batches} failed — retrying in {_RETRY_DELAY_SECS}s</b>\n"
        f"Symbols: {sym_str}\n"
        f"Error: <code>{error[:200]}</code>"
    )


def _notify_batch_permanent_fail(batch_num: int, total_batches: int, symbols: list[str], error: str) -> None:
    sym_str = ", ".join(f"<code>{s}</code>" for s in symbols)
    _tg_loud(
        f"🚨 <b>Batch {batch_num}/{total_batches} FAILED after retry</b>\n"
        f"<code>{len(symbols)}</code> stocks NOT updated this week\n"
        f"Symbols: {sym_str}\n"
        f"Error: <code>{error[:200]}</code>"
    )


def _notify_job_complete(
    analysis_date: date,
    updated_count: int,
    failed_symbols: list[str],
    duration_secs: float,
) -> None:
    mins = int(duration_secs // 60)
    secs = int(duration_secs % 60)
    status = "✅" if not failed_symbols else "⚠️"
    msg = (
        f"{status} <b>Key Levels Complete — {analysis_date}</b>\n"
        f"Updated: <code>{updated_count}</code> stocks | "
        f"Duration: <code>{mins}m {secs}s</code>"
    )
    if failed_symbols:
        fail_str = ", ".join(f"<code>{s}</code>" for s in failed_symbols)
        msg += f"\nNot updated: {fail_str}"
    _tg_loud(msg)


# ── Data helpers ───────────────────────────────────────────────────────────────

def _load_sector_map() -> dict:
    try:
        with open(_SECTOR_MAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("_load_sector_map: could not load sector map from %s (%s) — sector info will be UNKNOWN", _SECTOR_MAP_PATH, exc)
        return {}


def _fetch_chain(symbol: str, snapshot_date: date | None) -> list[dict]:
    """
    Fetch the full option chain for symbol on snapshot_date (all expiries).
    Returns list of dicts with keys: date, strike, type, oi, iv, premium.
    """
    if snapshot_date is None:
        logger.debug("%s: resolving latest snapshot date…", symbol)
        snapshot_date = get_latest_snapshot_date(symbol)
    if snapshot_date is None:
        logger.debug("%s: no snapshot found — chain will be empty", symbol)
        return []
    try:
        resp = (
            get_client()
            .table("options_snapshots")
            .select("strike,option_type,oi,iv,premium_close")
            .eq("symbol", symbol)
            .eq("snapshot_date", str(snapshot_date))
            .execute()
        )
        rows = []
        for r in (resp.data or []):
            rows.append({
                "date": str(snapshot_date),
                "strike": r.get("strike"),
                "type": r.get("option_type"),
                "oi": r.get("oi", 0),
                "iv": r.get("iv"),
                "premium": r.get("premium_close"),
            })
        logger.debug("%s: fetched %d option chain rows (snapshot=%s)", symbol, len(rows), snapshot_date)
        return rows
    except Exception as exc:
        logger.warning("%s: _fetch_chain failed: %s", symbol, exc)
        return []


def _run_stage1_for_symbol(symbol: str, analysis_date: date) -> dict | None:
    """Fetch data and run Stage-1 computation for one symbol. Returns None on error."""
    logger.info("  [%s] fetching OHLCV (up to 180 days)…", symbol)
    try:
        ohlcv = get_price_history(symbol, days=180)
    except Exception as exc:
        logger.error("  [%s] get_price_history failed: %s", symbol, exc)
        return None

    if not ohlcv:
        logger.warning("  [%s] no OHLCV data — skipping", symbol)
        return None

    logger.debug("  [%s] got %d OHLCV rows", symbol, len(ohlcv))

    chain = _fetch_chain(symbol, analysis_date)

    logger.info("  [%s] running Stage-1 computation…", symbol)
    try:
        result = build_stage1_output(symbol, ohlcv, chain)
        zones = result.get("candidate_zones", [])
        oi_lvls = result.get("oi_levels", [])
        logger.info(
            "  [%s] Stage-1 done — close=%.2f, zones=%d, OI levels=%d, chart=%s",
            symbol,
            result.get("last_close", 0),
            len(zones),
            len(oi_lvls),
            "ok" if result.get("chart_image_path") else "MISSING",
        )
        return result
    except Exception as exc:
        logger.error("  [%s] Stage-1 failed: %s", symbol, exc)
        return None


# ── Batch processor ────────────────────────────────────────────────────────────

def _process_batch(
    batch: list[str],
    analysis_date: date,
    sector_map: dict,
) -> tuple[list[str], list[str]]:
    """
    Run Stage-1 → API call → DB upsert for one batch.
    Returns (updated_symbols, failed_symbols).
    """
    stock_data: list[dict] = []
    stage1_failed: list[str] = []

    logger.info("Stage-1: processing %d stocks…", len(batch))
    for symbol in batch:
        result = _run_stage1_for_symbol(symbol, analysis_date)
        if result is None:
            stage1_failed.append(symbol)
            continue
        sector_stocks = sector_map.get("stocks", {})
        result["sector"] = sector_stocks.get(symbol, {}).get("sector", "UNKNOWN")
        result["sector_stance"] = "NEUTRAL"
        stock_data.append(result)

    if stage1_failed:
        logger.warning("Stage-1 failures: %s", stage1_failed)

    if not stock_data:
        logger.warning("No valid stock data in batch — skipping API call")
        return [], stage1_failed

    logger.info("Calling Claude API with %d stocks…", len(stock_data))
    claude_resp = call_claude_batch(stock_data, sector_map.get("stocks", {}))

    run_summary = claude_resp.get("run_summary", {})
    logger.info(
        "Claude response: %d stocks analyzed, %d high-conviction zones. %s",
        run_summary.get("total_stocks_analyzed", "?"),
        run_summary.get("high_conviction_zone_count", "?"),
        run_summary.get("notable_observations", ""),
    )

    logger.info("Writing to key_levels table…")
    upsert_result = upsert_key_levels(claude_resp)
    logger.info(
        "DB write done — updated: %s, failed: %s",
        upsert_result.get("updated", []),
        upsert_result.get("failed", []),
    )

    updated = upsert_result.get("updated", [])
    failed = stage1_failed + upsert_result.get("failed", [])
    return updated, failed


# ── Public entry point ─────────────────────────────────────────────────────────

def run_weekly_key_levels_job(analysis_date: date | None = None) -> dict:
    """
    Run the full Saturday key-level analysis job.

    Returns:
      {"analysis_date": str, "updated_count": int, "failed_symbols": list[str]}
    """
    if analysis_date is None:
        analysis_date = date.today()

    # Settings gate
    settings = get_claude_analysis_settings()
    if not settings.get("saturday_weekly_run", True):
        logger.info("run_weekly_key_levels_job: saturday_weekly_run disabled via settings — skipping")
        return {"analysis_date": str(analysis_date), "updated_count": 0, "failed_symbols": [], "skipped": True}

    # Pre-flight: fail fast if API key is missing
    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("run_weekly_key_levels_job: ANTHROPIC_API_KEY not set — aborting job")
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")

    start_time = time.time()
    logger.info("=" * 60)
    logger.info("Weekly key-levels job starting — %s", analysis_date)
    logger.info("=" * 60)

    universe: dict[str, Any] = get_stock_list_for_analysis()
    sector_map = _load_sector_map()
    symbols = list(universe.keys())

    if not symbols:
        logger.error("run_weekly_key_levels_job: stock universe is empty — nothing to process")
        _tg_loud(f"🚨 Key Levels job aborted — stock universe is empty ({analysis_date})")
        return {"analysis_date": str(analysis_date), "updated_count": 0, "failed_symbols": []}

    batches = [symbols[i: i + _BATCH_SIZE] for i in range(0, len(symbols), _BATCH_SIZE)]

    logger.info("Universe: %d stocks → %d batches of up to %d", len(symbols), len(batches), _BATCH_SIZE)
    _notify_job_start(analysis_date, len(symbols), len(batches))

    all_updated: list[str] = []
    all_failed: list[str] = []

    for batch_num, batch in enumerate(batches, start=1):
        logger.info("-" * 50)
        logger.info("Batch %d/%d: %s", batch_num, len(batches), batch)
        logger.info("-" * 50)

        batch_start = time.time()
        try:
            updated, failed = _process_batch(batch, analysis_date, sector_map)
            all_updated.extend(updated)
            all_failed.extend(failed)
            elapsed = time.time() - batch_start
            logger.info(
                "Batch %d/%d complete in %.1fs — updated=%d, failed=%d",
                batch_num, len(batches), elapsed, len(updated), len(failed),
            )
            _notify_batch_done(batch_num, len(batches), batch, updated, failed)

        except Exception as exc:
            elapsed = time.time() - batch_start
            err_str = str(exc)
            logger.error(
                "Batch %d/%d failed after %.1fs: %s — retrying in %ds…",
                batch_num, len(batches), elapsed, err_str, _RETRY_DELAY_SECS,
            )
            _notify_batch_retry(batch_num, len(batches), batch, err_str)
            time.sleep(_RETRY_DELAY_SECS)

            try:
                logger.info("Batch %d/%d retry attempt…", batch_num, len(batches))
                updated, failed = _process_batch(batch, analysis_date, sector_map)
                all_updated.extend(updated)
                all_failed.extend(failed)
                elapsed = time.time() - batch_start
                logger.info(
                    "Batch %d/%d retry succeeded in %.1fs — updated=%d, failed=%d",
                    batch_num, len(batches), elapsed, len(updated), len(failed),
                )
                _notify_batch_done(batch_num, len(batches), batch, updated, failed)

            except Exception as retry_exc:
                err_str2 = str(retry_exc)
                logger.error(
                    "Batch %d/%d failed after retry: %s. Symbols NOT updated: %s",
                    batch_num, len(batches), err_str2, batch,
                )
                _notify_batch_permanent_fail(batch_num, len(batches), batch, err_str2)
                all_failed.extend(batch)

    duration = time.time() - start_time
    summary = {
        "analysis_date": str(analysis_date),
        "updated_count": len(all_updated),
        "failed_symbols": all_failed,
    }

    logger.info("=" * 60)
    logger.info(
        "Weekly key-levels job complete in %.1fs — updated=%d, failed=%d",
        duration, len(all_updated), len(all_failed),
    )
    if all_failed:
        logger.warning("Symbols not updated this run: %s", all_failed)
    logger.info("=" * 60)

    _notify_job_complete(analysis_date, len(all_updated), all_failed, duration)
    return summary
