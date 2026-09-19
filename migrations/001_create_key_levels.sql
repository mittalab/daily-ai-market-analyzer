-- Run once in the Supabase SQL editor to create the key_levels table.
-- This table stores the weekly key support/resistance levels produced by the
-- Saturday Claude analysis job (key_levels/weekly_job.py).

CREATE TABLE IF NOT EXISTS key_levels (
    id               BIGSERIAL PRIMARY KEY,
    symbol           VARCHAR(20)   NOT NULL,
    level_type       VARCHAR(10)   NOT NULL,          -- 'SUPPORT' | 'RESISTANCE'
    zone_low         DECIMAL(12,2) NOT NULL,
    zone_high        DECIMAL(12,2) NOT NULL,
    conviction       VARCHAR(10)   NOT NULL,          -- 'HIGH' | 'MEDIUM' | 'LOW'
    touch_count      INTEGER       NOT NULL,
    last_touch_date  DATE,
    confluence_flags TEXT,                            -- JSON array stored as text
    reasoning        TEXT          NOT NULL,
    status           VARCHAR(12)   NOT NULL DEFAULT 'ACTIVE',   -- 'ACTIVE' | 'SUPERSEDED'
    breached_at      TIMESTAMP     NULL,
    analysis_date    DATE          NOT NULL,
    created_at       TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_key_levels_symbol_status
    ON key_levels (symbol, status);
