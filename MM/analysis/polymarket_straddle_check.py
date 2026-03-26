"""
Fetch Polymarket Trade History for Active/Recent BTC Markets

Analyze actual trade prices to see if YES and NO both hit below 40¢.
"""

import requests
import json
from datetime import datetime

print("=" * 80)
print("🔍 Fetching BTC hourly markets with trade data")
print("=" * 80)

# First, find BTC hourly markets (both open and recently closed)
url = "https://gamma-api.polymarket.com/events"
params = {
    "limit": 200,
    "active": "true",
}

resp = requests.get(url, params=params)
events = resp.json()

btc_markets = []

for event in events:
    title = event.get("title", "").lower()
    slug = event.get("slug", "")
    
    # Look for BTC hourly markets
    if "bitcoin" in title and ("up or down" in title):
        markets = event.get("markets", [])
        for m in markets:
            tokens_raw = m.get("clobTokenIds", [])
            if isinstance(tokens_raw, str):
                try:
                    tokens = json.loads(tokens_raw)
                except:
                    tokens = []
            else:
                tokens = tokens_raw
            
            if len(tokens) >= 2:
                btc_markets.append({
                    "title": event.get("title"),
                    "slug": slug,
                    "yes_token": tokens[0],
                    "no_token": tokens[1],
                    "end_date": m.get("endDate"),
                    "active": m.get("active", True),
                })

print(f"\nFound {len(btc_markets)} BTC hourly markets")

# Analyze trades for each market
results = []

for i, market in enumerate(btc_markets[:20]):  # Check up to 20 markets
    print(f"\n[{i+1}/{min(20, len(btc_markets))}] {market['title'][:60]}...")
    
    yes_trades = []
    no_trades = []
    
    # Fetch YES trades
    try:
        trades_url = "https://clob.polymarket.com/trades"
        params = {"asset_id": market["yes_token"], "limit": 500}
        resp = requests.get(trades_url, params=params)
        if resp.status_code == 200:
            data = resp.json()
            yes_trades = [float(t.get("price", 0)) for t in data if t.get("price")]
    except Exception as e:
        print(f"  YES trades error: {e}")
    
    # Fetch NO trades
    try:
        params = {"asset_id": market["no_token"], "limit": 500}
        resp = requests.get(trades_url, params=params)
        if resp.status_code == 200:
            data = resp.json()
            no_trades = [float(t.get("price", 0)) for t in data if t.get("price")]
    except Exception as e:
        print(f"  NO trades error: {e}")
    
    if yes_trades or no_trades:
        yes_min = min(yes_trades) if yes_trades else 1.0
        yes_max = max(yes_trades) if yes_trades else 0.0
        no_min = min(no_trades) if no_trades else 1.0
        no_max = max(no_trades) if no_trades else 0.0
        
        # Check if straddle was possible
        yes_below_40 = yes_min < 0.40
        no_below_40 = no_min < 0.40
        straddle_possible = yes_below_40 and no_below_40
        
        results.append({
            "title": market["title"],
            "yes_trades": len(yes_trades),
            "no_trades": len(no_trades),
            "yes_min": yes_min,
            "yes_max": yes_max,
            "no_min": no_min,
            "no_max": no_max,
            "yes_below_40": yes_below_40,
            "no_below_40": no_below_40,
            "straddle_possible": straddle_possible,
        })
        
        status = "✅ STRADDLE" if straddle_possible else "❌"
        print(f"  YES: {yes_min:.2f}-{yes_max:.2f} ({len(yes_trades)} trades) | NO: {no_min:.2f}-{no_max:.2f} ({len(no_trades)} trades) {status}")
    else:
        print(f"  No trade data available")

# Summary
print(f"\n{'=' * 80}")
print("📊 STRADDLE ANALYSIS FROM REAL POLYMARKET DATA")
print(f"{'=' * 80}")

if results:
    total = len(results)
    yes_opportunities = sum(1 for r in results if r["yes_below_40"])
    no_opportunities = sum(1 for r in results if r["no_below_40"])
    straddle_opportunities = sum(1 for r in results if r["straddle_possible"])
    
    print(f"\n  Markets analyzed: {total}")
    print(f"\n  YES traded below 40¢: {yes_opportunities}/{total} ({yes_opportunities/total*100:.1f}%)")
    print(f"  NO traded below 40¢:  {no_opportunities}/{total} ({no_opportunities/total*100:.1f}%)")
    print(f"\n  🎯 STRADDLE POSSIBLE (both < 40¢): {straddle_opportunities}/{total} ({straddle_opportunities/total*100:.1f}%)")
    
    if straddle_opportunities > 0:
        print(f"\n  Straddle-enabled markets:")
        for r in results:
            if r["straddle_possible"]:
                print(f"    - {r['title'][:60]}")
                print(f"      YES: {r['yes_min']:.2f}-{r['yes_max']:.2f} | NO: {r['no_min']:.2f}-{r['no_max']:.2f}")
else:
    print("  No trade data found")

print(f"\n{'=' * 80}")
