# Daily AI Market Analyzer: Database Documentation

This document provides a comprehensive overview of the PostgreSQL database architecture used by the Daily AI Market Analyzer. The system is designed to be fully stateful, enabling crash-resilient analysis, longitudinal performance tracking, and rich UI dashboards.

## Core Architectural Design
- **Persistence First:** Every decision, prompt, and response from Claude is persisted to enable auditing and resume-logic.
- **Derived Metrics:** Technical indicators (EMA, RSI, ATR) are computed in Python but anchored by the raw `price_history` and `futures_continuous_series` tables.
- **Separation of Concerns:** Ingestion tables (raw data), Analysis tables (AI outputs), and Operational tables (system state) are strictly separated.

---

## 1. Analysis & AI Tables

### `analysis_sessions`
Tracks each nightly pipeline run.
- **Significance:** Acts as the master record for a session.
- **Key Columns:**
  - `session_id`: Unique identifier (e.g., `SESSION_20260526`).
  - `status`: Current state (`STARTED`, `COMPLETE`, `FAILED`).
  - `market_regime`: The global market tone identified in Turn 1.
  - `nifty_close` / `vix_close`: Baseline market levels for that session.
  - `claude_cost_usd`: Cumulative cost of all AI turns in this session.

### `session_claude_turns`
Stores the raw input and output of every single interaction with Claude.
- **Significance:** Powers the **Deep Analysis UI** and the **Crash-Resilient Pipeline**. If the pipeline stops, it reads these rows to rebuild the chat context.
- **Key Columns:**
  - `turn_number`: 1 (Context), 2 (Pre-scan), 3+ (Deep Analysis).
  - `turn_type`: `market_context`, `prescan`, or `deep_analysis`.
  - `input_text`: The full prompt sent to Claude (Audit Trail).
  - `output_text`: The raw JSON response from Claude.
  - `input_tokens` / `output_tokens`: Used for accurate cost tracking.

---

## 2. Trading & Watchlist Tables

### `trade_setups`
The central ledger of every actionable setup recommended by the AI.
- **Significance:** Feeds the **Today** tab and the **Performance** tracker.
- **Key Columns:**
  - `stage`: `TRADE_READY` (Immediate), `WATCH` (Wait for trigger), `ON_RADAR` (Monitor).
  - `conviction_score`: 0-100 score based on the multi-factor framework.
  - `strike` / `option_type` / `expiry_date`: Recommended instrument details.
  - `entry_zone_low` / `entry_zone_high`: Range for option premium entry.
  - `rr_reasoning`: Claude's detailed justification for targets/SL.
  - `paper_outcome`: Automated tracking (`PROFIT`, `STOP_LOSS`, `EXPIRED`).

### `watchlist_staging`
Tracks stocks as they progress through the trade lifecycle across multiple days.
- **Significance:** Powers the **Watchlist** tab.
- **Key Columns:**
  - `days_in_stage`: How long a stock has been on WATCH or RADAR.
  - `direction_bias`: Whether we are looking for a LONG or SHORT trigger.
  - `last_analysis_notes`: Brief summary from the most recent deep analysis.

---

## 3. Market Data (Ingestion) Tables

### `price_history`
Daily Spot OHLCV data for Nifty 50 constituents.
- **Significance:** Source of truth for EMA, RSI, and ATR computations.
- **Key Columns:** `open`, `high`, `low`, `close`, `volume`.

### `futures_continuous_series`
Daily metrics for the Near-Month futures contract.
- **Significance:** Used to detect institutional delivery and basis (premium/discount) expansion.
- **Key Columns:**
  - `futures_open` / `high` / `low` / `price`: Complete futures price action.
  - `futures_volume`: Institutional participation metric.
  - `basis` / `basis_pct`: Difference between Futures and Spot price.
  - `rollover_pct`: Percentage of OI moved from Near to Next month.

### `continuous_oi_series`
Aggregated Option Interest metrics.
- **Significance:** Detects market sentiment via PCR and Max Pain.
- **Key Columns:**
  - `pcr_near`: Put-Call Ratio for the near expiry.
  - `max_pain`: The strike where option sellers experience minimum pain.
  - `near_month_oi` / `next_month_oi`: Total open interest per expiry.

### `options_snapshots`
The high-resolution 3:25 PM snapshot of the entire option chain.
- **Significance:** Used to identify **OI Walls** (Support/Resistance).
- **Key Columns:** `strike`, `option_type`, `oi`, `iv` (Implied Volatility).

---

## 4. System & Configuration

### `lot_sizes`
Mapping of symbols to their current NFO lot sizes.
- **Significance:** Ensures `trade_setups` have accurate risk calculations in Rupees.

### `fii_dii_flows`
Daily accumulation of institutional flow data from NSE.
- **Significance:** Injected into Market Context (Turn 1) to determine institutional bias.

### `system_config`
Runtime settings for the entire application.
- **Significance:** Allows adjusting thresholds (e.g., `min_dte`, `capital`) without code changes.

### `level1_shadow_tracks`
Audit table for stocks that *failed* the initial filters.
- **Significance:** Used for Phase 2 reconciliation to see if we "missed" a big move.

---

## 5. Security & Maintenance

### `kite_tokens`
Stores the active OAuth session for the Kite API.
- **Significance:** Required for nightly data ingestion.

### `rls_policies` (Internal)
Row Level Security rules in Supabase.
- **Significance:** Ensures the Frontend Dashboard is strictly **read-only** and cannot modify data.
