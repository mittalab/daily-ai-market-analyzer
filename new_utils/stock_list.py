"""
StockListBuilder — produces the complete universe of symbols for the daily run.

Sources (merged as a union, duplicates collapsed):
  1. Nifty 50 symbols          (from config/sector_map.json)       mandate = False
  2. Active Kite NFO positions  (open trades, requires Kite auth)   mandate = True
  3. interested_stocks config   (system_config table, comma-sep)    mandate = False
  4. Watchlist staging          (watchlist_staging table)           mandate = False

Any symbol that appears in the active-trades source keeps mandate=True regardless
of which other sources also list it.

Return shape of get_stock_list_for_analysis():
    {
        "RELIANCE":  {"symbol": "RELIANCE",  "mandate": False, "sources": ["nifty50"]},
        "TATASTEEL": {"symbol": "TATASTEEL", "mandate": True,  "sources": ["active_trade", "watchlist"]},
        "JIOFIN":    {"symbol": "JIOFIN",    "mandate": False, "sources": ["interested_stocks"]},
    }
"""
import logging
from typing import Literal

logger = logging.getLogger(__name__)

Source = Literal["nifty50", "active_trade", "interested_stocks", "watchlist"]


class StockListBuilder:
    """Builds the unified stock universe for each daily analysis run."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_stock_list_for_analysis(
        self,
        include_kite_trades: bool = True,
    ) -> dict[str, dict]:
        """
        Return the merged symbol universe with mandate flags.

        Args:
            include_kite_trades: Set False to skip the Kite positions call
                (useful in offline / test environments where no token exists).

        Returns:
            Dict keyed by symbol. Each value:
                {
                    "symbol":  str,
                    "mandate": bool,   # True → must run deep analysis
                    "sources": list[Source],
                }
        """
        result: dict[str, dict] = {}

        self._merge(result, self._nifty50_symbols(),      "nifty50",            mandate=False)
        self._merge(result, self._watchlist_symbols(),    "watchlist",          mandate=False)
        self._merge(result, self._interested_symbols(),   "interested_stocks",  mandate=False)

        if include_kite_trades:
            active = self._active_trade_symbols()
            self._merge(result, active, "active_trade", mandate=True)

        logger.info(
            "Stock universe: %d symbols (%d mandated)",
            len(result),
            sum(1 for v in result.values() if v["mandate"]),
        )
        return result

    # ------------------------------------------------------------------
    # Source loaders
    # ------------------------------------------------------------------

    def _nifty50_symbols(self) -> set[str]:
        try:
            from new_data_ingestion.nse_bhavcopy import get_nifty50_symbols
            return get_nifty50_symbols()
        except Exception as exc:
            logger.warning("Could not load Nifty 50 symbols: %s", exc)
            return set()

    def _watchlist_symbols(self) -> set[str]:
        try:
            from database.queries import get_watchlist
            return {row["symbol"] for row in get_watchlist() if row.get("symbol")}
        except Exception as exc:
            logger.warning("Could not load watchlist symbols: %s", exc)
            return set()

    def _interested_symbols(self) -> set[str]:
        try:
            from database.queries import get_interested_stocks
            stocks = get_interested_stocks()
            return set(stocks)
        except Exception as exc:
            logger.warning("Could not load interested_stocks config: %s", exc)
            return set()

    def _active_trade_symbols(self) -> set[str]:
        """Return underlying symbols of all currently open NFO positions."""
        try:
            from new_integration.kite_positions import fetch_fo_positions
            positions = fetch_fo_positions()
            nfo = positions.get("NFO", {})
            return set(nfo.keys())
        except Exception as exc:
            logger.warning("Could not fetch Kite NFO positions (skipping): %s", exc)
            return set()

    # ------------------------------------------------------------------
    # Merge helper
    # ------------------------------------------------------------------

    @staticmethod
    def _merge(
        result: dict[str, dict],
        symbols: set[str],
        source: Source,
        mandate: bool,
    ) -> None:
        for sym in symbols:
            if not sym:
                continue
            if sym not in result:
                result[sym] = {"symbol": sym, "mandate": mandate, "sources": [source]}
            else:
                if mandate:
                    result[sym]["mandate"] = True
                if source not in result[sym]["sources"]:
                    result[sym]["sources"].append(source)


# ── Convenience module-level functions ────────────────────────────────────────

def get_stock_list_for_analysis(include_kite_trades: bool = True) -> dict[str, dict]:
    """Module-level shortcut — delegates to StockListBuilder."""
    return StockListBuilder().get_stock_list_for_analysis(
        include_kite_trades=include_kite_trades
    )


# Stocks exiting or recently exited the F&O segment — add here to skip validation.
# These stocks may still appear in the NFO instruments list during wind-down but
# won't have mid/far-month contracts, causing perpetual validation failures.
_FO_EXCLUSIONS: set[str] = {
}


def fetch_kite_fo_stocks() -> list[str]:
    """
    Fetch the list of all equity stock symbols that have active F&O contracts.

    Excludes indices (e.g. NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50) by
    cross-referencing the NFO instruments list against the active NSE equity cash market instruments.
    Also excludes stocks in _FO_EXCLUSIONS (exiting F&O segment).

    Returns:
        Sorted list of stock symbols.
    """
    try:
        from database.queries import get_kite_token
        from new_data_ingestion.kite_ohlcv import get_kite

        token_row = get_kite_token()
        if not token_row:
            raise RuntimeError("No Kite access token in DB — run OAuth flow first")

        kite = get_kite(token_row["access_token"])

        logger.info("Fetching instruments from Kite to identify F&O stocks...")
        nse_instruments = kite.instruments("NSE")
        nfo_instruments = kite.instruments("NFO")

        # Get active equity symbols on NSE (segment == "NSE", instrument_type == "EQ")
        nse_stocks = {
            inst["tradingsymbol"]
            for inst in nse_instruments
            if inst.get("segment") == "NSE" and inst.get("instrument_type") == "EQ"
        }

        # Get unique F&O underlying names
        nfo_underlyings = {
            inst.get("name")
            for inst in nfo_instruments
            if inst.get("name")
        }

        # Keep only underlying names that are listed as active NSE equities
        fo_stocks = sorted(nfo_underlyings.intersection(nse_stocks) - _FO_EXCLUSIONS)
        if _FO_EXCLUSIONS:
            logger.info("Found %d F&O stocks (%d excluded: %s)", len(fo_stocks), len(_FO_EXCLUSIONS), sorted(_FO_EXCLUSIONS))
        else:
            logger.info("Found %d F&O stocks", len(fo_stocks))
        return fo_stocks

    except Exception as exc:
        logger.error("Could not fetch Kite F&O stocks: %s", exc)
        return []



# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(fetch_kite_fo_stocks())
    # import json
    # import sys
    # import argparse
    # import logging as _logging
    #
    # _logging.basicConfig(
    #     level=_logging.INFO,
    #     format="%(asctime)s  %(levelname)-8s %(message)s",
    #     datefmt="%H:%M:%S",
    # )
    #
    # parser = argparse.ArgumentParser(description="Print the daily analysis stock universe")
    # parser.add_argument("--no-kite", action="store_true", help="Skip Kite positions call")
    # parser.add_argument("--mandate-only", action="store_true", help="Print only mandated symbols")
    # parser.add_argument("--fo-stocks-only", action="store_true", help="Print only symbols having F&O contracts")
    # parser.add_argument("--json", action="store_true", help="Output raw JSON")
    # args = parser.parse_args()
    #
    # if args.fo_stocks_only:
    #     fo_stocks = fetch_kite_fo_stocks()
    #     if args.json:
    #         print(json.dumps(fo_stocks, indent=2))
    #     else:
    #         print(f"\n{'='*60}")
    #         print(f"  F&O Stocks Universe — {len(fo_stocks)} symbols")
    #         print(f"{'='*60}")
    #         for sym in fo_stocks:
    #             print(f"    {sym}")
    #         print(f"\n{'='*60}\n")
    #     sys.exit(0)
    #
    # universe = get_stock_list_for_analysis(include_kite_trades=not args.no_kite)
    #
    # if args.mandate_only:
    #     universe = {k: v for k, v in universe.items() if v["mandate"]}
    #
    # if args.json:
    #     print(json.dumps(universe, indent=2))
    # else:
    #     mandated = [v for v in universe.values() if v["mandate"]]
    #     others   = [v for v in universe.values() if not v["mandate"]]
    #
    #     print(f"\n{'='*60}")
    #     print(f"  Analysis Universe — {len(universe)} symbols")
    #     print(f"{'='*60}")
    #
    #     if mandated:
    #         print(f"\n  MANDATED ({len(mandated)}) — deep analysis required")
    #         for v in sorted(mandated, key=lambda x: x["symbol"]):
    #             print(f"    {v['symbol']:<20} [{', '.join(v['sources'])}]")
    #
    #     print(f"\n  STANDARD ({len(others)})")
    #     for v in sorted(others, key=lambda x: x["symbol"]):
    #         print(f"    {v['symbol']:<20} [{', '.join(v['sources'])}]")
    #
    #     print(f"\n{'='*60}\n")
