import asyncio
import math
import sys
import argparse
from data_feed import BinancePriceMonitor, PolymarketWebSocket, LocalOrderBook
from pricing import CryptoHourlyPricer

def calculate_implied_vol(target_price: float, current_price: float, strike: float, time_left: float) -> float:
    """
    Reverse engineer vol from price using Binary Search.
    
    Target: Price of YES token (0.0 to 1.0)
    current: Spot Price (Underlying)
    strike: Strike Price
    time_left: Seconds
    """
    low = 0.01
    high = 5.0 # 500% vol cap
    
    for _ in range(20): # Precision ~0.01%
        mid = (low + high) / 2
        p = CryptoHourlyPricer.calculate_probability(current_price, strike, time_left, mid)
        
        if p < target_price:
            low = mid # Need higher vol to get away from 0.5? 
            # WAIT: BS Binary Call price direction depends on Moneyness.
            # If OTM (Spot < Strike), Price < 0.5. Higher Vol -> Higher Price (towards 0.5)
            # If ITM (Spot > Strike), Price > 0.5. Higher Vol -> Lower Price (towards 0.5)
            
            # Let's check Moneyness logic carefully.
            # ITM: Spot > Strike. Prob -> 1.0 as Vol -> 0.
            # OTM: Spot < Strike. Prob -> 0.0 as Vol -> 0.
            
            # If Spot > Strike (ITM):
            # Price starts at 1.0 (low vol) and goes DOWN to 0.5 (high vol).
            # So if p < target, we have too much vol (price is too low, we want higher price).
            # So we reduce vol?
            pass
        elif p > target_price:
            pass
            
        # Re-eval logic simply:
        # We want P(vol) == target.
        # Function monotonicity depends on ITM/OTM.
        
        if current_price > strike: # ITM
             # Price decreases as Vol increases (uncertainty kills certainty)
             if p < target_price:
                 # Calculated price too low. We have too much vol.
                 high = mid
             else:
                 # Calculated price too high. We have too little vol.
                 low = mid
        else: # OTM
             # Price increases as Vol increases (chance to hit strike)
             if p < target_price:
                 # Calculated price too low. Need more vol.
                 low = mid
             else:
                 # Calculated price too high. Need less vol.
                 high = mid
                 
    return (low + high) / 2

async def main():
    parser = argparse.ArgumentParser(description="Implied Volatility Calculator")
    parser.add_argument("token_id", help="Polymarket Token ID (YES)")
    parser.add_argument("--strike", type=float, help="Strike Price (if known, else fetched from Binance candle open)")
    
    args = parser.parse_args()
    token_id = args.token_id
    
    print(f"🔮 CALCULATING IMPLIED VOLATILITY for {token_id[:10]}...")
    
    # Initialize Feeds
    poly_ws = PolymarketWebSocket([token_id])
    binance = BinancePriceMonitor()
    
    await poly_ws.connect()
    await binance.connect()
    
    print("⏳ Waiting for data stream...")
    await asyncio.sleep(5)
    
    try:
        # Get Data
        book = poly_ws.get_order_book(token_id)
        if not book or not book.mid_price:
            print("❌ No Polymarket Data (Order Book empty)")
            return
            
        poly_price = book.mid_price
        
        btc_price = binance.get_price("btcusdt")
        if not btc_price:
            print("❌ No Binance Data")
            return
            
        # Infer Strike?
        start_candle = binance.get_candle_open("btcusdt")
        strike = args.strike if args.strike else start_candle
        
        if not strike:
             print("❌ Could not determine Strike Price. Wait for next candle update or pass --strike.")
             return
             
        # Time Left
        time_left = CryptoHourlyPricer.get_time_remaining()
        
        print(f"\n📊 MARKET SNAPSHOT:")
        print(f"   Spot (BTC): ${btc_price:,.2f}")
        print(f"   Strike:     ${strike:,.2f}")
        print(f"   Diff:       ${btc_price - strike:,.2f} ({'ITM' if btc_price > strike else 'OTM'})")
        print(f"   Time Left:  {int(time_left)}s")
        print(f"   Poly Price: {poly_price:.4f} ({poly_price*100:.1f}%)")
        
        # Calculate Realized Vol (Binance)
        real_vol = binance.get_realized_volatility("btcusdt")
        print(f"   Realized Vol (Binance): {real_vol*100:.1f}%")
        
        # Calculate Model Price with Realized Vol
        model_price = CryptoHourlyPricer.calculate_probability(btc_price, strike, time_left, real_vol)
        print(f"   Model Price (Realized): {model_price:.4f}")
        print(f"   Divergence: {model_price - poly_price:.4f}")
        
        # Calculate Implied Vol
        imp_vol = calculate_implied_vol(poly_price, btc_price, strike, time_left)
        print(f"\n⚡ IMPLIED VOLATILITY: {imp_vol*100:.1f}%")
        
        ratio = imp_vol / max(real_vol, 0.01)
        print(f"   IV/RV Ratio: {ratio:.1f}x")
        
        if ratio > 1.5:
            print("\n🚨 HIGH RISK PREMIUM DETECTED.")
            print("   The market is pricing in MUCH higher volatility/risk than Binance shows.")
            print("   Likely causes: Event Risk, Hedging Costs, or 'Gambler's Premium'.")
            
    finally:
        await poly_ws.disconnect()
        await binance.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
