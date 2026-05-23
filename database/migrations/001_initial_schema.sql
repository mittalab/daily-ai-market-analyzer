-- Migration 001: Core operational tables
-- Run once against your Supabase project via the SQL Editor.
-- All tables use IF NOT EXISTS so re-running is safe.

-- ─────────────────────────────────────────────────────────────────────────────
-- trade_setups
-- Central ledger: every flagged setup, its parameters, and its outcome.
-- Paper trade fields populated by paper_trade_engine.py (Week 6).
-- Real trade fields reconciled from Kite order history.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trade_setups (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id              VARCHAR(50) NOT NULL,
    setup_date              DATE NOT NULL,
    symbol                  VARCHAR(20) NOT NULL,
    direction               VARCHAR(10) CHECK (direction IN ('LONG','SHORT')),
    stage                   VARCHAR(20),
    setup_type              VARCHAR(50),
    setup_maturity          VARCHAR(10),
    conviction_score        INTEGER,
    instrument              VARCHAR(50),
    strike                  NUMERIC,
    option_type             VARCHAR(5),
    expiry_date             DATE,
    entry_zone_low          NUMERIC,
    entry_zone_high         NUMERIC,
    stop_loss_premium       NUMERIC,
    target_1_premium        NUMERIC,
    target_2_premium        NUMERIC,
    underlying_stop         NUMERIC,
    lots                    INTEGER,
    lot_size                INTEGER,
    max_risk_inr            NUMERIC,
    risk_pct_capital        NUMERIC,
    target_reward_inr       NUMERIC,
    risk_reward             NUMERIC,
    iv_at_flag              NUMERIC,
    iv_assessment           VARCHAR(20),
    signals_contributing    TEXT[],
    scoring_breakdown       JSONB,
    claude_full_rationale   TEXT,
    mentor_explanation      TEXT,
    key_learning_today      TEXT,
    why_could_be_wrong      TEXT,
    market_regime           VARCHAR(30),
    vix_at_analysis         NUMERIC,
    days_to_expiry_at_flag  INTEGER,
    rollover_phase          VARCHAR(20),
    near_month_oi_at_flag   BIGINT,
    next_month_oi_at_flag   BIGINT,
    rollover_pct_at_flag    NUMERIC,
    user_response           VARCHAR(20),
    user_context_note       TEXT,
    user_response_at        TIMESTAMPTZ,
    entry_triggered         BOOLEAN DEFAULT FALSE,
    entry_date              DATE,
    actual_entry_price      NUMERIC,
    paper_outcome           VARCHAR(20),
    paper_exit_date         DATE,
    paper_exit_price        NUMERIC,
    paper_pnl_inr           NUMERIC,
    paper_holding_days      INTEGER,
    real_trade_executed     BOOLEAN DEFAULT FALSE,
    real_trade_pnl_inr      NUMERIC,
    kite_order_ids          TEXT[],
    rationale_held          BOOLEAN,
    signals_held            TEXT[],
    signals_failed          TEXT[],
    post_mortem_text        TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- analysis_sessions
-- One row per nightly pipeline run. Tracks costs, stage statuses, errors.
-- Session resume logic uses session_claude_turns to reconstruct conversation.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS analysis_sessions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id              VARCHAR(50) UNIQUE NOT NULL,
    session_date            DATE NOT NULL,
    status                  VARCHAR(20),
    stage_statuses          JSONB,
    stocks_level1_passed    INTEGER,
    stocks_deep_analyzed    INTEGER,
    trade_ready_count       INTEGER,
    watch_count             INTEGER,
    radar_count             INTEGER,
    market_regime           VARCHAR(30),
    nifty_close             NUMERIC,
    vix_close               NUMERIC,
    fii_net_flow_cr         NUMERIC,
    claude_tokens_input     INTEGER,
    claude_tokens_output    INTEGER,
    claude_cost_usd         NUMERIC,
    pipeline_duration_mins  INTEGER,
    prompt_versions         JSONB,
    telegram_message_ids    JSONB,
    errors                  JSONB,
    started_at              TIMESTAMPTZ,
    completed_at            TIMESTAMPTZ,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- session_claude_turns
-- Persists every Claude turn so the pipeline can resume after a crash
-- without replaying tokens. Restart rebuilds conversation from these rows.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS session_claude_turns (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      VARCHAR(50) NOT NULL,
    turn_number     INTEGER NOT NULL,
    turn_type       VARCHAR(30),
    symbol          VARCHAR(20),
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    output_text     TEXT,
    completed_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(session_id, turn_number)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- options_snapshots
-- Taken at 3:25 PM IST (5 min before close) — not at 3:30 PM (stale).
-- One row per symbol × date × expiry × strike × option_type.
-- IV field: impliedVolatility from NSE API (annualised %, filter > 0).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS options_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          VARCHAR(20) NOT NULL,
    snapshot_date   DATE NOT NULL,
    expiry_date     DATE NOT NULL,
    strike          NUMERIC NOT NULL,
    option_type     VARCHAR(5) NOT NULL,
    oi              BIGINT,
    oi_change       BIGINT,
    volume          BIGINT,
    iv              NUMERIC,
    premium_close   NUMERIC,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, snapshot_date, expiry_date, strike, option_type)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- price_history
-- Daily OHLCV from Kite historical_data(). Volume in shares.
-- 6 months fetched on first run; incremental nightly thereafter.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS price_history (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol      VARCHAR(20) NOT NULL,
    date        DATE NOT NULL,
    open        NUMERIC,
    high        NUMERIC,
    low         NUMERIC,
    close       NUMERIC,
    volume      BIGINT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_price_history_symbol_date ON price_history(symbol, date);

-- ─────────────────────────────────────────────────────────────────────────────
-- fii_dii_flows
-- One row per calendar date. Accumulated daily from NSE /api/fiidiiTradeReact.
-- Values already in Crores — do NOT divide.
-- On fetch failure: mark source='CACHED', pipeline uses previous row.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS fii_dii_flows (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date        DATE UNIQUE NOT NULL,
    fii_buy_cr  NUMERIC,
    fii_sell_cr NUMERIC,
    fii_net_cr  NUMERIC,
    dii_buy_cr  NUMERIC,
    dii_sell_cr NUMERIC,
    dii_net_cr  NUMERIC,
    source      VARCHAR(20) DEFAULT 'LIVE',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- lot_sizes
-- Refreshed every Sunday from Kite instruments master (never hardcoded).
-- Alert sent via Telegram when a lot size change is detected.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lot_sizes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          VARCHAR(20) NOT NULL,
    lot_size        INTEGER NOT NULL,
    previous_lot    INTEGER,
    fetched_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- kite_tokens
-- Stores the daily OAuth access token (expires midnight IST, not 6 AM).
-- user_id='primary' is the single row for this single-user system.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kite_tokens (
    user_id         VARCHAR(20) PRIMARY KEY DEFAULT 'primary',
    access_token    TEXT NOT NULL,
    generated_at    TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ
);

-- ─────────────────────────────────────────────────────────────────────────────
-- watchlist_staging
-- Tracks stocks across multiple pipeline sessions as they progress from
-- RADAR → WATCH → TRADE_READY. Persists Claude's longitudinal observations.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS watchlist_staging (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol              VARCHAR(20) NOT NULL,
    current_stage       VARCHAR(20),
    direction_bias      VARCHAR(10),
    days_in_stage       INTEGER DEFAULT 0,
    first_flagged_date  DATE,
    stage_history       JSONB,
    last_analysis_notes TEXT,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- system_config
-- Runtime configuration — no hardcoded constants in application code.
-- All thresholds, budgets, and timings live here.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS system_config (
    key         VARCHAR(100) PRIMARY KEY,
    value       TEXT NOT NULL,
    value_type  VARCHAR(20),
    description TEXT,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
