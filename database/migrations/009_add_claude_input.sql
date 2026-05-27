-- Migration 009: Add input_text to session_claude_turns
ALTER TABLE session_claude_turns ADD COLUMN IF NOT EXISTS input_text TEXT;
