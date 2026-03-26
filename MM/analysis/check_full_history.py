"""
Check if API gives FULL hourly trade history for swing trading validation.

Question: Can we see YES at 39¢ in minute 5 and NO at 40¢ in minute 10?
This would validate swing trading within a single hour.
"""

import requests
import json
from datetime import datetime, timedelta
from collections import defaultdict

print("=" * 80)
print("🔍 Checking: Does API give FULL hourly trade history?")
print("=" * 80)

# Market: January 31, 10AM ET = 15:00 UTC (market runs 10:00-11:00 AM ET = 15:00-16:00 UTC)
slug = "bitcoin-up-or-down-january-31-10am-et"

resp = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}")
data = resp.json()
market = data[0]["markets"][0]
condition_id = market.get("conditionId")
end_date = market.get("endDate")  # When market resolves

print(f"\nMarket: {slug}")
print(f"Condition ID: {condition_id}")
print(f"End Date: {end_date}")

# Fetch trades - try to get ALL of them
print(f"\n{'=' * 80}")
print("📊 Fetching ALL trades...")
print("=" * 80)

all_trades = []
offset = 0
limit = 1000

while True:
    trades_resp = requests.get(
        "https://data-api.polymarket.com/trades",
        params={"market": condition_id, "limit": limit, "offset": offset}
    )
    
    if trades_resp.status_code != 200:
        print(f"Error at offset {offset}: {trades_resp.status_code}")
        break
    
    trades = trades_resp.json()
    if not trades:
        break
    
    all_trades.extend(trades)
    print(f"  Fetched {len(trades)} trades (total: {len(all_trades)})")
    
    if len(trades) < limit:
        break
    
    offset += limit

print(f"\nTotal trades: {len(all_trades)}")

if not all_trades:
    print("No trades found!")
    exit()

# Analyze time range
timestamps = []
for t in all_trades:
    ts = t.get("timestamp")
    if isinstance(ts, int):
        dt = datetime.fromtimestamp(ts / 1000) if ts > 1e10 else datetime.fromtimestamp(ts)
        timestamps.append(dt)

if timestamps:
    min_ts = min(timestamps)
    max_ts = max(timestamps)
    duration = (max_ts - min_ts).total_seconds()
    
    print(f"\n{'=' * 80}")
    print("⏰ TIME RANGE OF TRADES")
    print("=" * 80)
    print(f"  First trade: {min_ts}")
    print(f"  Last trade:  {max_ts}")
    print(f"  Duration: {duration:.0f} seconds ({duration/60:.1f} minutes)")
    
    # We expect 60 minutes of data for an hourly market
    if duration > 3500:  # > ~58 minutes
        print(f"  ✅ FULL HOUR of data available!")
    else:
        print(f"  ⚠️ Only {duration/60:.1f} minutes of data (expected 60)")

# Group trades by minute
print(f"\n{'=' * 80}")
print("📊 PRICE BY MINUTE (YES and NO)")
print("=" * 80)

minute_data = defaultdict(lambda: {"yes_prices": [], "no_prices": []})

for t in all_trades:
    ts = t.get("timestamp")
    if isinstance(ts, int):
        dt = datetime.fromtimestamp(ts / 1000) if ts > 1e10 else datetime.fromtimestamp(ts)
    else:
        continue
    
    minute = dt.strftime("%H:%M")
    price = float(t.get("price", 0))
    outcome_idx = t.get("outcomeIndex")
    
    if outcome_idx == 0:  # YES/Up
        minute_data[minute]["yes_prices"].append(price)
    elif outcome_idx == 1:  # NO/Down
        minute_data[minute]["no_prices"].append(price)

# Sort by minute and show
sorted_minutes = sorted(minute_data.keys())

print(f"\n  {'Minute':<8} | {'YES Range':^15} | {'NO Range':^15} | Swing Opportunity")
print(f"  {'-'*8}-+-{'-'*15}-+-{'-'*15}-+-{'-'*20}")

yes_lows = []
no_lows = []

for minute in sorted_minutes:
    data = minute_data[minute]
    
    yes_min = min(data["yes_prices"]) if data["yes_prices"] else None
    yes_max = max(data["yes_prices"]) if data["yes_prices"] else None
    no_min = min(data["no_prices"]) if data["no_prices"] else None
    no_max = max(data["no_prices"]) if data["no_prices"] else None
    
    yes_str = f"{yes_min:.2f}-{yes_max:.2f}" if yes_min else "---"
    no_str = f"{no_min:.2f}-{no_max:.2f}" if no_min else "---"
    
    # Check for swing opportunities
    opportunity = ""
    if yes_min and yes_min <= 0.40:
        opportunity = f"YES @ {yes_min:.2f} ⬇️"
        yes_lows.append((minute, yes_min))
    if no_min and no_min <= 0.40:
        opportunity = f"NO @ {no_min:.2f} ⬇️"
        no_lows.append((minute, no_min))
    
    print(f"  {minute:<8} | {yes_str:^15} | {no_str:^15} | {opportunity}")

# Swing analysis
print(f"\n{'=' * 80}")
print("🎯 SWING TRADING OPPORTUNITY ANALYSIS")
print("=" * 80)

print(f"\nYES lows (≤40¢): {len(yes_lows)}")
if yes_lows:
    for minute, price in yes_lows[:5]:
        # Find YES max after this point
        later_minutes = [m for m in sorted_minutes if m > minute]
        if later_minutes:
            later_yes_prices = []
            for m in later_minutes:
                later_yes_prices.extend(minute_data[m]["yes_prices"])
            if later_yes_prices:
                max_later = max(later_yes_prices)
                profit = (max_later - price) / price * 100
                print(f"  {minute}: Buy YES @ {price:.2f} → Later max {max_later:.2f} = {profit:.1f}% profit")

print(f"\nNO lows (≤40¢): {len(no_lows)}")
if no_lows:
    for minute, price in no_lows[:5]:
        later_minutes = [m for m in sorted_minutes if m > minute]
        if later_minutes:
            later_no_prices = []
            for m in later_minutes:
                later_no_prices.extend(minute_data[m]["no_prices"])
            if later_no_prices:
                max_later = max(later_no_prices)
                profit = (max_later - price) / price * 100
                print(f"  {minute}: Buy NO @ {price:.2f} → Later max {max_later:.2f} = {profit:.1f}% profit")

print(f"\n{'=' * 80}")
print("💡 CONCLUSION")
print("=" * 80)
if duration > 3500:
    print("  ✅ API provides FULL hourly trade history!")
    print("  ✅ We can analyze minute-by-minute price movements")
    print("  ✅ Swing trading validation IS possible with this data")
else:
    print("  ⚠️ API may have limited historical data")
    print("  Consider: recording live data going forward")
