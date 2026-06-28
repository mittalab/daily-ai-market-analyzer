-- Migration 016: Add Turn 1 classification columns to analysis_sessions
-- Full Turn 1 JSON output is stored in session_claude_turns.output_text (turn_number=1).
-- Only the queryable scalar extracts live here.
-- market_trend, market_volatility, market_structure, execution_bias, fii_dii_stance
-- already exist from migration 013.
ALTER TABLE analysis_sessions
    ADD COLUMN IF NOT EXISTS session_risk_level    VARCHAR(20),
    ADD COLUMN IF NOT EXISTS conviction_multiplier NUMERIC;
