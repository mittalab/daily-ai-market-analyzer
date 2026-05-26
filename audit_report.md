# System Audit Report: Daily AI Market Analyzer
**Date:** 2026-05-25
**Auditor:** Gemini CLI

---

## 1. Pipeline Modules

### 1.1 `pipeline/data_ingestion.py`
- **WHAT IT DOES (Intended):** Orchestrates data ingestion from various sources in a specific order: NSE Bhavcopy (6:30 PM), NSE FII/DII (6:30 PM), NSE Option Chain (3:25 PM), Kite OHLCV (10:00 PM), Kite Futures OI (10:00 PM), and Kite Portfolio (10:00 PM).
- **WHAT THE CODE ACTUALLY DOES:** Implements three main entry points for the scheduler: `run_bhavcopy_job`, `run_snapshot_job`, and `run_kite_data_fetch`. It handles failures gracefully, logging errors and continuing with other sources. It uses cached FII/DII values if the live fetch fails.
- **KEY FUNCTIONS:**
  - `run_bhavcopy_job(for_date: date | None) -> dict`: Ingests Equity/Index bhavcopy and FII/DII.
  - `run_snapshot_job(snapshot_date: date | None) -> dict`: Ingests option chain IV snapshots for Nifty 50 stocks.
  - `run_kite_data_fetch(kite, rollover_phase: str) -> dict`: Ingests Kite OHLCV and Futures OI.
- **EDGE CASES HANDLED:**
  - FII/DII failure: Uses yesterday's cached value.
  - 404 on Bhavcopy: Retries up to 2 previous trading days.
  - Kite data: Skips symbols with empty data.
- **POTENTIAL GAPS / CONCERNS:**
  - `run_snapshot_job` considers success if >50% symbols pass, which might be too lenient for critical IV analysis.

### 1.2 `pipeline/level1_filter.py`
- **WHAT IT DOES (Intended):** Applies three hard elimination filters: Earnings within 5 days, ATR dead zone < 0.8%, and F&O liquidity ATM OI < 10,000.
- **WHAT THE CODE ACTUALLY DOES:** Implements these three filters sequentially. Uses NSE event calendar for earnings. Tracks eliminated stocks in `shadow_tracks` for ATR and Liquidity filters.
- **KEY FUNCTIONS:**
  - `run_level1_filter(symbols, analysis_date, kite, earnings_window) -> dict`: Main entry point.
  - `fetch_nse_earnings_window(analysis_date: date) -> dict`: Fetches events from NSE API.
  - `_filter_atr_dead(symbol: str) -> (bool, float)`: Calculates ATR% for elimination.
  - `_filter_fno_liquidity(symbol, analysis_date, current_price) -> (bool, int, bool)`: Checks ATM OI.
- **EDGE CASES HANDLED:**
  - Missing snapshot for Liquidity: Filter is skipped gracefully.
  - Insufficient price history for ATR: Filter is skipped.
  - Unexpected errors: Symbol is passed through generously.
- **POTENTIAL GAPS / CONCERNS:**
  - `_EARNINGS_KEYWORDS` might miss some corporate actions that aren't "financial results", "agm", or "board meeting".
  - F&O Liquidity check uses `last_trading_day` logic but might still be sensitive to stale snapshots if not updated exactly at 3:25 PM.

### 1.3 `pipeline/oi_series_builder.py`
- **WHAT IT DOES (Intended):** Builds continuous OI series for Nifty 50 stocks, calculating rollover phases, PCR, and Max Pain.
- **WHAT THE CODE ACTUALLY DOES:** Processes each symbol to calculate rollover phase based on days to expiry, computes PCR (Near and Total) and Max Pain from options snapshots, and calculates OI change vs previous day. Updates basis and basis_pct in the futures table.
- **KEY FUNCTIONS:**
  - `run_oi_series_builder(symbols, analysis_date) -> dict`: Main entry point.
  - `determine_rollover_phase(analysis_date, near_expiry) -> str`: Implements T-schedule.
  - `_calc_max_pain(rows, near_expiry_str) -> float`: Calculates settlement point of maximum buyer loss.
- **EDGE CASES HANDLED:**
  - Missing options snapshot: Falls back to futures OI for PCR/Rollover calculation (PCR becomes None).
  - Expiry day detection: Correctly identifies T=0 as "EXPIRY" phase.
- **POTENTIAL GAPS / CONCERNS:**
  - PCR calculation definition matches spec, but PCR total might be skewed if far-month liquidity is extremely low or noisy.

### 1.4 `pipeline/claude_session.py`
- **WHAT IT DOES (Intended):** Manages a multi-turn Claude session for market context assessment (Turn 1) and pre-scan of Level 1 passed stocks (Turn 2).
- **WHAT THE CODE ACTUALLY DOES:** Implements the two-turn flow using `anthropic` client with ephemeral caching. Assembles data packages (30d Nifty/VIX/FII-DII for T1, 30d Price/OI/Indicators for T2). Enforces token ceilings and calculates cost.
- **KEY FUNCTIONS:**
  - `run_claude_session(context_bundle, level1_passed, session_id) -> dict`: Main orchestrator.
  - `_call_claude(client, system_text, messages) -> Message`: Wrapper with retries and caching.
- **EDGE CASES HANDLED:**
  - Rate limits: 3 retries with exponential backoff.
  - JSON parse errors: Handled with fallback result dicts.
  - Token ceiling: Checks usage before Turn 2 to prevent overruns.
- **POTENTIAL GAPS / CONCERNS:**
  - `_MODEL` is "claude-sonnet-4-6" which might be a future placeholder or specific version; ensure it's available.
  - Turn 2 response truncation: Large number of stocks might hit `max_tokens` (12,000 output tokens limit).

### 1.5 `pipeline/paper_trade_engine.py`
- **WHAT IT DOES (Intended):** Manages paper trade entries (2nd day check) and exits (walk-forward simulation with SL/Target/Time-stop).
- **WHAT THE CODE ACTUALLY DOES:** 
  - Part A: Checks if FLAGGED setups touched the entry zone within 2 days. 
  - Part B: Simulates exits for ACTIVE trades using underlying OHLC for SL checks and option premiums for Target checks. 
  - Implements T1 (partial profit + SL to breakeven) and Day 5 expiry logic.
- **KEY FUNCTIONS:**
  - `run_paper_trade_engine(session_date) -> dict`: Main entry point.
  - `_check_exits(setup, today)`: Main exit simulation logic.
  - `_sl_hit_intraday(is_long, candle, sl_price) -> bool`: Checks underlying breach.
- **EDGE CASES HANDLED:**
  - Gap open: Underlying gap > 0.5% triggers TARGET_HIT at t2_premium.
  - Missing option data: Falls back to underlying price for entry trigger check.
  - SL vs Target: SL is always checked BEFORE target on the same day candle.
- **POTENTIAL GAPS / CONCERNS:**
  - Time stop at Day 5 uses closing price which might be unavailable if the pipeline runs before EOD data is fully ingested for that day.

### 1.6 `pipeline/morning_brief.py`
- **WHAT IT DOES (Intended):** Generates and sends a Telegram morning brief at 7 AM with market context and trade setups.
- **WHAT THE CODE ACTUALLY DOES:** Formats two messages (LOUD for TRADE_READY, SILENT for WATCH). Includes Nifty, VIX, FII/DII data, and detailed trade parameters.
- **KEY FUNCTIONS:**
  - `generate_morning_brief(session_date) -> (str, str)`: Formats messages.
  - `send_morning_brief(session_date)`: Sends to Telegram with 1.1s delay between messages.
- **EDGE CASES HANDLED:**
  - Missing session data: Falls back to latest FII/DII row.
  - No Trade Ready: Sends a "No setups" message with a dashboard link.
- **POTENTIAL GAPS / CONCERNS:**
  - `_trading_days` helper doesn't use a holiday calendar, leading to slight inaccuracies in DTE displays.

---

## 2. Integrations

### 2.1 `integrations/kite_oauth.py`
- **Implementation:** Handles Zerodha login URL generation and request_token exchange. Stores tokens in Supabase.
- **Concerns:** Token expiry is calculated as midnight IST, which is correct for Kite.

### 2.2 `integrations/kite_ohlcv.py`
- **Implementation:** Fetches 180-day OHLCV with 0.35s sleep between symbols. Caches instrument master.
- **Concerns:** None.

### 2.3 `integrations/kite_oi.py`
- **Implementation:** Fetches near + next month futures OI. `oi=True` is explicitly set.
- **Concerns:** Correctly handles lot size conversion (shares to lots).

### 2.4 `integrations/nse_bhavcopy.py`
- **Implementation:** Downloads Equity and Index bhavcopies. Corrects spec URL typo (/products/content/).
- **Concerns:** India VIX close is correctly prioritized over OHLC.

### 2.5 `integrations/nse_fii_dii.py`
- **Implementation:** Uses a warm-up session to bypass Akamai. Captures `netValue`.
- **Concerns:** 403 on homepage is handled as normal.

### 2.6 `integrations/nse_option_chain.py`
- **Implementation:** Fetches full chain, filters IV > 0, and captures first two expiries.
- **Concerns:** camelCase `impliedVolatility` used correctly.

### 2.7 `integrations/telegram.py`
- **Implementation:** HTML parse mode, 3 retries, 30s wait. LOUD/SILENT modes implemented.
- **Concerns:** 4096 char limit enforced via truncation.

---

## 3. API Endpoints

### 3.1 `api/dashboard.py`
- **GET /api/today:** Market context + setups. Includes "stale" flag if >24h.
- **GET /api/setup/{id}:** Detailed setup and paper trade status.
- **GET /api/positions:** Estimated P&L based on `price_history`.
- **GET /api/system/status:** Scheduler, token, and DB health check.

### 3.2 `api/manual_analysis.py`
- **POST /api/analyse:** Deep analysis for a single stock.
- **Implementation:** Bypasses Level 1 filter, fetches 6-month history and indicators, calls Claude Sonnet.
- **Gaps:** Feature gate exists but uses `manual_analysis_enabled` from `system_config`. Ensure this is seeded.

---

## 4. Overall Audit Summary
- **Spec Adherence:** High. All major sections (Ingestion, Level 1, OI Series, Claude Sessions, Paper Trade, Telegram) are implemented according to the spec.
- **Deviations Flagged:**
  1. Bhavcopy URL in `nse_bhavcopy.py` differs from spec (/products/content/ vs /content/cm/) - this is a confirmed working correction.
  2. `_EARNINGS_KEYWORDS` in `level1_filter.py` are limited to three terms.
  3. `paper_trade_engine` exit logic uses daily candles (low intraday resolution) but attempts to compensate with underlying OHLC checks.
- **Security:** Kite credentials and API keys are correctly managed via `.env` and Supabase. Token exchange is single-use.
- **Robustness:** Widespread use of retries, backoffs, and "fail-continue" patterns ensures the pipeline is resilient to single-point failures.
