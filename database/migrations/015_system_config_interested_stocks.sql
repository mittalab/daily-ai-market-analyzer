-- Migration 015: Add interested_stocks config key
-- Stores a comma-separated list of extra symbols to include in the daily analysis
-- run, beyond the Nifty 50 universe and watchlist. Edit the value in Supabase or
-- via set_system_config('interested_stocks', 'JIOFIN,IRFC,DIXON').
-- Applied 2026-06-25.

INSERT INTO system_config (key, value)
VALUES ('interested_stocks', '')
ON CONFLICT (key) DO NOTHING;
