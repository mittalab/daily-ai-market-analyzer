-- Migration 008: Add OHLC columns to futures_continuous_series
-- These allow storing open, high, low alongside the existing futures_price (close).
-- Applied 2026-05-26.

ALTER TABLE futures_continuous_series
  ADD COLUMN IF NOT EXISTS futures_open  NUMERIC,
  ADD COLUMN IF NOT EXISTS futures_high  NUMERIC,
  ADD COLUMN IF NOT EXISTS futures_low   NUMERIC;
