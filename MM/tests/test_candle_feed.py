"""
Verification Script for Phase 2: Binance Candle Tracker
"""
import asyncio
import logging
from data_feed import BinancePriceMonitor

# Configure logging to see what's happening
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestCandle")

async def test_candle_feed():
    print("--- Testing Binance Candle Feed (1H) ---")
    
    monitor = BinancePriceMonitor(symbols=["btcusdt"])
    
    # Callback to print updates
    # Callback to print updates
    count = 0
    def on_price(price):
        nonlocal count
        count += 1
        candle_open = monitor.get_candle_open("btcusdt")
        
        # Print first 5 updates and every 10th
        if count <= 5 or count % 10 == 0:
            print(f"Update #{count} | Price: {price.price} | CandleOpen: {candle_open}")
            
        if candle_open:
            print(f"✅ SUCCESS! BTC Price: ${price.price:,.2f} | 1H Candle Open: ${candle_open:,.2f}")
            # We can stop after one success
            asyncio.create_task(monitor.disconnect())
    
    monitor.on_price_update = on_price
    monitor.on_error = lambda e: print(f"❌ MONITOR ERROR: {e}")
    
    print("Connecting to Binance WS...")
    # Run for max 30 seconds to allow Kline update (can be 2-3s delay)
    try:
        await asyncio.wait_for(monitor.connect(), timeout=30.0)
    except asyncio.TimeoutError:
        print("Test finished (Timeout)")
    except Exception as e:
        # Expected to disconnect
        pass

if __name__ == "__main__":
    asyncio.run(test_candle_feed())
