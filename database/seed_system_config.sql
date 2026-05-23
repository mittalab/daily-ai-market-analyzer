-- Seed script: system_config default values
-- Run AFTER 001_initial_schema.sql.
-- Safe to re-run: ON CONFLICT DO NOTHING skips existing rows.
-- To change a value at runtime, UPDATE system_config SET value=... WHERE key=...

INSERT INTO system_config (key, value, value_type, description) VALUES
    ('capital_inr',               '500000',   'float',  'Total trading capital in INR'),
    ('risk_pct_min',              '0.02',     'float',  'Minimum risk per trade as decimal (2%)'),
    ('risk_pct_max',              '0.03',     'float',  'Maximum risk per trade as decimal (3%)'),
    ('min_rr_ratio',              '2.0',      'float',  'Minimum risk-reward ratio — hard gate, reject below'),
    ('max_concurrent_trades',     '3',        'int',    'Maximum open positions at any time'),
    ('conviction_trade_ready',    '75',       'int',    'Minimum conviction score for Trade Ready stage'),
    ('conviction_watch',          '55',       'int',    'Minimum conviction score for Watch stage'),
    ('conviction_radar',          '35',       'int',    'Minimum conviction score for On Radar stage'),
    ('atr_dead_zone_pct',         '0.8',      'float',  'ATR(14) as % of price below which stock is eliminated'),
    ('earnings_buffer_days',      '5',        'int',    'Trading days before earnings — stock eliminated'),
    ('min_atm_oi',                '10000',    'int',    'Minimum ATM options OI for liquidity filter'),
    ('min_dte_trading_days',      '6',        'int',    'Minimum trading days to expiry for options selection'),
    ('claude_monthly_budget_usd', '60',       'float',  'Hard ceiling for Claude API spend per month'),
    ('claude_warning_threshold',  '0.75',     'float',  'Fraction of budget at which Telegram warning fires'),
    ('max_deep_analysis_stocks',  '18',       'int',    'Maximum stocks sent to Claude deep analysis per session'),
    ('pipeline_start_time_ist',   '22:00',    'string', 'Nightly pipeline start time in IST'),
    ('morning_brief_time_ist',    '07:00',    'string', 'Morning brief Telegram message time in IST'),
    ('snapshot_time_ist',         '15:25',    'string', 'IV snapshot time — 5 min before market close'),
    ('pipeline_enabled',          'true',     'bool',   'Master switch: set false to pause all scheduled jobs')
ON CONFLICT (key) DO NOTHING;
