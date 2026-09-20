"""
DB write layer for option_sell_recommendations table.

persist_recommendations(rows) — supersede existing ACTIVE rows for the symbol,
    then insert new ACTIVE rows. Mirror of key_levels/db.py write pattern.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from database.client import get_client

logger = logging.getLogger(__name__)

MIGRATION_SQL = """
-- Run once in the Supabase SQL editor.
CREATE TABLE IF NOT EXISTS option_sell_recommendations (
    id               BIGSERIAL PRIMARY KEY,
    symbol           TEXT         NOT NULL,
    analysis_date    DATE         NOT NULL,
    action           TEXT         NOT NULL,
    zone_id          BIGINT       REFERENCES key_levels(id),
    spot_price       NUMERIC      NOT NULL,
    strike           NUMERIC      NOT NULL,
    expiry_date      DATE         NOT NULL,
    premium          NUMERIC      NOT NULL,
    oi               BIGINT,
    iv               NUMERIC,
    computed_delta   NUMERIC      NOT NULL,
    roi_pct          NUMERIC      NOT NULL,
    annualized_pct   NUMERIC      NOT NULL,
    conviction       TEXT         NOT NULL,
    rationale        TEXT         NOT NULL,
    status           TEXT         NOT NULL DEFAULT 'ACTIVE',
    created_at       TIMESTAMP    DEFAULT now(),
    updated_at       TIMESTAMP    DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_optsr_symbol_status ON option_sell_recommendations (symbol, status);
CREATE INDEX IF NOT EXISTS idx_optsr_analysis_date ON option_sell_recommendations (analysis_date);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def persist_recommendations(rows: list[dict]) -> None:
    """
    Supersede all prior ACTIVE rows for the symbol, then insert new ACTIVE rows.
    `rows` must be a non-empty list sharing the same symbol.
    """
    if not rows:
        return

    symbol = rows[0]["symbol"]
    client = get_client()

    # Step 1: supersede prior ACTIVE rows
    client.table("option_sell_recommendations").update(
        {"status": "SUPERSEDED", "updated_at": _now_iso()}
    ).eq("symbol", symbol).eq("status", "ACTIVE").execute()

    # Step 2: insert new ACTIVE rows (add updated_at timestamp)
    insert_rows = [{**r, "updated_at": _now_iso()} for r in rows]
    client.table("option_sell_recommendations").insert(insert_rows).execute()

    logger.info("persist_recommendations: %s — %d row(s) written", symbol, len(rows))
