"""
Phase 5 — Saturday orchestrator for weekly key-level analysis.

Entry point: run_weekly_key_levels_job(analysis_date=None) -> dict

Flow per batch (8-12 stocks):
  1. Fetch OHLCV + option chain for each stock.
  2. Run Phase-1 build_stage1_output() per stock.
  3. Build and send the multimodal Claude API request (Phase 3).
  4. Upsert the response into key_levels table (Phase 4).
  Failed batches are retried once; unresolved failures are logged.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date
from typing import Any

from database.client import get_client
from database.queries import get_latest_snapshot_date, get_price_history
from key_levels.api_client import call_claude_batch
from key_levels.db import upsert_key_levels
from key_levels.stage1 import build_stage1_output
from new_utils.stock_list import get_stock_list_for_analysis

logger = logging.getLogger(__name__)

_BATCH_SIZE = 10
_RETRY_DELAY_SECS = 30
_SECTOR_MAP_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "sector_map.json")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_sector_map() -> dict:
    try:
        with open(_SECTOR_MAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _fetch_chain(symbol: str, snapshot_date: date | None) -> list[dict]:
    """
    Fetch the full option chain for symbol on snapshot_date without filtering
    by expiry (we want all strikes for OI-wall detection).
    Returns list of dicts with keys: date, strike, type, oi, iv, premium.
    """
    if snapshot_date is None:
        snapshot_date = get_latest_snapshot_date(symbol)
    if snapshot_date is None:
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
        return rows
    except Exception as exc:
        logger.warning("_fetch_chain failed for %s: %s", symbol, exc)
        return []


def _run_stage1_for_symbol(symbol: str, analysis_date: date) -> dict | None:
    """Fetch data and compute Stage-1 output for one symbol. Returns None on error."""
    try:
        ohlcv = get_price_history(symbol, days=180)
        if not ohlcv:
            logger.warning("%s: no OHLCV data; skipping", symbol)
            return None
        chain = _fetch_chain(symbol, analysis_date)
        return build_stage1_output(symbol, ohlcv, chain)
    except Exception as exc:
        logger.error("Stage-1 failed for %s: %s", symbol, exc)
        return None


def _process_batch(
    batch: list[str],
    analysis_date: date,
    sector_map: dict,
    universe: dict,
) -> tuple[list[str], list[str]]:
    """
    Run Stage-1 → API call → DB upsert for one batch.
    Returns (updated_symbols, failed_symbols).
    """
    stock_data: list[dict] = []
    stage1_failed: list[str] = []

    for symbol in batch:
        result = _run_stage1_for_symbol(symbol, analysis_date)
        if result is None:
            stage1_failed.append(symbol)
            continue
        # Attach sector metadata consumed by api_client
        sector_stocks = sector_map.get("stocks", {})
        result["sector"] = sector_stocks.get(symbol, {}).get("sector", "UNKNOWN")
        result["sector_stance"] = "NEUTRAL"
        stock_data.append(result)

    if not stock_data:
        return [], stage1_failed

    claude_resp = call_claude_batch(stock_data, sector_map.get("stocks", {}))
    upsert_result = upsert_key_levels(claude_resp)

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

    logger.info("Weekly key-levels job starting for %s", analysis_date)

    universe: dict[str, Any] = get_stock_list_for_analysis()
    sector_map = _load_sector_map()
    symbols = list(universe.keys())

    batches = [symbols[i: i + _BATCH_SIZE] for i in range(0, len(symbols), _BATCH_SIZE)]
    all_updated: list[str] = []
    all_failed: list[str] = []

    for batch_num, batch in enumerate(batches, start=1):
        logger.info("Batch %d/%d: %s", batch_num, len(batches), batch)
        try:
            updated, failed = _process_batch(batch, analysis_date, sector_map, universe)
            all_updated.extend(updated)
            all_failed.extend(failed)
            logger.info("Batch %d done — updated=%d, failed=%d", batch_num, len(updated), len(failed))
        except Exception as exc:
            logger.error("Batch %d failed (%s); retrying in %ds…", batch_num, exc, _RETRY_DELAY_SECS)
            time.sleep(_RETRY_DELAY_SECS)
            try:
                updated, failed = _process_batch(batch, analysis_date, sector_map, universe)
                all_updated.extend(updated)
                all_failed.extend(failed)
                logger.info("Batch %d retry succeeded — updated=%d, failed=%d", batch_num, len(updated), len(failed))
            except Exception as retry_exc:
                logger.error(
                    "Batch %d failed after retry (%s). Symbols NOT updated this run: %s",
                    batch_num, retry_exc, batch,
                )
                all_failed.extend(batch)

    summary = {
        "analysis_date": str(analysis_date),
        "updated_count": len(all_updated),
        "failed_symbols": all_failed,
    }
    logger.info("Weekly key-levels job complete: %s", summary)
    return summary
