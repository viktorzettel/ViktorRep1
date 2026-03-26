"""
Analyze the CURRENT order book to understand price range and liquidity.
"""

import requests
import json
from client_wrapper import PolymarketClient

# Get active markets for different hours
slugs = [
    "bitcoin-up-or-down-february-1-4pm-et",
    "bitcoin-up-or-down-february-1-5pm-et", 
    "bitcoin-up-or-down-february-1-6pm-et",
]

pc = PolymarketClient()
client = pc.get_client()

print("=" * 80)
print("📊 LIVE ORDER BOOK ANALYSIS")
print("=" * 80)

for slug in slugs:
    print(f"\n🎯 {slug}")
    
    try:
        resp = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}")
        data = resp.json()
        
        if not data:
            print("  Not found")
            continue
        
        market = data[0]["markets"][0]
        
        # Get prices
        prices = json.loads(market.get("outcomePrices", "[]"))
        yes_price = float(prices[0]) if prices else 0.5
        no_price = float(prices[1]) if len(prices) > 1 else 0.5
        
        # Get tokens
        tokens = json.loads(market.get("clobTokenIds", "[]"))
        if len(tokens) < 2:
            print("  No tokens")
            continue
        
        yes_token = tokens[0]
        no_token = tokens[1]
        
        # Get order books
        yes_book = client.get_order_book(yes_token)
        no_book = client.get_order_book(no_token)
        
        print(f"  Current YES: {yes_price:.2f} | NO: {no_price:.2f}")
        print(f"\n  YES Order Book:")
        print(f"    Bids (BUY YES): {len(yes_book.bids)} orders")
        print(f"    Asks (SELL YES): {len(yes_book.asks)} orders")
        
        if yes_book.bids:
            best_bid = float(yes_book.bids[0].price)
            lowest_bid = float(yes_book.bids[-1].price) if yes_book.bids else 0
            print(f"    Bid range: {lowest_bid:.2f} to {best_bid:.2f}")
        
        if yes_book.asks:
            best_ask = float(yes_book.asks[0].price)
            highest_ask = float(yes_book.asks[-1].price) if yes_book.asks else 1
            print(f"    Ask range: {best_ask:.2f} to {highest_ask:.2f}")
        
        # Calculate: if you wanted to buy YES at 38¢, is there liquidity?
        print(f"\n  Liquidity Check:")
        
        yes_at_38 = sum(float(b.size) for b in yes_book.bids if float(b.price) >= 0.38)
        yes_below_38 = sum(float(b.size) for b in yes_book.bids if float(b.price) < 0.38)
        
        print(f"    YES bids at 38¢+: {yes_at_38:.1f} shares")
        print(f"    YES bids below 38¢: {yes_below_38:.1f} shares")
        
        # Check NO side
        print(f"\n  NO Order Book:")
        print(f"    Bids (BUY NO): {len(no_book.bids)} orders")
        
        if no_book.bids:
            best_bid = float(no_book.bids[0].price)
            lowest_bid = float(no_book.bids[-1].price) if no_book.bids else 0
            print(f"    Bid range: {lowest_bid:.2f} to {best_bid:.2f}")
        
        no_at_38 = sum(float(b.size) for b in no_book.bids if float(b.price) >= 0.38)
        no_below_38 = sum(float(b.size) for b in no_book.bids if float(b.price) < 0.38)
        
        print(f"    NO bids at 38¢+: {no_at_38:.1f} shares")
        print(f"    NO bids below 38¢: {no_below_38:.1f} shares")
        
        # Spread analysis
        if yes_book.bids and yes_book.asks:
            bid = float(yes_book.bids[0].price)
            ask = float(yes_book.asks[0].price)
            spread = ask - bid
            mid = (bid + ask) / 2
            print(f"\n  Spread: {spread:.2f} ({spread/mid*100:.1f}%)")
        
    except Exception as e:
        print(f"  Error: {e}")

print(f"\n{'=' * 80}")
print("💡 CONCLUSION")
print("=" * 80)
print("""
  The key question for straddle strategy:
  
  1. Do BOTH YES and NO have buyers at 38¢ or below?
     - If yes → straddle is possible
     - If no → you can only buy one side cheap
  
  2. Is there enough liquidity to fill your orders at 38¢?
     - Need ~13 shares per side (for $5 budget)
     
  3. What's the typical spread?
     - Wide spread = harder to exit at profit
     - Tight spread = easier scalping
""")
