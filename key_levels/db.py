"""
Phase 4 — Supabase write layer for key_levels table.

upsert_key_levels(claude_response) -> dict
    Per symbol in the Claude response:
      1. UPDATE status='SUPERSEDED' for existing ACTIVE rows.
      2. INSERT new rows with status='ACTIVE'.
    Returns {"updated": [symbol, ...], "failed": [symbol, ...]}.

MIGRATION_SQL: DDL to run once in the Supabase SQL editor.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from database.client import get_client

logger = logging.getLogger(__name__)

MIGRATION_SQL = """
-- Run once in the Supabase SQL editor.
CREATE TABLE IF NOT EXISTS key_levels (
    id               BIGSERIAL PRIMARY KEY,
    symbol           VARCHAR(20)   NOT NULL,
    level_type       VARCHAR(10)   NOT NULL,
    zone_low         DECIMAL(12,2) NOT NULL,
    zone_high        DECIMAL(12,2) NOT NULL,
    conviction       VARCHAR(10)   NOT NULL,
    touch_count      INTEGER       NOT NULL,
    last_touch_date  DATE,
    confluence_flags TEXT,
    reasoning        TEXT          NOT NULL,
    status           VARCHAR(12)   NOT NULL DEFAULT 'ACTIVE',
    breached_at      TIMESTAMP     NULL,
    analysis_date    DATE          NOT NULL,
    created_at       TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_key_levels_symbol_status
    ON key_levels (symbol, status);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_row(symbol: str, level: dict, analysis_date: str) -> dict:
    flags = level.get("confluence_flags", [])
    return {
        "symbol": symbol,
        "level_type": level["level_type"],
        "zone_low": float(level["zone_low"]),
        "zone_high": float(level["zone_high"]),
        "conviction": level["conviction"],
        "touch_count": int(level["touch_count"]),
        "last_touch_date": level.get("last_touch_date"),
        "confluence_flags": json.dumps(flags) if isinstance(flags, list) else str(flags),
        "reasoning": level["reasoning"],
        "status": "ACTIVE",
        "breached_at": None,
        "analysis_date": analysis_date,
        "updated_at": _now_iso(),
    }


def upsert_key_levels(claude_response: dict) -> dict:
    """
    Write Claude's key-level analysis to the key_levels table.

    Strategy (per symbol):
      Step 1 — Mark existing ACTIVE rows as SUPERSEDED.
      Step 2 — Insert new rows with status=ACTIVE.

    Supabase PostgREST does not support multi-statement transactions from the
    client. Each step is atomic per-statement. On INSERT failure the symbol is
    logged and added to 'failed'; existing rows remain SUPERSEDED so the next
    weekly run will re-insert cleanly.
    """
    client = get_client()
    results: dict[str, list] = {"updated": [], "failed": []}

    for entry in claude_response.get("key_level_analysis", []):
        symbol: str = entry["symbol"]
        analysis_date: str = entry.get("analysis_date", "")
        levels: list[dict] = entry.get("levels", [])

        if not analysis_date:
            logger.warning("upsert_key_levels: analysis_date missing for %s — row will have empty date", symbol)

        if not levels:
            logger.warning(
                "upsert_key_levels: no levels returned by Claude for %s — will supersede old rows but insert nothing",
                symbol,
            )

        try:
            # Step 1: supersede previous active rows
            client.table("key_levels").update(
                {"status": "SUPERSEDED", "updated_at": _now_iso()}
            ).eq("symbol", symbol).eq("status", "ACTIVE").execute()

            # Step 2: insert new rows
            if levels:
                rows = [_build_row(symbol, lv, analysis_date) for lv in levels]
                client.table("key_levels").insert(rows).execute()

            results["updated"].append(symbol)
            logger.info("upsert_key_levels: %s — %d level(s) written", symbol, len(levels))

        except Exception as exc:
            logger.error("upsert_key_levels failed for %s: %s", symbol, exc)
            results["failed"].append(symbol)

    return results
