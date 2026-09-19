"""
Unit tests for key_levels/stage1.py — Phase 1.

Run:  python -m pytest key_levels/tests/test_stage1.py -v
      python key_levels/tests/test_stage1.py        (standalone example)
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import math
import random
import unittest
from datetime import date, timedelta

import numpy as np
import pandas as pd

from key_levels.stage1 import (
    build_stage1_output,
    cluster_pivots,
    compute_technicals,
    detect_pivots,
    extract_oi_levels,
    tag_zone_character,
)


# ── Synthetic data helpers ─────────────────────────────────────────────────────

def _make_ohlcv(closes: list[float], start: str = "2025-01-01") -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a close price series."""
    n = len(closes)
    dates = [str(date.fromisoformat(start) + timedelta(days=i)) for i in range(n)]
    opens = [c * 0.995 for c in closes]
    highs = [c * 1.005 for c in closes]
    lows = [c * 0.995 for c in closes]
    volumes = [1_000_000] * n
    return pd.DataFrame({
        "date": dates, "open": opens, "high": highs,
        "low": lows, "close": closes, "volume": volumes,
    })


def _make_ohlcv_with_pivots() -> pd.DataFrame:
    """
    120-row series with three clear LOW zones (~100, ~150, ~200) and two HIGH zones (~130, ~180).
    Pattern: sinusoidal swings between 100 and 200 with multiple touches.
    """
    n = 120
    dates = [str(date(2025, 1, 1) + timedelta(days=i)) for i in range(n)]
    closes = []
    for i in range(n):
        # Two-cycle sine wave between 100 and 200
        v = 150 + 50 * math.sin(2 * math.pi * i / 40)
        closes.append(round(v, 2))

    opens = [c * random.uniform(0.997, 1.003) for c in closes]
    highs = [c * random.uniform(1.003, 1.008) for c in closes]
    lows = [c * random.uniform(0.992, 0.997) for c in closes]
    volumes = [random.randint(800_000, 1_200_000) for _ in range(n)]
    return pd.DataFrame({
        "date": dates, "open": opens, "high": highs,
        "low": lows, "close": closes, "volume": volumes,
    })


def _make_reversal_ohlcv() -> pd.DataFrame:
    """
    60-row series with a known V-shaped reversal at ~100:
    price falls to 100, bounces sharply back to 130+.
    High volume on the reversal bar.
    """
    n = 60
    dates = [str(date(2025, 1, 1) + timedelta(days=i)) for i in range(n)]
    closes = []
    for i in range(n):
        if i < 25:
            closes.append(round(130 - i, 2))          # declining: 130→106
        elif i == 25:
            closes.append(100.0)                        # reversal low
        else:
            closes.append(round(100 + (i - 25) * 1.2, 2))  # recovering

    opens = [c * 0.998 for c in closes]
    highs = [c * 1.007 for c in closes]
    lows = [c * 0.993 for c in closes]

    volumes = [1_000_000] * n
    volumes[25] = 4_000_000  # volume climax on reversal bar

    # Add prominent lower wick on reversal bar
    lows[25] = closes[25] * 0.985
    highs[25] = closes[25] * 1.002
    opens[25] = closes[25] * 1.005  # opens above close → bearish body (still a reversal wick day)

    return pd.DataFrame({
        "date": dates, "open": opens, "high": highs,
        "low": lows, "close": closes, "volume": volumes,
    })


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestComputeTechnicals(unittest.TestCase):

    def test_known_series_ema_and_atr(self):
        # 60 rows of constant 100 → EMA20 = EMA50 = 100, ATR ≈ 0 (no variation)
        closes = [100.0] * 60
        df = _make_ohlcv(closes)
        t = compute_technicals(df)

        self.assertAlmostEqual(t["ema20"], 100.0, places=1)
        self.assertAlmostEqual(t["ema50"], 100.0, places=1)
        self.assertIsNotNone(t["atr14"])
        self.assertGreaterEqual(t["atr14"], 0.0)

    def test_hv_computed(self):
        # 65 rows with clear daily swings so log returns have non-trivial stdev
        random.seed(0)
        closes = [100.0]
        for _ in range(64):
            closes.append(round(closes[-1] * (1 + random.uniform(-0.015, 0.015)), 4))
        df = _make_ohlcv(closes)
        t = compute_technicals(df)

        self.assertIsNotNone(t["hv20"])
        self.assertIsNotNone(t["hv60"])
        self.assertGreater(t["hv20"], 0)
        self.assertGreater(t["hv60"], 0)
        self.assertTrue(math.isfinite(t["hv20"]))
        self.assertTrue(math.isfinite(t["hv60"]))

    def test_insufficient_rows_returns_none(self):
        closes = [100.0] * 10  # fewer than 20
        df = _make_ohlcv(closes)
        t = compute_technicals(df)

        self.assertIsNone(t["ema20"])
        self.assertIsNone(t["ema50"])
        self.assertIsNone(t["hv20"])
        self.assertIsNone(t["hv60"])

    def test_returns_rounded_floats(self):
        closes = [100 + i * 0.3 for i in range(55)]
        df = _make_ohlcv(closes)
        t = compute_technicals(df)
        for key in ("ema20", "ema50", "atr14"):
            val = t[key]
            if val is not None:
                self.assertEqual(val, round(val, 2))


class TestClusterPivots(unittest.TestCase):

    def _synthetic_pivots(self):
        """Two distinct LOW zones (~100 and ~200) and one HIGH zone (~150)."""
        pivots = []
        for p in [99.5, 100.0, 100.5, 100.2]:          # LOW zone ~100
            pivots.append({"price": p, "date": "2025-03-01", "type": "LOW", "volume_ratio": 1.2})
        for p in [199.5, 200.0, 200.5]:                  # LOW zone ~200
            pivots.append({"price": p, "date": "2025-06-01", "type": "LOW", "volume_ratio": 1.0})
        for p in [149.5, 150.0, 150.5, 150.2]:          # HIGH zone ~150
            pivots.append({"price": p, "date": "2025-04-15", "type": "HIGH", "volume_ratio": 1.3})
        return pivots

    def test_correct_cluster_count(self):
        pivots = self._synthetic_pivots()
        atr14 = 5.0  # tolerance = 0.75 * 5 = 3.75 — enough to merge the ~0.5 spread clusters
        clusters = cluster_pivots(pivots, atr14)

        low_clusters = [c for c in clusters if c["type"] == "LOW"]
        high_clusters = [c for c in clusters if c["type"] == "HIGH"]

        self.assertEqual(len(low_clusters), 2)   # ~100 and ~200
        self.assertEqual(len(high_clusters), 1)  # ~150

    def test_zone_bounds_valid(self):
        pivots = self._synthetic_pivots()
        clusters = cluster_pivots(pivots, atr14=5.0)
        for c in clusters:
            self.assertLessEqual(c["zone_low"], c["zone_high"])
            self.assertGreater(c["touch_count"], 0)

    def test_top5_cap(self):
        # Create 7 distinct LOW zones
        pivots = []
        for zone in range(7):
            base = 100 + zone * 20
            for _ in range(zone + 1):  # touch_count = zone+1
                pivots.append({"price": float(base), "date": "2025-01-01", "type": "LOW", "volume_ratio": 1.0})
        clusters = cluster_pivots(pivots, atr14=2.0)
        low_clusters = [c for c in clusters if c["type"] == "LOW"]
        self.assertLessEqual(len(low_clusters), 5)

    def test_no_pivots_returns_empty(self):
        self.assertEqual(cluster_pivots([], 5.0), [])

    def test_none_atr_returns_empty(self):
        pivots = [{"price": 100.0, "date": "2025-01-01", "type": "LOW", "volume_ratio": 1.0}]
        self.assertEqual(cluster_pivots(pivots, None), [])


class TestZoneCharacter(unittest.TestCase):

    def test_follow_through_positive_for_support(self):
        df = _make_reversal_ohlcv()
        # Manually create a support cluster around the reversal low (~100)
        clusters = [{
            "type": "LOW",
            "zone_low": 98.0,
            "zone_high": 102.0,
            "touch_count": 1,
            "last_touch_date": "2025-01-26",
            "avg_volume_ratio_on_touches": 4.0,
        }]
        tagged = tag_zone_character(clusters, df)
        self.assertEqual(len(tagged), 1)
        zone = tagged[0]

        # After the reversal at ~100, price climbs → follow_through_strength should be positive
        self.assertGreater(zone["follow_through_strength"], 0)

    def test_wick_rejection_count_gte_1(self):
        df = _make_reversal_ohlcv()
        clusters = [{
            "type": "LOW",
            "zone_low": 98.0,
            "zone_high": 102.0,
            "touch_count": 1,
            "last_touch_date": "2025-01-26",
            "avg_volume_ratio_on_touches": 4.0,
        }]
        tagged = tag_zone_character(clusters, df)
        # The reversal bar has high wick range relative to body → should count as wick rejection
        self.assertGreaterEqual(tagged[0]["wick_rejection_count"], 1)

    def test_volume_expansion_on_reversal_gt_1(self):
        df = _make_reversal_ohlcv()
        clusters = [{
            "type": "LOW",
            "zone_low": 98.0,
            "zone_high": 102.0,
            "touch_count": 1,
            "last_touch_date": "2025-01-26",
            "avg_volume_ratio_on_touches": 4.0,
        }]
        tagged = tag_zone_character(clusters, df)
        # Reversal bar volume = 4×normal → expansion > 1
        self.assertGreater(tagged[0]["volume_expansion_on_reversal"], 1.0)

    def test_no_touches_returns_defaults(self):
        df = _make_ohlcv([200.0] * 40)  # price never near zone at 100
        clusters = [{
            "type": "LOW",
            "zone_low": 98.0,
            "zone_high": 102.0,
            "touch_count": 0,
            "last_touch_date": "2025-01-01",
            "avg_volume_ratio_on_touches": 1.0,
        }]
        tagged = tag_zone_character(clusters, df)
        z = tagged[0]
        self.assertEqual(z["reversal_speed"], 0.0)
        self.assertEqual(z["wick_rejection_count"], 0)


class TestExtractOiLevels(unittest.TestCase):

    def _make_chain(self):
        rows = []
        for strike in range(90, 115, 5):  # 90, 95, 100, 105, 110
            for otype in ("PE", "CE"):
                rows.append({
                    "option_type": otype,
                    "strike": float(strike),
                    "oi": (115 - strike) * 1000 if otype == "PE" else strike * 800,
                    "iv": 20.0,
                    "premium_close": 2.0,
                })
        return rows

    def test_filters_by_band(self):
        chain = self._make_chain()
        levels = extract_oi_levels(chain, spot=100.0, band_pct=0.05, top_n=5)
        for lv in levels:
            self.assertLessEqual(abs(lv["strike"] - 100.0) / 100.0, 0.05)

    def test_top_n_per_type(self):
        chain = self._make_chain()
        levels = extract_oi_levels(chain, spot=100.0, band_pct=0.20, top_n=2)
        pe_levels = [l for l in levels if l["type"] == "PE"]
        ce_levels = [l for l in levels if l["type"] == "CE"]
        self.assertLessEqual(len(pe_levels), 2)
        self.assertLessEqual(len(ce_levels), 2)

    def test_empty_chain_returns_empty(self):
        self.assertEqual(extract_oi_levels([], spot=100.0), [])
        self.assertEqual(extract_oi_levels(None, spot=100.0), [])


# ── Runnable example with synthetic data ──────────────────────────────────────

def _run_example():
    import json
    random.seed(42)
    df = _make_ohlcv_with_pivots()

    # Synthetic option chain around last close (~150)
    last_close = df["close"].iloc[-1]
    chain = []
    for strike in range(int(last_close * 0.85), int(last_close * 1.15) + 1, 5):
        for otype in ("PE", "CE"):
            chain.append({
                "option_type": otype,
                "strike": float(strike),
                "oi": random.randint(5000, 50000),
                "iv": round(random.uniform(15, 35), 2),
                "premium_close": round(random.uniform(1, 20), 2),
            })

    print("Running build_stage1_output() on synthetic OHLCV data…")
    try:
        result = build_stage1_output("SYNTHETIC", df, chain)
    except Exception as exc:
        print(f"  Chart render skipped (mplfinance may not be installed): {exc}")
        # Re-run without chart rendering for a clean demo
        from key_levels.stage1 import (
            cluster_pivots, compute_technicals, detect_pivots,
            extract_oi_levels, tag_confluence, tag_zone_character,
        )
        tech = compute_technicals(df)
        pivots = detect_pivots(df)
        clusters = cluster_pivots(pivots, tech["atr14"])
        clusters = tag_confluence(clusters, tech["ema20"], tech["ema50"], df)
        clusters = tag_zone_character(clusters, df)
        result = {
            "symbol": "SYNTHETIC",
            "last_close": round(float(df["close"].iloc[-1]), 2),
            **tech,
            "candidate_zones": clusters,
            "oi_levels": extract_oi_levels(chain, float(df["close"].iloc[-1])),
            "chart_image_path": "(skipped)",
        }

    output = {k: v for k, v in result.items() if k != "chart_image_path"}
    print(json.dumps(output, indent=2, default=str))
    print(f"\nchart_image_path: {result.get('chart_image_path', '(skipped)')}")


if __name__ == "__main__":
    _run_example()
    print("\n--- Running unit tests ---\n")
    unittest.main(argv=[""], exit=False, verbosity=2)
