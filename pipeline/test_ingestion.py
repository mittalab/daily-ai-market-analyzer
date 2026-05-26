"""
Integration test runner for Week 2 data ingestion.

Run from the project root:
    python -m pipeline.test_ingestion

Tests each source independently and prints results. Each test is independent —
failure of one does not abort the rest. Kite tests are skipped if no valid token.
"""
import sys
import os
import traceback
from datetime import date

# Ensure project root is on sys.path when run as a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

PASS = "[PASS]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"
SEP  = "-" * 60


def section(title: str) -> None:
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


# ── 1. Equity bhavcopy ────────────────────────────────────────────────────────

def test_equity_bhavcopy() -> bool:
    section("TEST 1: NSE Equity Bhavcopy > price_history")
    try:
        from integrations.nse_bhavcopy import fetch_equity_bhavcopy, equity_bhavcopy_to_price_rows, last_trading_day
        from database.queries import upsert_price_history, get_price_history

        trade_date = last_trading_day()
        print(f"  Last trading day : {trade_date}")

        eq_df, actual_date = fetch_equity_bhavcopy()
        print(f"  Bhavcopy date    : {actual_date}")
        print(f"  Raw rows fetched : {len(eq_df)}")

        rows = equity_bhavcopy_to_price_rows(eq_df, actual_date)
        print(f"  Nifty 50 rows    : {len(rows)}")

        n = upsert_price_history(rows)
        print(f"  Rows upserted    : {n}")

        # Spot-check: fetch HDFCBANK from DB
        hist = get_price_history("HDFCBANK", days=3)
        if hist:
            latest = hist[0]
            print(f"  HDFCBANK latest  : close={latest.get('close')}, date={latest.get('date')}")
        else:
            print("  HDFCBANK         : no rows found (symbol may not be in today's bhavcopy yet)")

        print(f"\n  {PASS} Equity bhavcopy OK")
        return True
    except Exception:
        print(f"\n  {FAIL} Equity bhavcopy FAILED:")
        traceback.print_exc()
        return False


# ── 2. Indices bhavcopy (VIX) ─────────────────────────────────────────────────

def test_indices_bhavcopy() -> bool:
    section("TEST 2: NSE Indices Bhavcopy > VIX + Nifty50")
    try:
        from integrations.nse_bhavcopy import fetch_indices_bhavcopy, indices_to_price_rows, last_trading_day
        from database.queries import upsert_price_history, get_price_history

        ref_date = last_trading_day()
        indices, actual_date = fetch_indices_bhavcopy(ref_date)

        print(f"  Date             : {actual_date}")
        print(f"  Indices returned : {len(indices)}")
        print(f"  India VIX        : {indices.get('India VIX')}")
        print(f"  Nifty 50         : {indices.get('Nifty 50')}")
        print(f"  Nifty Bank       : {indices.get('Nifty Bank')}")

        rows = indices_to_price_rows(indices, actual_date)
        print(f"  DB rows prepared : {len(rows)}")

        n = upsert_price_history(rows)
        print(f"  Rows upserted    : {n}")

        # Verify VIX stored
        vix_hist = get_price_history("INDIA_VIX", days=3)
        if vix_hist:
            print(f"  INDIA_VIX in DB  : {vix_hist[0].get('close')} on {vix_hist[0].get('date')}")
        else:
            print("  INDIA_VIX in DB  : not found")

        print(f"\n  {PASS} Indices bhavcopy OK")
        return True
    except Exception:
        print(f"\n  {FAIL} Indices bhavcopy FAILED:")
        traceback.print_exc()
        return False


# ── 3. FII / DII ─────────────────────────────────────────────────────────────

def test_fii_dii() -> bool:
    section("TEST 3: NSE FII/DII flows")
    try:
        from integrations.nse_fii_dii import create_nse_session, fetch_fii_dii, fii_dii_to_db_row
        from integrations.nse_bhavcopy import last_trading_day
        from database.queries import upsert_fii_dii_flow, get_fii_dii_flows

        trade_date = last_trading_day()
        print(f"  Trade date       : {trade_date}")

        session = create_nse_session()
        print("  NSE session      : created")

        data = fetch_fii_dii(session)
        fii = data.get("FII", {})
        dii = data.get("DII", {})
        print(f"  FII net (Cr)     : {fii.get('netValue')}")
        print(f"  DII net (Cr)     : {dii.get('netValue')}")

        row = fii_dii_to_db_row(data, trade_date)
        upsert_fii_dii_flow(row)
        print("  Upserted to DB   : ok")

        recent = get_fii_dii_flows(days=3)
        print(f"  Recent rows in DB: {len(recent)}")
        if recent:
            r = recent[0]
            print(f"  Latest row       : date={r.get('date')}, FII={r.get('fii_net_cr')}, DII={r.get('dii_net_cr')}")

        print(f"\n  {PASS} FII/DII OK")
        return True
    except Exception:
        print(f"\n  {FAIL} FII/DII FAILED:")
        traceback.print_exc()
        return False


# ── 4. Kite OHLCV (HDFCBANK) ─────────────────────────────────────────────────

def test_kite_ohlcv() -> bool:
    section("TEST 4: Kite OHLCV > HDFCBANK last 5 rows")
    try:
        from integrations.kite_oauth import get_authenticated_kite, validate_token
        from integrations.kite_ohlcv import fetch_ohlcv_all, ohlcv_to_price_rows
        from database.queries import upsert_price_history, get_price_history

        if not validate_token():
            print(f"  {SKIP} No valid Kite token — complete OAuth flow first")
            return True  # Not a failure, just skipped

        kite = get_authenticated_kite()
        print("  Kite auth        : OK")

        ohlcv = fetch_ohlcv_all(kite, ["HDFCBANK"], days=10)
        df = ohlcv.get("HDFCBANK")

        if df is None or df.empty:
            print(f"  {FAIL} HDFCBANK: empty DataFrame returned")
            return False

        print(f"  Rows fetched     : {len(df)}")
        print(f"\n  Last 5 rows:")
        print(f"  {'date':<12}  {'open':>10}  {'high':>10}  {'low':>10}  {'close':>10}  {'volume':>12}")
        for _, row in df.tail(5).iterrows():
            print(
                f"  {str(row.name)[:10]:<12}  "
                f"{row['open']:>10.2f}  "
                f"{row['high']:>10.2f}  "
                f"{row['low']:>10.2f}  "
                f"{row['close']:>10.2f}  "
                f"{int(row['volume']):>12,}"
            )

        price_rows = ohlcv_to_price_rows("HDFCBANK", df)
        n = upsert_price_history(price_rows)
        print(f"\n  Rows upserted    : {n}")

        print(f"\n  {PASS} Kite OHLCV OK")
        return True
    except Exception:
        print(f"\n  {FAIL} Kite OHLCV FAILED:")
        traceback.print_exc()
        return False


# ── 5. Kite Futures OI (NIFTY) ───────────────────────────────────────────────

def test_kite_futures_oi() -> bool:
    section("TEST 5: Kite Futures OI > NIFTY last 5 rows")
    try:
        from integrations.kite_oauth import get_authenticated_kite, validate_token
        from integrations.kite_oi import fetch_futures_oi_all, futures_oi_to_series_rows
        from database.queries import upsert_futures_series

        if not validate_token():
            print(f"  {SKIP} No valid Kite token — complete OAuth flow first")
            return True

        kite = get_authenticated_kite()
        print("  Kite auth        : OK")

        oi_data = fetch_futures_oi_all(kite, ["NIFTY"], days=10)
        entry = oi_data.get("NIFTY", {})
        near_df   = entry.get("near")
        lot_size  = entry.get("lot_size", 1)
        near_exp  = entry.get("near_expiry")

        if near_df is None or near_df.empty:
            print(f"  {FAIL} NIFTY near futures: empty DataFrame")
            return False

        print(f"  Lot size         : {lot_size}")
        print(f"  Near expiry      : {near_exp}")
        print(f"  Rows fetched     : {len(near_df)}")
        print(f"\n  Last 5 rows (near):")
        print(f"  {'date':<12}  {'close':>10}  {'OI (lots)':>12}  {'volume':>10}")
        for _, row in near_df.tail(5).iterrows():
            oi_lots = int(row.get("oi", 0)) // lot_size if lot_size else 0
            print(
                f"  {str(row.name)[:10]:<12}  "
                f"{row.get('close', 0):>10.2f}  "
                f"{oi_lots:>12,}  "
                f"{int(row.get('volume', 0)):>10,}"
            )

        next_df = entry.get("next")
        if next_df is None:
            next_df = type(near_df)()
        rows = futures_oi_to_series_rows(
            "NIFTY", near_df, next_df, lot_size,
            entry.get("near_expiry"), entry.get("next_expiry"), "NORMAL",
        )
        for row in rows:
            upsert_futures_series(row)
        print(f"\n  Rows upserted    : {len(rows)}")

        print(f"\n  {PASS} Kite Futures OI OK")
        return True
    except Exception:
        print(f"\n  {FAIL} Kite Futures OI FAILED:")
        traceback.print_exc()
        return False


# ── Runner ────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  Week 2 Ingestion Test Runner")
    print(f"  Date: {date.today()}")
    print("=" * 60)

    results = {
        "Equity bhavcopy" : test_equity_bhavcopy(),
        "Indices / VIX"   : test_indices_bhavcopy(),
        "FII/DII"         : test_fii_dii(),
        "Kite OHLCV"      : test_kite_ohlcv(),
        "Kite Futures OI" : test_kite_futures_oi(),
    }

    print(f"\n{'=' * 60}")
    print("  Summary")
    print("=" * 60)
    for name, ok in results.items():
        status = PASS if ok else FAIL
        print(f"  {status}  {name}")

    failed = sum(1 for ok in results.values() if not ok)
    if failed == 0:
        print(f"\n  All tests passed.")
    else:
        print(f"\n  {failed} test(s) failed — check output above.")
    print()


if __name__ == "__main__":
    main()
