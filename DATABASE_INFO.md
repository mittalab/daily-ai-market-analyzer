# Daily AI Market Analyzer: Database Documentation

Last updated: 2026-06-25. Row counts are live as of this date.

This document provides a comprehensive overview of the PostgreSQL (Supabase) database.
The system is fully stateful — every AI decision, prompt, and market data point is persisted
to enable crash-resilient pipelines, longitudinal performance tracking, and rich UI dashboards.

---

## Core Architectural Design

- **Persistence First:** Every Claude turn is stored so the pipeline can resume after a crash without replaying tokens.
- **Derived Metrics:** Indicators (EMA, RSI, ATR) are computed in Python from `price_history`. OI analytics are computed from `options_snapshots` into `continuous_oi_series`.
- **Separation of Concerns:** Raw ingestion tables → Computed series tables → AI output tables → Operational tables.

---

## Migrations Applied

| # | File | Summary |
|---|------|---------|
| 001 | `001_initial_schema.sql` | Core tables: trade_setups, analysis_sessions, session_claude_turns, options_snapshots, price_history, fii_dii_flows, lot_sizes, kite_tokens, watchlist_staging, system_config |
| 002 | `002_oi_series.sql` | continuous_oi_series, futures_continuous_series |
| 003 | `003_shadow_tracks.sql` | level1_shadow_tracks |
| 004 | `004_rls_policies.sql` | Row-level security for read-only frontend |
| 005 | `005_widen_status.sql` | Widen status column in analysis_sessions |
| 006 | `006_add_outcome_note.sql` | Add outcome_note to trade_setups |
| 007 | `007_watchlist_unique_symbol.sql` | Unique constraint on watchlist_staging.symbol |
| 008 | `008_futures_ohlcv.sql` | Add futures_open/high/low to futures_continuous_series |
| 009 | `009_add_claude_input.sql` | Add input_text to session_claude_turns |
| 010 | `010_futures_volume.sql` | Add futures_volume to futures_continuous_series |
| 011 | `011_add_rr_reasoning.sql` | Add rr_reasoning + t1_hit/t1_exit_price/t1_pnl_inr to trade_setups |
| 012 | `012_widen_text_columns.sql` | Widen text columns for longer Claude outputs |
| 013 | `013_add_regime_dimensions.sql` | Add market_trend, market_volatility, market_structure, execution_bias, fii_dii_stance to analysis_sessions and trade_setups |
| 014 | `014_futures_snapshots.sql` | New futures_snapshots table — raw per-expiry futures from bhavcopy |

---

## 1. Analysis & AI Tables

### `analysis_sessions` — 9 rows
One row per nightly pipeline run. Acts as the master record for a session.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | Auto-generated |
| `session_id` | VARCHAR(50) UNIQUE | e.g. `SESSION_20260526` |
| `session_date` | DATE | Trading date of the run |
| `status` | VARCHAR | `RUNNING`, `COMPLETE`, `FAILED` |
| `stage_statuses` | JSONB | Per-stage completion flags |
| `stocks_level1_passed` | INT | How many stocks passed Level 1 filter |
| `stocks_deep_analyzed` | INT | How many got full Claude deep analysis |
| `trade_ready_count` | INT | TRADE_READY setups produced |
| `watch_count` | INT | WATCH setups produced |
| `radar_count` | INT | ON_RADAR setups produced |
| `market_regime` | VARCHAR(30) | Overall regime label from Turn 1 |
| `market_trend` | VARCHAR | Trend dimension (e.g. `UPTREND`, `DOWNTREND`) |
| `market_volatility` | VARCHAR | Volatility dimension (e.g. `LOW_VOL`, `HIGH_VOL`) |
| `market_structure` | VARCHAR | Structure dimension (e.g. `BULLISH_STRUCTURE`) |
| `execution_bias` | VARCHAR | Execution stance (e.g. `BUY_DIPS`, `SELL_RALLIES`) |
| `fii_dii_stance` | VARCHAR | Institutional flow stance |
| `nifty_close` / `vix_close` | NUMERIC | Baseline market levels |
| `fii_net_flow_cr` | NUMERIC | Net FII flow in Crores for the session date |
| `claude_tokens_input` / `output` | INT | Token usage across all turns |
| `claude_cost_usd` | NUMERIC | Total AI cost for the session |
| `pipeline_duration_mins` | INT | Wall-clock time |
| `prompt_versions` / `telegram_message_ids` / `errors` | JSONB | Audit metadata |
| `started_at` / `completed_at` / `created_at` | TIMESTAMPTZ | Timestamps |

---

### `session_claude_turns` — 98 rows
Every single Claude interaction, stored verbatim for crash-resume and audit.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `session_id` | VARCHAR(50) | FK to analysis_sessions.session_id |
| `turn_number` | INT | 1=Market Context, 2=Pre-scan, 3+=Deep Analysis |
| `turn_type` | VARCHAR(30) | `market_context`, `prescan`, `deep_analysis` |
| `symbol` | VARCHAR(20) | NULL for context/prescan turns |
| `input_tokens` / `output_tokens` | INT | Cost tracking per turn |
| `input_text` | TEXT | Full prompt sent to Claude |
| `output_text` | TEXT | Raw JSON response from Claude |
| `completed_at` | TIMESTAMPTZ | |
| **UNIQUE** | | `(session_id, turn_number)` |

---

## 2. Trading & Watchlist Tables

### `trade_setups` — 62 rows
Central ledger of every actionable setup flagged by Claude.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `session_id` | VARCHAR(50) | |
| `setup_date` | DATE | |
| `symbol` | VARCHAR(20) | |
| `direction` | VARCHAR(10) | `LONG` or `SHORT` |
| `stage` | VARCHAR(20) | `TRADE_READY`, `WATCH`, `ON_RADAR` |
| `setup_type` | VARCHAR(50) | e.g. `BREAKOUT`, `MEAN_REVERSION` |
| `setup_maturity` | VARCHAR(10) | |
| `conviction_score` | INT | 0–100 |
| `instrument` | VARCHAR(50) | e.g. `CE`, `PE`, `FUT` |
| `strike` / `option_type` / `expiry_date` | — | Recommended option details |
| `entry_zone_low` / `entry_zone_high` | NUMERIC | Premium entry range |
| `stop_loss_premium` / `target_1_premium` / `target_2_premium` | NUMERIC | Risk levels |
| `underlying_stop` | NUMERIC | Spot-level invalidation |
| `lots` / `lot_size` / `max_risk_inr` / `risk_pct_capital` | — | Position sizing |
| `target_reward_inr` / `risk_reward` | NUMERIC | |
| `iv_at_flag` / `iv_assessment` | — | IV context at time of flag |
| `signals_contributing` | TEXT[] | Signal names that fired |
| `scoring_breakdown` | JSONB | Per-signal score breakdown |
| `claude_full_rationale` / `mentor_explanation` | TEXT | Claude's detailed reasoning |
| `key_learning_today` / `why_could_be_wrong` | TEXT | |
| `rr_reasoning` | TEXT | Claude's target/SL justification |
| `market_regime` | VARCHAR(30) | Regime at time of flag |
| `market_trend` / `market_volatility` / `market_structure` / `execution_bias` / `fii_dii_stance` | VARCHAR | Regime dimensions at flag time |
| `vix_at_analysis` / `days_to_expiry_at_flag` / `rollover_phase` | — | Market context |
| `near_month_oi_at_flag` / `next_month_oi_at_flag` / `rollover_pct_at_flag` | — | OI context |
| `user_response` / `user_context_note` / `user_response_at` | — | User feedback |
| `entry_triggered` | BOOL | Whether entry condition was met |
| `entry_date` / `actual_entry_price` | — | Real trade entry |
| `paper_outcome` | VARCHAR(20) | `PROFIT`, `STOP_LOSS`, `EXPIRED`, `OPEN` |
| `paper_exit_date` / `paper_exit_price` / `paper_pnl_inr` / `paper_holding_days` | — | Paper trade result |
| `t1_hit` | BOOL | Whether Target 1 was hit |
| `t1_exit_price` / `t1_pnl_inr` | NUMERIC | T1 partial exit details |
| `outcome_note` | TEXT | Manual outcome commentary |
| `real_trade_executed` / `real_trade_pnl_inr` / `kite_order_ids` | — | Actual trade reconciliation |
| `rationale_held` / `signals_held` / `signals_failed` / `post_mortem_text` | — | Post-trade review |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

---

### `watchlist_staging` — 24 rows
Tracks stocks across pipeline sessions as they progress through RADAR → WATCH → TRADE_READY.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `symbol` | VARCHAR(20) UNIQUE | |
| `current_stage` | VARCHAR(20) | `RADAR`, `WATCH`, `TRADE_READY` |
| `direction_bias` | VARCHAR(10) | `LONG` or `SHORT` |
| `days_in_stage` | INT | |
| `first_flagged_date` | DATE | |
| `stage_history` | JSONB | Full stage transition log |
| `last_analysis_notes` | TEXT | Summary from most recent deep analysis |
| `updated_at` | TIMESTAMPTZ | |

---

## 3. Market Data (Raw Ingestion) Tables

### `options_snapshots` — 48,060 rows
Strike-level option chain data. Populated from two sources:
- **Live (3:25 PM IST):** `run_option_chain_snapshot.py` — NSE option-chain-v3 API, NIFTY + 52 stocks, 3 expiries each. `iv` is populated.
- **Historical bhavcopy:** `backfill_fo_bhavcopy.py` — Pre Jul-08-2024: legacy archive CSV. From Jul-08-2024: UDiFF format (NSE Circular 62424). `iv` is NULL for bhavcopy rows.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `symbol` | VARCHAR(20) | NIFTY stored as `NIFTY_50` |
| `snapshot_date` | DATE | |
| `expiry_date` | DATE | |
| `strike` | NUMERIC | |
| `option_type` | VARCHAR(5) | `CE` or `PE` |
| `oi` / `oi_change` / `volume` | BIGINT | |
| `iv` | NUMERIC | Implied Volatility %; NULL for bhavcopy rows |
| `premium_close` | NUMERIC | Last traded price / settlement price |
| `created_at` | TIMESTAMPTZ | |
| **UNIQUE** | | `(symbol, snapshot_date, expiry_date, strike, option_type)` |

**Symbols:** NIFTY_50, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50 + 52 stocks from `sector_map.json`.

---

### `futures_snapshots` — 0 rows (table created 2026-06-25, backfill pending)
Raw per-expiry futures data from bhavcopy. Distinct from `futures_continuous_series` (which is aggregated near-month only).
Populated by `backfill_fo_bhavcopy.py` alongside options_snapshots.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `symbol` | VARCHAR(20) | NIFTY stored as `NIFTY_50` |
| `snapshot_date` | DATE | |
| `expiry_date` | DATE | Near/mid/far expiry |
| `open_price` / `high_price` / `low_price` / `close_price` | NUMERIC | Intraday OHLC |
| `settle_price` | NUMERIC | NSE settlement price (preferred for illiquid contracts) |
| `oi` / `oi_change` / `volume` | BIGINT | |
| `underlying_price` | NUMERIC | Spot price; populated from UDiFF only |
| `created_at` | TIMESTAMPTZ | |
| **UNIQUE** | | `(symbol, snapshot_date, expiry_date)` |

**Instruments covered:** Stock futures (STF) + Index futures (IDF). Same 56 symbols as options_snapshots.

---

### `futures_continuous_series` — 1,230 rows
Aggregated daily near-month futures view. Built by `oi_series_builder.py` from Kite historical data.
One row per symbol per day — not per expiry.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `symbol` / `date` | — | UNIQUE key |
| `rollover_phase` | VARCHAR(20) | `PRE_ROLLOVER`, `ROLLOVER`, `POST_ROLLOVER` |
| `near_expiry` / `next_expiry` | DATE | |
| `futures_open` / `futures_high` / `futures_low` / `futures_price` | NUMERIC | Near-month OHLC (price = close) |
| `futures_volume` | BIGINT | Near-month volume |
| `spot_price` | NUMERIC | Underlying spot close |
| `basis` / `basis_pct` | NUMERIC | Futures − Spot; expanding = institutional bullish |
| `near_month_oi` / `next_month_oi` / `oi_change` | BIGINT | OI in lots |
| `in_rollover_week` / `is_expiry_day` | BOOL | |
| `rollover_pct` | NUMERIC | % OI shifted to next month |
| `created_at` | TIMESTAMPTZ | |

---

### `continuous_oi_series` — 1,150 rows
Aggregated option interest metrics. Built nightly from `options_snapshots` by `oi_series_builder.py`.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `symbol` / `date` | — | UNIQUE key |
| `rollover_phase` | VARCHAR(20) | |
| `near_expiry` / `next_expiry` | DATE | |
| `near_month_oi` / `next_month_oi` / `total_oi` / `oi_change` | BIGINT | |
| `in_rollover_week` / `is_expiry_day` | BOOL | |
| `rollover_pct` | NUMERIC | |
| `pcr_near` | NUMERIC | Put-Call Ratio for near expiry |
| `pcr_total` | NUMERIC | Put-Call Ratio across all expiries |
| `max_pain` | NUMERIC | Strike with minimum option seller pain |
| `created_at` | TIMESTAMPTZ | |

---

### `price_history` — 16,687 rows
Daily spot OHLCV from Kite `historical_data()`. Source of truth for EMA, RSI, ATR.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `symbol` / `date` | — | UNIQUE key |
| `open` / `high` / `low` / `close` | NUMERIC | |
| `volume` | BIGINT | In shares |
| `created_at` | TIMESTAMPTZ | |

---

### `fii_dii_flows` — 17 rows
Daily FII/DII institutional flow from NSE `/api/fiidiiTradeReact`. Values in Crores.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `date` | DATE UNIQUE | |
| `fii_buy_cr` / `fii_sell_cr` / `fii_net_cr` | NUMERIC | FII activity |
| `dii_buy_cr` / `dii_sell_cr` / `dii_net_cr` | NUMERIC | DII activity |
| `source` | VARCHAR(20) | `LIVE` or `CACHED` (when fetch fails, previous day reused) |
| `created_at` | TIMESTAMPTZ | |

---

## 4. System & Configuration Tables

### `lot_sizes` — 26 rows
NFO lot sizes per symbol. Refreshed weekly from Kite instruments master.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `symbol` | VARCHAR(20) UNIQUE | |
| `lot_size` | INT | Current lot size |
| `previous_lot` | INT | Previous value — used to detect changes and alert |
| `fetched_at` | TIMESTAMPTZ | |

---

### `kite_tokens`
Single-row table for the daily Kite OAuth token. PK is `user_id='primary'` (no `id` column).

| Column | Type | Notes |
|--------|------|-------|
| `user_id` | VARCHAR(20) PK | Always `'primary'` |
| `access_token` | TEXT | Daily token; expires at midnight IST |
| `generated_at` / `expires_at` | TIMESTAMPTZ | |

---

### `system_config`
Runtime configuration. PK is `key` (no `id` column). Allows changing thresholds without code changes.

| Column | Type | Notes |
|--------|------|-------|
| `key` | VARCHAR(100) PK | Config key name |
| `value` | TEXT | Config value (always string; parse per value_type) |
| `value_type` | VARCHAR(20) | `int`, `float`, `string`, `bool` |
| `description` | TEXT | Human-readable explanation |
| `updated_at` | TIMESTAMPTZ | |

---

### `level1_shadow_tracks` — 0 rows
Audit log of stocks eliminated at Level 1 filter. Used to check if we "missed" a significant move.

| Column | Notes |
|--------|-------|
| `symbol`, `session_date` | Stock and session that eliminated it |
| `filter_reason` | Why it failed Level 1 |
| `track_until_date` | Check outcome 5 trading days later |
| `reconciled_at` | Set when outcome is written back |

---

## 5. Security

### RLS Policies
Row Level Security is applied to all tables. The frontend uses the Supabase anon key which
has read-only SELECT access — it cannot INSERT, UPDATE, or DELETE any data.
The backend uses the service key (full access) via `database/client.py`.
