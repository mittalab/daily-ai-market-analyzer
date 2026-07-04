import unittest
import pandas as pd
import numpy as np
from database.queries import get_price_history
from indicators.technical import (
    compute_stock_indicators,
    calculate_ema,
    calculate_rsi,
    calculate_atr,
    calculate_macd,
    volume_ratio
)

class TestIndicatorEvaluation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Fetch actual historical data for test stocks
        cls.symbols = ["HDFCBANK", "RELIANCE"]
        cls.data = {}
        for sym in cls.symbols:
            rows = get_price_history(sym, days=250)
            if rows:
                cls.data[sym] = pd.DataFrame(rows)
            else:
                print(f"Warning: No DB price history found for {sym}, tests will use dummy data.")
                # Create dummy data if DB empty
                dates = pd.date_range(end=pd.Timestamp.today(), periods=250)
                cls.data[sym] = pd.DataFrame({
                    "date": dates.strftime("%Y-%m-%d"),
                    "open": np.random.uniform(100, 200, 250),
                    "high": np.random.uniform(200, 300, 250),
                    "low": np.random.uniform(50, 100, 250),
                    "close": np.random.uniform(100, 200, 250),
                    "volume": np.random.uniform(10000, 50000, 250)
                })

    def test_schema_integrity(self):
        """Verify that compute_stock_indicators returns all required fields in the schema."""
        symbols = ["HDFCBANK", "RELIANCE"]
        data = {}
        for sym in symbols:
            rows = get_price_history(sym, days=250)
            if rows:
                data[sym] = pd.DataFrame(rows)
        for sym, df in data.items():
            result = compute_stock_indicators(df)
            
            required_keys = [
                "ema20", "ema50", "ema180", "atr14", "atr_pct", "rsi14",
                "macd_line", "macd_signal", "macd_histogram", "macd_histogram_direction",
                "rsi_last_20", "macd_hist_last_20", "price_vs_ema20", "price_vs_ema50",
                "price_vs_ema180", "ema_arrangement", "volume_ratio_20d",
                "computation_method", "warnings"
            ]
            
            for key in required_keys:
                self.assertIn(key, result, f"Missing key {key} in results for {sym}")
                
            # Verify lists have 20 elements if data is sufficient
            self.assertEqual(len(result["rsi_last_20"]), 20)
            self.assertEqual(len(result["macd_hist_last_20"]), 20)
            print(result)

    def test_ema180_handling_on_short_data(self):
        """Verify that dynamic warning and null outputs work when data is less than 180 rows."""
        df_short = self.data["HDFCBANK"].tail(100).copy().reset_index(drop=True)
        result = compute_stock_indicators(df_short)
        
        self.assertIsNone(result["ema180"], "EMA180 should be null for < 180 rows")
        self.assertEqual(result["price_vs_ema180"], "unavailable")
        self.assertTrue(any("EMA180 unavailable" in w for w in result["warnings"]))

    def test_pandas_ta_vs_fallback_math(self):
        """Compare pandas-ta results side-by-side with custom math to verify convergence (< 1% diff)."""
        print("\n" + "="*80)
        print(f"{'Indicator':<15} | {'pandas-ta':<12} | {'Fallback':<12} | {'Difference %':<12}")
        print("="*80)

        for sym, df in self.data.items():
            # Clean and format df just like compute_stock_indicators does
            df_cleaned = df.copy()
            for col in ["open", "high", "low", "close", "volume"]:
                df_cleaned[col] = pd.to_numeric(df_cleaned[col], errors="coerce")
            df_cleaned = df_cleaned.dropna(subset=["close"]).reset_index(drop=True)
            
            # Primary pandas-ta computation
            res_ta = compute_stock_indicators(df_cleaned)
            
            # Forced fallback computations
            fallback_ema20 = calculate_ema(df_cleaned["close"], 20).iloc[-1]
            fallback_ema50 = calculate_ema(df_cleaned["close"], 50).iloc[-1]
            fallback_ema180 = calculate_ema(df_cleaned["close"], 180).iloc[-1]
            fallback_atr = calculate_atr(df_cleaned, 14).iloc[-1]
            fallback_rsi = calculate_rsi(df_cleaned["close"], 14).iloc[-1]
            fallback_macd_l, fallback_macd_s, fallback_macd_h = calculate_macd(df_cleaned["close"])
            
            comparisons = [
                ("EMA20", res_ta["ema20"], fallback_ema20, 0.5), # max 0.5% diff
                ("EMA50", res_ta["ema50"], fallback_ema50, 0.5),
                ("EMA180", res_ta["ema180"], fallback_ema180, 1.5), # max 1.5% diff for long periods
                ("RSI14", res_ta["rsi14"], fallback_rsi, 1.0),   # max 1.0% diff
                ("ATR14", res_ta["atr14"], fallback_atr, 1.0),
                ("MACD_LINE", res_ta["macd_line"], fallback_macd_l.iloc[-1], 1.0),
                ("MACD_SIGNAL", res_ta["macd_signal"], fallback_macd_s.iloc[-1], 1.0),
                ("MACD_HIST", res_ta["macd_histogram"], fallback_macd_h.iloc[-1], 1.0),
            ]
            
            print(f"\n--- Stock: {sym} ---")
            for name, ta_val, fb_val, max_allowed_diff in comparisons:
                if ta_val is None or fb_val is None:
                    print(f"{name:<15} | {'None':<12} | {'None':<12} | {'—':<12}")
                    continue
                    
                diff = abs(ta_val - fb_val) / abs(ta_val) * 100
                print(f"{name:<15} | {ta_val:<12.4f} | {fb_val:<12.4f} | {diff:<12.4f}%")
                
                self.assertLess(
                    diff, 
                    max_allowed_diff, 
                    f"{name} difference too high between pandas-ta and fallback: {diff:.4f}%"
                )

if __name__ == "__main__":
    unittest.main()
