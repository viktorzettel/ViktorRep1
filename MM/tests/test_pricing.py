"""
Verification Suite for Pricing Engine
"""
import unittest
import math
from pricing import CryptoHourlyPricer

class TestPricing(unittest.TestCase):
    
    def test_deep_itm_expiry(self):
        # S=105, K=100, T=1s, Vol=50%
        # Should be 100%
        prob = CryptoHourlyPricer.calculate_probability(
            current_price=105,
            open_price=100,
            time_remaining_seconds=1,
            volatility=0.5
        )
        self.assertAlmostEqual(prob, 1.0, places=4)

    def test_deep_otm_expiry(self):
        # S=95, K=100, T=1s
        # Should be 0%
        prob = CryptoHourlyPricer.calculate_probability(
            current_price=95,
            open_price=100,
            time_remaining_seconds=1,
            volatility=0.5
        )
        self.assertAlmostEqual(prob, 0.0, places=4)

    def test_atm_long_duration(self):
        # S=100, K=100, T=1h (3600s), Vol=50% (0.5)
        # Should be roughly 0.50 (slightly less due to vol drag)
        prob = CryptoHourlyPricer.calculate_probability(
            current_price=100,
            open_price=100,
            time_remaining_seconds=3600,
            volatility=0.5
        )
        print(f"\nATM 1H Probability: {prob}")
        self.assertTrue(0.48 < prob < 0.51)

    def test_volatility_impact(self):
        # S=101, K=100 (Slightly ITM)
        # If Vol low -> High Prob
        # If Vol high -> Lower Prob (uncertainty)
        prob_low_vol = CryptoHourlyPricer.calculate_probability(101, 100, 3600, 0.1)
        prob_high_vol = CryptoHourlyPricer.calculate_probability(101, 100, 3600, 1.0)
        
        print(f"Low Vol Prob: {prob_low_vol} | High Vol Prob: {prob_high_vol}")
        self.assertTrue(prob_low_vol > prob_high_vol)

    def test_zero_time(self):
        # Exact expiry
        self.assertEqual(CryptoHourlyPricer.calculate_probability(101, 100, 0, 0.5), 1.0)
        self.assertEqual(CryptoHourlyPricer.calculate_probability(99, 100, 0, 0.5), 0.0)

if __name__ == '__main__':
    unittest.main()
