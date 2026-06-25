-- Migration 014: Raw per-expiry futures snapshot table
-- Populated by backfill_fo_bhavcopy.py (UDiFF bhavcopy, Jul-08-2024 onwards)
-- and by run_option_chain_snapshot.py extension if added later.
-- One row per symbol × date × expiry. Both stock (STF) and index (IDF) futures.
-- Applied 2026-06-25.

CREATE TABLE IF NOT EXISTS futures_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          VARCHAR(20)  NOT NULL,
    snapshot_date   DATE         NOT NULL,
    expiry_date     DATE         NOT NULL,
    open_price      NUMERIC,
    high_price      NUMERIC,
    low_price       NUMERIC,
    close_price     NUMERIC,
    settle_price    NUMERIC,
    oi              BIGINT,
    oi_change       BIGINT,
    volume          BIGINT,
    underlying_price NUMERIC,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, snapshot_date, expiry_date)
);

CREATE INDEX IF NOT EXISTS idx_futures_snapshots_symbol_date
    ON futures_snapshots(symbol, snapshot_date);
