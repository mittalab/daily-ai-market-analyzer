-- Migration 013: Add Trend, Volatility, Structure, Execution Bias, and FII/DII Stance columns to analysis_sessions and trade_setups
ALTER TABLE analysis_sessions
    ADD COLUMN IF NOT EXISTS market_trend VARCHAR(20),
    ADD COLUMN IF NOT EXISTS market_volatility VARCHAR(20),
    ADD COLUMN IF NOT EXISTS market_structure VARCHAR(20),
    ADD COLUMN IF NOT EXISTS execution_bias VARCHAR(30),
    ADD COLUMN IF NOT EXISTS fii_dii_stance VARCHAR(20);

ALTER TABLE trade_setups
    ADD COLUMN IF NOT EXISTS market_trend VARCHAR(20),
    ADD COLUMN IF NOT EXISTS market_volatility VARCHAR(20),
    ADD COLUMN IF NOT EXISTS market_structure VARCHAR(20),
    ADD COLUMN IF NOT EXISTS execution_bias VARCHAR(30),
    ADD COLUMN IF NOT EXISTS fii_dii_stance VARCHAR(20);
