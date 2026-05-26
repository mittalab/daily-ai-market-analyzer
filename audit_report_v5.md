# System Audit Report V5.0: Data Quality & Completeness
**Date:** 2026-05-26
**Auditor:** Gemini CLI

═══════════════════════════════════════════════
## SECTION 1: EXACT DATA PACKAGE PER STOCK
═══════════════════════════════════════════════

### 1.1 PRICE HISTORY (Equity OHLCV) - HDFCBANK Example
*   **Source:** `price_history` table.
*   **Query:** `get_price_history(symbol, days=180)`.
*   **Indicators Computed (Python):**
    *   **EMA20/50/200:** `ewm(span=N, adjust=False)`. EMA200 is only sent if `len(df) >= 200`.
    *   **RSI14:** Wilder's smoothing via `ewm(com=13)`.
    *   **ATR14:** True Range = `max(H-L, |H-prevC|, |L-prevC|)`. Sent as absolute and ATR%.
    *   **MACD:** (12, 26, 9) - returns Line, Signal, and Histogram.
    *   **Volume Ratio:** `rolling(3).mean() / rolling(20).mean()`.
*   **Format sent to Claude:**
    *   **Pre-computed scalars:** `ema20`, `ema50`, `ema200`, `rsi14`, `atr_pct14`, `vol_ratio`, `macd`, `macd_signal`, `macd_hist`.
    *   **Time Series:** `ohlcv_120d` (JSON array of dicts containing `date`, `open`, `high`, `low`, `close`, `volume`).

### 1.2 FUTURES DATA
*   **Source:** `futures_continuous_series` table.
*   **Payload:** Last 30 rows.
*   **Fields:** `date`, `futures_price` (Closing only), `near_oi`, `next_oi`, `basis`, `basis_pct`, `rollover_pct`.
*   **Critical Gap:** Only closing price is sent. Claude cannot perform intraday price action analysis on futures.

### 1.3 OPTIONS CHAIN DATA
*   **Source:** `options_snapshots` table (3:25 PM snapshot).
*   **Payload:** Strike-wise array for the near-month expiry.
*   **Fields:** `strike`, `type`, `oi`, `iv`.
*   **OI Walls:** Top 5 CE and PE strikes by OI are identified and sent in the `oi_walls` dict.
*   **PCR/Pain:** `pcr_near` and `max_pain` scalars are included.
*   **Fallback:** If today's snapshot is missing, the system tries yesterday's snapshot and adds a quality note: `"IV data from YYYY-MM-DD (yesterday's snapshot)"`.

### 1.4 CONTINUOUS OI SERIES
*   **Source:** `continuous_oi_series` table.
*   **Payload:** Last 30 rows.
*   **Fields:** `date`, `near_oi`, `next_oi`, `oi_change`, `pcr_near`, `max_pain`, `rollover_pct`, `is_expiry_day`.

### 1.5 SECTOR DATA
*   **Source:** `sector_map.json` + `price_history`.
*   **Payload:** `sector`, `sector_index`, and `sector_index_5d` (JSON array of last 5 days of index closes).
*   **Gap:** Sector indices (e.g., NIFTY BANK) only have 2 days of history in the DB. Relative performance and EMAs for sectors are currently impossible to compute accurately.

### 1.6 PREVIOUS SETUPS
*   **Status:** **STILL MISSING.** `build_stock_package` does not query the `trade_setups` table for historical entries of the current symbol. Claude has no context on previous performance for the specific stock.

═══════════════════════════════════════════════
## SECTION 2: INDEX AND MARKET DATA
═══════════════════════════════════════════════

### 2.1 NIFTY 50 INDEX DATA
*   **Stored as:** `NIFTY_50` in `price_history`.
*   **History:** ~140 days (Earliest: 2025-11-04).
*   **Claude Turn 1:** Receives 30 days of closes + pre-computed EMA20, EMA50, and 20d return.

### 2.2 BANK NIFTY INDEX DATA
*   **Stored as:** `NIFTY_BANK` in `price_history`.
*   **History:** **EXTREMELY POOR.** Only 2 days (2026-05-22 and 2026-05-25).
*   **Context:** Included in Turn 3-10 (Deep Analysis) for banking stocks, but only for the last 5 days.

### 2.3 INDIA VIX
*   **Stored as:** `INDIA_VIX` in `price_history`.
*   **History:** ~140 days. Correct.

### 2.4 FII/DII DATA
*   **Source:** `fii_dii_flows` table.
*   **Payload:** 30 days of `fii_net_cr` and `dii_net_cr`.
*   **Heuristic:** No trend or running totals are pre-computed; Claude is expected to infer the trend from the raw 30-day array.

═══════════════════════════════════════════════
## SECTION 3: COMPUTED vs FETCHED DATA MAP
═══════════════════════════════════════════════

| Data Point | Source | Status | Method |
|:--- | :--- | :--- | :--- |
| Stock Price | Kite/Bhavcopy | FETCHED | `price_history.close` |
| EMAs | Python | COMPUTED | `ewm(span=N, adjust=False)` |
| RSI14 | Python | COMPUTED | `Wilder's ewm(com=13)` |
| ATR14 | Python | COMPUTED | `ewm(TrueRange, span=14)` |
| MACD | Python | COMPUTED | `EMA(12)-EMA(26)` |
| Volume Ratio | Python | COMPUTED | `rolling(3)/rolling(20)` |
| Options IV | NSE Snapshot | FETCHED | `implied_volatility` |
| Max Pain | Python (Ingest)| FETCHED | Pre-computed during ingestion |
| PCR | Python (Ingest)| FETCHED | Pre-computed during ingestion |
| Market Regime | Python | COMPUTED | Deterministic logic in `indicators/regime.py` |

═══════════════════════════════════════════════
## SECTION 4: MANUAL ANALYSIS — CUSTOM STOCKS
═══════════════════════════════════════════════

### 4.1 DATA FETCH FLOW
1.  **DB Check:** `price_history` queried for 180 days.
2.  **Kite Fetch:** If empty, fetches 180d OHLCV from Zerodha Kite, stores in DB, and proceeds.
3.  **Lot Size:** Checks `lot_sizes` table; if missing, fetches from Kite NFO instruments master and caches in DB.
4.  **Degradation:** Custom stocks (non-Nifty50) **do not have** Futures Series or OI Series as these are only collected nightly for Nifty50. Claude receives `No OI series data for SYMBOL` and `No futures series data for SYMBOL`.

### 4.2 DATA QUALITY NOTES
Example for **JIOFIN**:
> `["Price history fetched on-demand from Kite for JIOFIN", "No OI series data for JIOFIN", "No futures series data for JIOFIN", "No options snapshot for JIOFIN — IV unavailable"]`

Claude's reasoning for custom stocks is significantly handicapped by the absence of F&O data.

═══════════════════════════════════════════════
## SECTION 5: DATA FRESHNESS & HEALTH
═══════════════════════════════════════════════

### 5.1 Freshness Audit (2026-05-26)
*   `price_history`: **Latest: 2026-05-25** (Up to date).
*   `fii_dii_flows`: **Latest: 2026-05-25** (Up to date).
*   `continuous_oi_series`: **Latest: 2026-05-22** (**STALE - 4 days old**).
*   `options_snapshots`: **EMPTY.** No snapshots stored for the current Nifty 50 set.

### 5.2 Gaps & Integrity
*   **EMA200:** Only valid for stocks with 200+ days. `HDFCBANK` has ~180 days (fetched from Kite). **EMA200 is currently None for all stocks.**
*   **Sector Indices:** All indices except Nifty 50 and India VIX have only 2 days of history. Indicators like RSI or MACD for sectors are invalid.
*   **Duplicate Check:** `UNIQUE(symbol, date)` on `price_history` is enforced; no duplicate rows found.

═══════════════════════════════════════════════
## SECTION 6: WHAT CLAUDE ACTUALLY SEES
═══════════════════════════════════════════════

### 6.1 Input Context (Turn 2 Pre-Scan)
Claude is sent a massive JSON payload (~15k tokens) containing 30-day price trends and basic indicators for ~47 stocks.
*   **Evidence:** `session_claude_turns` shows Input=14,674 tokens, Output=6,120 tokens for Turn 2.
*   **Risk:** Response truncation is high. Current `max_tokens` is 12,000, but a 50-stock pre-scan with reasoning frequently exceeds 6,000 tokens.

### 6.2 Output Quality (APOLLOHOSP)
Claude's reasoning correctly identified price/EMA crossovers but failed to incorporate sector context due to the "2-day history" gap in `NIFTY_PHARMA`.

═══════════════════════════════════════════════
## SECTION 7: CRITICAL GAPS REQUIRING ACTION
═══════════════════════════════════════════════

1.  **Index Backfill:** Historical bhavcopy for all sector indices (`NIFTY BANK`, `NIFTY IT`, etc.) must be backfilled for 180 days immediately. Current 2-day history renders sector analysis useless.
2.  **Options Snapshot Failure:** `options_snapshots` table is empty. The 3:25 PM job (`run_snapshot_job`) is likely failing to bypass Akamai or NSE URL has changed. IV analysis is currently dead.
3.  **EMA200 Warm-up:** Increase initial Kite fetch from 180 days to 250 days to ensure EMA200 is available for all stocks from Day 1.
4.  **Previous Outcome Integration:** Implement the `trade_setups` history lookup in `build_stock_package` so Claude knows if its previous calls for a stock were winners or losers.

═══════════════════════════════════════════════
## AUDIT SCORING
═══════════════════════════════════════════════

Price data completeness:    7/10 (Missing index history)
Futures data quality:       6/10 (No OHLC, only Close)
Options data quality:       1/10 (**CRITICAL: Table Empty**)
Indicator accuracy:         8/10 (Formulas correct, history sparse)
Context completeness:       5/10 (Sector/Previous setups missing)
Custom stock handling:      8/10 (On-demand Kite fetch works well)
Data freshness:             5/10 (OI Series stale)
Prompt completeness:        9/10 (Full Section 8 structure)

**Overall Data Quality Score: 6.1/10**
*(Remediation of Options Snapshot and Index Backfill required immediately)*
