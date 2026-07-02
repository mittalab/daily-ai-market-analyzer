-- Migration 017: Add forward_list column to analysis_sessions
ALTER TABLE analysis_sessions ADD COLUMN IF NOT EXISTS forward_list JSONB;
