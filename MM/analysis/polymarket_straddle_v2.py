"""
Fetch Polymarket Trade History for BTC hourly markets

Search more broadly and check trade data.
"""

import requests
import json

print("=" * 80)
print("🔍 Searching for ALL BTC-related markets")
print("=" * 80)

# Broader search
url = "https://gamma-api.polymarket.com/markets"
params = {
    "limit": 200,
}

resp = requests.get(url, params=params)
markets = resp.json()

print(f"Fetched {len(markets)} markets")

# Find BTC hourly markets
btc_hourly = []

for m in markets:
    question = m.get("question", "").lower()
    # Look for hourly BTC markets
    if "bitcoin" in question and ("hour" in question or "am" in question or "pm" in question):
        tokens_raw = m.get("clobTokenIds", [])
        if isinstance(tokens_raw, str):
            try:
                tokens = json.loads(tokens_raw)
            except:
                tokens = []
        else:
            tokens = tokens_raw
        
        if len(tokens) >= 1:
            btc_hourly.append({
                "question": m.get("question"),
                "slug": m.get("slug"),
                "tokens": tokens,
                "active": m.get("active"),
                "closed": m.get("closed"),
            })

print(f"\nFound {len(btc_hourly)} BTC hourly markets:")
for m in btc_hourly[:10]:
    status = "ACTIVE" if m.get("active") else "CLOSED"
    print(f"  [{status}] {m['question'][:70]}")

# Now fetch trades for these markets
print(f"\n{'=' * 80}")
print("📊 ANALYZING TRADE DATA")
print(f"{'=' * 80}")

results = []

for i, market in enumerate(btc_hourly[:15]):
    print(f"\n[{i+1}] {market['question'][:60]}...")
    
    if len(market["tokens"]) < 2:
        print("  Only 1 token found, skipping")
        continue
    
    yes_token = market["tokens"][0]
    no_token = market["tokens"][1] if len(market["tokens"]) > 1 else None
    
    # Fetch trades for YES token
    yes_prices = []
    no_prices = []
    
    try:
        trades_url = "https://clob.polymarket.com/trades"
        resp = requests.get(trades_url, params={"asset_id": yes_token, "limit": 1000})
        if resp.status_code == 200:
            trades = resp.json()
            yes_prices = [float(t["price"]) for t in trades if "price" in t]
            print(f"  YES: {len(yes_prices)} trades", end="")
            if yes_prices:
                print(f" | Range: {min(yes_prices):.2f} - {max(yes_prices):.2f}")
            else:
                print()
    except Exception as e:
        print(f"  YES error: {e}")
    
    if no_token:
        try:
            resp = requests.get(trades_url, params={"asset_id": no_token, "limit": 1000})
            if resp.status_code == 200:
                trades = resp.json()
                no_prices = [float(t["price"]) for t in trades if "price" in t]
                print(f"  NO:  {len(no_prices)} trades", end="")
                if no_prices:
                    print(f" | Range: {min(no_prices):.2f} - {max(no_prices):.2f}")
                else:
                    print()
        except Exception as e:
            print(f"  NO error: {e}")
    
    if yes_prices or no_prices:
        yes_min = min(yes_prices) if yes_prices else 1.0
        no_min = min(no_prices) if no_prices else 1.0
        
        yes_below_40 = yes_min < 0.40
        no_below_40 = no_min < 0.40
        straddle = yes_below_40 and no_below_40
        
        results.append({
            "question": market["question"],
            "yes_min": yes_min,
            "no_min": no_min,
            "yes_below_40": yes_below_40,
            "no_below_40": no_below_40,
            "straddle": straddle,
        })
        
        if straddle:
            print(f"  ✅ STRADDLE POSSIBLE!")

# Summary
print(f"\n{'=' * 80}")
print("📊 SUMMARY")
print(f"{'=' * 80}")

if results:
    total = len(results)
    straddles = sum(1 for r in results if r["straddle"])
    yes_below = sum(1 for r in results if r["yes_below_40"])
    no_below = sum(1 for r in results if r["no_below_40"])
    
    print(f"\n  Markets with trade data: {total}")
    print(f"  YES hit < 40¢: {yes_below}/{total} ({yes_below/total*100:.1f}%)")
    print(f"  NO hit < 40¢:  {no_below}/{total} ({no_below/total*100:.1f}%)")
    print(f"  STRADDLE possible: {straddles}/{total} ({straddles/total*100:.1f}%)")
    
    if straddles > 0:
        print(f"\n  Straddle markets:")
        for r in results:
            if r["straddle"]:
                print(f"    {r['question'][:60]}")
                print(f"      YES min: {r['yes_min']:.2f} | NO min: {r['no_min']:.2f}")
else:
    print("  No usable trade data found")
