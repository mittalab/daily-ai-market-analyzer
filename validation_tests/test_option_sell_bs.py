"""
Unit tests for option_sell.blackscholes.bs_delta.
"""
import math
import unittest

from option_sell.blackscholes import bs_delta


class TestBsDelta(unittest.TestCase):

    def test_atm_call_delta_near_half(self):
        # ATM call: delta should be slightly above 0.50 due to positive drift term
        delta = bs_delta(spot=100, strike=100, iv_annual=0.20, t_years=30/365, risk_free_rate=0.07, option_type="CE")
        self.assertFalse(math.isnan(delta))
        self.assertGreater(delta, 0.50)
        self.assertLess(delta, 0.60)

    def test_atm_put_delta_near_minus_half(self):
        delta = bs_delta(spot=100, strike=100, iv_annual=0.20, t_years=30/365, risk_free_rate=0.07, option_type="PE")
        self.assertFalse(math.isnan(delta))
        self.assertLess(delta, -0.40)
        self.assertGreater(delta, -0.55)

    def test_deep_otm_put_small_abs_delta(self):
        # Very deep OTM put: |delta| should be < 0.10
        delta = bs_delta(spot=100, strike=80, iv_annual=0.20, t_years=30/365, risk_free_rate=0.07, option_type="PE")
        self.assertFalse(math.isnan(delta))
        self.assertLess(abs(delta), 0.10)

    def test_zero_time_returns_nan(self):
        delta = bs_delta(spot=100, strike=100, iv_annual=0.20, t_years=0, risk_free_rate=0.07, option_type="CE")
        self.assertTrue(math.isnan(delta))

    def test_negative_time_returns_nan(self):
        delta = bs_delta(spot=100, strike=100, iv_annual=0.20, t_years=-1, risk_free_rate=0.07, option_type="CE")
        self.assertTrue(math.isnan(delta))

    def test_zero_iv_returns_nan(self):
        delta = bs_delta(spot=100, strike=100, iv_annual=0.0, t_years=30/365, risk_free_rate=0.07, option_type="CE")
        self.assertTrue(math.isnan(delta))

    def test_zero_spot_returns_nan(self):
        delta = bs_delta(spot=0, strike=100, iv_annual=0.20, t_years=30/365, risk_free_rate=0.07, option_type="CE")
        self.assertTrue(math.isnan(delta))

    def test_call_delta_in_01_range(self):
        delta = bs_delta(spot=100, strike=110, iv_annual=0.25, t_years=45/365, risk_free_rate=0.07, option_type="CE")
        self.assertFalse(math.isnan(delta))
        self.assertGreater(delta, 0)
        self.assertLess(delta, 1)

    def test_put_delta_in_minus1_0_range(self):
        delta = bs_delta(spot=100, strike=90, iv_annual=0.25, t_years=45/365, risk_free_rate=0.07, option_type="PE")
        self.assertFalse(math.isnan(delta))
        self.assertGreater(delta, -1)
        self.assertLess(delta, 0)


if __name__ == "__main__":
    unittest.main()
