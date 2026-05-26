# System Audit Report V3.0: Production-Ready Assessment
**Date:** 2026-05-25
**Auditor:** Gemini CLI

---

## 1. System Architecture & Data Flow

The system operates as a **State-Synchronised Pipeline** where raw data from external sources is transformed into financial state, then interpreted by an LLM (Claude) to generate trading signals.

### 1.1 Data Ingestion Loop (The "Ingest → DB" Flow)
*   **15:25 (NSE Snapshot):** Captures IV data. Critical for nightly volatility assessment.
*   **18:30 (NSE Bhavcopy):** Updates `price_history` for Nifty 50 + Indices.
*   **22:00 (Kite Ingest):** Fetches authoritative 180-day OHLCV and Futures OI.
*   **Reconciliaton:** `fii_dii_flows` uses a `source='CACHED'` fallback if NSE API is down, ensuring Turn 1 (Market Context) never fails due to missing macro data.

### 1.2 Analysis Loop (The "DB → AI → Signal" Flow)
*   **Market Regime (Python):** Deterministic classification (EMA, VIX, Returns) provides the "frame" for AI.
*   **Level 1 Filter (Python):** Hard elimination (Earnings, Liquidity, ATR Dead Zone) reduces the stock set.
*   **Claude Turn 1 & 2:** Contextual assessment and stock pre-scan.
*   **Signal Persistence:** `trade_setups` table acts as the source of truth for the Dashboard and Morning Brief.

---

## 2. Technical Indicator Verification

The system avoids third-party TA-Lib binaries in favour of pure-pandas implementations (`indicators/technical.py`).

| Indicator | Implementation Logic | Verification |
| :--- | :--- | :--- |
| **EMA** | `ewm(span=n, adjust=False)` | Matches standard recursive formula. |
| **RSI** | Wilder's smoothing via `ewm(com=period-1)` | Accurate. Handles 0-loss case via `.replace(0, nan)`. |
| **ATR** | Max(H-L, \|H-prevC\|, \|L-prevC\|) with `ewm` | Correctly captures gap volatility. |
| **Volume Ratio** | `rolling(3).mean() / rolling(20).mean()` | Standard relative volume metric. |

### Market Regime Classification (Section 11)
*   **VIX > 20:** Triggers `HIGH_VOLATILITY` or `BEAR_HIGH_VOLATILITY`.
*   **Trend:** Requires `Price > EMA20 > EMA50` + `20d return > 3%` for `BULL_TRENDING`.
*   **Sideways:** Detected via 15-day range % < 4%.
*   **Fallback:** Early-stage DB ( < 20 days data) correctly defaults to `SIDEWAYS_WIDE`.

---

## 3. Service Resilience Audit

### 3.1 NSE Integration (`integrations/nse_*.py`)
*   **Warm-up Pattern:** Correctly implements the 2-step warm-up (homepage → market-data page) to acquire Akamai cookies before hitting `/api/` endpoints.
*   **Cookie Handling:** 3s sleep after homepage hit is a verified safety window for Akamai challenge resolution.
*   **Error Recovery:** Bhavcopy fetch implements a 3-attempt retry loop with `last_trading_day` lookback (up to 10 days).

### 3.2 Kite Integration (`integrations/kite_*.py`)
*   **Rate Limiting:** `time.sleep(0.35)` between per-symbol calls (~3 req/sec) is well within Zerodha's 10 req/sec limit, providing a 3x safety margin.
*   **Token Expiry:** `midnight IST` logic is implemented to match Kite's actual daily token cycle.

---

## 4. Frontend-Backend Integration

### 4.1 API Surface Area
*   **Type Safety:** `frontend/src/api.ts` uses TypeScript interfaces (`AnalyseResponse`, `TodayResponse`) matching the Pydantic models in FastAPI.
*   **Error Propagation:** The `apiFetch` wrapper correctly parses `body.detail` from FastAPI's `HTTPException`, ensuring specific server errors (e.g., "Kite token expired") reach the UI.
*   **Manual Analysis:** The `POST /api/analyse` endpoint bypasses L1 filters but uses the same `_build_stock_package` logic as the nightly pipeline, ensuring consistency between automated and manual analysis.

---

## 5. Security & Integrity Audit

### 5.1 Credential Management
*   **Secret Protection:** All keys (`KITE_API_KEY`, `ANTHROPIC_API_KEY`, etc.) are isolated in `.env`.
*   **Token Isolation:** `kite_tokens` is excluded from RLS `anon_read` policies, preventing any exposure of active session tokens to the frontend.

### 5.2 Data Integrity
*   **Idempotency:** All DB sinks use `ON CONFLICT DO UPDATE`. Pipeline crashes can be safely restarted without duplicate rows or primary key violations.
*   **Atomic Updates:** `update_analysis_session` patches the state after every stage, allowing for granular observability of pipeline progress.

---

## 6. Production Hardening: Gaps to 1.0

The following items are required before the system can be considered "Mission Critical":

1.  **Budget Monitoring:** `claude_monthly_budget_usd` exists in DB but is not checked during `run_claude_session`.
2.  **Autonomous Nightly Loop:** `orchestrator.py` currently stops after Turn 2 (Pre-scan). The logic to iterate Turn 3-10 (Deep Analysis) for high-priority stocks is missing from the main loop.
3.  **Market Hours Interlock:** No programmatic lock prevents the pipeline from running during live market hours (9:15-15:30), which would result in partial/stale data ingestion.
4.  **Signal Attribution:** `system_memory` and `signal_attribution` columns are implemented as empty Phase 2 placeholders; early Phase 1 trade outcomes should start being piped into these for future calibration.

---

## 7. Audit Summary Score

| Category | Score | Notes |
| :--- | :--- | :--- |
| **Architecture** | 10/10 | Excellent decoupling of ingestion and analysis. |
| **Financial Math** | 9/10 | Indicator implementation is clean and verified. |
| **Resilience** | 8/10 | Good retry logic; needs multi-trigger cron schedule. |
| **Security** | 9/10 | RLS policies and secret management are solid. |
| **AI Integration** | 7/10 | Strong prompt engineering; needs budget safety. |

**Overall Maturity: 8.6/10**
The codebase is architecturally sound and functionally complete for manual/supervised operation. Programmatic budget enforcement and autonomous loop completion are the final hurdles for full automation.
