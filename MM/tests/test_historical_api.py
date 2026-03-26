"""
Fetch Polymarket Historical Price Data using documented endpoints.

From the API doc:
- GET /prices-history on CLOB API
- GET /trades on Data API
"""

import requests
import json

print("=" * 80)
print("🔍 Testing Polymarket Historical Data Endpoints")
print("=" * 80)

# First get a closed market's token IDs
slug = "bitcoin-up-or-down-january-31-10am-et"
print(f"\nFetching market: {slug}")

resp = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}")
data = resp.json()

if not data:
    print("Market not found")
    exit()

market = data[0]["markets"][0]
tokens = json.loads(market.get("clobTokenIds", "[]"))
condition_id = market.get("conditionId")

print(f"Condition ID: {condition_id}")
print(f"Token IDs: {tokens}")

yes_token = tokens[0] if tokens else None
no_token = tokens[1] if len(tokens) > 1 else None

# ============================================================================
# TEST 1: CLOB API - Price History
# ============================================================================
print(f"\n{'=' * 80}")
print("📊 TEST 1: CLOB /prices-history")
print("=" * 80)

if yes_token:
    # Try different URL patterns
    endpoints = [
        f"https://clob.polymarket.com/prices-history?token_id={yes_token}&interval=1d",
        f"https://clob.polymarket.com/prices-history?token_id={yes_token}",
        f"https://clob.polymarket.com/price-history?token_id={yes_token}",
        f"https://clob.polymarket.com/price-history?token_id={yes_token}&fidelity=60",
    ]
    
    for url in endpoints:
        try:
            resp = requests.get(url, timeout=10)
            print(f"\n{url.split('?')[0]}...")
            print(f"  Status: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                print(f"  Response type: {type(data)}")
                if isinstance(data, dict):
                    print(f"  Keys: {list(data.keys())}")
                    if "history" in data:
                        hist = data["history"]
                        print(f"  History points: {len(hist)}")
                        if hist:
                            print(f"  Sample: {hist[:3]}")
                elif isinstance(data, list):
                    print(f"  Items: {len(data)}")
                    if data:
                        print(f"  Sample: {data[:3]}")
            else:
                print(f"  Body: {resp.text[:200]}")
        except Exception as e:
            print(f"  Error: {e}")

# ============================================================================
# TEST 2: Data API - Trades
# ============================================================================
print(f"\n{'=' * 80}")
print("📊 TEST 2: Data API /trades")
print("=" * 80)

if condition_id:
    endpoints = [
        f"https://data-api.polymarket.com/trades?market={condition_id}&limit=100",
        f"https://data-api.polymarket.com/trades?condition_id={condition_id}&limit=100",
    ]
    
    for url in endpoints:
        try:
            resp = requests.get(url, timeout=10)
            print(f"\n{url.split('?')[0]}...")
            print(f"  Status: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    print(f"  Trades found: {len(data)}")
                    if data:
                        trade = data[0]
                        print(f"  Sample trade keys: {list(trade.keys())}")
                        if "price" in trade:
                            prices = [float(t["price"]) for t in data if "price" in t]
                            print(f"  Price range: {min(prices):.2f} - {max(prices):.2f}")
                else:
                    print(f"  Response: {data}")
            else:
                print(f"  Body: {resp.text[:200]}")
        except Exception as e:
            print(f"  Error: {e}")

# ============================================================================
# TEST 3: Alternative - timeseries endpoint
# ============================================================================
print(f"\n{'=' * 80}")
print("📊 TEST 3: Timeseries endpoint")
print("=" * 80)

if yes_token:
    endpoints = [
        f"https://clob.polymarket.com/timeseries?token_id={yes_token}",
        f"https://gamma-api.polymarket.com/timeseries?token_id={yes_token}",
    ]
    
    for url in endpoints:
        try:
            resp = requests.get(url, timeout=10)
            print(f"\n{url.split('?')[0]}...")
            print(f"  Status: {resp.status_code}")
            if resp.status_code == 200:
                print(f"  Response: {resp.text[:500]}")
        except Exception as e:
            print(f"  Error: {e}")

print(f"\n{'=' * 80}")
