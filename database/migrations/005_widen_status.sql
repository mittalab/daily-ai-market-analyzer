-- Migration 005: Widen analysis_sessions.status from VARCHAR(20) to VARCHAR(50)
--
-- Required because full status strings like "PRE_PROCESSING_COMPLETE" (23 chars)
-- and "ANALYSIS_COMPLETE" (17 chars) exceed the original VARCHAR(20) limit.
--
-- Run in Supabase SQL Editor.

ALTER TABLE analysis_sessions
    ALTER COLUMN status TYPE VARCHAR(50);
