"""
Analyze the last 100 closed BTC hourly markets.
Goal: Check if YES and NO *ever* both dropped below 40¢ in the same market.
"""

import requests
import json
import time

print("=" * 80)
print("🔍 Fetching last 100 BTC Hourly Markets")
print("=" * 80)

# 1. Fetch Markets
markets_to_analyze = []
offset = 0
limit = 50  # Fetch in batches

while len(markets_to_analyze) < 100:
    url = "https://gamma-api.polymarket.com/events"
    params = {
        "closed": "true",
        "limit": limit,
        "offset": offset,
        "tag_slug": "bitcoin" # Filter for Bitcoin markets if possible, or just search
    }
    
    try:
        resp = requests.get(url, params=params)
        events = resp.json()
        
        if not events:
            print("No more events found.")
            break
            
        for e in events:
            title = e.get("title", "").lower()
            slug = e.get("slug", "")
            
            # Filter for BTC Hourly markets (Up or Down)
            if "bitcoin" in title and "up or down" in title:
                # Get the market details
                market_list = e.get("markets", [])
                if market_list:
                    m = market_list[0] # Usually only 1 market per event for these
                    condition_id = m.get("conditionId")
                    if condition_id:
                        markets_to_analyze.append({
                            "slug": slug,
                            "condition_id": condition_id,
                            "title": e.get("title")
                        })
                        
    except Exception as e:
        print(f"Error fetching events: {e}")
        time.sleep(1)
    
    offset += limit
    print(f"Found {len(markets_to_analyze)} potential markets...")
    if offset > 1000: # Safety break
        break

markets_to_analyze = markets_to_analyze[:100]
print(f"\n✅ Ready to analyze {len(markets_to_analyze)} markets.")

# 2. Analyze Trades
print(f"\n{'=' * 80}")
print("📊 Analyzing Trade History")
print("=" * 80)
print(f"{'Market':<40} | {'YES Low':^8} | {'NO Low':^8} | {'Both < 40?'}")
print("-" * 80)

straddle_count = 0
success_markets = []

for i, m in enumerate(markets_to_analyze):
    slug = m["slug"]
    cid = m["condition_id"]
    short_name = slug.replace("bitcoin-up-or-down-", "")[:40]
    
    try:
        # Fetch trades (limit 2000 should cover significant moves, usually 
        # extreme dips happen with volume, but we want min price check)
        # Using a reasonable limit to be polite to API, max useful usually.
        trades_url = "https://data-api.polymarket.com/trades"
        t_params = {"market": cid, "limit": 5000} # Get plenty of history
        
        t_resp = requests.get(trades_url, params=t_params)
        if t_resp.status_code != 200:
            print(f"{short_name:<40} | {'ERR':^8} | {'ERR':^8} | Error")
            continue
            
        trades = t_resp.json()
        
        if not trades:
            print(f"{short_name:<40} | {'NoData':^8} | {'NoData':^8} | No Trades")
            continue
            
        yes_prices = [float(t["price"]) for t in trades if t.get("outcomeIndex") == 0]
        no_prices = [float(t["price"]) for t in trades if t.get("outcomeIndex") == 1]
        
        if not yes_prices: yes_prices = [1.0] # Default if no trades
        if not no_prices: no_prices = [1.0]
        
        yes_min = min(yes_prices)
        no_min = min(no_prices)
        
        is_straddle = yes_min < 0.40 and no_min < 0.40
        
        status = "❌"
        if is_straddle:
            status = "✅ YES!"
            straddle_count += 1
            success_markets.append(m["title"])
        elif yes_min < 0.40:
            status = "YES only"
        elif no_min < 0.40:
            status = "NO only"
        else:
            status = "Neither"
            
        print(f"{short_name:<40} | {yes_min:^8.2f} | {no_min:^8.2f} | {status}")
        
    except Exception as e:
        print(f"{short_name:<40} | {'ERR':^8} | {'ERR':^8} | {e}")
        
    # Rate limit nice-ness
    # time.sleep(0.1) 

print(f"\n{'=' * 80}")
print("🏁 FINAL RESULTS")
print("=" * 80)
print(f"Markets Analyzed: {len(markets_to_analyze)}")
print(f"Straddle Opportunities (Both < 40¢): {straddle_count}")

if success_markets:
    print("\nMarkets where it happened:")
    for title in success_markets:
        print(f" - {title}")
else:
    print("\nConclusion: In the last 100 markets, finding BOTH tokens below 40¢ was IMPOSSIBLE.")
    print("This confirms the arbitrage constraint (PriceA + PriceB ≈ 1.00).")
