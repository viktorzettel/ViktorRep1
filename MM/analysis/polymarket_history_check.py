"""
Fetch Polymarket Historical Data

Check if we can get historical price data for BTC hourly markets
to see how often YES and NO both traded below 40¢.
"""

import requests
import json
from datetime import datetime, timedelta

# ============================================================================
# APPROACH 1: Gamma API - Check for historical markets
# ============================================================================

print("=" * 80)
print("🔍 Searching for historical BTC hourly markets on Polymarket")
print("=" * 80)

# Get recent closed BTC hourly markets
url = "https://gamma-api.polymarket.com/events"
params = {
    "closed": "true",
    "limit": 50,
}

resp = requests.get(url, params=params)
events = resp.json()

btc_hourly_markets = []

for event in events:
    title = event.get("title", "").lower()
    if "bitcoin" in title and ("up or down" in title or "hourly" in title):
        btc_hourly_markets.append({
            "title": event.get("title"),
            "id": event.get("id"),
            "slug": event.get("slug"),
            "end_date": event.get("endDate"),
        })

print(f"\nFound {len(btc_hourly_markets)} closed BTC hourly markets:")
for m in btc_hourly_markets[:10]:
    print(f"  - {m['title']}")

# ============================================================================
# APPROACH 2: CLOB API - Check for trade history
# ============================================================================

print(f"\n{'=' * 80}")
print("🔍 Checking CLOB API for trade history")
print(f"{'=' * 80}")

# Try to get a recent market's token ID
if btc_hourly_markets:
    slug = btc_hourly_markets[0].get("slug")
    print(f"\nChecking market: {slug}")
    
    # Get market details
    market_url = f"https://gamma-api.polymarket.com/events?slug={slug}"
    resp = requests.get(market_url)
    
    if resp.status_code == 200:
        data = resp.json()
        if data and "markets" in data[0]:
            market = data[0]["markets"][0]
            tokens_raw = market.get("clobTokenIds", [])
            
            if isinstance(tokens_raw, str):
                tokens = json.loads(tokens_raw)
            else:
                tokens = tokens_raw
            
            if tokens:
                token_id = tokens[0]
                print(f"Token ID: {token_id[:30]}...")
                
                # Try to get trade history
                trades_url = f"https://clob.polymarket.com/trades"
                trades_params = {"asset_id": token_id, "limit": 100}
                
                try:
                    trades_resp = requests.get(trades_url, params=trades_params)
                    if trades_resp.status_code == 200:
                        trades = trades_resp.json()
                        print(f"Found {len(trades)} trades")
                        
                        if trades:
                            # Analyze price range
                            prices = [float(t.get("price", 0)) for t in trades if t.get("price")]
                            if prices:
                                print(f"\nPrice stats for this market:")
                                print(f"  Min: {min(prices):.2f}")
                                print(f"  Max: {max(prices):.2f}")
                                print(f"  Range: {max(prices) - min(prices):.2f}")
                                
                                below_40 = sum(1 for p in prices if p < 0.40)
                                print(f"  Trades below 40¢: {below_40}/{len(prices)} ({below_40/len(prices)*100:.1f}%)")
                    else:
                        print(f"Trades API returned {trades_resp.status_code}")
                except Exception as e:
                    print(f"Error fetching trades: {e}")

# ============================================================================
# APPROACH 3: Timeseries API
# ============================================================================

print(f"\n{'=' * 80}")
print("🔍 Checking for timeseries/candle data")
print(f"{'=' * 80}")

# Try different API endpoints that might have historical data
endpoints_to_try = [
    "https://clob.polymarket.com/prices-history",
    "https://gamma-api.polymarket.com/prices",
    "https://strapi-matic.poly.market/markets",
]

for endpoint in endpoints_to_try:
    try:
        resp = requests.get(endpoint, timeout=5)
        print(f"\n{endpoint}")
        print(f"  Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"  Response type: {type(data)}")
            if isinstance(data, list):
                print(f"  Items: {len(data)}")
            elif isinstance(data, dict):
                print(f"  Keys: {list(data.keys())[:5]}")
    except Exception as e:
        print(f"  Error: {e}")

# ============================================================================
# APPROACH 4: Check if there's a GraphQL or websocket history
# ============================================================================

print(f"\n{'=' * 80}")
print("💡 CONCLUSION")
print(f"{'=' * 80}")

print("""
  Polymarket historical price data options:
  
  1. TRADE HISTORY: Available via /trades endpoint
     - Can reconstruct price movements from individual trades
     - But: closed markets may have limited data
     
  2. ORDER BOOK SNAPSHOTS: Not available historically
     - We'd need to record this ourselves going forward
     
  3. ALTERNATIVE: Use the trades data we can fetch to validate
     - For each closed market, check min/max YES and NO prices
     - See if both touched below 40¢
     
  RECOMMENDATION:
  - Start recording live Polymarket order book data now
  - Use what historical trade data we can fetch
  - Or: test the straddle strategy live and track results
""")
