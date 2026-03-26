"""
Analyze REAL Polymarket trade data for BTC hourly markets.
"""

import requests
import json
from datetime import datetime, timedelta

print("=" * 80)
print("🔍 Fetching BTC hourly markets and their trade history")
print("=" * 80)

# Get multiple hourly markets (past few days)
base_slugs = [
    "bitcoin-up-or-down-february-1-10am-et",
    "bitcoin-up-or-down-february-1-9am-et",
    "bitcoin-up-or-down-february-1-11am-et",
    "bitcoin-up-or-down-january-31-10am-et",
    "bitcoin-up-or-down-january-31-9am-et",
    "bitcoin-up-or-down-january-31-11am-et",
    "bitcoin-up-or-down-january-31-3pm-et",
    "bitcoin-up-or-down-january-31-4pm-et",
    "bitcoin-up-or-down-january-30-10am-et",
    "bitcoin-up-or-down-january-30-9am-et",
]

results = []

for slug in base_slugs:
    print(f"\n📊 {slug}")
    
    try:
        resp = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}")
        data = resp.json()
        
        if not data:
            print("  Not found")
            continue
        
        event = data[0]
        market = event.get("markets", [{}])[0]
        
        # Get token IDs
        tokens_raw = market.get("clobTokenIds", [])
        if isinstance(tokens_raw, str):
            tokens = json.loads(tokens_raw)
        else:
            tokens = tokens_raw
        
        if len(tokens) < 2:
            print("  No tokens found")
            continue
        
        yes_token = tokens[0]
        no_token = tokens[1]
        
        # Current prices
        prices_raw = market.get("outcomePrices", "[]")
        if isinstance(prices_raw, str):
            prices = json.loads(prices_raw)
        else:
            prices = prices_raw
        
        current_yes = float(prices[0]) if prices else 0.5
        current_no = float(prices[1]) if len(prices) > 1 else 0.5
        
        print(f"  Current: YES={current_yes:.2f} NO={current_no:.2f}")
        
        # Fetch trade history for YES
        trades_url = "https://clob.polymarket.com/trades"
        yes_resp = requests.get(trades_url, params={"asset_id": yes_token, "limit": 500})
        no_resp = requests.get(trades_url, params={"asset_id": no_token, "limit": 500})
        
        yes_trades = yes_resp.json() if yes_resp.status_code == 200 else []
        no_trades = no_resp.json() if no_resp.status_code == 200 else []
        
        yes_prices = [float(t["price"]) for t in yes_trades if "price" in t]
        no_prices = [float(t["price"]) for t in no_trades if "price" in t]
        
        if yes_prices:
            print(f"  YES: {len(yes_prices)} trades | Min: {min(yes_prices):.2f} | Max: {max(yes_prices):.2f}")
        else:
            print(f"  YES: No trades")
        
        if no_prices:
            print(f"  NO:  {len(no_prices)} trades | Min: {min(no_prices):.2f} | Max: {max(no_prices):.2f}")
        else:
            print(f"  NO:  No trades")
        
        # Check straddle possibility
        yes_min = min(yes_prices) if yes_prices else 1.0
        no_min = min(no_prices) if no_prices else 1.0
        yes_max = max(yes_prices) if yes_prices else 0.0
        no_max = max(no_prices) if no_prices else 0.0
        
        yes_below_40 = yes_min < 0.40
        no_below_40 = no_min < 0.40
        straddle = yes_below_40 and no_below_40
        
        results.append({
            "slug": slug,
            "yes_trades": len(yes_prices),
            "no_trades": len(no_prices),
            "yes_min": yes_min,
            "yes_max": yes_max,
            "no_min": no_min,
            "no_max": no_max,
            "yes_below_40": yes_below_40,
            "no_below_40": no_below_40,
            "straddle": straddle,
        })
        
        if straddle:
            print(f"  ✅ STRADDLE WAS POSSIBLE!")
        elif yes_below_40:
            print(f"  📉 YES dipped below 40¢")
        elif no_below_40:
            print(f"  📈 NO dipped below 40¢ (YES spiked)")
        else:
            print(f"  ➡️ Neither touched 40¢")
            
    except Exception as e:
        print(f"  Error: {e}")

# Summary
print(f"\n{'=' * 80}")
print("📊 SUMMARY FROM REAL POLYMARKET DATA")
print(f"{'=' * 80}")

if results:
    total = len(results)
    yes_dips = sum(1 for r in results if r["yes_below_40"])
    no_dips = sum(1 for r in results if r["no_below_40"])
    straddles = sum(1 for r in results if r["straddle"])
    
    print(f"\n  Markets analyzed: {total}")
    print(f"\n  YES traded below 40¢: {yes_dips}/{total} ({yes_dips/total*100:.1f}%)")
    print(f"  NO traded below 40¢:  {no_dips}/{total} ({no_dips/total*100:.1f}%)")
    print(f"\n  🎯 STRADDLE POSSIBLE: {straddles}/{total} ({straddles/total*100:.1f}%)")
    
    # Show all results in table
    print(f"\n  {'Market':<45} | {'YES Range':^15} | {'NO Range':^15} | Status")
    print(f"  {'-'*45}-+-{'-'*15}-+-{'-'*15}-+-{'-'*10}")
    
    for r in results:
        market_name = r["slug"].replace("bitcoin-up-or-down-", "")[:40]
        yes_range = f"{r['yes_min']:.2f}-{r['yes_max']:.2f}"
        no_range = f"{r['no_min']:.2f}-{r['no_max']:.2f}"
        
        if r["straddle"]:
            status = "✅ STRADDLE"
        elif r["yes_below_40"]:
            status = "YES<40"
        elif r["no_below_40"]:
            status = "NO<40"
        else:
            status = "none<40"
        
        print(f"  {market_name:<45} | {yes_range:^15} | {no_range:^15} | {status}")
else:
    print("  No data found")

print(f"\n{'=' * 80}")
