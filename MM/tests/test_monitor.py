
import logging
import sys
from monitoring import MarketMonitor, MarketMetrics
from strategy import CryptoHourlyStrategy, StrategyConfig, DualQuote, Quote
from client_wrapper import PolymarketClient

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_monitor():
    # TEST 1: LIVE DATA FETCH
    token_id = "31909393856053520018507280554368586128735607865481470209833276228711603596664" # Example
    print(f"--- Testing MarketMonitor with Token {token_id} ---")
    
    monitor = MarketMonitor(token_id)
    metrics = monitor.calculate_metrics()
    
    if metrics:
        print(f"✅ Metrics Calculated:")
        print(f"   Current Price: {metrics.current_price}")
        print(f"   RSI (14): {metrics.rsi_14:.2f}")
        print(f"   VWAP: {metrics.vwap_session:.4f}")
        print(f"   Rank: {metrics.percentile_rank:.1f}%")
        print(f"   Zones: P10={metrics.p10:.2f}, Median={metrics.vwap_session:.2f}, P90={metrics.p90:.2f}")
    else:
        print("❌ Failed to calculate metrics (maybe not enough history?)")
        # Mock Metrics for Logic Test
        metrics = MarketMetrics(
            current_price=0.35,
            rsi_14=35.0,
            vwap_session=0.50,
            percentile_rank=15.0, # Target: DEEP_VAL / CHEAP
            p10=0.20, p20=0.30, p40=0.45, p60=0.55, p80=0.70, p90=0.80
        )
        print("⚠️ Using MOCK metrics for Strategy Test.")

    # TEST 2: STRATEGY LOGIC
    print("\n--- Testing Heatmap Strategy Logic ---")
    
    # Mock Strategy class wrapper
    class MockBinance:
        def get_price(self, pair): return 95000.0
        def get_candle_open(self, pair): return 94000.0
        def get_realized_volatility(self, pair): return 0.5
        def get_time_remaining(self): return 1800
        def get_realized_moments(self, pair): return 0.0, 0.0 # Skew, Kurt
        def get_price_ema(self, pair, window_seconds): return 95000.0
        def get_volume_flow(self, pair, window_seconds): return 0.0
        
    strat = CryptoHourlyStrategy("Test Market", MockBinance())
    
    # Needs valid market data to run, manually inject
    from crypto_parser import CryptoMarketData
    strat.market_data = CryptoMarketData("BTC", "hourly", 95000, "up")
    
    # TEST SCENARIO: Rank 15% (Deep/Cheap) but RSI 50 (Neutral) -> Should trigger BLOCK
    print(f"Testing Scenario: Rank {metrics.percentile_rank:.1f}% | RSI {metrics.rsi_14:.1f}")
    
    # Init Strategy with Mock Monitor having explicit values for this test
    # We can pass metrics directly to get_dual_quotes override
    
    metrics_override = MarketMetrics(
         current_price=0.35,
         rsi_14=50.0, # Neutral (Should Block Sniper)
         vwap_session=0.50,
         percentile_rank=15.0, # Deep Value
         p10=0.20, p20=0.30, p40=0.45, p60=0.55, p80=0.70, p90=0.80
    )
    
    quote = strat.get_dual_quotes(0.35, 0.0, metrics=metrics_override)
    
    print(f"Quote Result:")
    print(f"   YES Bid/Ask: {quote.yes.bid:.3f} / {quote.yes.ask:.3f}")
    print(f"   Spread: {quote.yes.spread:.3f}")
    
    meta = quote.metadata
    print(f"   Metadata: {meta}")
    
    # Verification: RSI 50 should NO LONGER BLOCK. Spread should be 0.04 (Deep Value)
    if quote.yes.spread >= 0.04:
        print("✅ RSI Bypass Verified. Spread is 0.04 (Sniper Mode active despite Neutral RSI).")
    else:
        print(f"❌ Logic Check Failed. Spread is {quote.yes.spread:.3f} (Still blocked?)")

if __name__ == "__main__":
    test_monitor()
