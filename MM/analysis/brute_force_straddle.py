"""
BRUTE FORCE STRADDLE CHECK

Generates 100+ hypothetical hourly market slugs and checks them directly.
Pattern: bitcoin-up-or-down-<month>-<day>-<hour>-et
Examples: bitcoin-up-or-down-january-31-10am-et
"""

import requests
import concurrent.futures
from datetime import datetime, timedelta

print("=" * 80)
print("🔨 BRUTE FORCE CHECK: Generating & Checking Hourly Slugs")
print("=" * 80)

# Generate Slugs for Jan 20 - Jan 31
slugs_to_check = []
months = {1: "january"}
hours = [9, 10, 11, 12, 1, 2, 3, 4, 5, 8] # Common trading hours
am_pm = {9: "am", 10: "am", 11: "am", 12: "pm", 1: "pm", 2: "pm", 3: "pm", 4: "pm", 5: "pm", 8: "am"}

# Generate for last 15 days of Jan
for day in range(15, 32):
    for h in hours:
        suffix = am_pm[h]
        slug = f"bitcoin-up-or-down-january-{day}-{h}{suffix}-et"
        slugs_to_check.append(slug)

print(f"Generated {len(slugs_to_check)} potential hourly slugs.")

# Results
verified_markets = []
straddles = []
yes_only = []
no_only = []

def check_slug(slug):
    try:
        # Check if market exists
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        resp = requests.get(url, timeout=5)
        data = resp.json()
        
        if not data:
            return None
            
        m = data[0]["markets"][0]
        cid = m.get("conditionId")
        title = data[0].get("title")
        
        # Analyze trades
        t_url = "https://data-api.polymarket.com/trades"
        t_resp = requests.get(t_url, params={"market": cid, "limit": 1000}, timeout=5)
        trades = t_resp.json()
        
        if not trades:
            return None
            
        yes_prices = [float(t["price"]) for t in trades if t.get("outcomeIndex") == 0]
        no_prices = [float(t["price"]) for t in trades if t.get("outcomeIndex") == 1]
        
        if not yes_prices: yes_prices = [1.0]
        if not no_prices: no_prices = [1.0]
        
        yes_min = min(yes_prices)
        no_min = min(no_prices)
        
        is_straddle = yes_min < 0.40 and no_min < 0.40
        
        return {
            "slug": slug,
            "title": title,
            "yes_min": yes_min,
            "no_min": no_min,
            "is_straddle": is_straddle
        }
    except:
        return None

print("\n🚀 Checking slugs in parallel...")

with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
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
                status = "YES only"
                yes_only.append(res)
            elif res["no_min"] < 0.40:
                status = "NO only"
                no_only.append(res)
            
            print(f"{res['slug'].replace('bitcoin-up-or-down-', ''):<30} | Y:{res['yes_min']:.2f} | N:{res['no_min']:.2f} | {status}")

print(f"\n{'=' * 80}")
print("🏁 FINAL BRUTE FORCE RESULTS")
print("=" * 80)
print(f"Verified Hourly Markets Found: {len(verified_markets)}")
print(f"Straddle Opportunities: {len(straddles)}")
print(f"YES < 40¢ Only: {len(yes_only)}")
print(f"NO < 40¢ Only: {len(no_only)}")

if straddles:
    print("\nStraddle Markets:")
    for m in straddles:
        print(f" - {m['slug']}")
