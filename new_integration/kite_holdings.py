"""
Kite Connect — fetch open equity holdings.

Covers NSE and BSE equity holdings.
Reads the access token from Supabase kite_tokens table.

Returns a dict keyed by exchange and symbol:
    {
        "NSE": {
            "HINDALCO": [{"symbol": ..., "qty": ..., "avg": ..., "ltp": ..., "pnl": ...}],
        },
        "BSE": {
            "AFIL": [...]
        }
    }

Run standalone:
    py -m new_integration.kite_holdings
    py -m new_integration.kite_holdings --json
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
_TARGET_EXCHANGES = {"NSE", "BSE"}

# Name remaps if needed (Kite name → canonical name used in this project)
_NAME_REMAP: dict[str, str] = {}


def fetch_holdings() -> dict[str, dict[str, list[dict]]]:
    """
    Fetch equity holdings from Kite.

    Returns:
        Nested dict: { exchange → { symbol → [holding_entry, ...] } }

        Example:
        {
          "NSE": {
            "HINDALCO": [{
              "symbol": "HINDALCO",
              "isin": "INE038A01020",
              "qty": 700,
              "free_qty": 0,
              "t1_qty": 0,
              "collateral_qty": 700,
              "collateral_type": "pledge",
              "avg": 1032.15,
              "ltp": 953.20,
              "close": 953.20,
              "pnl": -55263.50,
              "current_value": 667240.00,
              "investment_value": 722503.50,
              "day_change": 0.0,
              "day_change_pct": 0.0,
              "product": "CNC",
              "exchange": "NSE"
            }]
          },
          "BSE": { ... }
        }
    """
    token_row = get_kite_token()
    if not token_row:
        raise RuntimeError(
            "No Kite access token in DB — run the OAuth flow first"
        )

    kite = get_kite(token_row["access_token"])
    raw = kite.holdings()

    logger.info("Raw holdings fetched: %d total", len(raw))

    # Initialize result with target exchanges present
    result: dict[str, dict[str, list[dict]]] = {ex: {} for ex in sorted(_TARGET_EXCHANGES)}

    for row in raw:
        exchange = row.get("exchange", "")
        if exchange not in _TARGET_EXCHANGES:
            continue

        # Calculate total quantity including T1, Collateral, and MTF
        qty = int(row.get("quantity", 0))
        t1_qty = int(row.get("t1_quantity", 0))
        collateral_qty = int(row.get("collateral_quantity", 0))
        
        mtf_qty = 0
        mtf = row.get("mtf")
        if isinstance(mtf, dict):
            mtf_qty = int(mtf.get("quantity", 0))
        elif mtf is not None:
            try:
                mtf_qty = int(mtf)
            except (ValueError, TypeError):
                pass

        total_qty = qty + t1_qty + collateral_qty + mtf_qty

        # Skip zero quantity holdings
        if total_qty == 0:
            continue

        symbol = row.get("tradingsymbol", "")
        symbol = _NAME_REMAP.get(symbol, symbol)

        avg_price = float(row.get("average_price", 0) or 0)
        last_price = float(row.get("last_price", 0) or 0)
        close_price = float(row.get("close_price", 0) or 0)
        pnl = float(row.get("pnl", 0) or 0)
        day_change = float(row.get("day_change", 0) or 0)
        day_change_pct = float(row.get("day_change_percentage", 0) or 0)

        investment_value = total_qty * avg_price
        current_value = total_qty * last_price

        entry = {
            "symbol":             symbol,
            "qty":                total_qty,
            "free_qty":           qty,
            "t1_qty":             t1_qty,
            "collateral_qty":     collateral_qty,
            "collateral_type":    row.get("collateral_type", ""),
            "avg":                round(avg_price, 4),
            "ltp":                round(last_price, 2),
            "close":              round(close_price, 2),
            "pnl":                round(pnl, 2),
            "current_value":      round(current_value, 2),
            "investment_value":   round(investment_value, 2),
            "day_change":         round(day_change, 2),
            "day_change_pct":     round(day_change_pct, 2),
            "isin":               row.get("isin", ""),
            "product":            row.get("product", ""),
            "exchange":           exchange,
        }

        result.setdefault(exchange, {}).setdefault(symbol, []).append(entry)

    return result


def _print_holdings(holdings: dict[str, dict[str, list[dict]]]) -> None:
    total_investment = 0.0
    total_current_value = 0.0
    total_pnl = 0.0

    # Calculate totals
    for ex_map in holdings.values():
        for rows in ex_map.values():
            for h in rows:
                total_investment += h["investment_value"]
                total_current_value += h["current_value"]
                total_pnl += h["pnl"]

    if not any(holdings.values()):
        print("\n  No holdings found.\n")
        return

    print(f"\n{'='*95}")
    print("  Open Equity Holdings by Exchange -> Symbol")
    print(f"{'='*95}")

    for exchange in sorted(holdings):
        symbols_map = holdings[exchange]
        if not symbols_map:
            continue
        print(f"\n  [{exchange}]")
        print(f"    {'Symbol':<18} {'Qty (F/T/C)':<18} {'Avg':>10} {'LTP':>10} {'Invested':>12} {'Current':>12} {'P&L':>15}")
        print(f"    {'-'*91}")
        for symbol in sorted(symbols_map):
            rows = symbols_map[symbol]
            for h in rows:
                qty_str = f"{h['qty']}"
                breakdown = []
                if h['qty'] != h['free_qty']:
                    if h['free_qty']:
                        breakdown.append(f"F:{h['free_qty']}")
                    if h['t1_qty']:
                        breakdown.append(f"T:{h['t1_qty']}")
                    if h['collateral_qty']:
                        breakdown.append(f"C:{h['collateral_qty']}")
                if breakdown:
                    qty_str += f" ({'/'.join(breakdown)})"
                
                sign = "+" if h["pnl"] >= 0 else ""
                pnl_pct = (h["pnl"] / h["investment_value"] * 100) if h["investment_value"] else 0.0
                pnl_str = f"{sign}{h['pnl']:.2f} ({pnl_pct:+.2f}%)"
                print(
                    f"    {h['symbol']:<18} {qty_str:<18} "
                    f"{h['avg']:>10.2f} {h['ltp']:>10.2f} "
                    f"{h['investment_value']:>12.2f} {h['current_value']:>12.2f} "
                    f"{pnl_str:>15}"
                )

    net_pnl_pct = (total_pnl / total_investment * 100) if total_investment else 0.0
    sign = "+" if total_pnl >= 0 else ""
    print(f"\n{'='*95}")
    print(f"  {'TOTAL INVESTED VALUE:':<25} {total_investment:>12.2f}")
    print(f"  {'TOTAL CURRENT VALUE:':<25} {total_current_value:>12.2f}")
    print(f"  {'NET TOTAL P&L:':<25} {sign}{total_pnl:>11.2f} ({net_pnl_pct:+.2f}%)")
    print(f"{'='*95}\n")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser(
        description="Fetch equity holdings from Kite"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output raw JSON instead of formatted table",
    )
    args = parser.parse_args()

    logger.info("Fetching equity holdings from Kite (NSE + BSE)...")
    try:
        holdings = fetch_holdings()
        total_sym = sum(len(rows) for ex_map in holdings.values() for rows in ex_map.values())
        total_qty = sum(h["qty"] for ex_map in holdings.values() for rows in ex_map.values() for h in rows)
        logger.info("Found %d holdings symbol(s), %d total quantity", total_sym, total_qty)

        if args.json:
            print(json.dumps(holdings, indent=2))
        else:
            _print_holdings(holdings)
    except Exception as exc:
        logger.error("Failed to fetch holdings: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
