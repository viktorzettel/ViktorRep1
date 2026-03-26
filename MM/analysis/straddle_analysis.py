"""
Straddle Strategy Validation

Question: How often does price swing enough in one hour to allow buying
BOTH YES and NO below 40¢?

Strategy:
- Buy YES when < 40¢ (price DOWN from strike)
- Buy NO when < 40¢ (price UP from strike, so YES > 60¢)
- Hold until resolution
- Sell winner at ~98¢, loser at ~2¢
"""

import pandas as pd
import numpy as np

print("Loading minute data...")
df = pd.read_csv("btc_minute_data_60d.csv")
df["hour_key"] = df["date"] + "_" + df["hour_et"].astype(str).str.zfill(2)

print(f"Analyzing {df['hour_key'].nunique()} hours...\n")

# Calculate required swing
# For YES to hit 40¢, roughly need BTC ~0.5% below strike
# For NO to hit 40¢ (YES > 60¢), roughly need BTC ~0.5% above strike
# So total swing needed: ~1% (from -0.5% to +0.5%)

ENTRY_THRESHOLD_PCT = 0.4  # 40¢ in % from 50¢ (actually ~0.4-0.5% BTC move)

results = []

for hour_key, group in df.groupby("hour_key"):
    if len(group) < 55:
        continue
    
    group = group.sort_values("minute")
    strike = group.iloc[0]["open"]
    final_close = group.iloc[-1]["close"]
    outcome = "UP" if final_close > strike else "DOWN"
    
    # Track min and max distance from strike
    min_pct = 0
    max_pct = 0
    yes_cheap_minute = None
    no_cheap_minute = None
    
    for _, row in group.iterrows():
        dist_pct = ((row["close"] - strike) / strike) * 100
        
        if dist_pct < min_pct:
            min_pct = dist_pct
            yes_cheap_minute = row["minute"]
        if dist_pct > max_pct:
            max_pct = dist_pct
            no_cheap_minute = row["minute"]
    
    total_swing = max_pct - min_pct
    
    # Can we buy YES below 40¢? (need ~ -0.4% move from mid, roughly -0.3% from strike)
    yes_below_40 = min_pct < -0.3
    
    # Can we buy NO below 40¢? (need YES > 60¢, roughly +0.3% from strike)
    no_below_40 = max_pct > 0.3
    
    # Can we do BOTH?
    straddle_possible = yes_below_40 and no_below_40
    
    # Order matters - need to buy YES first then NO, or vice versa
    if straddle_possible:
        # Check which came first
        if yes_cheap_minute is not None and no_cheap_minute is not None:
            buy_order = "YES→NO" if yes_cheap_minute < no_cheap_minute else "NO→YES"
        else:
            buy_order = "UNKNOWN"
    else:
        buy_order = "N/A"
    
    results.append({
        "hour_key": hour_key,
        "hour_et": group.iloc[0]["hour_et"],
        "min_pct": min_pct,
        "max_pct": max_pct,
        "total_swing": total_swing,
        "yes_below_40": yes_below_40,
        "no_below_40": no_below_40,
        "straddle_possible": straddle_possible,
        "buy_order": buy_order,
        "outcome": outcome,
    })

results_df = pd.DataFrame(results)

print("=" * 80)
print("📊 STRADDLE STRATEGY VALIDATION")
print("=" * 80)

total = len(results_df)
yes_opportunities = results_df["yes_below_40"].sum()
no_opportunities = results_df["no_below_40"].sum()
straddle_opportunities = results_df["straddle_possible"].sum()

print(f"\n  Total hours analyzed: {total}")
print(f"\n  Opportunities to buy YES < 40¢: {yes_opportunities} ({yes_opportunities/total*100:.1f}%)")
print(f"  Opportunities to buy NO < 40¢:  {no_opportunities} ({no_opportunities/total*100:.1f}%)")
print(f"\n  🎯 STRADDLE POSSIBLE (both): {straddle_opportunities} ({straddle_opportunities/total*100:.1f}%)")

# By hour
print(f"\n{'=' * 80}")
print("📊 STRADDLE OPPORTUNITIES BY HOUR (ET)")
print(f"{'=' * 80}")

hourly = results_df.groupby("hour_et").agg({
    "straddle_possible": "sum",
    "total_swing": "mean",
    "hour_key": "count",
}).rename(columns={"hour_key": "total"})

hourly["straddle_rate"] = hourly["straddle_possible"] / hourly["total"] * 100

print(f"\n{'Hour':<8} | {'Straddle Rate':^15} | {'Avg Swing':^12} | Count")
print("-" * 55)

for hour in range(24):
    if hour in hourly.index:
        row = hourly.loc[hour]
        bar = "█" * int(row["straddle_rate"] / 5)
        print(f"{hour:>2}:00    | {row['straddle_rate']:>10.1f}%    | {row['total_swing']:>10.2f}% | {int(row['total']):<5} {bar}")

# Profitability analysis
print(f"\n{'=' * 80}")
print("💰 PROFITABILITY ANALYSIS")
print(f"{'=' * 80}")

straddles = results_df[results_df["straddle_possible"]]

print(f"\n  Assuming:")
print(f"    - Buy YES at 38¢ ($5) → {5/0.38:.1f} shares")
print(f"    - Buy NO at 38¢ ($5) → {5/0.38:.1f} shares")
print(f"    - Total investment: $10")
print(f"    - Sell at resolution: Winner at 98¢, Loser at 2¢")

shares = 5 / 0.38
winner_value = shares * 0.98
loser_value = shares * 0.02
total_return = winner_value + loser_value
profit = total_return - 10

print(f"\n  Per straddle:")
print(f"    Winner: {shares:.1f} × $0.98 = ${winner_value:.2f}")
print(f"    Loser:  {shares:.1f} × $0.02 = ${loser_value:.2f}")
print(f"    Total return: ${total_return:.2f}")
print(f"    Profit: ${profit:.2f} ({profit/10*100:.1f}%)")

print(f"\n  Projected 60-day results:")
print(f"    Straddle opportunities: {straddle_opportunities}")
print(f"    Profit per straddle: ${profit:.2f}")
print(f"    Total profit: ${straddle_opportunities * profit:.2f}")

# Edge cases
print(f"\n{'=' * 80}")
print("⚠️ EDGE CASES & RISKS")
print(f"{'=' * 80}")

print("""
  1. TIMING RISK:
     - You might buy YES, but price never swings back to buy NO
     - Then you're stuck with just one side → normal 50/50 bet

  2. EXECUTION RISK:
     - Need to actually fill BOTH orders at good prices
     - Slippage could eat into profits

  3. RESOLUTION RISK:
     - What if market closes at 95/5 not 98/2?
     - Less profit but still positive
     
  4. LIQUIDITY RISK:
     - At 98/2, the loser side has almost no liquidity
     - Might not be able to sell loser at 2¢ (maybe 1¢)
""")

# What if we don't get perfect 98/2?
print(f"\n  Sensitivity Analysis:")
for winner_price, loser_price in [(0.98, 0.02), (0.95, 0.05), (0.90, 0.10), (0.85, 0.15)]:
    w = shares * winner_price
    l = shares * loser_price
    p = w + l - 10
    print(f"    At {int(winner_price*100)}/{int(loser_price*100)}: ${w:.2f} + ${l:.2f} = ${w+l:.2f} → Profit: ${p:.2f} ({p/10*100:.1f}%)")

print(f"\n{'=' * 80}")
print("💡 CONCLUSION")
print(f"{'=' * 80}")

if straddle_opportunities / total > 0.15:
    verdict = "✅ VIABLE"
else:
    verdict = "⚠️ RARE OPPORTUNITY"

print(f"""
  Straddle Success Rate: {straddle_opportunities/total*100:.1f}%
  Verdict: {verdict}
  
  Best Hours: Check the table above - target hours with >25% straddle rate
  
  Strategy:
  1. Wait for price to dip (YES < 40¢) → Buy YES $5
  2. Wait for price to swing back (NO < 40¢) → Buy NO $5
  3. If #2 never happens → treat as normal directional trade
  4. At minute 55+, sell both sides at market
""")
