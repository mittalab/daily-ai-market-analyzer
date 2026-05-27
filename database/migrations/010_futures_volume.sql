-- Migration 010: Add futures_volume to futures_continuous_series
ALTER TABLE futures_continuous_series ADD COLUMN IF NOT EXISTS futures_volume BIGINT;
