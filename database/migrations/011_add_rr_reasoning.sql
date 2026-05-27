-- Migration 011: Add rr_reasoning to trade_setups
ALTER TABLE trade_setups ADD COLUMN IF NOT EXISTS rr_reasoning TEXT;
