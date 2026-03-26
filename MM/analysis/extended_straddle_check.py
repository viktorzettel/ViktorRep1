import requests
import concurrent.futures
import time

print("=" * 80)
print("🔨 EXTENDED STRADDLE CHECK: Hourly BTC Up/Down Markets (Jan 15-31)")
print("Including 5am-7am and 6pm ET")
print("=" * 80)

# Generate potential slugs for January 15–31
slugs_to_check = []

# EXTENDED hours: 5am to 6pm inclusive
hours = [5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6] 
am_pm = {
    5: "am", 6: "am", 7: "am", 8: "am", 9: "am", 10: "am", 11: "am",
    12: "pm", 1: "pm", 2: "pm", 3: "pm", 4: "pm", 5: "pm", 6: "pm"
}

for day in range(15, 32):
    for h in hours:
        suffix = am_pm[h]
        slug = f"bitcoin-up-or-down-january-{day}-{h}{suffix}-et"
        slugs_to_check.append(slug)

print(f"Generated {len(slugs_to_check)} potential hourly slugs.")

# Results containers
verified_markets = []
straddles = []
yes_only = []
no_only = []

def check_slug(slug):
    try:
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200: return None
        events = resp.json()
        if not events: return None
        event = events[0]
        if "markets" not in event or not event["markets"]: return None
        
        market = event["markets"][0]
        condition_id = market.get("conditionId")
        if not condition_id: return None
        
        # Limit trades to first 2000 for speed, usually enough to catch extreme volatility
        trades_url = "https://data-api.polymarket.com/trades"
        params = {"market": condition_id, "limit": 2000}
        
        t_resp = requests.get(trades_url, params=params, timeout=10)
        if t_resp.status_code != 200: return None
        trades = t_resp.json()
        if not trades: return None
        
        yes_prices = [float(t["price"]) for t in trades if t.get("outcomeIndex") == 0]
        no_prices = [float(t["price"]) for t in trades if t.get("outcomeIndex") == 1]
        
        if not yes_prices: yes_prices = [1.0]
        if not no_prices: no_prices = [1.0]
        
        yes_min = min(yes_prices)
        no_min = min(no_prices)
        
        is_straddle = yes_min < 0.42 and no_min < 0.42        
        
        result = {
            "slug": slug,
            "title": event.get("title", ""),
            "yes_min": yes_min,
            "no_min": no_min,
            "is_straddle": is_straddle,
            "trades": len(trades)
        }
        return result
    
    except:
        return None

print("\n🚀 Checking slugs in parallel...")

with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
    futures = {executor.submit(check_slug, slug): slug for slug in slugs_to_check}
    
    for future in concurrent.futures.as_completed(futures):
        res = future.result()
        if res:
            verified_markets.append(res)
            
            status = "Neither"
            if res["is_straddle"]:
                status = "✅ STRADDLE"
                straddles.append(res)
            elif res["yes_min"] < 0.40:
                yes_only.append(res)
            elif res["no_min"] < 0.40:
                no_only.append(res)
            
            if res["is_straddle"]:
                short_slug = res["slug"].replace("bitcoin-up-or-down-", "")
                print(f"{short_slug:<30} | Y:{res['yes_min']:.2f} | N:{res['no_min']:.2f} | {status}")

print(f"\n{'=' * 80}")
print("🏁 FINAL RESULTS (Jan 15-31, 5am-6pm ET)")
print("=" * 80)
print(f"Verified Markets: {len(verified_markets)}")
print(f"Straddles Found: {len(straddles)}")
if len(verified_markets) > 0:
    print(f"Success Rate: {len(straddles)/len(verified_markets)*100:.1f}%")
else:
    print("Success Rate: N/A")
