-- Migration 004: Row Level Security policies
-- Run after 001/002/003 (RLS already enabled on all tables from Supabase defaults).
-- Safe to re-run: CREATE POLICY IF NOT EXISTS is not supported in older PG,
-- so this uses DROP IF EXISTS + CREATE to make it idempotent.
--
-- Access model:
--   service_role : bypasses RLS entirely — no policy needed
--   anon         : read-only SELECT on dashboard-facing tables only
--   Sensitive tables (kite_tokens, session_claude_turns,
--                     level1_shadow_tracks, lot_sizes) : no anon policy = no access

-- ─────────────────────────────────────────────────────────────────────────────
-- trade_setups — dashboard shows today's setups and setup detail screen
-- ─────────────────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS "anon_read" ON trade_setups;
CREATE POLICY "anon_read" ON trade_setups
    FOR SELECT TO anon
    USING (true);

-- ─────────────────────────────────────────────────────────────────────────────
-- analysis_sessions — dashboard shows session history and pipeline status
-- ─────────────────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS "anon_read" ON analysis_sessions;
CREATE POLICY "anon_read" ON analysis_sessions
    FOR SELECT TO anon
    USING (true);

-- ─────────────────────────────────────────────────────────────────────────────
-- options_snapshots — dashboard shows IV data per symbol
-- ─────────────────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS "anon_read" ON options_snapshots;
CREATE POLICY "anon_read" ON options_snapshots
    FOR SELECT TO anon
    USING (true);

-- ─────────────────────────────────────────────────────────────────────────────
-- continuous_oi_series — dashboard shows OI trend charts
-- ─────────────────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS "anon_read" ON continuous_oi_series;
CREATE POLICY "anon_read" ON continuous_oi_series
    FOR SELECT TO anon
    USING (true);

-- ─────────────────────────────────────────────────────────────────────────────
-- futures_continuous_series — dashboard shows futures basis and OI
-- ─────────────────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS "anon_read" ON futures_continuous_series;
CREATE POLICY "anon_read" ON futures_continuous_series
    FOR SELECT TO anon
    USING (true);

-- ─────────────────────────────────────────────────────────────────────────────
-- watchlist_staging — dashboard shows Watch and Radar stocks
-- ─────────────────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS "anon_read" ON watchlist_staging;
CREATE POLICY "anon_read" ON watchlist_staging
    FOR SELECT TO anon
    USING (true);

-- ─────────────────────────────────────────────────────────────────────────────
-- fii_dii_flows — dashboard shows 30-day FII/DII context
-- ─────────────────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS "anon_read" ON fii_dii_flows;
CREATE POLICY "anon_read" ON fii_dii_flows
    FOR SELECT TO anon
    USING (true);

-- ─────────────────────────────────────────────────────────────────────────────
-- price_history — dashboard shows price charts on setup detail screen
-- ─────────────────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS "anon_read" ON price_history;
CREATE POLICY "anon_read" ON price_history
    FOR SELECT TO anon
    USING (true);

-- ─────────────────────────────────────────────────────────────────────────────
-- system_config — dashboard reads thresholds for display (e.g. conviction cutoffs)
-- ─────────────────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS "anon_read" ON system_config;
CREATE POLICY "anon_read" ON system_config
    FOR SELECT TO anon
    USING (true);

-- ─────────────────────────────────────────────────────────────────────────────
-- BLOCKED tables — no anon policy created = zero access under RLS
--
--   kite_tokens          : contains live OAuth access token — backend only
--   session_claude_turns : raw Claude output, internal pipeline only
--   level1_shadow_tracks : internal filter analysis, not shown on dashboard
--   lot_sizes            : internal reference data, backend only
-- ─────────────────────────────────────────────────────────────────────────────
