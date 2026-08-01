-- Migration 018: Add JSONB analysis columns to trade_setups
-- These hold the full nested objects from Claude Turn 3 output so the
-- frontend can read options_setup, fut_setup, key_levels,
-- instrument_decision, and recommended_trade directly from the row.
ALTER TABLE trade_setups
    ADD COLUMN IF NOT EXISTS options_setup        JSONB,
    ADD COLUMN IF NOT EXISTS fut_setup            JSONB,
    ADD COLUMN IF NOT EXISTS key_levels           JSONB,
    ADD COLUMN IF NOT EXISTS instrument_decision  JSONB,
    ADD COLUMN IF NOT EXISTS recommended_trade    JSONB;
