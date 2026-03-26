"""
Analyze Historical Trades from Polymarket Data API

Check if YES and NO both traded below 40¢ in closed BTC hourly markets.
"""

import requests
import json
from collections import defaultdict

print("=" * 80)
print("🔍 Fetching Historical Trade Data for BTC Hourly Markets")
print("=" * 80)

# List of closed BTC hourly markets to analyze
slugs = [
    "bitcoin-up-or-down-january-31-10am-et",
    "bitcoin-up-or-down-january-31-9am-et",
    "bitcoin-up-or-down-january-31-11am-et",
    "bitcoin-up-or-down-january-31-3pm-et",
    "bitcoin-up-or-down-january-31-4pm-et",
    "bitcoin-up-or-down-january-30-10am-et",
    "bitcoin-up-or-down-january-30-9am-et",
    "bitcoin-up-or-down-january-30-11am-et",
    "bitcoin-up-or-down-january-29-10am-et",
    "bitcoin-up-or-down-january-29-9am-et",
    "bitcoin-up-or-down-january-28-10am-et",
    "bitcoin-up-or-down-january-28-9am-et",
]

results = []

for slug in slugs:
    print(f"\n📊 {slug}")
    
    try:
        # Get market info
        resp = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}")
        data = resp.json()
        
        if not data:
            print("  Not found")
            continue
        
        market = data[0]["markets"][0]
        condition_id = market.get("conditionId")
        
        if not condition_id:
            print("  No condition ID")
            continue
        
        # Fetch ALL trades for this market
        trades_resp = requests.get(
            "https://data-api.polymarket.com/trades",
            params={"market": condition_id, "limit": 1000}
        )
        
        if trades_resp.status_code != 200:
            print(f"  Error: {trades_resp.status_code}")
            continue
        
        trades = trades_resp.json()
        print(f"  Trades: {len(trades)}")
        
        if not trades:
            print("  No trades")
            continue
        
        # Separate YES and NO trades (outcomeIndex 0 = Up/Yes, 1 = Down/No)
        yes_prices = []
        no_prices = []
        
        for t in trades:
            price = float(t.get("price", 0))
            outcome_idx = t.get("outcomeIndex")
            outcome = t.get("outcome", "").lower()
            
            # Determine if YES or NO
            if outcome_idx == 0 or "up" in outcome:
                yes_prices.append(price)
            elif outcome_idx == 1 or "down" in outcome:
                no_prices.append(price)
        
        if yes_prices:
            yes_min = min(yes_prices)
            yes_max = max(yes_prices)
            print(f"  YES (Up): {len(yes_prices)} trades | Range: {yes_min:.2f} - {yes_max:.2f}")
        else:
            yes_min = 1.0
            yes_max = 0.0
            print(f"  YES: No trades")
        
        if no_prices:
            no_min = min(no_prices)
            no_max = max(no_prices)
            print(f"  NO (Down): {len(no_prices)} trades | Range: {no_min:.2f} - {no_max:.2f}")
        else:
            no_min = 1.0
            no_max = 0.0
            print(f"  NO: No trades")
        
        # Check straddle possibility
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
        else:
            if yes_below_40:
                print(f"  📉 Only YES went below 40¢")
            elif no_below_40:
                print(f"  📈 Only NO went below 40¢")
            else:
                print(f"  ➡️ Neither dropped below 40¢")
        
    except Exception as e:
        print(f"  Error: {e}")

# ============================================================================
# SUMMARY
# ============================================================================
print(f"\n{'=' * 80}")
print("📊 STRADDLE ANALYSIS SUMMARY")
print("=" * 80)

if results:
    total = len(results)
    yes_dips = sum(1 for r in results if r["yes_below_40"])
    no_dips = sum(1 for r in results if r["no_below_40"])
    straddles = sum(1 for r in results if r["straddle"])
    
    print(f"\n  Markets analyzed: {total}")
    print(f"\n  YES traded below 40¢: {yes_dips}/{total} ({yes_dips/total*100:.1f}%)")
    print(f"  NO traded below 40¢:  {no_dips}/{total} ({no_dips/total*100:.1f}%)")
    print(f"\n  🎯 STRADDLE POSSIBLE: {straddles}/{total} ({straddles/total*100:.1f}%)")
    
    # Detailed table
    print(f"\n  {'Market':<35} | {'YES Range':^15} | {'NO Range':^15} | Status")
    print(f"  {'-'*35}-+-{'-'*15}-+-{'-'*15}-+-{'-'*15}")
    
    for r in results:
        name = r["slug"].replace("bitcoin-up-or-down-", "")[:30]
        yes_range = f"{r['yes_min']:.2f}-{r['yes_max']:.2f}"
        no_range = f"{r['no_min']:.2f}-{r['no_max']:.2f}"
        
        if r["straddle"]:
            status = "✅ STRADDLE"
        elif r["yes_below_40"]:
            status = "YES<40 only"
        elif r["no_below_40"]:
            status = "NO<40 only"
        else:
            status = "neither<40"
        
        print(f"  {name:<35} | {yes_range:^15} | {no_range:^15} | {status}")
    
    # Calculate potential profit
    if straddles > 0:
        print(f"\n{'=' * 80}")
        print("💰 POTENTIAL PROFIT (if you caught all straddles)")
        print("=" * 80)
        
        # Each straddle: buy YES&NO at ~38¢ each ($5), sell one at ~98¢, other at ~2¢
        profit_per_straddle = 3.16  # $13.16 - $10
        total_profit = straddles * profit_per_straddle
        print(f"  Straddles: {straddles}")
        print(f"  Profit per straddle: ${profit_per_straddle:.2f}")
        print(f"  Total potential profit: ${total_profit:.2f}")
else:
    print("  No data")

print(f"\n{'=' * 80}")
