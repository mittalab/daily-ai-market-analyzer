-- Migration 012: Widen VARCHAR(50) columns that hold Claude-generated text to TEXT.
-- setup_type and instrument in trade_setups can exceed 50 chars.
-- elimination_reason in level1_filter_shadow can also be a full sentence.

ALTER TABLE trade_setups
    ALTER COLUMN setup_type  TYPE TEXT,
    ALTER COLUMN instrument  TYPE TEXT;

ALTER TABLE level1_shadow_tracks
    ALTER COLUMN elimination_reason TYPE TEXT;
