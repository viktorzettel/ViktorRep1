"""
Swing Trading Validation

Question: If you buy when price dips below 38¢, does price EVER bounce back
high enough to sell at profit during the hour?

Strategy:
- Buy when YES < 38¢ (BTC dropped ~0.5% from strike)
- Need price to bounce at least +5-10¢ for profit
- Don't care about final outcome, just need ONE exit opportunity
"""

import pandas as pd
import numpy as np

print("Loading minute data...")
df = pd.read_csv("btc_minute_data_60d.csv")
df["hour_key"] = df["date"] + "_" + df["hour_et"].astype(str).str.zfill(2)

# Focus on 9-11 AM ET (high volatility hours)
volatile_hours = df[df["hour_et"].isin([9, 10, 11])].copy()

print(f"Analyzing 9-11 AM ET hours...")

# ============================================================================
# ANALYSIS: If price dips, does it EVER bounce back?
# ============================================================================

results = []

for hour_key, group in volatile_hours.groupby("hour_key"):
    if len(group) < 55:
        continue
    
    group = group.sort_values("minute")
    strike = group.iloc[0]["open"]
    
    # Track price trajectory
    prices = []
    for _, row in group.iterrows():
        dist_pct = ((row["close"] - strike) / strike) * 100
        prices.append({
            "minute": row["minute"],
            "dist_pct": dist_pct,
        })
    
    prices_df = pd.DataFrame(prices)
    
    # Scenario: Price dipped to -0.5% (YES at ~35-38¢)
    # Question: Did it EVER come back above -0.2% (YES at ~45¢)?
    
    min_dist = prices_df["dist_pct"].min()
    max_after_min = None
    
    if min_dist < -0.3:  # Price dipped significantly
        min_idx = prices_df["dist_pct"].idxmin()
        after_min = prices_df.loc[min_idx:]
        if len(after_min) > 1:
            max_after_min = after_min["dist_pct"].max()
    
    # Scenario: What's the max bounce from the lowest point?
    if min_dist < -0.3 and max_after_min is not None:
        bounce = max_after_min - min_dist
        results.append({
            "hour_key": hour_key,
            "hour_et": group.iloc[0]["hour_et"],
            "min_dist": min_dist,
            "max_after_min": max_after_min,
            "bounce": bounce,
            "exit_possible": max_after_min > min_dist + 0.15,  # At least 0.15% bounce (~5¢)
            "good_exit": max_after_min > min_dist + 0.3,  # At least 0.3% bounce (~10¢)
        })

results_df = pd.DataFrame(results)

print(f"\n{'=' * 80}")
print("📊 SWING TRADING VALIDATION")
print(f"{'=' * 80}")

print(f"\nHours where price dipped >0.3%: {len(results_df)}")

if len(results_df) > 0:
    exit_possible = results_df["exit_possible"].sum()
    good_exit = results_df["good_exit"].sum()
    
    print(f"\n  ✅ Exit possible (+0.15% bounce): {exit_possible}/{len(results_df)} ({exit_possible/len(results_df)*100:.1f}%)")
    print(f"  ✅ Good exit (+0.30% bounce): {good_exit}/{len(results_df)} ({good_exit/len(results_df)*100:.1f}%)")
    
    print(f"\n  Average min dip: {results_df['min_dist'].mean():.2f}%")
    print(f"  Average max recovery: {results_df['max_after_min'].mean():.2f}%")
    print(f"  Average bounce size: {results_df['bounce'].mean():.2f}%")

# By hour
print(f"\n{'=' * 80}")
print("📊 BY HOUR")
print(f"{'=' * 80}")

for hour in [9, 10, 11]:
    subset = results_df[results_df["hour_et"] == hour]
    if len(subset) > 0:
        exit_rate = subset["exit_possible"].sum() / len(subset) * 100
        good_rate = subset["good_exit"].sum() / len(subset) * 100
        avg_bounce = subset["bounce"].mean()
        print(f"\n  {hour}:00 AM ET ({len(subset)} dip hours):")
        print(f"    Exit possible: {exit_rate:.1f}%")
        print(f"    Good exit: {good_rate:.1f}%")
        print(f"    Avg bounce: {avg_bounce:.2f}%")

# ============================================================================
# ANALYSIS 2: Risk - How often does it NEVER bounce back?
# ============================================================================
print(f"\n{'=' * 80}")
print("⚠️ RISK ANALYSIS: Hours with NO exit opportunity")
print(f"{'=' * 80}")

no_exit = results_df[~results_df["exit_possible"]]
print(f"\n  Hours with dip but NO bounce: {len(no_exit)}/{len(results_df)} ({len(no_exit)/len(results_df)*100:.1f}%)")

if len(no_exit) > 0:
    print(f"\n  These hours:")
    for _, row in no_exit.head(5).iterrows():
        print(f"    {row['hour_key']}: Dipped to {row['min_dist']:.2f}%, max recovery {row['max_after_min']:.2f}%")

# ============================================================================
# RECOMMENDATION
# ============================================================================
print(f"\n{'=' * 80}")
print("💡 RECOMMENDATION")
print(f"{'=' * 80}")

total_dip_hours = len(results_df)
always_exit = exit_possible / total_dip_hours * 100 if total_dip_hours > 0 else 0
always_good = good_exit / total_dip_hours * 100 if total_dip_hours > 0 else 0

if always_exit > 90:
    verdict = "✅ EXCELLENT"
elif always_exit > 80:
    verdict = "✅ GOOD"
elif always_exit > 70:
    verdict = "🔵 ACCEPTABLE"
else:
    verdict = "⚠️ RISKY"

print(f"""
  Strategy: Buy on dips, sell on bounce during 9-11 AM ET
  
  Exit success rate: {always_exit:.1f}% {verdict}
  Good exit rate: {always_good:.1f}%
  
  Conclusion: {'Run the bot during 9-11 AM!' if always_exit > 75 else 'Consider tighter stops.'}
""")
