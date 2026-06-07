# Data Ingestion & Pre-flight Guide

This guide covers how the system populates data daily and how to manually recover from failures using the independent "Uber Script".

---

## 📅 Scheduled Lifecycle (Automatic)
The system runs these jobs automatically via APScheduler (defined in `scheduler.py`):

| Time (IST) | Job Name | Source | Purpose |
| :--- | :--- | :--- | :--- |
| **06:00 AM** | Keepalive | Supabase | Prevents free-tier database hibernation. |
| **07:00 AM** | Morning Brief | Telegram | Summarizes last night's analysis and open trades. |
| **03:20 PM** | Option Snapshot | Kite / NSE | Captures IV, OI, and Premiums (5 mins before close). |
| **06:30 PM** | Bhavcopy | NSE | Fetches Equity prices, Indices (VIX), and FII/DII flows. |
| **07:00 PM** | Token Reminder | Kite | Alerts via Telegram if the Zerodha token needs refresh. |
| **07:00 PM+** | Bhavcopy Retries | NSE | Automatically retries at 19:00, 19:30, and 20:00 if data is missing. |
| **09:30 PM** | **Pre-flight Check** | System | Validates Token, DB, and Data Freshness before the pipeline. |
| **10:00 PM** | **Main Pipeline** | Claude AI | Runs Level 1 filter, Deep Analysis, and Paper Trading. |

---

## 🚀 The Uber Ingestion Script (Manual Recovery)

If you receive a **🚨 Pre-flight Check FAILED** notification on Telegram, use the Uber Script to independently populate missing data.

### Location:
`pipeline/_run_preflight_ingestion.py`

### How to Run:
Open your terminal in the project root and run:

```bash
# 1. Activate your virtual environment (if not already)
.venv\Scripts\activate

# 2. Run smart population
# (Only fetches data that is actually missing for today)
python pipeline/_run_preflight_ingestion.py

# 3. Force re-population
# (Overwrites today's data even if it exists)
python pipeline/_run_preflight_ingestion.py --force
```

### What it checks/populates:
1.  **Database Connection:** Ensures Supabase is reachable.
2.  **Kite Token:** Validates if you need to visit the refresh URL.
3.  **Equity & Index Prices:** Fills `price_history` table.
4.  **FII/DII Flows:** Fills `fii_dii_flows` table.
5.  **Option Chain Snapshots:** Fills `options_snapshots` (Uses Kite primary for reliability + NSE for IV).

---

## 🔧 Granular Component Recovery

If you only want to run one specific part of the ingestion, you can use these Python one-liners:

### Refresh Option Snapshots Only
```bash
python -c "from pipeline.data_ingestion import run_snapshot_job; run_snapshot_job()"
```

### Refresh Bhavcopy / FII Only
```bash
python -c "from pipeline.data_ingestion import run_bhavcopy_job; run_bhavcopy_job()"
```

### Backfill Multi-Day Price History
To fetch the last 250 days for all Nifty 50 and Watchlist stocks:
```bash
python pipeline/_backfill_full.py
```

---

## 🔴 Common Failure Fixes

### ❌ [Errno 11001] getaddrinfo failed
*   **Cause:** DNS / Internet connectivity issue on the host machine.
*   **Fix:** Check your internet connection. Once restored, run the **Uber Script**.

### ❌ Kite token expired / invalid
*   **Cause:** Zerodha tokens expire daily at midnight IST.
*   **Fix:** Visit [https://api.abhishekmittal.in/kite/refresh](https://api.abhishekmittal.in/kite/refresh) and log in.

### ❌ FII/DII data stale
*   **Cause:** NSE often delays publishing this data (sometimes until 8:00 PM).
*   **Fix:** The scheduler retries automatically. If it still fails by 9:30 PM, run the **Uber Script**.

### ❌ Option snapshot missing
*   **Cause:** NSE blocked the scrape or the machine was offline at 3:20 PM.
*   **Fix:** Run the **Uber Script**. It will use the **Kite API Fallback** to get OI and Price data even if NSE is blocking.
