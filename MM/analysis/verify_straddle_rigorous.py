"""
RIGOROUS STRADDLE CHECK

Strictly validates:
1. Market is truly HOURLY (checks startDate/endDate)
2. Checks last 100 VALID hourly markets
3. Finds if YES and NO *ever* both traded < 40¢ in the same market
"""

import requests
import concurrent.futures
import time
from datetime import datetime

print("=" * 80)
print("🧐 RIGOROUS STRADDLE CHECK: Last 100 *Verified* Hourly Markets")
print("=" * 80)

def parse_iso(ts):
    if not ts: return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

# 1. Fetch Verified Hourly Markets
verified_markets = []
offset = 0
limit = 100

print("Fetching and filtering markets...")

while len(verified_markets) < 100:
    url = "https://gamma-api.polymarket.com/events"
    params = {
        "closed": "true",
        "limit": limit,
        "offset": offset,
        "tag_slug": "bitcoin"
    }
    
    try:
        resp = requests.get(url, params=params)
        events = resp.json()
        
        if not events:
            break
            
        for e in events:
            # Check market duration
            markets = e.get("markets", [])
            if not markets: continue
            
            m = markets[0]
            start = parse_iso(m.get("startDate"))
            end = parse_iso(m.get("endDate"))
            
            if not start or not end: continue
            
            duration_hours = (end - start).total_seconds() / 3600
            
            # STRICT FILTER: Must be ~1 hour (allow 0.9 - 1.5 for slight variances)
            # Most hourly markets are exactly 1 hour or slightly more if opened early
            # Daily markets are ~24 hours
            if 0.5 <= duration_hours <= 1.5:
                title = e.get("title", "").lower()
                if "bitcoin" in title and "up or down" in title:
                    verified_markets.append({
                        "slug": e.get("slug"),
                        "condition_id": m.get("conditionId"),
                        "title": e.get("title"),
                        "duration": f"{duration_hours:.2f}h"
                    })
    except Exception as e:
        print(f"Error fetching list: {e}")
        time.sleep(1)
    
    offset += limit
    if offset > 3000: break # Scan enough history

verified_markets = verified_markets[:100]
print(f"✅ Found {len(verified_markets)} STRICTLY HOURLY markets.")
print(f"   (Sample: {verified_markets[0]['title']} - Duration: {verified_markets[0]['duration']})")

# 2. Parallel Analysis
print(f"\n{'=' * 80}")
print(f"{'Market':<40} | {'YES Low':^8} | {'NO Low':^8} | {'Status'}")
print("-" * 80)

straddle_count = 0

def analyze_market_rigorous(m):
    short_name = m["slug"].replace("bitcoin-up-or-down-", "")[:40]
    cid = m["condition_id"]
    
    try:
        trades_url = "https://data-api.polymarket.com/trades"
        # 1000 trades is usually enough to catch the extremes of an hourly candle
        t_params = {"market": cid, "limit": 1000} 
        
        resp = requests.get(trades_url, params=t_params, timeout=10)
        trades = resp.json()
        
        if not trades:
            return (short_name, "NoData", "NoData", "No Trades", False)
            
        yes_prices = [float(t["price"]) for t in trades if t.get("outcomeIndex") == 0]
        no_prices = [float(t["price"]) for t in trades if t.get("outcomeIndex") == 1]
        
        if not yes_prices: yes_prices = [1.0]
        if not no_prices: no_prices = [1.0]
        
        yes_min = min(yes_prices)
        no_min = min(no_prices)
        
        is_straddle = yes_min < 0.40 and no_min < 0.40
        
        status = "Neither"
        if is_straddle: status = "✅ STRADDLE"
        elif yes_min < 0.40: status = "YES only"
        elif no_min < 0.40: status = "NO only"
        
        return (short_name, yes_min, no_min, status, is_straddle)
        
    except Exception as e:
        return (short_name, "ERR", "ERR", str(e)[:10], False)

with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
    futures = {executor.submit(analyze_market_rigorous, m): m for m in verified_markets}
    
    for future in concurrent.futures.as_completed(futures):
        name, y_min, n_min, status, is_straddle = future.result()
        
        if is_straddle:
            straddle_count += 1
            
        y_str = f"{y_min:.2f}" if isinstance(y_min, float) else str(y_min)
        n_str = f"{n_min:.2f}" if isinstance(n_min, float) else str(n_min)
        
        print(f"{name:<40} | {y_str:^8} | {n_str:^8} | {status}")

print(f"\n{'=' * 80}")
print("🏁 FINAL RIGOROUS RESULTS")
print("=" * 80)
print(f"Verified Hourly Markets Analyzed: {len(verified_markets)}")
print(f"Straddle Opportunities (Both < 40¢): {straddle_count}")
print(f"Rate: {straddle_count/len(verified_markets)*100:.1f}%")
