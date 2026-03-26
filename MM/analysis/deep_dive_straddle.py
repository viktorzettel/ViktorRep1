"""
DEEP DIVE on CONFIRMED Straddle Market

Validate exact timing of lows for: bitcoin-up-or-down-january-20-10am-et
Prove that YES and NO both went < 40¢ sequentially.
"""

import requests
import json
from datetime import datetime

slug = "bitcoin-up-or-down-january-20-10am-et"

print("=" * 80)
print(f"🔬 DEEP DIVE: {slug}")
print("=" * 80)

# 1. Get Condition ID
resp = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}")
data = resp.json()
m = data[0]["markets"][0]
condition_id = m.get("conditionId")
print(f"Condition ID: {condition_id}")

# 2. Get Trades
print("\nFetching trades...")
t_resp = requests.get("https://data-api.polymarket.com/trades", params={"market": condition_id, "limit": 5000})
trades = t_resp.json()

def parse_ts(ts):
    if not ts: return None
    if isinstance(ts, int):
        return datetime.fromtimestamp(ts/1000)
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

# 3. Analyze Lows
yes_trades = []
no_trades = []

for t in trades:
    price = float(t.get("price"))
    idx = t.get("outcomeIndex")
    ts = parse_ts(t.get("timestamp"))
    
    if idx == 0: yes_trades.append({"price": price, "ts": ts})
    elif idx == 1: no_trades.append({"price": price, "ts": ts})

yes_trades.sort(key=lambda x: x["ts"])
no_trades.sort(key=lambda x: x["ts"])

print(f"\nTrades: {len(yes_trades)} YES | {len(no_trades)} NO")

# Find lowest points
min_yes = min(yes_trades, key=lambda x: x["price"])
min_no = min(no_trades, key=lambda x: x["price"])

print(f"\n📉 PRICE EXTREMES")
print(f"  YES Low: {min_yes['price']:.2f} at {min_yes['ts'].strftime('%H:%M:%S')}")
print(f"  NO Low:  {min_no['price']:.2f} at {min_no['ts'].strftime('%H:%M:%S')}")

# Find periods under 40 cents
print(f"\n⏱️ TIMING ANALYSIS")

print("\nYES < 40¢ periods:")
yes_under_40 = [t for t in yes_trades if t["price"] < 0.40]
if yes_under_40:
    start_y = yes_under_40[0]["ts"]
    end_y = yes_under_40[-1]["ts"]
    print(f"  From {start_y.strftime('%H:%M:%S')} to {end_y.strftime('%H:%M:%S')}")
    print(f"  Price range: {min([t['price'] for t in yes_under_40]):.2f} - {max([t['price'] for t in yes_under_40]):.2f}")

print("\nNO < 40¢ periods:")
no_under_40 = [t for t in no_trades if t["price"] < 0.40]
if no_under_40:
    start_n = no_under_40[0]["ts"]
    end_n = no_under_40[-1]["ts"]
    print(f"  From {start_n.strftime('%H:%M:%S')} to {end_n.strftime('%H:%M:%S')}")
    print(f"  Price range: {min([t['price'] for t in no_under_40]):.2f} - {max([t['price'] for t in no_under_40]):.2f}")

print("\n-------------------------------------------------------------")
if yes_under_40 and no_under_40:
    diff = (start_n - start_y).total_seconds() / 60
    print(f"✅ CONFIRMED STRADDLE!")
    print(f"   Time difference between dips: {diff:.1f} minutes")
    
    if start_n > end_y or start_y > end_n:
        print("   SEQUENTIAL: They occurred at different times (as expected).")
    else:
        print("   OVERLAP: They overlapped? (This would be weird arbitrage)")
else:
    print("❌ Not a straddle.")
