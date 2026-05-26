# System Audit Report V2: Deep Dive Analysis
**Date:** 2026-05-25
**Auditor:** Gemini CLI

═══════════════════════════════════════════════
## SECTION 1: PROMPT ENGINEERING AUDIT
═══════════════════════════════════════════════

### 1.1 TURN 1 — Market Context Prompt
*   **System Prompt:** Built by `pipeline/system_prompt_builder.py`.
    *   [x] Market regime instruction
    *   [x] Rollover phase context block
    *   [x] PCR interpretation guide
    *   [x] Operating rules (capital, risk, RR gate)
    *   [x] Active watchlist
    *   [x] Open positions
    *   [x] Recent outcomes (last 7 days)
*   **User Message Payload:** Constructed in `_build_turn1_message` (`pipeline/claude_session.py`).
    *   [x] 30 days of Nifty data (OHLC)
    *   [x] 30 days of VIX data (Close)
    *   [x] 30 days of FII/DII data (Net Cr)
*   **Data Format:** Sent as stringified JSON.
*   **Expected JSON output:** `session_narrative`, `risk_flags`, `favourable_setups`, `index_key_levels`. Matches implementation.

### 1.2 TURN 2 — Pre-Scan Prompt
*   **User Message Payload:** Constructed in `_build_turn2_message`.
    *   [x] Days of price history: 30 days of closes.
    *   [x] Indicators: RSI(14), EMA20, EMA50, ATR%(14), Volume Ratio.
    *   [x] OI data: Last 10 days of near-month OI, PCR near, Max Pain, Rollover Phase.
    *   [x] Sector context: **Missing** in Turn 2 payload.
*   **Direction & Forwarding:** Determined entirely by Claude's assessment based on the provided data package.
*   **max_tokens:** Set to **12,000** (Fix confirmed).

### 1.3 TURNS 3-10 — Deep Analysis Prompts
*   **Implementation:** Currently handled via `api/manual_analysis.py` (On-Demand), not yet integrated into the nightly pipeline loop.
*   **Data Package:**
    *   [x] 120 days (extracted from 180) OHLCV with indicators.
    *   [x] EMA20, EMA50, EMA200 values.
    *   [x] RSI, ATR, MACD values.
    *   [x] OI series: 30 days.
    *   [x] Futures series: 30 days.
    *   [x] Options snapshot: Included.
    *   [x] Sector context: Included (5 days of sector index).
    *   [ ] Previous setups: **Missing**.
*   **Conviction Scoring Layers:** Layer definitions and points are correctly outlined in the JSON structure request.
*   **Hard Rules:** Capital (₹5M), Risk (2-3%), Min RR (1:2), Expiry (Monthly Tuesday) are present in the system prompt text.
*   **Output Requirements:** Mentor explanation, key learning, and "why could be wrong" are requested.
*   **max_tokens:** Set to **3000** per deep turn.

### 1.4 TURN N+1 — Selection Prompt
*   **Status:** **Not Implemented.** The pipeline orchestrator ends at Turn 2, forwarding stocks to the dashboard, rather than making final selections autonomously in a multi-stock selection turn.

### 1.5 RECONCILIATION PROMPT
*   **Status:** **Not Implemented.** The table `level1_shadow_tracks` exists, but the logic to review the 5-day outcome via Claude is missing (deferred to Phase 2).

═══════════════════════════════════════════════
## SECTION 2: DATA COMPLETENESS AUDIT
═══════════════════════════════════════════════

### 2.1 Context Bundle — `pipeline/context_builder.py`
*   **Structure:** Returns `session_date`, `session_id`, `config`, `regime`, `system_memory`, `active_watchlist`, `open_positions`, `recent_outcomes`, `active_directives`, `available_slots`, `max_slots`, `rollover_context`.
*   **Verification:**
    *   [x] system_memory, active_directives are empty arrays (Phase 1).
    *   [x] active_watchlist, open_positions, recent_outcomes, config correctly sourced.
    *   [x] available_slots correctly calculated (`max_slots - len(open_positions)`).
    *   [x] rollover_context correctly fetched.
*   **Bootstrap Mode:** No special prompt injection for Days 1-30.

### 2.2 Deep Analysis Data Package
*   **Rows Fetched:** 180 days of `price_history` fetched, last 120 days serialized into prompt.
*   **EMAs:** Calculated correctly using pandas `ewm(span=N, adjust=False)`.
*   **Sector Data:** Sector mapping lookup is performed correctly using `config/sector_map.json`.

### 2.3 System Prompt Assembly
*   Rollover phase block dynamically populated based on current `continuous_oi_series`.
*   PCR interpretation guide and operating rules explicitly stated.

═══════════════════════════════════════════════
## SECTION 3: FINANCIAL ACCURACY AUDIT
═══════════════════════════════════════════════

### 3.1 Position Sizing Formula
*   **Status:** **Delegated to Claude.** There is no deterministic Python code calculating the lot size or enforcing the 2-3% risk rule mathematically. Claude calculates `lots` and `max_risk_inr` and outputs it in the JSON response.
*   **Risk:** High. LLM math is not reliable for strict risk management constraints.

### 3.2 RR Gate Verification
*   **Status:** **Delegated to Claude.** Enforced only as a text instruction in the prompt. No Python validation rejects a setup if the returned RR is < 2.0.

### 3.3 Paper Trade P&L Calculation
*   **Calculation (`pipeline/paper_trade_engine.py`):**
    *   Brokerage: `min(40.0 * lots, (entry_price * lots * lot_size) * 0.0003) * 2`. This is perfectly aligned with the spec.
    *   Gross PNL: `(exit_price - entry_price) * remaining_lots * lot_size + t1_partial_pnl`.
    *   Net PNL: `Gross - Brokerage`.
    *   Test Case yields exactly `25834.48` (rounded to ₹25,834). ✅ Passed.

### 3.4 Expiry Selection Logic
*   **Status:** **Delegated to Claude.** Expiry selection is an instruction ("Min DTE: 6", "Monthly Tuesday"), but Python does not calculate or enforce the selected date.

### 3.5 OI Units Verification
*   **Status:** Correct. `integrations/kite_oi.py` fetches the lot size from the NFO instruments master and divides the raw shares to store `oi_lots`. `lot_size` comes dynamically from the API, not hardcoded. ✅ Passed.

═══════════════════════════════════════════════
## SECTION 4: SCHEDULER AUDIT
═══════════════════════════════════════════════

### 4.1 Scheduled Jobs
The scheduler configures 5 cron jobs and 1 dynamic job.
1.  **Supabase keepalive:** `06:00` daily.
2.  **Option chain snapshot:** `15:25` Mon-Fri.
3.  **Bhavcopy download:** `18:30` Mon-Fri.
4.  **Token reminder:** `19:00` Mon-Fri.
5.  **Main pipeline:** `22:00` Mon-Fri.
6.  **Morning brief:** Dynamically scheduled by the main pipeline for `07:00` the next day.

### 4.2 Market Hours Guard
*   **Status:** **Missing.** There is no Python code blocking the pipeline or manual analysis endpoint between 9:00-16:00 IST.

### 4.3 Midnight Token Guard
*   **Status:** **Missing.** The pipeline does not check if it started after 21:50 IST to adjust token fetching logic.

### 4.4 Trading Day Detection
*   **Implementation:** `is_trading_day` in `scheduler.py` works by checking if a new row exists in `fii_dii_flows` for the given date. It does NOT use `sector_map.json` holidays. This acts as a reliable heuristic but fails open on DB errors.

═══════════════════════════════════════════════
## SECTION 5: COST TRACKING AUDIT
═══════════════════════════════════════════════

### 5.1 Per-Turn Cost Logging
*   **Tracking:** Input/output tokens are saved per-turn in `session_claude_turns`.
*   **Calculation:** Total session cost is calculated accurately in `claude_session.py`: `cost_usd = round(total_input / 1_000_000 * 3.00 + total_output / 1_000_000 * 15.00, 6)`.
*   **Monthly Cumulative:** **Missing.**

### 5.2 Session Cost JSON File
*   **Status:** **Missing.** No code exists to generate `logs/session_cost_{YYYYMMDD}.json`.

### 5.3 Budget Circuit Breaker
*   **Status:** **Missing.** The $60 budget exists in `seed_system_config.sql`, but there is no logic in the Python code that evaluates this value to halt execution or trigger warnings.

### 5.4 Telegram Cost Notification
*   **Status:** Implemented. Included in `send_pipeline_complete` via `Cost: ${cost_usd:.2f}`.

═══════════════════════════════════════════════
## SECTION 6: DATABASE INTEGRITY AUDIT
═══════════════════════════════════════════════

### 6.1 Table Verification
*   All 13 core tables exist as defined in migrations 001-003.

### 6.2 `system_config` Completeness
*   Configuration is loaded correctly, but the budget/threshold configurations are not utilized by the Python logic.

### 6.3 Upsert Idempotency
*   `price_history`: `UNIQUE(symbol, date)` - Correct.
*   `options_snapshots`: `UNIQUE(symbol, snapshot_date, expiry_date, strike, option_type)` - Correct.
*   `continuous_oi_series`: `UNIQUE(symbol, date)` - Correct.
*   All data ingestion methods utilize `ON CONFLICT DO UPDATE`.

### 6.4 RLS Policies
*   Migration 004 correctly applies `anon_read` to dashboard-facing tables and omits sensitive tables (`kite_tokens`, `session_claude_turns`, `level1_shadow_tracks`, `lot_sizes`), ensuring zero anon access.

═══════════════════════════════════════════════
## SECTION 7: MISSING FEATURES AUDIT
═══════════════════════════════════════════════

### 7.1 Phase 1 Features
*   [ ] Session cost JSON logging (logs/session_cost_*.json) - **MISSING**
*   [ ] Context quality flags in Telegram - **MISSING**
*   [x] Dashboard link in all key notifications - **IMPLEMENTED**
*   [x] Paper trade outcome Telegram notifications - **IMPLEMENTED**
*   [ ] Weekly paper trade Saturday summary - **MISSING**
*   [x] Stale data banner on dashboard (>24h) - **IMPLEMENTED**
*   [ ] Trading days delta in stale banner - **PARTIAL** (Uses raw hours elapsed)
*   [x] Supabase keepalive (6 AM job) - **IMPLEMENTED**
*   [ ] Instruments master weekly refresh (Sunday 6:05 AM) - **MISSING**
*   [x] Morning brief "no session" handling - **PARTIAL** (Handles missing session but lacks specific Monday logic)
*   [ ] Pre-flight check at 9:30 PM - **MISSING**
*   [ ] Bhavcopy retry 4 times - **MISSING** (Scheduler only triggers once at 18:30)

### 7.2 Phase 2 Deferred Features
*   [ ] Signal attribution table updates - Deferred
*   [ ] Session directives - Deferred
*   [ ] Post-mortem generation - Deferred
*   [ ] Config Console UI - Deferred
*   [ ] Weekly debrief - Deferred
*   [ ] Monthly calibration - Deferred
*   [ ] Shadow track analysis - Deferred

═══════════════════════════════════════════════
## SECTION 8: KNOWN BUGS AND FIXES APPLIED
═══════════════════════════════════════════════

*   **Bug 1:** `max_tokens=6000` for pre-scan was too small, truncating JSON. Fixed by increasing to `12000`.
*   **Bug 2:** NSE bhavcopy column names differed from spec (`OPEN_PRICE` instead of `OPEN`). Field mapping was updated in code.
*   **Bug 3:** NSE index names were title case ("Nifty 50") instead of uppercase. Lookup made case-insensitive/mapped correctly.
*   **Bug 4:** Underlying SL was set to 0.0 after T1. Fixed to reset to the underlying entry price to maintain breakeven logic.

═══════════════════════════════════════════════
## SECTION 9: CRITICAL GAPS REQUIRING ACTION
═══════════════════════════════════════════════

1.  **Deterministic Position Sizing & RR Enforcement:** Currently left to LLM hallucination. Must implement strict mathematical validation in Python that rejects setups violating the 2-3% risk or 1:2 RR rules before database insertion.
2.  **Budget Circuit Breaker:** Code ignores the $60 monthly ceiling. A database query calculating total month-to-date spend must be integrated into `claude_session.py` to abort if exceeded.
3.  **Nightly Deep Analysis Pipeline:** The `orchestrator.py` stops at Turn 2. The pipeline needs to loop over forwarded stocks to perform Turn 3-10 deep analysis automatically.
4.  **Market Hours Execution Guard:** Add a strict block during 09:00 - 16:00 IST to prevent stale snapshot caching or mid-day partial data ingestion.
5.  **Bhavcopy Retry Mechanism:** Relying on a single 18:30 cron trigger is fragile due to NSE delays. Implement the 4-tier retry schedule specified.

═══════════════════════════════════════════════
## AUDIT SCORING
═══════════════════════════════════════════════

Data Ingestion:          8/10
Level 1 Filter:          9/10
OI Series Builder:       9/10
Claude Prompts:          8/10
Financial Accuracy:      5/10 (High risk via LLM delegation)
Paper Trade Engine:      9/10
Scheduler:               7/10
Cost Tracking:           4/10 (Missing JSON logging & limits)
Database Integrity:      10/10
Missing Features:        6/10

**Overall system readiness for production: 7.5/10**
*(Requires remediation of Critical Gaps 1 & 2 before live deployment)*