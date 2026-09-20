-- Run once in the Supabase SQL editor to create the option_sell_recommendations table.
-- This table stores daily pre-market option SELL recommendations produced by
-- the 8:00 AM IST Mon-Fri mechanical job (option_sell/job.py).

CREATE TABLE IF NOT EXISTS option_sell_recommendations (
    id               BIGSERIAL PRIMARY KEY,
    symbol           TEXT         NOT NULL,
    analysis_date    DATE         NOT NULL,
    action           TEXT         NOT NULL,        -- 'SELL_PE' | 'SELL_CE'
    zone_id          BIGINT       REFERENCES key_levels(id),
    spot_price       NUMERIC      NOT NULL,
    strike           NUMERIC      NOT NULL,
    expiry_date      DATE         NOT NULL,
    premium          NUMERIC      NOT NULL,
    oi               BIGINT,
    iv               NUMERIC,
    computed_delta   NUMERIC      NOT NULL,
    roi_pct          NUMERIC      NOT NULL,        -- decimal fraction (0.018 = 1.8%)
    annualized_pct   NUMERIC      NOT NULL,
    conviction       TEXT         NOT NULL,        -- carried from key_levels.conviction
    rationale        TEXT         NOT NULL,
    status           TEXT         NOT NULL DEFAULT 'ACTIVE',  -- 'ACTIVE' | 'SUPERSEDED'
    created_at       TIMESTAMP    DEFAULT now(),
    updated_at       TIMESTAMP    DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_optsr_symbol_status ON option_sell_recommendations (symbol, status);
CREATE INDEX IF NOT EXISTS idx_optsr_analysis_date ON option_sell_recommendations (analysis_date);
