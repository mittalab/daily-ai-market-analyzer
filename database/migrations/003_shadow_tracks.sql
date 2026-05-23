-- Migration 003: Shadow tracking table
-- Run after 002_oi_series.sql.
--
-- Why separate migration: shadow tracking is a Phase 2 analysis feature.
-- The table is created in Phase 1 so data accumulates from day one,
-- but the reconciliation logic (checking 5-day outcomes) is Phase 2.

-- ─────────────────────────────────────────────────────────────────────────────
-- level1_shadow_tracks
-- Secretly tracks stocks eliminated by Level 1 filters (ATR dead zone,
-- low OI liquidity). After 5 trading days, checks if the stock moved >5%.
-- Evidence feeds the monthly filter evolution review (Phase 2).
-- Earnings-eliminated stocks are NOT shadow tracked (genuine elimination).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS level1_shadow_tracks (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol               VARCHAR(20) NOT NULL,
    elimination_date     DATE NOT NULL,
    elimination_reason   VARCHAR(50) NOT NULL,
    atr_pct              NUMERIC,
    price_at_elimination NUMERIC,
    track_until_date     DATE,
    price_after_5d       NUMERIC,
    move_pct             NUMERIC,
    significant_move     BOOLEAN,
    reconciled_at        TIMESTAMPTZ,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);
