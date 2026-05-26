# System Audit Report V6.0: Telegram & DB Validation
**Date:** 2026-05-26
**Auditor:** Gemini CLI

═══════════════════════════════════════════════
## SECTION 1: TELEGRAM NOTIFICATION AUDIT
═══════════════════════════════════════════════

The current Telegram notification system relies on **process intent** rather than **database ground truth**. While the notifications reporting success are generally accurate, they do not perform a final verification against the database before broadcasting.

### 1.1 `send_pipeline_start`
- **Call Source:** `pipeline/orchestrator.py`
- **Status:** Reports connectivity status (Token, Snapshot, Bhavcopy flags).
- **Validation:** **PARTIAL.** Only validates connectivity, not actual row counts.

### 1.2 `send_pipeline_complete`
- **Call Source:** `pipeline/orchestrator.py`
- **Status:** Reports setup counts and costs.
- **Validation:** **WEAK.** Reports setup counts from memory; does not verify if those setups were successfully committed to the `trade_setups` table.

### 1.3 `job_bhavcopy_job` (Scheduler)
- **Call Source:** `scheduler.py`
- **Status:** Reports rows stored for Equity and Indices.
- **Validation:** **PARTIAL.** Reports counts returned by the ingestion function, but doesn't query the DB to ensure they exist.

### 1.4 `job_option_snapshot` (Scheduler)
- **Status:** **CRITICAL GAP.** Reports "OK" even if 49% of symbols fail (logic requires >50% success).
- **Validation:** **NONE.** Does not verify that IV data is actually present in `options_snapshots`. (Confirmed: table is currently EMPTY despite previous "OK" messages).

═══════════════════════════════════════════════
## SECTION 2: DATABASE GROUND TRUTH (Verification)
═══════════════════════════════════════════════

| Table | Latest Date | Count | Health |
| :--- | :--- | :--- | :--- |
| `price_history` | 2026-05-26 | 10,778 | ✅ Healthy |
| `fii_dii_flows` | 2026-05-26 | 3 | ✅ Healthy |
| `continuous_oi_series` | 2026-05-26 | 338 | ✅ Healthy |
| `options_snapshots` | - | 0 | ❌ **CRITICAL: TABLE EMPTY** |
| `analysis_sessions` | 2026-05-22 | 1 | ⚠️ **STALE (4 days ago)** |
| `trade_setups` | - | 0 | ⚠️ **EMPTY (No signals generated)** |

### Analysis of Stale Session:
The pipeline ran successfully on **May 22**. Since then (May 23-26), even though Bhavcopy and FII/DII data were successfully stored (Ground Truth: May 26), **no analysis session records exist**. This indicates the 10:00 PM `run_pipeline` job is likely failing silently before `create_analysis_session` is called or the scheduler is failing to fire it.

═══════════════════════════════════════════════
## SECTION 3: PROPOSED HARDENING
═══════════════════════════════════════════════

To achieve the "Verified Notification" requirement, the following changes are mandatory:

### 3.1 DB-Backed Validation Helper
Implement `database/queries.py:verify_data_ingestion(table, date)` to return actual row counts.

### 3.2 Hardened Notification Flow (Example)
Instead of:
`summary = run_bhavcopy_job(); send_silent(f"Stored {summary['equity_rows']}")`
Use:
`run_bhavcopy_job(); actual_count = get_row_count('price_history', date); send_silent(f"Verified {actual_count} rows in DB")`

### 3.3 Target Updates
1.  **`scheduler.py` (Bhavcopy):** Cross-check `price_history` counts for the date.
2.  **`scheduler.py` (Snapshot):** Cross-check `options_snapshots` row count. If 0, send LOUD error instead of silent warning.
3.  **`pipeline/orchestrator.py`:** Verify setup commits to `trade_setups` before sending the "Complete" message.

═══════════════════════════════════════════════
## AUDIT SCORING
═══════════════════════════════════════════════

Notification Accuracy:      7/10 (Accurate but unverified)
Ground Truth Connection:    2/10 (**CRITICAL GAP: Process vs DB state**)
Verification Redundancy:    1/10 (Almost zero cross-checking)

**Overall Confidence Score: 3.3/10**
*(The system reports what it tried to do, not what actually happened in the database)*
