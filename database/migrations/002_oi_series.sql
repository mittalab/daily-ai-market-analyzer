-- Migration 002: OI continuous series tables
-- Run after 001_initial_schema.sql.
--
-- Why separate migration: OI series tables are the most complex schema
-- and are populated by oi_series_builder.py (Week 3). Keeping them
-- separate makes it easier to reset OI data without touching core tables.

-- ─────────────────────────────────────────────────────────────────────────────
-- continuous_oi_series
-- One row per symbol per date. Built nightly from options_snapshots.
-- Aggregates strike-wise OI by expiry to give near/next month totals,
-- PCR, max pain, and rollover %. is_expiry_day=TRUE marks settlement
-- days where OI drops to 0 — these are noise, never used as signals.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS continuous_oi_series (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol              VARCHAR(20) NOT NULL,
    date                DATE NOT NULL,
    rollover_phase      VARCHAR(20),
    near_expiry         DATE,
    next_expiry         DATE,
    near_month_oi       BIGINT,
    next_month_oi       BIGINT,
    total_oi            BIGINT,
    oi_change           BIGINT,
    in_rollover_week    BOOLEAN DEFAULT FALSE,
    is_expiry_day       BOOLEAN DEFAULT FALSE,
    rollover_pct        NUMERIC,
    pcr_near            NUMERIC,
    pcr_total           NUMERIC,
    max_pain            NUMERIC,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- futures_continuous_series
-- One row per symbol per date. Built nightly from Kite historical OI data.
-- Basis = futures_price - spot_price. Expanding basis + rising OI = bullish.
-- OI in lots (already divided by lot_size before storage).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS futures_continuous_series (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol              VARCHAR(20) NOT NULL,
    date                DATE NOT NULL,
    rollover_phase      VARCHAR(20),
    near_expiry         DATE,
    next_expiry         DATE,
    futures_price       NUMERIC,
    spot_price          NUMERIC,
    basis               NUMERIC,
    basis_pct           NUMERIC,
    near_month_oi       BIGINT,
    next_month_oi       BIGINT,
    oi_change           BIGINT,
    in_rollover_week    BOOLEAN DEFAULT FALSE,
    is_expiry_day       BOOLEAN DEFAULT FALSE,
    rollover_pct        NUMERIC,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);
