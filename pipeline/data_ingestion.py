"""
Data ingestion orchestrator — called by the scheduler jobs and main pipeline.

Ingest order (spec Section 5):
  1. NSE bhavcopy equity + indices  (6:30 PM job)
  2. NSE FII/DII                    (6:30 PM job, same NSE session)
  3. NSE option chain snapshot      (3:25 PM job only — not in main pipeline)
  4. Kite OHLCV 50 stocks           (10:00 PM main pipeline)
  5. Kite futures OI near + next    (10:00 PM main pipeline)
  6. Kite portfolio positions       (10:00 PM main pipeline, skipped if pre-flight late)

Each source logs success/failure and continues — one source failing never crashes the pipeline.
"""
import logging
from datetime import date, datetime, timezone

import pytz

from database.queries import (
    get_fii_dii_flows,
    get_latest_fii_dii,
    upsert_fii_dii_flow,
    upsert_futures_series,
    upsert_options_snapshots,
    upsert_price_history,
)
from integrations.nse_bhavcopy import (
    equity_bhavcopy_to_price_rows,
    fetch_equity_bhavcopy,
    fetch_indices_bhavcopy,
    get_nifty50_symbols,
    indices_to_price_rows,
    last_trading_day,
)
from integrations.nse_fii_dii import create_nse_session, fetch_fii_dii, fii_dii_to_db_row
from integrations.nse_option_chain import run_snapshot_batch
from integrations.kite_oi import fetch_futures_oi_all, futures_oi_to_series_rows
from integrations.kite_ohlcv import fetch_ohlcv_all, ohlcv_to_price_rows

logger = logging.getLogger(__name__)
IST    = pytz.timezone("Asia/Kolkata")

NIFTY50 = sorted(get_nifty50_symbols())


# ── 6:30 PM job ───────────────────────────────────────────────────────────────

def run_bhavcopy_job(for_date: date | None = None) -> dict:
    """
    Download NSE bhavcopy (equity + indices) and FII/DII. Store in Supabase.
    Called by the 6:30 PM scheduler job with up to 4 attempts (scheduler handles retries).

    Returns summary dict with {trade_date, equity_rows, index_rows, fii_ok, vix, nifty50_close}.
    On any source failure: logs error, continues with remaining sources.
    """
    summary: dict = {"ok": True, "errors": []}

    # ── Equity bhavcopy ────────────────────────────────────────────────────────
    trade_date: date | None = None
    try:
        eq_df, trade_date = fetch_equity_bhavcopy(for_date)
        eq_rows           = equity_bhavcopy_to_price_rows(eq_df, trade_date)
        n_upserted        = upsert_price_history(eq_rows)
        summary["trade_date"]   = str(trade_date)
        summary["equity_rows"]  = n_upserted
        logger.info("Equity bhavcopy: %d rows stored for %s", n_upserted, trade_date)
    except Exception as exc:
        logger.error("Equity bhavcopy FAILED: %s", exc)
        summary["errors"].append(f"equity_bhavcopy: {exc}")
        summary["equity_rows"] = 0

    # ── Indices bhavcopy ───────────────────────────────────────────────────────
    try:
        ref_date     = trade_date or last_trading_day(for_date)
        indices, _   = fetch_indices_bhavcopy(ref_date)
        index_rows   = indices_to_price_rows(indices, ref_date)
        upsert_price_history(index_rows)
        summary["vix"]          = indices.get("India VIX")
        summary["nifty50_close"] = indices.get("NIFTY 50")
        summary["index_rows"]   = len(index_rows)
        logger.info(
            "Indices bhavcopy: VIX=%.2f, Nifty50=%s",
            summary.get("vix") or 0.0,
            summary.get("nifty50_close") or "N/A",
        )
    except Exception as exc:
        logger.error("Indices bhavcopy FAILED: %s", exc)
        summary["errors"].append(f"indices_bhavcopy: {exc}")
        summary["vix"]        = None
        summary["index_rows"] = 0

    # ── FII/DII ────────────────────────────────────────────────────────────────
    nse_session = None
    try:
        nse_session = create_nse_session()
        fii_dii     = fetch_fii_dii(nse_session)
        ref_date    = trade_date or last_trading_day(for_date)
        db_row      = fii_dii_to_db_row(fii_dii, ref_date)
        upsert_fii_dii_flow(db_row)
        summary["fii_net_cr"] = fii_dii.get("FII", {}).get("netValue")
        summary["dii_net_cr"] = fii_dii.get("DII", {}).get("netValue")
        summary["fii_ok"]     = True
        logger.info(
            "FII/DII stored: FII %+.0f Cr, DII %+.0f Cr",
            summary.get("fii_net_cr", 0),
            summary.get("dii_net_cr", 0),
        )
    except Exception as exc:
        logger.error("FII/DII FAILED — will use cached value: %s", exc)
        summary["errors"].append(f"fii_dii: {exc}")
        summary["fii_ok"] = False
        # Use yesterday's cached value (spec Section 4 — pipeline continues)
        cached = get_latest_fii_dii()
        if cached:
            cached_row = dict(cached)
            cached_row["source"] = "CACHED"
            ref_date = trade_date or last_trading_day(for_date)
            cached_row["date"] = str(ref_date)
            upsert_fii_dii_flow(cached_row)
            logger.info("FII/DII: using cached value from %s", cached.get("date"))

    summary["ok"] = len(summary["errors"]) == 0
    return summary


# ── 3:25 PM job ───────────────────────────────────────────────────────────────

def run_snapshot_job(snapshot_date: date | None = None) -> dict:
    """
    Fetch option chain IV snapshot for all 50 Nifty stocks. Store in Supabase.
    FIX: Tiered fallback to Kite Quotes if NSE scrape is blocked.
    """
    snap_date = snapshot_date or date.today()
    summary: dict = {"ok": False, "rows_stored": 0, "failed_symbols": [], "source": "NSE"}

    # Tier 1: Try high-fidelity NSE scrape
    try:
        from integrations.nse_fii_dii import create_nse_session
        nse_session = create_nse_session()
        all_rows, failed = run_snapshot_batch(nse_session, NIFTY50, snap_date)
        
        if len(all_rows) > 100:  # Heuristic for meaningful success
            n_upserted = upsert_options_snapshots(all_rows)
            summary.update({"ok": True, "rows_stored": n_upserted, "failed_symbols": failed})
            return summary
    except Exception as exc:
        logger.warning("NSE Snapshot scrape failed: %s — attempting Kite fallback", exc)

    # Tier 2: Fallback to Kite Quotes
    try:
        logger.info("Triggering Tier 2 Fallback: Kite Option Quotes")
        from database.queries import get_kite_token
        from integrations.kite_ohlcv import get_kite, get_option_symbols, fetch_option_quotes
        
        token_row = get_kite_token()
        if not token_row:
            raise RuntimeError("Kite token missing — cannot run Tier 2 fallback")
        
        kite = get_kite(token_row["access_token"])
        fallback_rows = []
        
        for symbol in NIFTY50:
            # 1. Get near/next month option symbols
            instruments = get_option_symbols(kite, symbol)
            if instruments.empty:
                continue
                
            # 2. Fetch live quotes (LTP, OI)
            quotes = fetch_option_quotes(kite, instruments)
            
            # 3. Format for options_snapshots (IV will be None from Kite API)
            for tk, q in quotes.items():
                # tk is e.g. 'NFO:HDFCBANK24MAY1500CE'
                tsym = tk.split(":")[-1]
                inst = instruments[instruments["tradingsymbol"] == tsym].iloc[0]
                
                fallback_rows.append({
                    "symbol":        symbol,
                    "snapshot_date": str(snap_date),
                    "expiry_date":   str(inst["expiry"]),
                    "strike":        float(inst["strike"]),
                    "option_type":   inst["instrument_type"],
                    "oi":            int(q["oi"]),
                    "oi_change":     int(q["oi_day_high"] - q["oi_day_low"]), # Best estimate
                    "volume":        int(q["volume"]),
                    "iv":            None, # Kite API doesn't provide IV
                    "premium_close": float(q["last_price"]),
                })
            time.sleep(0.1) # Respect rate limits
            
        if fallback_rows:
            n_upserted = upsert_options_snapshots(fallback_rows)
            summary.update({
                "ok": True, 
                "rows_stored": n_upserted, 
                "source": "KITE",
                "failed_symbols": [s for s in NIFTY50 if s not in [r["symbol"] for r in fallback_rows]]
            })
            logger.info("Kite Fallback successful: Stored %d option rows (IV will be None)", n_upserted)
        
    except Exception as exc:
        logger.error("Snapshot Tier 2 Fallback FAILED: %s", exc)
        summary["error"] = str(exc)

    return summary


# ── 10:00 PM main pipeline ────────────────────────────────────────────────────

def run_kite_data_fetch(kite, rollover_phase: str = "NORMAL") -> dict:
    """
    Fetch all Kite data for tonight's pipeline. Store in Supabase.
    Receives authenticated KiteConnect instance from the pipeline orchestrator.

    Steps (spec Section 5, 10 PM block):
      1. Instruments master → cached in kite_ohlcv/kite_oi modules
      2. OHLCV 50 stocks (6 months) → price_history
      3. Futures OI near + next month → futures_continuous_series
      4. Portfolio positions (returned, not stored — reconciliation is Week 6)

    Returns summary dict.
    """
    summary: dict = {"ok": True, "errors": [], "symbols_ohlcv": 0, "symbols_oi": 0}

    # ── OHLCV ──────────────────────────────────────────────────────────────────
    try:
        ohlcv_data = fetch_ohlcv_all(kite, NIFTY50, days=250)
        price_rows: list[dict] = []
        for symbol, df in ohlcv_data.items():
            if not df.empty:
                price_rows.extend(ohlcv_to_price_rows(symbol, df))
                summary["symbols_ohlcv"] += 1
        upsert_price_history(price_rows)
        logger.info("Kite OHLCV: %d symbols, %d rows stored", summary["symbols_ohlcv"], len(price_rows))
    except Exception as exc:
        logger.error("Kite OHLCV FAILED: %s", exc)
        summary["errors"].append(f"kite_ohlcv: {exc}")

    # ── Futures OI ─────────────────────────────────────────────────────────────
    try:
        oi_data = fetch_futures_oi_all(kite, NIFTY50, days=30)
        for symbol, entry in oi_data.items():
            near_df      = entry.get("near", None)
            next_df      = entry.get("next", None)
            lot_size     = entry.get("lot_size", 1)
            near_expiry  = entry.get("near_expiry")
            next_expiry  = entry.get("next_expiry")

            if near_df is None or near_df.empty:
                continue

            effective_next = next_df if next_df is not None else type(near_df)()
            rows = futures_oi_to_series_rows(
                symbol, near_df, effective_next,
                lot_size, near_expiry, next_expiry, rollover_phase,
            )
            for row in rows:
                upsert_futures_series(row)
            summary["symbols_oi"] += 1

        logger.info("Kite futures OI: %d symbols stored", summary["symbols_oi"])
    except Exception as exc:
        logger.error("Kite futures OI FAILED: %s", exc)
        summary["errors"].append(f"kite_oi: {exc}")

    # ── Portfolio ──────────────────────────────────────────────────────────────
    try:
        positions = kite.positions()
        orders    = kite.orders()
        summary["positions"] = positions
        summary["orders"]    = orders
        logger.info(
            "Kite portfolio: %d positions, %d orders",
            len(positions.get("net", [])),
            len(orders),
        )
    except Exception as exc:
        logger.warning("Kite portfolio fetch failed (non-blocking): %s", exc)
        summary["positions"] = {}
        summary["orders"]    = []

    summary["ok"] = len(summary["errors"]) == 0
    return summary
