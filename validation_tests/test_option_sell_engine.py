"""
Unit tests for option_sell.engine — zone selection, strike snapping, staleness.
"""
import unittest
from datetime import date, timedelta

from option_sell.engine import _is_stale, _snap_strike, run_symbol
from option_sell import config


REF_DATE = date(2025, 10, 1)
EXPIRY   = date(2025, 10, 28)  # last Tuesday of October 2025


def _zone(
    *,
    level_type="SUPPORT",
    zone_low=90.0,
    zone_high=95.0,
    conviction="HIGH",
    touch_count=3,
    last_touch_date=None,
):
    if last_touch_date is None:
        last_touch_date = str(REF_DATE - timedelta(days=10))
    return {
        "id": 1,
        "level_type": level_type,
        "zone_low": zone_low,
        "zone_high": zone_high,
        "conviction": conviction,
        "touch_count": touch_count,
        "last_touch_date": last_touch_date,
        "reasoning": "test",
        "analysis_date": str(REF_DATE),
    }


def _chain_row(strike, option_type, premium=2.0, oi=1000, iv=20.0):
    return {
        "strike": float(strike),
        "option_type": option_type,
        "premium_close": float(premium),
        "oi": oi,
        "iv": float(iv),
    }


class TestIsStale(unittest.TestCase):

    def test_recent_touch_not_stale(self):
        zone = _zone(last_touch_date=str(REF_DATE - timedelta(days=10)), touch_count=3)
        self.assertFalse(_is_stale(zone, REF_DATE))

    def test_old_touch_low_count_is_stale(self):
        old = str(REF_DATE - timedelta(days=config.STALENESS_DAYS + 1))
        zone = _zone(last_touch_date=old, touch_count=3)
        self.assertTrue(_is_stale(zone, REF_DATE))

    def test_old_touch_high_count_not_stale(self):
        old = str(REF_DATE - timedelta(days=config.STALENESS_DAYS + 1))
        zone = _zone(last_touch_date=old, touch_count=config.HIGH_TOUCH_OVERRIDE)
        self.assertFalse(_is_stale(zone, REF_DATE))

    def test_no_last_touch_date_is_stale(self):
        zone = _zone(last_touch_date=None, touch_count=2)
        zone["last_touch_date"] = None
        self.assertTrue(_is_stale(zone, REF_DATE))


class TestSnapStrike(unittest.TestCase):

    def test_snaps_to_nearest(self):
        chain = [_chain_row(2800, "PE"), _chain_row(2850, "PE"), _chain_row(2900, "PE")]
        result = _snap_strike(chain, 2847.3, "PE")
        self.assertIsNotNone(result)
        self.assertEqual(float(result["strike"]), 2850.0)

    def test_filters_by_option_type(self):
        chain = [_chain_row(100, "CE"), _chain_row(100, "PE")]
        result = _snap_strike(chain, 100, "CE")
        self.assertEqual(result["option_type"], "CE")

    def test_empty_chain_returns_none(self):
        self.assertIsNone(_snap_strike([], 100, "PE"))

    def test_no_matching_type_returns_none(self):
        chain = [_chain_row(100, "CE")]
        self.assertIsNone(_snap_strike(chain, 100, "PE"))


class TestRunSymbolZoneFiltering(unittest.TestCase):

    def _minimal_chain(self, spot):
        """Strikes well OTM relative to spot."""
        return [
            _chain_row(spot * 0.85, "PE", premium=3.0, iv=25.0),
            _chain_row(spot * 1.15, "CE", premium=3.0, iv=25.0),
        ]

    def test_spot_inside_support_zone_excluded(self):
        # spot=100 inside zone [95, 105] — zone_high >= spot, so support excluded
        zones = [_zone(level_type="SUPPORT", zone_low=95.0, zone_high=105.0, conviction="HIGH")]
        chain = self._minimal_chain(100)
        recs = run_symbol("TEST", REF_DATE, 100.0, zones, chain, atr=2.0, expiry_date=EXPIRY)
        # No SELL_PE should be generated (support zone_high=105 >= spot=100)
        pe_recs = [r for r in recs if r["action"] == "SELL_PE"]
        self.assertEqual(len(pe_recs), 0)

    def test_spot_inside_resist_zone_excluded(self):
        # spot=100 inside zone [95, 105] — zone_low=95 <= spot=100, so resist excluded
        zones = [_zone(level_type="RESISTANCE", zone_low=95.0, zone_high=105.0, conviction="HIGH")]
        chain = self._minimal_chain(100)
        recs = run_symbol("TEST", REF_DATE, 100.0, zones, chain, atr=2.0, expiry_date=EXPIRY)
        ce_recs = [r for r in recs if r["action"] == "SELL_CE"]
        self.assertEqual(len(ce_recs), 0)

    def test_support_strictly_below_spot_qualifies_for_selection(self):
        # Support zone_high=90 < spot=100: should be selected as nearest support
        zones = [_zone(level_type="SUPPORT", zone_low=85.0, zone_high=90.0, conviction="HIGH")]
        # Empty chain → no rec, but zone should be selected without error
        recs = run_symbol("TEST", REF_DATE, 100.0, zones, [], atr=2.0, expiry_date=EXPIRY)
        self.assertEqual(recs, [])  # empty chain, no crash

    def test_low_conviction_excluded(self):
        zones = [_zone(level_type="SUPPORT", zone_low=85.0, zone_high=90.0, conviction="LOW")]
        chain = self._minimal_chain(100)
        recs = run_symbol("TEST", REF_DATE, 100.0, zones, chain, atr=2.0, expiry_date=EXPIRY)
        pe_recs = [r for r in recs if r["action"] == "SELL_PE"]
        self.assertEqual(len(pe_recs), 0)

    def test_stale_zone_excluded(self):
        old = str(REF_DATE - timedelta(days=config.STALENESS_DAYS + 5))
        zones = [_zone(level_type="SUPPORT", zone_low=85.0, zone_high=90.0, conviction="HIGH",
                       touch_count=2, last_touch_date=old)]
        chain = self._minimal_chain(100)
        recs = run_symbol("TEST", REF_DATE, 100.0, zones, chain, atr=2.0, expiry_date=EXPIRY)
        pe_recs = [r for r in recs if r["action"] == "SELL_PE"]
        self.assertEqual(len(pe_recs), 0)

    def test_spot_exactly_at_zone_high_excluded_from_support(self):
        # zone_high == spot → not strictly below → excluded
        zones = [_zone(level_type="SUPPORT", zone_low=90.0, zone_high=100.0, conviction="HIGH")]
        chain = self._minimal_chain(100)
        recs = run_symbol("TEST", REF_DATE, 100.0, zones, chain, atr=2.0, expiry_date=EXPIRY)
        pe_recs = [r for r in recs if r["action"] == "SELL_PE"]
        self.assertEqual(len(pe_recs), 0)


if __name__ == "__main__":
    unittest.main()
