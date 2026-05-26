ALTER TABLE trade_setups
  ADD COLUMN outcome_note   TEXT,
  ADD COLUMN t1_hit         BOOLEAN DEFAULT FALSE,
  ADD COLUMN t1_exit_price  NUMERIC,
  ADD COLUMN t1_pnl_inr     NUMERIC;
