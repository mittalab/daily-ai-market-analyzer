"""
Test: Kite OHLCV for all known indices + FUT data for JIOFIN and IOC.

Validates get_nse_token (EQ fallback for indices) and
db_to_kite_fut_name (DB symbol → Kite NFO name).

Run:
    py -m validation_tests.test_kite_ohlcv_and_fut
"""
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from new_data_ingestion.kite_oauth import get_authenticated_kite
from new_data_ingestion.kite_ohlcv import get_nse_token, fetch_ohlcv, _DB_TO_KITE_INDEX
from new_data_ingestion.kite_oi import fetch_futures_oi_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

ALL_INDICES = [
    "NIFTY_50",
    "NIFTY_BANK",
    "NIFTY_IT",
    "NIFTY_ENERGY",
    "NIFTY_AUTO",
    "NIFTY_FMCG",
    "NIFTY_PHARMA",
    "NIFTY_FIN_SERVICE",
    "NIFTY_METAL",
    "NIFTY_INFRA",
    "NIFTY_CONSUMPTION",
    "NIFTY_MEDIA",
]

FUT_SYMBOLS = ["JIOFIN", "IOC"]


# ── OHLCV test ────────────────────────────────────────────────────────────────

_OHLCV_HDR = f"  {'Date':<12} {'Open':>10} {'High':>10} {'Low':>10} {'Close':>10} {'Volume':>14}"
_OHLCV_SEP = f"  {'-'*70}"


def _print_ohlcv_rows(df) -> None:
    print(_OHLCV_HDR)
    print(_OHLCV_SEP)
    for _, r in df.iterrows():
        dt = r["date"]
        if hasattr(dt, "date"):
            dt = dt.date()
        print(
            f"  {str(dt):<12} "
            f"{r['open']:>10.2f} {r['high']:>10.2f} "
            f"{r['low']:>10.2f} {r['close']:>10.2f} "
            f"{int(r['volume']):>14,}"
        )


def test_index_ohlcv(kite, target_date: date) -> None:
    to_date = target_date

    passed = 0
    for db_sym in ALL_INDICES:
        kite_sym = _DB_TO_KITE_INDEX.get(db_sym, db_sym.replace("_", " "))
        print(f"\n{'='*74}")
        print(f"  INDEX OHLCV — {db_sym}  (Kite: '{kite_sym}')  date={to_date}")
        print(f"{'='*74}")
        try:
            token = get_nse_token(kite, db_sym)
            df    = fetch_ohlcv(kite, token, to_date, to_date)
            if df.empty:
                print("  [NO DATA RETURNED]")
            else:
                _print_ohlcv_rows(df)
                passed += 1
        except Exception as exc:
            print(f"  FAIL — {exc}")

    print(f"\n  ── Index OHLCV summary: {passed}/{len(ALL_INDICES)} returned data ──")


# ── Futures test ──────────────────────────────────────────────────────────────

_FUT_HDR = (
    f"  {'Date':<12} {'Open':>10} {'High':>10} {'Low':>10} "
    f"{'Close':>10} {'Volume':>12} {'OI (shares)':>14} {'OI (lots)':>10} {'OI Chg':>8}"
)
_FUT_SEP = f"  {'-'*98}"


def _print_futures_rows(df) -> None:
    print(_FUT_HDR)
    print(_FUT_SEP)
    for _, r in df.iterrows():
        dt = r["date"]
        if hasattr(dt, "date"):
            dt = dt.date()
        print(
            f"  {str(dt):<12} "
            f"{r['open']:>10.2f} {r['high']:>10.2f} "
            f"{r['low']:>10.2f} {r['close']:>10.2f} "
            f"{int(r['volume']):>12,} "
            f"{int(r['oi']):>14,} "
            f"{r['oi_lots']:>10.0f} "
            f"{r['oi_change']:>+8.0f}"
        )


def test_stock_futures(kite, target_date: date) -> None:
    results = fetch_futures_oi_all(kite, FUT_SYMBOLS, target_date=target_date)

    for db_sym in FUT_SYMBOLS:
        entry = results.get(db_sym)
        if not entry:
            print(f"\n{'='*74}")
            print(f"  FUTURES — {db_sym}: NO DATA (symbol not found in NFO)")
            print(f"{'='*74}")
            continue

        for label in ("near", "next"):
            expiry = entry.get(f"{label}_expiry")
            df     = entry.get(label)
            if expiry is None:
                continue
            print(f"\n{'='*74}")
            print(f"  FUTURES — {db_sym}  [{label.upper()} expiry: {expiry}]")
            print(f"{'='*74}")
            if df is None or df.empty:
                print("  [NO DATA RETURNED]")
            else:
                _print_futures_rows(df)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("Connecting to Kite...")
    kite = get_authenticated_kite()
    logger.info("Connected.")

    m_date = date.today() - timedelta(days=7)
    test_index_ohlcv(kite, m_date)
    test_stock_futures(kite, m_date)

    print()


if __name__ == "__main__":
    main()
