"""
End-to-End Logic Test for Hourly Sniper Strategy
Mocks external data feeds to verify decision engine.
"""
import unittest
from unittest.mock import MagicMock
from strategy import CryptoHourlyStrategy, StrategyConfig
from data_feed import BinancePriceMonitor

class TestHourlySniper(unittest.TestCase):
    
    def setUp(self):
        # Mock the Binance Monitor
        self.mock_binance = MagicMock(spec=BinancePriceMonitor)
        
    def test_btc_up_scenario(self):
        """
        Scenario:
        Market: "Will BTC be up at 12:00?"
        Open Price: $100,000
        Current Price: $100,100 (UP)
        Time Left: 30 minutes
        Vol: 50%
        
        Expected: Fair Prob > 0.50. We should quote aggressively on YES.
        """
        title = "Will BTC be higher than the open at 12:00 UTC?"
        
        # Setup Data Checks
        self.mock_binance.get_price.return_value = 100100.0
        self.mock_binance.get_candle_open.return_value = 100000.0
        self.mock_binance.get_realized_volatility.return_value = 0.5
        self.mock_binance.get_realized_moments.return_value = (0.0, 0.0) # (Skew, Kurtosis)
        self.mock_binance.get_price_ema.return_value = 100050.0
        self.mock_binance.get_volume_flow.return_value = 0.0
        
        # Init Strategy
        strat = CryptoHourlyStrategy(title, self.mock_binance)
        
        # Get Quotes
        # Inventory = 0
        quotes = strat.get_dual_quotes(0.50, 0)
        
        print(f"\nScenario: BTC UP (+$100)")
        print(f"Fair YES Bid: {quotes.yes.bid:.3f}")
        print(f"Fair YES Ask: {quotes.yes.ask:.3f}")
        
        # Assertions
        self.assertGreater(quotes.yes.mid, 0.50, "Should favor YES side")
        self.assertLess(quotes.yes.spread, 0.15, "Spread should be reasonable")

    def test_btc_crash_scenario(self):
        """
        Scenario:
        Market: "Will BTC be up?"
        Open: $100,000
        Current: $99,000 (DOWN 1%)
        Time Left: 5 mins
        
        Expected: Fair Prob -> 0.0. Quote YES near 0.01.
        """
        title = "Will BTC be higher than the open?"
        
        self.mock_binance.get_price.return_value = 99000.0
        self.mock_binance.get_candle_open.return_value = 100000.0
        self.mock_binance.get_realized_volatility.return_value = 0.5
        self.mock_binance.get_realized_moments.return_value = (0.0, 0.0)
        self.mock_binance.get_price_ema.return_value = 99500.0
        self.mock_binance.get_volume_flow.return_value = 0.0
        
        strat = CryptoHourlyStrategy(title, self.mock_binance)
        
        quotes = strat.get_dual_quotes(0.50, 0)
        
        print(f"\nScenario: BTC CRASH (-$1000)")
        print(f"Fair YES Bid: {quotes.yes.bid:.3f}")
        print(f"Fair YES Ask: {quotes.yes.ask:.3f}")
        
        self.assertLess(quotes.yes.mid, 0.30, "Should be very bearish")
        # Should likely be near 0
    
    def test_eth_market_parsing(self):
        title = "Will ETH be above 3000?" 
        # Note: Our parser handles "Will ETH be up?" generic, 
        # but if specific strike is mentioned, parser might fail if not adapted.
        # But Phase 1 parser was generic "Up/Down".
        # Let's test standard "Will ETH be higher than open?"
        title = "Will ETH be above the open?"
        
        self.mock_binance.get_price.return_value = 3050
        self.mock_binance.get_candle_open.return_value = 3000
        self.mock_binance.get_realized_volatility.return_value = 0.6
        self.mock_binance.get_realized_moments.return_value = (0.0, 0.0)
        self.mock_binance.get_price_ema.return_value = 3025.0
        self.mock_binance.get_volume_flow.return_value = 0.0
        
        strat = CryptoHourlyStrategy(title, self.mock_binance)
        
        # Check internal state
        self.assertEqual(strat.market_data.asset, "ETH")
        
        quotes = strat.get_dual_quotes(0.50, 0)
        self.assertGreater(quotes.yes.mid, 0.50)

if __name__ == '__main__':
    unittest.main()
