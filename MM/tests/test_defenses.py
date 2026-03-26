"""
Verification Suite for Phase 5 Defenses: Gamma Guard & Basis Guard
"""
import unittest
from unittest.mock import MagicMock, patch
from strategy import CryptoHourlyStrategy, StrategyConfig
from data_feed import BinancePriceMonitor

class TestDefenses(unittest.TestCase):
    
    def setUp(self):
        self.mock_binance = MagicMock(spec=BinancePriceMonitor)
        # Default benign market
        self.title = "Will BTC be up at 12:00?"
        
    @patch('pricing.CryptoHourlyPricer.get_time_remaining')
    def test_gamma_guard_trigger(self, mock_time):
        """
        Scenario: T-60s (Danger Zone). Price is ATM.
        Result: Should PULL QUOTES (Return Safety Quote).
        """
        mock_time.return_value = 60 # 1 minute left
        
        # Fair Value ~ 0.50 (ATM)
        self.mock_binance.get_price.return_value = 100000.0
        self.mock_binance.get_candle_open.return_value = 100000.0
        self.mock_binance.get_realized_volatility.return_value = 0.5
        
        strat = CryptoHourlyStrategy(self.title, self.mock_binance)
        
        quotes = strat.get_dual_quotes(0.50, 0)
        
        print(f"\nGamma Guard Test (ATM): Spread={quotes.yes.spread:.3f}")
        
        # Safety Quote is 0.01 / 0.99 -> Spread 0.98
        self.assertGreater(quotes.yes.spread, 0.90, "Gamma Guard should trigger safety quotes (wide spread)")

    @patch('pricing.CryptoHourlyPricer.get_time_remaining')
    def test_gamma_guard_pass_deep_itm(self, mock_time):
        """
        Scenario: T-60s (Danger Zone). Price is Deep ITM.
        Result: Should QUOTE (It's safe).
        """
        mock_time.return_value = 60
        
        # Fair Value ~ 1.0 (Deep ITM)
        self.mock_binance.get_price.return_value = 105000.0 # +5%
        self.mock_binance.get_candle_open.return_value = 100000.0
        self.mock_binance.get_realized_volatility.return_value = 0.5
        
        strat = CryptoHourlyStrategy(self.title, self.mock_binance)
        
        quotes = strat.get_dual_quotes(0.99, 0)
        
        print(f"Gamma Guard Test (Deep ITM): Spread={quotes.yes.spread:.3f}")
        
        # Valid quote matches standard spread (~0.05 + basis) 
        self.assertLess(quotes.yes.spread, 0.20, "Gamma Guard should ALLOW deep ITM quotes")

    def test_basis_guard(self):
        """
        Scenario: Normal time.
        Result: Spread should include Basis Buffer (0.02).
        """
        # Fair Value ~ 0.50
        self.mock_binance.get_price.return_value = 100000.0
        self.mock_binance.get_candle_open.return_value = 100000.0
        self.mock_binance.get_realized_volatility.return_value = 0.1 # Low Vol -> Base Spread small
        
        config = StrategyConfig(min_spread=0.02, basis_risk_buffer=0.05) # Large buffer for test
        strat = CryptoHourlyStrategy(self.title, self.mock_binance, config=config)
        
        quotes = strat.get_dual_quotes(0.50, 0)
        
        print(f"Basis Guard Test: Spread={quotes.yes.spread:.3f}")
        
        # Expected: Min Spread (0.02) + Vol Component (Small) + Basis Buffer (0.05) ~= 0.07+
        self.assertGreater(quotes.yes.spread, 0.05, "Spread should reflect basis buffer")

if __name__ == '__main__':
    unittest.main()
