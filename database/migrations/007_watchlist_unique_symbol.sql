-- Migration 007: Add UNIQUE constraint on watchlist_staging.symbol
-- Required for Supabase upsert with on_conflict="symbol" to work.
-- Safe to re-run: DROP CONSTRAINT IF EXISTS before CREATE.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM   pg_constraint
        WHERE  conrelid = 'watchlist_staging'::regclass
        AND    contype  = 'u'
        AND    conname  = 'watchlist_staging_symbol_key'
    ) THEN
        ALTER TABLE watchlist_staging ADD CONSTRAINT watchlist_staging_symbol_key UNIQUE (symbol);
    END IF;
END $$;
