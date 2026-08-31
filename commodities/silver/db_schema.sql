-- ============================================================================
-- LAYER 1: RAW ATOMIC DATA TABLES (For Dashboard Charts & Backtesting)
-- ============================================================================

-- 1. MCX Silver Daily OHLCV + OI (Per Contract Expiry: NEAR, MID, FAR)
CREATE TABLE IF NOT EXISTS public.silver_daily_ohlcv (
                                                         session_date DATE NOT NULL,
                                                         contract_type VARCHAR(20) NOT NULL, -- 'NEAR', 'MID', 'FAR'
    tradingsymbol VARCHAR(50) NOT NULL,
    expiry_date DATE NOT NULL,
    days_to_expiry INT NOT NULL,
    open NUMERIC(12, 2),
    high NUMERIC(12, 2),
    low NUMERIC(12, 2),
    close NUMERIC(12, 2),
    volume BIGINT,
    open_interest BIGINT,
    oi_change_pct NUMERIC(8, 2),
    change_pct NUMERIC(8, 2),
    ema_20 NUMERIC(12, 2),
    ema_50 NUMERIC(12, 2),
    ema_180 NUMERIC(12, 2),
    atr_14 NUMERIC(12, 2),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (session_date, contract_type)
    );

-- 2. Global Macro Daily Benchmarks
CREATE TABLE IF NOT EXISTS public.global_macro_daily (
                                                         session_date DATE PRIMARY KEY,
                                                         comex_silver_close NUMERIC(10, 4),
    comex_silver_change_pct NUMERIC(8, 2),
    comex_gold_close NUMERIC(10, 4),
    dxy_close NUMERIC(8, 4),
    dxy_change_pct NUMERIC(8, 2),
    us_10y_yield NUMERIC(6, 4),
    us_10y_real_yield NUMERIC(6, 4),
    usdinr_spot NUMERIC(8, 4),
    gold_silver_ratio NUMERIC(8, 2),
    created_at TIMESTAMPTZ DEFAULT NOW()
    );


-- ============================================================================
-- LAYER 2: IMMUTABLE AI PIPELINE TABLE (For Claude Execution & Audit Trail)
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.silver_pipeline_data (
                                                           session_date DATE PRIMARY KEY,
                                                           created_at TIMESTAMPTZ DEFAULT NOW(),
    turn_1_payload JSONB NOT NULL, -- Frozen input snapshot for Macro Intelligence
    turn_3_payload JSONB NOT NULL, -- Frozen input snapshot for Technical Setup
    turn_1_analysis JSONB,         -- Claude Turn 1 response JSON
    turn_3_analysis JSONB          -- Claude Turn 3 response JSON
    );


-- ============================================================================
-- INDEXES (For Fast Range Queries & Dashboard Filtering)
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_silver_ohlcv_date ON public.silver_daily_ohlcv(session_date DESC);
CREATE INDEX IF NOT EXISTS idx_macro_date ON public.global_macro_daily(session_date DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_date ON public.silver_pipeline_data(session_date DESC);

-- GIN Indexes on JSONB payloads (for fast nested key searching)
CREATE INDEX IF NOT EXISTS idx_turn_1_payload_gin ON public.silver_pipeline_data USING GIN (turn_1_payload);
CREATE INDEX IF NOT EXISTS idx_turn_3_payload_gin ON public.silver_pipeline_data USING GIN (turn_3_payload);