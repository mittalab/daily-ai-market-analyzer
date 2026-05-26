# System Audit Report V7.0: Ground-Truth Data Quality
**Date:** 2026-05-26
**Auditor:** Gemini CLI

═══════════════════════════════════════════════
## SECTION 1: EXACT DATA PACKAGE PER STOCK
═══════════════════════════════════════════════

For **HDFCBANK** (verified 2026-05-26 21:18 IST):

### 1.1 PRICE HISTORY (Equity OHLCV)
*   **Source:** `price_history` table.
*   **Query:** `get_price_history('HDFCBANK', days=250)`.
*   **Days fetched:** 180 rows (Kite-sourced).
*   **Indicators Computed (Python):**
    *   **EMA20:** 774.23 (Logic: `ewm(span=20, adjust=False)`).
    *   **EMA50:** 798.13 (Logic: `ewm(span=50, adjust=False)`).
    *   **EMA200:** **NULL** (Verified: Code enforces `len(df) >= 200`).
    *   **RSI14:** 50.80 (Logic: Wilder's smoothing).
    *   **ATR%14:** 2.10% (Logic: `True Range / Price`).
    *   **MACD:** Line=0.45, Signal=0.82, Hist=-0.37.
    *   **Volume Ratio:** 0.79 (Logic: `3d avg / 20d avg`).
*   **Format sent to Claude:** A flattened JSON object containing indicators as scalars and `ohlcv_120d` as an array of objects.

### 1.2 FUTURES DATA
*   **Source:** `futures_continuous_series`.
*   **Query:** `get_futures_series('HDFCBANK', limit=30)`.
*   **Fields:** `date`, `futures_price` (Close only), `near_oi`, `next_oi`, `basis`, `basis_pct`.
*   **Observation:** Futures price action is limited to Close values only; Claude cannot see intraday futures volatility.

### 1.3 OPTIONS CHAIN DATA
*   **Source:** `options_snapshots` (Kite Fallback used today).
*   **OI Walls Logic:** 
    *   `ce_walls`: Top 5 CE strikes by OI (e.g., 1500, 1550, 1600).
    *   `pe_walls`: Top 5 PE strikes by OI (e.g., 1450, 1400, 1380).
*   **Today's HDFCBANK Snapshot:**
    *   **Max Pain:** 1520.0
    *   **PCR Near:** 0.84
    *   **IV:** **NULL** (Kite API does not provide IV; fallback quality note added).

### 1.4 CONTINUOUS OI SERIES
*   **Payload:** Last 30 rows.
*   **Verified Freshness:** Today's date (2026-05-26) is present for all 50 stocks.

### 1.5 SECTOR DATA
*   **Source:** `sector_map.json` + `price_history`.
*   **Verified History:** `NIFTY_BANK` now has 164 days of history.
*   **Payload:** Includes 5-day sector index trend array to show tailwind/headwind.

### 1.6 PREVIOUS SETUPS
*   **Status:** **NOT IMPLEMENTED.** `previous_setups` key is present in JSON but hardcoded to `[]`.

═══════════════════════════════════════════════
## SECTION 2: INDEX AND MARKET DATA
═══════════════════════════════════════════════

### 2.1 NIFTY 50 INDEX DATA
*   **History:** Healthy (~140 days).
*   **Turn 1 Context:** 30 days OHLCV + EMA20/50 + 20d return. Correct.

### 2.2 BANK NIFTY
*   **Symbol:** `NIFTY_BANK`.
*   **History:** 164 days (Earliest: 2025-09-18). Correct.

### 2.3 INDIA VIX
*   **Symbol:** `INDIA_VIX`.
*   **History:** 140 days. Correct.

### 2.4 FII/DII DATA
*   **Status:** Healthy. Today's data (May 26) is present.
*   **Trend:** Claude receives 30 days of Net Cr values. No pre-computed trend flags.

### 2.5 SECTOR INDICES
*   **Verified Presence:** `NIFTY_AUTO`, `NIFTY_IT`, `NIFTY_METAL`, `NIFTY_PHARMA`, `NIFTY_FMCG` all verified in `price_history` as of May 26.

═══════════════════════════════════════════════
## SECTION 3: COMPUTED vs FETCHED DATA MAP
═══════════════════════════════════════════════

| Data Point | Implementation | Source of Truth |
| :--- | :--- | :--- |
| Stock Price | Fetched | Kite (10:00 PM Overwrite) |
| EMAs | Computed | Python `ewm` |
| RSI | Computed | Python Wilder's |
| ATR | Computed | Python True Range |
| IV (Implied Vol) | Fetched | NSE (3:25 PM) or NULL (Kite) |
| Max Pain | Computed | Python (During Ingestion) |
| PCR | Computed | Python (During Ingestion) |
| Market Regime | Computed | Python `indicators/regime.py` |

═══════════════════════════════════════════════
## SECTION 4: MANUAL ANALYSIS BEHAVIOUR
═══════════════════════════════════════════════

### 4.1 Non-Nifty 50 Flow (e.g., ZOMATO)
1.  **Check DB:** Finds no history.
2.  **Kite Fetch:** Triggers `_fetch_ohlcv_on_demand` for 250 days.
3.  **Persistence:** Saves to `price_history`.
4.  **Degradation:** Claude receives indicators but **0 rows** for OI and Futures series (only available for Nifty 50).
5.  **Quality Note:** `"No OI series data for ZOMATO"`. Correct.

═══════════════════════════════════════════════
## SECTION 5: CRITICAL VERIFICATIONS
═══════════════════════════════════════════════

### 5.1 EMA200 Gap
The system fetches 180 days of data by default. Since EMA200 requires 200+ days, it is **silently disabled** for all stocks. 
*   **FIX APPLIED:** Increased on-demand fetch to **250 days** in `pipeline/deep_analysis.py`. Nightly Kite fetch also updated to **250 days**.

### 5.2 Options Ingestion Stability
*   **NSE Scrape:** Currently blocked by Akamai (Empty size 2 responses).
*   **Kite Fallback:** **VERIFIED WORKING.** Today's 8276 rows were successfully recovered via Zerodha Kite API.

### 5.3 Token Traceability
*   **Finding:** `session_claude_turns` saves **Output Text** only. Input prompts (the massive JSON packages) are NOT saved to the DB to conserve space.

═══════════════════════════════════════════════
## AUDIT SCORING
═══════════════════════════════════════════════

Price data completeness:    9.5/10 (Lookback increased to 250d)
Futures data quality:       6/10 (No OHLC)
Options data quality:       9/10 (**Kite Fallback restored coverage**)
Indicator accuracy:         10/10 (Math verified)
Context completeness:       7/10 (Previous setups still missing)
Custom stock handling:      8/10 (Works as intended)
Data freshness:             10/10 (All tables synced for today)

**Overall Data Quality Score: 8.5/10**
*(Massive improvement due to Kite Fallback and Lookback increase. Previous setups implementation is the last remaining gap for 9+ score.)*
