"""
Kite Connect — fetch open F&O + commodity positions.

Covers NFO (equity derivatives) and MCX (commodities like SILVER, GOLD).
Reads the access token from Supabase kite_tokens table.

Returns a dict keyed by underlying symbol:
    {
        "NIFTY":  [{"symbol": ..., "qty": ..., "avg": ..., "ltp": ..., "pnl": ...}, ...],
        "SILVER": [{"symbol": ..., "qty": ..., "avg": ..., "ltp": ..., "pnl": ...}],
    }

Run standalone:
    py -m new_integration.kite_positions
    py -m new_integration.kite_positions --include-day
    py -m new_integration.kite_positions --json
"""
import argparse
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from database.queries import get_kite_token
from new_data_ingestion.kite_ohlcv import get_kite

logger = logging.getLogger(__name__)

# Exchanges to include
_TARGET_EXCHANGES = {"NFO", "MCX"}

# Underlying name remaps (Kite name → canonical name used in this project)
_NAME_REMAP: dict[str, str] = {}


# ── Instrument master → underlying name ────────────────────────────────────────

def _build_token_name_map(kite) -> dict[int, str]:
    """Return instrument_token → underlying name for NFO and MCX."""
    result: dict[int, str] = {}
    for exchange in _TARGET_EXCHANGES:
        try:
            for inst in kite.instruments(exchange):
                token = inst.get("instrument_token")
                name  = inst.get("name", "")
                if token and name:
                    result[int(token)] = name
            logger.debug("Loaded %s instrument names", exchange)
        except Exception as exc:
            logger.warning("Could not load %s instruments: %s", exchange, exc)
    return result


def _fallback_underlying(tradingsymbol: str) -> str:
    """
    Best-effort underlying extraction when instrument master lookup fails.
    Strips trailing FUT / CE / PE and the date portion (YYMM or DDMONYY).
    """
    ts = tradingsymbol
    for suffix in ("FUT", "CE", "PE"):
        if ts.endswith(suffix):
            ts = ts[: -len(suffix)]
            break

    # Strip trailing digits (strike or date portion like 2507, 24500)
    while ts and ts[-1].isdigit():
        ts = ts[:-1]

    # Strip trailing month abbreviation if present (JAN, FEB, … DEC)
    months = {"JAN", "FEB", "MAR", "APR", "MAY", "JUN",
               "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"}
    if len(ts) >= 3 and ts[-3:].upper() in months:
        ts = ts[:-3]

    # Strip remaining trailing digits (day part of DDMONYY)
    while ts and ts[-1].isdigit():
        ts = ts[:-1]

    return ts or tradingsymbol


# ── Core fetch ──────────────────────────────────────────────────────────────────

def fetch_fo_positions(net_only: bool = True) -> dict[str, dict[str, list[dict]]]:
    """
    Fetch open F&O / commodity positions from Kite.

    Args:
        net_only: If True (default) use net positions. If False, also merge
                  intraday (day) positions not present in net.

    Returns:
        Nested dict: { exchange → { underlying → [position, ...] } }

        Example:
        {
          "NFO": {
            "NIFTY_50": [{"symbol": "NIFTY2507FUT", "qty": 75, ...}],
            "RELIANCE": [{"symbol": "RELIANCE25JULFUT", ...}],
          },
          "MCX": {
            "SILVER": [{"symbol": "SILVERM25JULFUT", ...}],
          }
        }
    """
    token_row = get_kite_token()
    if not token_row:
        raise RuntimeError(
            "No Kite access token in DB — run the OAuth flow first"
        )

    kite = get_kite(token_row["access_token"])
    raw  = kite.positions()

    bucket: list[dict] = list(raw.get("net", []))
    if not net_only:
        seen = {r["tradingsymbol"] for r in bucket}
        for r in raw.get("day", []):
            if r["tradingsymbol"] not in seen:
                bucket.append(r)

    # Keep only target exchanges and non-zero quantity
    open_positions = [
        r for r in bucket
        if r.get("exchange") in _TARGET_EXCHANGES and int(r.get("quantity", 0)) != 0
    ]

    logger.info(
        "Raw positions: %d total, %d open on %s",
        len(bucket), len(open_positions), "/".join(sorted(_TARGET_EXCHANGES)),
    )

    # Build token → underlying name map (one API call per exchange)
    token_name = _build_token_name_map(kite)

    # Initialise result with all target exchanges present even if empty
    result: dict[str, dict[str, list[dict]]] = {ex: {} for ex in sorted(_TARGET_EXCHANGES)}

    for row in open_positions:
        exchange   = row.get("exchange", "")
        token      = int(row.get("instrument_token", 0))
        underlying = token_name.get(token) or _fallback_underlying(
            row.get("tradingsymbol", "")
        )
        underlying = _NAME_REMAP.get(underlying, underlying)

        entry = {
            "symbol":     row.get("tradingsymbol", ""),
            "qty":        int(row.get("quantity", 0)),
            "avg":        float(row.get("average_price", 0) or 0),
            "ltp":        float(row.get("last_price", 0) or 0),
            "pnl":        round(float(row.get("pnl", 0) or 0), 2),
            "unrealised": round(float(row.get("unrealised", 0) or 0), 2),
            "realised":   round(float(row.get("realised", 0) or 0), 2),
            "product":    row.get("product", ""),
            "exchange":   exchange,
            "buy_qty":    int(row.get("buy_quantity", 0)),
            "sell_qty":   int(row.get("sell_quantity", 0)),
        }
        result.setdefault(exchange, {}).setdefault(underlying, []).append(entry)

    return result


# ── CLI ─────────────────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )


def _print_positions(positions: dict[str, dict[str, list[dict]]]) -> None:
    total_pnl = sum(
        p["pnl"]
        for ex_map in positions.values()
        for rows in ex_map.values()
        for p in rows
    )
    if not any(positions.values()):
        print("\n  No open F&O / commodity positions.\n")
        return

    print(f"\n{'='*68}")
    print("  Open Positions by Exchange → Underlying")
    print(f"{'='*68}")

    for exchange in sorted(positions):
        underlyings = positions[exchange]
        if not underlyings:
            continue
        print(f"\n  [{exchange}]")
        for underlying in sorted(underlyings):
            rows = underlyings[underlying]
            print(f"\n    {underlying}")
            print(f"    {'Symbol':<26} {'Qty':>6} {'Avg':>10} {'LTP':>10} {'P&L':>11}")
            print(f"    {'-'*63}")
            for p in rows:
                sign = "+" if p["pnl"] >= 0 else ""
                print(
                    f"    {p['symbol']:<26} {p['qty']:>6} "
                    f"{p['avg']:>10.2f} {p['ltp']:>10.2f} "
                    f"{sign}{p['pnl']:>10.2f}"
                )

    sign = "+" if total_pnl >= 0 else ""
    print(f"\n{'='*68}")
    print(f"  {'NET TOTAL P&L':>60} {sign}{total_pnl:>7.2f}")
    print(f"{'='*68}\n")


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser(
        description="Fetch open F&O + commodity positions from Kite"
    )
    parser.add_argument(
        "--include-day", action="store_true",
        help="Also include intraday (day) positions not present in net",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output raw JSON instead of formatted table",
    )
    args = parser.parse_args()

    logger.info("Fetching open positions from Kite (NFO + MCX)...")
    positions = fetch_fo_positions(net_only=not args.include_day)
    total_pos = sum(len(rows) for ex_map in positions.values() for rows in ex_map.values())
    total_und = sum(len(ex_map) for ex_map in positions.values())
    logger.info("Found %d underlying(s), %d total position(s)", total_und, total_pos)

    if args.json:
        print(json.dumps(positions, indent=2))
    else:
        _print_positions(positions)


if __name__ == "__main__":
    main()
