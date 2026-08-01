"""
Consolidated data ingestion utility helpers.
Provides modular APIs called by the self-healing validation recovery loop.
"""
import logging
import time
from datetime import date, datetime, timedelta
import pandas as pd
import requests

from database.queries import (
    upsert_price_history,
    upsert_price_history_new_only,
    upsert_fii_dii_flow,
    get_latest_fii_dii,
    upsert_options_snapshots,
    upsert_futures_snapshots,
    get_kite_token,
    get_client,
)
from new_data_ingestion.nse_bhavcopy import (
    fetch_equity_bhavcopy,
    fetch_indices_bhavcopy,
    equity_bhavcopy_to_price_rows,
    indices_to_price_rows,
    last_trading_day,
)
from new_data_ingestion.nse_option_chain import run_snapshot_batch, make_nse_session
from new_data_ingestion.kite_oauth import get_authenticated_kite
from new_data_ingestion.kite_ohlcv import fetch_ohlcv_all, ohlcv_to_price_rows, get_kite, get_option_symbols, fetch_option_quotes
from new_data_ingestion.kite_oi import fetch_futures_oi_all, futures_oi_to_snapshots_rows
from new_data_ingestion.backfill_vix import run_backfill as run_vix_backfill
from new_data_ingestion.fo_bhavcopy import run_backfill as run_fo_bhavcopy_backfill
from new_utils.stock_list import get_stock_list_for_analysis

logger = logging.getLogger(__name__)


def ingest_today_bhavcopy(for_date: date | None = None, no_overwrite: bool = False) -> dict:
    """
    Download NSE bhavcopy (equity + indices) for a specific date (defaults to last trading day).
    Stores results in Supabase `price_history`.

    no_overwrite=True uses INSERT ... ON CONFLICT DO NOTHING so existing rows
    (e.g. from Kite analysis) are preserved.
    """
    summary: dict = {"ok": True, "errors": [], "trade_date": None, "equity_rows": 0, "index_rows": 0, "fii_ok": False}
    target_date = last_trading_day(for_date)
    summary["trade_date"] = str(target_date)
    _upsert = upsert_price_history_new_only if no_overwrite else upsert_price_history

    # 1. Equity Bhavcopy
    try:
        eq_df, trade_date = fetch_equity_bhavcopy(target_date)
        eq_rows = equity_bhavcopy_to_price_rows(eq_df, trade_date)
        n_upserted = _upsert(eq_rows)
        summary["equity_rows"] = n_upserted
        logger.info("Equity bhavcopy ingested: %d rows for %s (no_overwrite=%s)", n_upserted, trade_date, no_overwrite)
    except Exception as exc:
        logger.error("Equity bhavcopy ingest FAILED: %s", exc)
        summary["errors"].append(f"equity_bhavcopy: {exc}")
        summary["ok"] = False

    # 2. Indices Bhavcopy
    try:
        indices, _ = fetch_indices_bhavcopy(target_date)
        index_rows = indices_to_price_rows(indices, target_date)
        n_upserted = _upsert(index_rows)
        summary["index_rows"] = len(index_rows)
        logger.info("Indices bhavcopy ingested: %d indices for %s (no_overwrite=%s)", n_upserted, target_date, no_overwrite)
    except Exception as exc:
        logger.error("Indices bhavcopy ingest FAILED: %s", exc)
        summary["errors"].append(f"indices_bhavcopy: {exc}")
        summary["ok"] = False

    # # 3. FII/DII Scraper
    # try:
    #     nse_session = create_nse_session()
    #     fii_dii = fetch_fii_dii(nse_session)
    #     db_row = fii_dii_to_db_row(fii_dii, target_date)
    #     upsert_fii_dii_flow(db_row)
    #     summary["fii_ok"] = True
    #     logger.info("FII/DII flow ingested for %s", target_date)
    # except Exception as exc:
    #     logger.warning("FII/DII ingest failed, using cached values: %s", exc)
    #     summary["errors"].append(f"fii_dii: {exc}")
    #     cached = get_latest_fii_dii()
    #     if cached:
    #         cached_row = dict(cached)
    #         cached_row.pop("id", None)
    #         cached_row.pop("created_at", None)
    #         cached_row["source"] = "CACHED"
    #         cached_row["date"] = str(target_date)
    #         upsert_fii_dii_flow(cached_row)
    #         logger.info("FII/DII using cached value from %s", cached.get("date"))

    return summary


def ingest_today_options(snapshot_date: date | None = None, symbols: list[str] | None = None) -> dict:
    """
    Fetch and store option chain snapshots for all symbols on the snapshot_date (defaults to today).
    Uses Kite Quotes as primary source and NSE scrape as secondary enrichment.
    """
    snap_date = snapshot_date or date.today()
    summary: dict = {"ok": False, "rows_stored": 0, "source": "NONE", "failed_symbols": [], "errors": []}
    stock_list_for_analysis = get_stock_list_for_analysis()
    target_symbols = symbols or list(stock_list_for_analysis.keys())

    # # A. Kite Quotes (Primary)
    # kite_rows = []
    # try:
    #     token_row = get_kite_token()
    #     if token_row:
    #         kite = get_kite(token_row["access_token"])
    #         for symbol in target_symbols:
    #             try:
    #                 instruments = get_option_symbols(kite, symbol)
    #                 if instruments.empty:
    #                     continue
    #                 quotes = fetch_option_quotes(kite, instruments)
    #                 for tk, q in quotes.items():
    #                     try:
    #                         tsym = tk.split(":")[-1]
    #                         matches = instruments[instruments["tradingsymbol"] == tsym]
    #                         if matches.empty:
    #                             continue
    #                         inst = matches.iloc[0]
    #                         expiry = inst["expiry"]
    #                         if hasattr(expiry, "date"):
    #                             expiry = expiry.date()
    #                         oi_high = q.get("oi_day_high") or 0
    #                         oi_low  = q.get("oi_day_low")  or 0
    #                         kite_rows.append({
    #                             "symbol":        symbol,
    #                             "snapshot_date": str(snap_date),
    #                             "expiry_date":   str(expiry),
    #                             "strike":        float(inst["strike"]),
    #                             "option_type":   inst["instrument_type"],
    #                             "oi":            int(q.get("oi") or 0),
    #                             "oi_change":     int(oi_high - oi_low),
    #                             "volume":        int(q.get("volume") or 0),
    #                             "iv":            None,
    #                             "premium_close": float(q.get("last_price") or 0),
    #                         })
    #                     except Exception:
    #                         continue
    #                 time.sleep(0.1)
    #             except Exception as sym_exc:
    #                 logger.warning("Kite option skip %s: %s", symbol, sym_exc)
    #
    #         if kite_rows:
    #             n = upsert_options_snapshots(kite_rows)
    #             summary.update({"ok": True, "rows_stored": n, "source": "KITE"})
    #             logger.info("Kite options snapshot stored: %d rows", n)
    #     else:
    #         summary["errors"].append("Kite token not found in DB")
    # except Exception as exc:
    #     logger.error("Kite option snapshot failed: %s", exc)
    #     summary["errors"].append(f"kite_options: {exc}")
    #
    # # B. NSE Scrape (Secondary/Enrichment for IVs)
    failed: list[str] = []
    try:
        nse_session = make_nse_session()
        nse_rows: list[dict]
        nse_rows, failed = run_snapshot_batch(nse_session, symbols=target_symbols, snapshot_date=snap_date)
        if nse_rows:
            n = upsert_options_snapshots(nse_rows)
            summary["ok"] = True
            summary["rows_stored"] = max(summary["rows_stored"], n)
            summary["source"] = "NSE"
            summary["failed_symbols"] = failed
            logger.info("NSE options snapshot stored/enriched: %d rows (source: %s)", n, summary["source"])
    except Exception as exc:
        logger.warning("NSE options enrichment failed (using Kite data): %s", exc)
        summary["errors"].append(f"nse_options_enrichment: {exc}")
        failed = list(target_symbols)

    # Bhavcopy OHLCV fallback for symbols that failed options scraping.
    # Options data is missing but at minimum price_history should be populated.
    if failed:
        logger.warning(
            "Options scrape failed for %d symbol(s): %s — attempting bhavcopy OHLCV fallback",
            len(failed), failed,
        )
        try:
            bhav_df, trade_date = fetch_equity_bhavcopy(snap_date)
            failed_upper = {s.upper() for s in failed}
            bhav_df_filtered = bhav_df[bhav_df["SYMBOL"].str.strip().str.upper().isin(failed_upper)]
            fallback_rows = equity_bhavcopy_to_price_rows(bhav_df_filtered, trade_date)
            if fallback_rows:
                upsert_price_history_new_only(fallback_rows)
                saved_syms = [r["symbol"] for r in fallback_rows]
                summary["bhavcopy_fallback_symbols"] = saved_syms
                logger.info(
                    "Bhavcopy OHLCV fallback: %d row(s) saved for option-failed symbols: %s",
                    len(fallback_rows), saved_syms,
                )
            else:
                logger.warning("Bhavcopy fallback: no matching rows found for %s", failed)
        except Exception as bhav_exc:
            logger.error("Bhavcopy OHLCV fallback also failed: %s", bhav_exc)
            summary["errors"].append(f"bhavcopy_fallback: {bhav_exc}")

    return summary


def _is_index(symbol: str) -> bool:
    """Return True for known NSE index symbols — these need OHLCV but not stock-style futures."""
    return symbol.startswith("NIFTY") or symbol == "INDIA_VIX"


def ingest_today_kite_data(for_date: date | None = None, symbols: list[str] | None = None) -> dict:
    """
    Ingest Kite OHLCV and futures snapshots for a specific date via the Kite API.

    - OHLCV is fetched for ALL symbols (stocks + indices).
    - Futures are fetched for STOCK symbols only (indices are excluded).
    - Fetches only the target_date — no historical lookback.
    """
    summary: dict = {"ok": True, "errors": [], "symbols_ohlcv": 0, "symbols_oi": 0}
    target_date    = for_date or date.today()
    target_symbols = symbols
    stock_symbols  = [s for s in target_symbols if not _is_index(s)]

    try:
        kite = get_authenticated_kite()

        # 1. OHLCV — all symbols (stocks + indices), single date only
        ohlcv_data = fetch_ohlcv_all(kite, target_symbols, target_date=target_date)
        price_rows = []
        for symbol, df in ohlcv_data.items():
            if not df.empty:
                price_rows.extend(ohlcv_to_price_rows(symbol, df))
                summary["symbols_ohlcv"] += 1
        if price_rows:
            upsert_price_history(price_rows)
            logger.info("Kite OHLCV ingested: %d symbols, %d rows for %s",
                        summary["symbols_ohlcv"], len(price_rows), target_date)

        # 2. Futures — stock symbols only, single date only
        if len(stock_symbols) > 0:
            oi_data = fetch_futures_oi_all(kite, stock_symbols, target_date=target_date)
            snapshot_rows = []

            for symbol, entry in oi_data.items():
                near_df     = entry.get("near")
                next_df     = entry.get("next")
                near_expiry = entry.get("near_expiry")
                next_expiry = entry.get("next_expiry")

                if near_df is None or near_df.empty:
                    continue

                rows = futures_oi_to_snapshots_rows(
                    symbol, near_df, next_df if next_df is not None else pd.DataFrame(),
                    near_expiry, next_expiry,
                )
                snapshot_rows.extend(rows)
                summary["symbols_oi"] += 1

            if snapshot_rows:
                n = upsert_futures_snapshots(snapshot_rows)
                logger.info("Kite futures snapshots stored: %d rows for %d symbols on %s",
                            n, summary["symbols_oi"], target_date)

    except Exception as exc:
        logger.error("Kite data fetch FAILED: %s", exc)
        summary["errors"].append(f"kite_data_fetch: {exc}")
        summary["ok"] = False

    return summary


def _check_backfill_coverage(target_date: date) -> None:
    """
    After a full backfill, log which expected symbols are missing in each table.
    Three bulk queries: price_history (equity + indices), futures_snapshots, options_snapshots.
    """
    expected_stocks  = sorted(get_stock_list_for_analysis().keys())
    expected_indices = ["INDIA_VIX", "NIFTY_50"]
    all_equity       = expected_stocks + expected_indices

    # ── OHLCV (equity + indices) ──────────────────────────────────────────────
    try:
        resp = (
            get_client()
            .table("price_history")
            .select("symbol")
            .eq("date", str(target_date))
            .in_("symbol", all_equity)
            .limit(len(all_equity) + 10)
            .execute()
        )
        present = {r["symbol"] for r in resp.data}
        missing_stocks  = [s for s in expected_stocks  if s not in present]
        missing_indices = [s for s in expected_indices if s not in present]
        logger.info(
            "Backfill coverage [OHLCV]   %s: %d/%d stocks, %d/%d indices%s%s",
            target_date,
            len(expected_stocks)  - len(missing_stocks),  len(expected_stocks),
            len(expected_indices) - len(missing_indices), len(expected_indices),
            f" | stocks missing: {missing_stocks}"   if missing_stocks  else "",
            f" | indices missing: {missing_indices}" if missing_indices else "",
        )
    except Exception as exc:
        logger.error("OHLCV coverage check failed for %s: %s", target_date, exc)

    # ── Futures ───────────────────────────────────────────────────────────────
    try:
        resp = (
            get_client()
            .table("futures_snapshots")
            .select("symbol")
            .eq("snapshot_date", str(target_date))
            .in_("symbol", expected_stocks)
            .limit(len(expected_stocks) * 3)   # up to 2 expiries per stock + buffer
            .execute()
        )
        present = {r["symbol"] for r in resp.data}
        missing = [s for s in expected_stocks if s not in present]
        logger.info(
            "Backfill coverage [Futures] %s: %d/%d stocks%s",
            target_date,
            len(expected_stocks) - len(missing), len(expected_stocks),
            f" | missing: {missing}" if missing else "",
        )
    except Exception as exc:
        logger.error("Futures coverage check failed for %s: %s", target_date, exc)

    # ── Options ───────────────────────────────────────────────────────────────
    try:
        resp = (
            get_client()
            .table("options_snapshots")
            .select("symbol")
            .eq("snapshot_date", str(target_date))
            .in_("symbol", expected_stocks)
            .limit(10000)   # many strikes per symbol; dedup in Python
            .execute()
        )
        present = {r["symbol"] for r in resp.data}
        missing = [s for s in expected_stocks if s not in present]
        logger.info(
            "Backfill coverage [Options] %s: %d/%d stocks%s",
            target_date,
            len(expected_stocks) - len(missing), len(expected_stocks),
            f" | missing: {missing}" if missing else "",
        )
    except Exception as exc:
        logger.error("Options coverage check failed for %s: %s", target_date, exc)


def backfill_historical_date(target_date: date, symbol: str | None = None, options_to_heal : bool = True) -> dict:
    """
    Backfill past historical data for a specific date:
    - Equity & Indices bhavcopy -> price_history
    - F&O bhavcopy -> options_snapshots and futures_snapshots
    - India VIX -> price_history
    """
    summary: dict = {"ok": True, "errors": [], "bhavcopy": None, "fo_bhavcopy": None, "vix": None}
    logger.info("Starting historical backfill for date %s (symbol filter: %s)", target_date, symbol)

    # 1. Equity & Index Bhavcopy
    try:
        bhav_summary = ingest_today_bhavcopy(target_date)
        summary["bhavcopy"] = bhav_summary
        if not bhav_summary["ok"]:
            summary["ok"] = False
    except Exception as exc:
        logger.error("Bhavcopy backfill failed for %s: %s", target_date, exc)
        summary["errors"].append(f"bhavcopy: {exc}")
        summary["ok"] = False


    # 2. F&O Bhavcopy (Populates option snaps and futures snaps)
    if options_to_heal:
        try:
            fo_summary = run_fo_bhavcopy_backfill([target_date])
            summary["fo_bhavcopy"] = fo_summary
            if target_date in fo_summary.get("failed", []):
                summary["ok"] = False
        except Exception as exc:
            logger.error("F&O bhavcopy backfill failed for %s: %s", target_date, exc)
            summary["errors"].append(f"fo_bhavcopy: {exc}")
            summary["ok"] = False

    # # 3. Coverage validation — log which symbols landed in each table
    # _check_backfill_coverage(target_date)

    return summary

if __name__ == '__main__':
    ingest_today_options(symbols=['JIOFIN'])