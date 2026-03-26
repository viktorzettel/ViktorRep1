"""
Verification Suite for Phase 7: Advanced Signals
Verifies Volume Skew and Time-Phased Logic Components.
"""
import unittest
from unittest.mock import MagicMock
from strategy import CryptoHourlyStrategy, StrategyConfig
from data_feed import BinancePriceMonitor

class TestPhase7(unittest.TestCase):
    
    def setUp(self):
        self.mock_binance = MagicMock(spec=BinancePriceMonitor)
        self.title = "Will BTC be up at 12:00?" 
        
    def test_volume_skew_up(self):
        """
        Scenario: Price is Neutral.
        Volume Flow is Huge Positive (Net Buy).
        Result: Probability should increase.
        """
        self.mock_binance.get_price.return_value = 100.0
        self.mock_binance.get_candle_open.return_value = 100.0
        self.mock_binance.get_realized_volatility.return_value = 0.5
        # Neutral Trend
        self.mock_binance.get_price_ema.return_value = 100.0
        
        # Huge Buy Flow (+50 BTC)
        self.mock_binance.get_volume_flow.return_value = 50.0
        
        config = StrategyConfig(volume_skew_strength=0.2)
        strat = CryptoHourlyStrategy(self.title, self.mock_binance, config=config)
        
        prob = strat.get_dual_quotes(0.5, 0).yes.mid
        print(f"\nVolume Skew Test: ATM -> {prob:.3f}")
        
        self.assertGreater(prob, 0.50, "Positive Volume Flow should boost YES Prob")

    def test_volume_skew_down(self):
        """
        Scenario: Volume Flow is Negative (Net Sell).
        Result: Probability should decrease.
        """
        self.mock_binance.get_price.return_value = 100.0
        self.mock_binance.get_candle_open.return_value = 100.0
        self.mock_binance.get_realized_volatility.return_value = 0.5
        self.mock_binance.get_price_ema.return_value = 100.0
        
        # Huge Sell Flow (-50 BTC)
        self.mock_binance.get_volume_flow.return_value = -50.0
        
        config = StrategyConfig(volume_skew_strength=0.2)
        strat = CryptoHourlyStrategy(self.title, self.mock_binance, config=config)
        
        prob = strat.get_dual_quotes(0.5, 0).yes.mid
        print(f"Volume Sell Test: ATM -> {prob:.3f}")
        
        self.assertLess(prob, 0.50, "Negative Volume Flow should cut YES Prob")

if __name__ == '__main__':
    unittest.main()
