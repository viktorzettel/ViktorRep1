"""
Verification Suite for Phase 6: Active Manager
Verifies Trend Tracker and Min Volatility Floor.
"""
import unittest
from unittest.mock import MagicMock
from strategy import CryptoHourlyStrategy, StrategyConfig
from data_feed import BinancePriceMonitor

class TestActiveManager(unittest.TestCase):
    
    def setUp(self):
        self.mock_binance = MagicMock(spec=BinancePriceMonitor)
        self.mock_binance.get_volume_flow.return_value = 0.0
        self.title = "Will BTC be up at 12:00?" # YES = UP
        
    def test_trend_up_skew(self):
        """
        Scenario: Price is slightly above Open (Fair ~ 0.52).
        BUT Trend is UP (Price 100 > EMA 99).
        Result: Probability should be boosted.
        """
        self.mock_binance.get_price.return_value = 100.0
        self.mock_binance.get_candle_open.return_value = 99.8 # Slight Up
        self.mock_binance.get_realized_volatility.return_value = 0.5
        # EMA is 99 (Price > EMA by 1%)
        self.mock_binance.get_price_ema.return_value = 99.0
        
        # 1. Strategy WITHOUT Trend
        config_neutral = StrategyConfig(trend_skew_strength=0.0)
        strat_neutral = CryptoHourlyStrategy(self.title, self.mock_binance, config=config_neutral)
        prob_neutral = (strat_neutral.get_dual_quotes(0.5, 0).yes.mid)
        
        # 2. Strategy WITH Trend
        config_trend = StrategyConfig(trend_skew_strength=1.0) # Full strength
        strat_trend = CryptoHourlyStrategy(self.title, self.mock_binance, config=config_trend)
        prob_trend = (strat_trend.get_dual_quotes(0.5, 0).yes.mid)
        
        print(f"\nTrend Test: Neutral={prob_neutral:.3f} | TrendBoosted={prob_trend:.3f}")
        
        self.assertGreater(prob_trend, prob_neutral, "Up Trend should boost YES Probability")

    def test_trend_down_skew(self):
        """
        Scenario: Price < EMA.
        Result: Probability should be cut.
        """
        self.mock_binance.get_price.return_value = 100.0
        self.mock_binance.get_candle_open.return_value = 100.0 # ATM
        self.mock_binance.get_realized_volatility.return_value = 0.5
        # EMA is 101 (Price < EMA by 1%)
        self.mock_binance.get_price_ema.return_value = 101.0
        
        config_trend = StrategyConfig(trend_skew_strength=1.0)
        strat = CryptoHourlyStrategy(self.title, self.mock_binance, config=config_trend)
        prob = strat.get_dual_quotes(0.5, 0).yes.mid
        
        print(f"Down Trend Test: ATM Prob (approx 0.50) -> {prob:.3f}")
        self.assertLess(prob, 0.50, "Down Trend should cut YES Probability")

    def test_min_volatility_floor(self):
        """
        Scenario: Realized Vol is 5% (0.05).
        Result: Strategy should use 30% (0.30).
        Spread should be wider than if it used 5%.
        """
        self.mock_binance.get_price.return_value = 100.0
        self.mock_binance.get_candle_open.return_value = 100.0
        # Tiny Vol
        self.mock_binance.get_realized_volatility.return_value = 0.05 
        # Neutral Trend
        self.mock_binance.get_price_ema.return_value = 100.0
        
        config = StrategyConfig(min_volatility=0.30, min_spread=0.01)
        strat = CryptoHourlyStrategy(self.title, self.mock_binance, config=config)
        
        # Expected Spread: 0.30 * 0.1 = 0.03 (plus basis guard 0.02) = ~0.05
        # If it used 0.05 vol: 0.05 * 0.1 = 0.005 -> clamped to min_spread 0.01 + basis 0.02 = 0.03
        
        quotes = strat.get_dual_quotes(0.5, 0)
        print(f"\nVol Floor Test: Spread is {quotes.yes.spread:.3f}")
        
        self.assertGreater(quotes.yes.spread, 0.04, "Spread should reflect 30% Vol Floor, not 5%")

if __name__ == '__main__':
    unittest.main()
