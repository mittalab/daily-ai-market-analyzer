"""
Manual data refresh — run before the 10 PM pipeline when scheduled jobs failed.

Usage:
    py pipeline/_manual_populate.py               # all: FII/DII + snapshot + Kite OHLCV/OI
    py pipeline/_manual_populate.py --fii-only
    py pipeline/_manual_populate.py --snap-only
    py pipeline/_manual_populate.py --kite-only   # OHLCV + futures OI for Nifty50 + watchlist

What it does:
  1. Bhavcopy (FII/DII + NSE equity prices) — Nifty50 + watchlist via get_ingestion_symbols()
  2. Option snapshot (Kite Tier 2 fallback — OI without IV) — same symbol set
  3. Kite OHLCV (250d history) + futures OI (30d near+next) — Nifty50 + watchlist
     This is what the 10PM pipeline would do; useful to pre-populate before it runs.
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("manual_populate")


def run_fii():
    from datetime import date
    from pipeline.data_ingestion import run_bhavcopy_job
    logger.info("=== [2/3] Bhavcopy / FII-DII refresh (Nifty50 + watchlist) ===")
    summary = run_bhavcopy_job(date.today())
    if summary.get("fii_ok"):
        logger.info("FII/DII: LIVE  FII=%+.0f Cr  DII=%+.0f Cr",
                    summary.get("fii_net_cr", 0), summary.get("dii_net_cr", 0))
    else:
        logger.warning("FII/DII: CACHED (live fetch failed) — %s", summary.get("errors"))
    logger.info("Equity rows stored: %d | VIX: %s",
                summary.get("equity_rows", 0), summary.get("vix"))
    return summary


def run_snapshot():
    from datetime import date
    from pipeline.data_ingestion import run_snapshot_job
    logger.info("=== [3/3] Option snapshot — Kite Tier 2 fallback (Nifty50 + watchlist) ===")
    summary = run_snapshot_job(date.today())
    if summary.get("ok"):
        logger.info("Snapshot OK: %d rows stored  source=%s  failed=%s",
                    summary.get("rows_stored", 0),
                    summary.get("source", "?"),
                    summary.get("failed_symbols", []))
    else:
        logger.error("Snapshot FAILED: %s", summary.get("error", "unknown"))
    return summary


def run_kite():
    """
    Fetch Kite OHLCV (250d) + futures OI (30d near+next) for Nifty50 + ALL watchlist stages.
    Delegates to run_kite_data_fetch() which now calls get_ingestion_symbols(all_stages=True).
    """
    from pipeline.data_ingestion import get_ingestion_symbols, run_kite_data_fetch
    from database.queries import get_kite_token
    from integrations.kite_ohlcv import get_kite
    from integrations.nse_bhavcopy import get_nifty50_symbols

    logger.info("=== [1/3] Kite OHLCV + futures OI (Nifty50 + ALL watchlist stages) ===")

    token_row = get_kite_token()
    if not token_row:
        logger.error("Kite token missing — run /kite/refresh first")
        return {"ok": False, "error": "no_token"}

    kite = get_kite(token_row["access_token"])

    symbols = get_ingestion_symbols(all_stages=True)
    nifty50 = set(get_nifty50_symbols())
    extra   = sorted(s for s in symbols if s not in nifty50)
    logger.info(
        "Symbol set: %d total (%d Nifty50 + %d extra: %s)",
        len(symbols), len(nifty50), len(extra), extra or "none",
    )

    summary = run_kite_data_fetch(kite, rollover_phase="NORMAL")
    if summary.get("ok"):
        logger.info("Kite OK: OHLCV=%d symbols  futures_OI=%d symbols",
                    summary.get("symbols_ohlcv", 0), summary.get("symbols_oi", 0))
    else:
        logger.warning("Kite partial: errors=%s", summary.get("errors", []))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manual data populate before 10PM pipeline")
    parser.add_argument("--fii-only",  action="store_true", help="Bhavcopy/FII only")
    parser.add_argument("--snap-only", action="store_true", help="Option snapshot only")
    parser.add_argument("--kite-only", action="store_true", help="Kite OHLCV + futures OI only")
    args = parser.parse_args()

    results = {}

    if args.fii_only:
        results["fii"] = run_fii()
    elif args.snap_only:
        results["snapshot"] = run_snapshot()
    elif args.kite_only:
        results["kite"] = run_kite()
    else:
        results["kite"]     = run_kite()
        results["fii"]      = run_fii()
        results["snapshot"] = run_snapshot()

    print("\n=== Summary ===")
    if "fii" in results:
        f = results["fii"]
        print(f"FII/DII ok:    {f.get('fii_ok')} | Equity rows: {f.get('equity_rows', 0)} | VIX: {f.get('vix')}")
    if "snapshot" in results:
        s = results["snapshot"]
        print(f"Snapshot ok:   {s.get('ok')} | Rows: {s.get('rows_stored', 0)} | Source: {s.get('source', 'N/A')}")
    if "kite" in results:
        k = results["kite"]
        print(f"Kite OHLCV:    {k.get('symbols_ohlcv', 0)} symbols")
        print(f"Kite Futures:  {k.get('symbols_oi', 0)} symbols")
        if k.get("errors"):
            print(f"Kite errors:   {k['errors']}")
