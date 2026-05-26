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
            "Indices bhavcopy: VIX=%.2f, Nifty50=%.2f",
            summary.get("vix", 0),
            summary.get("nifty50_close", 0),
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
    Called by the 3:25 PM scheduler job (5 minutes before market close).

    On failure: logged + Telegram LOUD alert sent by scheduler (not here).
    Returns summary dict with {rows_stored, failed_symbols, ok}.
    """
    snap_date = snapshot_date or date.today()
    summary: dict = {"ok": False, "rows_stored": 0, "failed_symbols": []}

    try:
        nse_session = create_nse_session()
        all_rows, failed = run_snapshot_batch(nse_session, NIFTY50, snap_date)
        n_upserted = upsert_options_snapshots(all_rows)
        summary["rows_stored"]    = n_upserted
        summary["failed_symbols"] = failed
        summary["ok"]             = len(failed) < len(NIFTY50) // 2   # >50% OK = success
        logger.info(
            "Snapshot job: %d rows stored, %d symbols failed",
            n_upserted, len(failed),
        )
    except Exception as exc:
        logger.error("Snapshot job FAILED: %s", exc)
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
