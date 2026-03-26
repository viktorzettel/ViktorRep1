import requests
import concurrent.futures
import time

print("=" * 80)
print("🔨 FINAL STRADDLE CHECK: Hourly BTC Up/Down Markets (January 15-31)")
print("=" * 80)

# Generate potential slugs for January 15–31 (most recent closed markets as of Feb 1, 2026)
slugs_to_check = []
hours = [8, 9, 10, 11, 12, 1, 2, 3, 4, 5]  # Common trading hours
am_pm = {8: "am", 9: "am", 10: "am", 11: "am", 12: "pm", 1: "pm", 2: "pm", 3: "pm", 4: "pm", 5: "pm"}

for day in range(15, 32):
    for h in hours:
        suffix = am_pm[h]
        slug = f"bitcoin-up-or-down-january-{day}-{h}{suffix}-et"
        slugs_to_check.append(slug)

print(f"Generated {len(slugs_to_check)} potential hourly slugs (~170).")

# Results containers
verified_markets = []
straddles = []
yes_only = []
no_only = []

def check_slug(slug):
    try:
        # Step 1: Fetch the event by slug (correct path-based endpoint)
        # Fix: valid endpoint is usually /events?slug=...
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return None
        events = resp.json()
        if not events: # Gamma returns list
            return None
            
        event = events[0]
        if "markets" not in event or not event["markets"]:
            return None
        
        market = event["markets"][0]  # These are binary markets → one market with two outcomes
        condition_id = market.get("conditionId")
        title = event.get("title", "Unknown")
        if not condition_id:
            return None
        
        # Step 2: Fetch ALL trades with pagination
        trades_url = "https://data-api.polymarket.com/trades"
        all_trades = []
        offset = 0
        limit = 1000  # Reduced to avoiding 414 URI Too Long or timeouts on large fetches if default is small
        # Actually API max is often lower or safer at 500-1000.
        
        # Just getting the first page (up to 1000) is usually enough for proof of concept
        # fetching ALL pages might take forever for 100 markets.
        # Let's stick to 1 page for speed, or a simple loop limited to 5000 trades.
        
        while len(all_trades) < 5000: # Cap at 5000 to be safe on time
            params = {"market": condition_id, "limit": limit, "offset": offset}
            t_resp = requests.get(trades_url, params=params, timeout=10)
            if t_resp.status_code != 200:
                break
            page_trades = t_resp.json()
            if not page_trades:
                break
            all_trades.extend(page_trades)
            offset += len(page_trades)
            if len(page_trades) < limit:  # Last page
                break
        
        if not all_trades:
            return None
        
        # Step 3: Extract prices by outcome (0 = YES/Up, 1 = NO/Down)
        yes_prices = [float(t["price"]) for t in all_trades if t.get("outcomeIndex") == 0]
        no_prices = [float(t["price"]) for t in all_trades if t.get("outcomeIndex") == 1]
        
        yes_min = min(yes_prices) if yes_prices else 1.0
        no_min = min(no_prices) if no_prices else 1.0
        
        is_straddle = yes_min < 0.40 and no_min < 0.40
        
        result = {
            "slug": slug,
            "title": title,
            "yes_min": yes_min,
            "no_min": no_min,
            "is_straddle": is_straddle,
            "total_trades": len(all_trades)
        }
        return result
    
    except Exception as e:
        # Silent fail on any error (network, JSON, etc.)
        return None

print("\n🚀 Checking slugs in parallel (this may take a few minutes)...\n")

with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:  # Reduced workers
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
                status = "YES <40¢ only"
                yes_only.append(res)
            elif res["no_min"] < 0.40:
                status = "NO <40¢ only"
                no_only.append(res)
            
            short_slug = res["slug"].replace("bitcoin-up-or-down-", "")
            print(f"{short_slug:<30} | Trades: {res['total_trades']:<6} | Y:{res['yes_min']:.3f} | N:{res['no_min']:.3f} | {status}")

print(f"\n{'=' * 80}")
print("🏁 FINAL RESULTS (January 15–31 Hourly BTC Markets)")
print("=" * 80)
print(f"Verified Existing Markets: {len(verified_markets)}")
print(f"Total Straddle Opportunities: {len(straddles)}")
print(f"YES dipped <40¢ only: {len(yes_only)}")
print(f"NO dipped <40¢ only: {len(no_only)}")

if straddles:
    print("\n✅ STRADDLE MARKETS FOUND (both sides traded <40¢ at some point):")
    for m in straddles:
        print(f" - {m['slug']} | Y min: {m['yes_min']:.3f} | N min: {m['no_min']:.3f} | Trades: {m['total_trades']}")
else:
    print("\n❌ No straddle opportunities found in this period.")
