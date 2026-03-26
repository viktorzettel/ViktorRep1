"""
Quick dry-run script to get a single decision from the prediction bot.

Usage:
    python dry_run_decision.py [market_url]
    
Example:
    python dry_run_decision.py https://polymarket.com/event/btc-updown-15m-1770375600
"""

import asyncio
import sys
import time
from datetime import datetime, timezone

from binary_prediction_bot import (
    fetch_chainlink_aligned_price,
    fetch_binance_order_book,
    parse_market_url_epoch,
    fetch_polymarket_prices,
    estimate_p_up,
    make_decision,
    compute_har_rv,
    fetch_binance_klines,
    fetch_funding_rate,
    compute_funding_signal,
)


async def get_quick_decision(market_url: str = None, strike_price: float = None):
    """Get a single decision for the current moment."""
    
    print("\n🤖 QUICK DECISION\n")
    
    # Parse market URL if provided
    time_remaining_min = 7.5  # Default
    if market_url:
        epoch = parse_market_url_epoch(market_url)
        if epoch:
            close_time = datetime.fromtimestamp(epoch, tz=timezone.utc)
            time_remaining_sec = epoch - time.time()
            time_remaining_min = time_remaining_sec / 60
            
            if time_remaining_sec < 0:
                print(f"❌ Market already closed at {close_time.strftime('%H:%M:%S UTC')}")
                print(f"   Please provide a future market URL\n")
                return
            
            print(f"Market closes: {close_time.strftime('%H:%M:%S UTC')} ({time_remaining_min:.1f}m remaining)")
        else:
            print("⚠️  Could not parse market URL")
    
    # Get strike price from user if not provided
    if strike_price is None:
        try:
            strike_input = input("Enter strike price (from Polymarket): $")
            strike_price = float(strike_input.replace(",", "").strip())
        except ValueError:
            print("❌ Invalid price. Using current price as fallback.")
            current_price, _ = await fetch_chainlink_aligned_price()
            strike_price = current_price
    
    # Fetch data
    current_price, price_source = await fetch_chainlink_aligned_price()
    open_price = strike_price
    
    # Compute signals
    klines = await fetch_binance_klines("1m", 1000)
    har_vol = compute_har_rv(klines)
    funding_rate = await fetch_funding_rate()
    funding_signal = compute_funding_signal(funding_rate)
    
    # Estimate probability
    p_up = estimate_p_up(
        current_price=current_price,
        open_price=open_price,
        har_volatility=har_vol,
        micro_vol=har_vol,
        ofi=0.0,
        momentum=0.0,
        funding_signal=funding_signal,
        time_remaining_minutes=time_remaining_min
    )
    
    # Get market price (placeholder)
    yes_price = 0.50
    
    # Make decision
    decision = make_decision(p_up, yes_price)
    
    # Output
    delta_pct = (current_price - open_price) / open_price * 100
    print(f"\nStrike:  ${open_price:,.2f}")
    print(f"Current: ${current_price:,.2f} ({price_source}) → {delta_pct:+.2f}%")
    print(f"P(up):   {p_up:.1%}")
    print(f"Market:  {yes_price:.2f}")
    
    print("\n" + "─"*50)
    
    if decision.action == "BUY":
        print(f"✅ {decision.action} {decision.target.upper()}")
        print(f"   Edge: {decision.edge:.1%} | EV: ${decision.ev:.2f}")
    else:
        print(f"⏸️  {decision.action}")
        print(f"   {decision.reason}")
    
    print("─"*50 + "\n")


async def main():
    # Get command line arguments
    # Usage: python dry_run_decision.py [market_url] [strike_price]
    
    market_url = None
    strike_price = None
    
    if len(sys.argv) > 1:
        market_url = sys.argv[1]
    
    if len(sys.argv) > 2:
        try:
            strike_price = float(sys.argv[2].replace(",", ""))
        except ValueError:
            pass
    
    await get_quick_decision(market_url, strike_price)


if __name__ == "__main__":
    asyncio.run(main())
