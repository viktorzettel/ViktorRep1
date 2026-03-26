
import unittest
from strategy import CryptoHourlyStrategy, StrategyConfig
from monitoring import MarketMetrics
import logging

# Setup minimal logging
logging.basicConfig(level=logging.INFO)

class TestMirrorStrategy(unittest.TestCase):
    def setUp(self):
        self.strategy = CryptoHourlyStrategy("Bitcoin Up/Down 12PM", binance_monitor=None)
        
    def test_bubble_mirror(self):
        """Test Rank=90% (Bubble) -> Aggressive NO, Defensive YES."""
        metrics = MarketMetrics(
            current_price=0.70,
            rsi_14=75,
            vwap_session=0.50,
            percentile_rank=90, # BUBBLE
            p10=0.40, p20=0.45, p40=0.50, p60=0.55, p80=0.65, p90=0.75
        )
        
        quote = self.strategy._calculate_heatmap_quotes(market_mid=0.70, inventory=0, metrics=metrics)
        
        print(f"\n[BUBBLE TEST] YES Mid: {quote.yes.mid:.3f} | NO Mid: {quote.no.mid:.3f}")
        print(f"YES Bid/Ask: {quote.yes.bid:.3f}/{quote.yes.ask:.3f} (Spread: {quote.yes.spread:.3f})")
        print(f"NO Bid/Ask: {quote.no.bid:.3f}/{quote.no.ask:.3f} (Spread: {quote.no.spread:.3f})")
        
        # YES should be wide (Safety)
        self.assertGreaterEqual(quote.yes.spread, 0.09)
        # NO should be tight (Aggressive)
        self.assertLessEqual(quote.no.spread, 0.02)
        
    def test_deep_val_mirror(self):
        """Test Rank=10% (Deep Value) -> Aggressive YES, Defensive NO."""
        metrics = MarketMetrics(
            current_price=0.30,
            rsi_14=25,
            vwap_session=0.50,
            percentile_rank=10, # DEEP VALUE
            p10=0.25, p20=0.35, p40=0.45, p60=0.55, p80=0.65, p90=0.75
        )
        
        quote = self.strategy._calculate_heatmap_quotes(market_mid=0.30, inventory=0, metrics=metrics)
        
        print(f"\n[DEEP VAL TEST] YES Mid: {quote.yes.mid:.3f} | NO Mid: {quote.no.mid:.3f}")
        print(f"YES Bid/Ask: {quote.yes.bid:.3f}/{quote.yes.ask:.3f} (Spread: {quote.yes.spread:.3f})")
        print(f"NO Bid/Ask: {quote.no.bid:.3f}/{quote.no.ask:.3f} (Spread: {quote.no.spread:.3f})")
        
        # YES should be tight (Aggressive)
        self.assertLessEqual(quote.yes.spread, 0.02)
        # NO should be wide (Safety)
        self.assertGreaterEqual(quote.no.spread, 0.09)

if __name__ == "__main__":
    unittest.main()
