"""
BTC Intra-Hour Volatility Analysis

Analyzes:
1. Which 5-minute intervals have the highest volatility
2. Volatility by hour of day
3. Volatility clustering patterns
4. Where scaling (position sizing) would work best
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')

# ============================================================================
# CONFIG
# ============================================================================

BINANCE_API = "https://api.binance.com/api/v3/klines"
SYMBOL = "BTCUSDT"
LOOKBACK_DAYS = 60
UTC = timezone.utc
ET_OFFSET = -5

# 5-minute intervals within an hour
INTERVALS = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 25), (25, 30), (30, 35), (35, 40), (40, 45), (45, 50), (50, 55), (55, 60)]


def fetch_binance_klines_paginated(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """Fetch klines with pagination."""
    all_klines = []
    current_start = start_ms
    
    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": 1000,
        }
        resp = requests.get(BINANCE_API, params=params)
        resp.raise_for_status()
        klines = resp.json()
        
        if not klines:
            break
            
        all_klines.extend(klines)
        current_start = klines[-1][6] + 1
        
        if len(klines) < 1000:
            break
            
        if len(all_klines) % 10000 == 0:
            print(f"  Fetched {len(all_klines)} candles...")
    
    return all_klines


def process_minute_data(raw_klines: list) -> pd.DataFrame:
    """Process raw 1-minute klines."""
    data = []
    for k in raw_klines:
        open_time_utc = datetime.fromtimestamp(k[0] / 1000, tz=UTC)
        open_time_et = open_time_utc + timedelta(hours=ET_OFFSET)
        
        data.append({
            "timestamp_utc": open_time_utc,
            "timestamp_et": open_time_et,
            "date": open_time_et.strftime("%Y-%m-%d"),
            "hour_et": open_time_et.hour,
            "minute": open_time_et.minute,
            "weekday": open_time_et.strftime("%A"),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })
    
    return pd.DataFrame(data)


def calculate_interval_volatility(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate volatility for each 5-minute interval within each hour.
    
    For each hourly window:
    - Split into 5-minute intervals
    - Calculate: range, std dev, and directional move
    """
    df["hour_key"] = df["date"] + "_" + df["hour_et"].astype(str).str.zfill(2)
    
    all_intervals = []
    
    for hour_key, group in df.groupby("hour_key"):
        if len(group) < 55:
            continue
            
        group = group.sort_values("minute")
        strike = group.iloc[0]["open"]
        final_close = group.iloc[-1]["close"]
        label = 1 if final_close > strike else 0
        
        for start_min, end_min in INTERVALS:
            subset = group[(group["minute"] >= start_min) & (group["minute"] < end_min)]
            if len(subset) < 3:
                continue
            
            # Calculate volatility metrics for this interval
            high = subset["high"].max()
            low = subset["low"].min()
            range_pct = ((high - low) / strike) * 100
            
            # Directional move in this interval
            interval_open = subset.iloc[0]["open"]
            interval_close = subset.iloc[-1]["close"]
            dir_move = ((interval_close - interval_open) / interval_open) * 100
            
            # Volume
            vol = subset["volume"].sum()
            
            # Distance from strike at end of interval
            dist_from_strike = ((interval_close - strike) / strike) * 100
            
            all_intervals.append({
                "hour_key": hour_key,
                "date": group.iloc[0]["date"],
                "hour_et": group.iloc[0]["hour_et"],
                "weekday": group.iloc[0]["weekday"],
                "interval": f"{start_min}-{end_min}",
                "interval_start": start_min,
                "strike": strike,
                "range_pct": range_pct,
                "dir_move_pct": dir_move,
                "volume": vol,
                "dist_from_strike_pct": dist_from_strike,
                "final_outcome": label,
            })
    
    return pd.DataFrame(all_intervals)


def analyze_volatility_by_interval(df_intervals: pd.DataFrame):
    """Analyze volatility patterns by 5-minute interval."""
    print(f"\n{'=' * 100}")
    print("📊 VOLATILITY BY 5-MINUTE INTERVAL (Within Hour)")
    print(f"{'=' * 100}")
    
    interval_stats = df_intervals.groupby("interval").agg({
        "range_pct": ["mean", "std", "median"],
        "dir_move_pct": ["mean", "std"],
        "volume": "mean",
    }).round(4)
    
    # Flatten column names
    interval_stats.columns = ["_".join(col) for col in interval_stats.columns]
    
    print(f"\n{'Interval':<12} | {'Avg Range':^10} | {'Std Range':^10} | {'Med Range':^10} | {'Avg Move':^10} | {'Std Move':^10}")
    print("-" * 80)
    
    # Sort by interval start time
    sorted_intervals = sorted(interval_stats.index, key=lambda x: int(x.split("-")[0]))
    
    for interval in sorted_intervals:
        row = interval_stats.loc[interval]
        bar = "█" * int(row["range_pct_mean"] * 30)
        print(f"{interval:<12} | {row['range_pct_mean']:>8.3f}% | {row['range_pct_std']:>8.3f}% | {row['range_pct_median']:>8.3f}% | {row['dir_move_pct_mean']:>+8.3f}% | {row['dir_move_pct_std']:>8.3f}%")
    
    return interval_stats


def analyze_volatility_by_hour(df_intervals: pd.DataFrame):
    """Analyze which hours have highest volatility."""
    print(f"\n{'=' * 100}")
    print("📊 VOLATILITY BY HOUR OF DAY (ET)")
    print(f"{'=' * 100}")
    
    hourly_vol = df_intervals.groupby("hour_et")["range_pct"].agg(["mean", "std", "count"])
    hourly_vol.columns = ["avg_range", "std_range", "count"]
    
    print(f"\n{'Hour (ET)':<12} | {'Avg Range':^10} | {'Std Range':^10} | {'Count':^8} | Visual")
    print("-" * 70)
    
    for hour in range(24):
        if hour in hourly_vol.index:
            row = hourly_vol.loc[hour]
            bar = "█" * int(row["avg_range"] * 30)
            print(f"{hour:>2}:00-{hour+1:>2}:00   | {row['avg_range']:>8.3f}% | {row['std_range']:>8.3f}% | {int(row['count']):>8} | {bar}")
    
    return hourly_vol


def analyze_scaling_opportunities(df_intervals: pd.DataFrame):
    """
    Identify where scaling (position sizing) would work well.
    
    Scaling works best when:
    1. Volatility is predictable (low std relative to mean)
    2. Directional moves are consistent
    3. Early intervals predict final outcome
    """
    print(f"\n{'=' * 100}")
    print("📊 SCALING OPPORTUNITY ANALYSIS")
    print(f"{'=' * 100}")
    
    # First 30 minutes only (actionable range)
    first_30 = df_intervals[df_intervals["interval_start"] < 30].copy()
    
    # Group by interval
    interval_groups = {}
    for interval in first_30["interval"].unique():
        subset = first_30[first_30["interval"] == interval]
        
        # Calculate predictive power
        # Correlation between distance from strike and final outcome
        corr = subset["dist_from_strike_pct"].corr(subset["final_outcome"])
        
        # Volatility consistency (lower = more predictable)
        vol_consistency = subset["range_pct"].std() / subset["range_pct"].mean()
        
        # Movement bias
        up_count = (subset["dir_move_pct"] > 0).sum()
        down_count = (subset["dir_move_pct"] < 0).sum()
        bias = abs(up_count - down_count) / len(subset)
        
        interval_groups[interval] = {
            "correlation": corr,
            "vol_consistency": vol_consistency,
            "avg_range": subset["range_pct"].mean(),
            "std_range": subset["range_pct"].std(),
            "bias": bias,
            "count": len(subset),
        }
    
    print("\n  Scaling Score = Higher correlation + Lower volatility deviation\n")
    print(f"{'Interval':<12} | {'Corr w/ Outcome':^15} | {'Vol Consistency':^15} | {'Avg Range':^10} | {'Scaling Score':^14}")
    print("-" * 80)
    
    # Calculate scaling score
    for interval in sorted(interval_groups.keys(), key=lambda x: int(x.split("-")[0])):
        data = interval_groups[interval]
        # Scaling score: high correlation is good, low vol_consistency is good
        scaling_score = data["correlation"] - data["vol_consistency"]
        
        # Visual indicator
        if scaling_score > 0.2:
            indicator = "🟢 Good"
        elif scaling_score > 0:
            indicator = "🔵 OK"
        else:
            indicator = "🟠 Caution"
        
        print(f"{interval:<12} | {data['correlation']:>+13.3f} | {data['vol_consistency']:>13.3f} | {data['avg_range']:>8.3f}% | {scaling_score:>+10.3f} {indicator}")
    
    # Best scaling windows
    print("\n  💡 BEST WINDOWS FOR SCALING:")
    best_windows = sorted(interval_groups.items(), key=lambda x: x[1]["correlation"] - x[1]["vol_consistency"], reverse=True)[:3]
    for i, (interval, data) in enumerate(best_windows, 1):
        print(f"     {i}. {interval}: Correlation={data['correlation']:.3f}, Consistency={data['vol_consistency']:.3f}")


def main():
    print("=" * 100)
    print("🔬 BTC Intra-Hour Volatility Analysis")
    print("=" * 100)
    
    # Fetch data
    end_time = datetime.now(UTC)
    start_time = end_time - timedelta(days=LOOKBACK_DAYS)
    
    print(f"\nDataset: {LOOKBACK_DAYS} days ({start_time.strftime('%Y-%m-%d')} to {end_time.strftime('%Y-%m-%d')})")
    print("\nFetching data...")
    
    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)
    
    raw_klines = fetch_binance_klines_paginated(SYMBOL, "1m", start_ms, end_ms)
    print(f"✅ Fetched {len(raw_klines):,} minute candles")
    
    # Process minute data
    df_minutes = process_minute_data(raw_klines)
    
    # Save minute data
    df_minutes.to_csv("btc_minute_data_60d.csv", index=False)
    print(f"✅ Saved minute data to btc_minute_data_60d.csv")
    
    # Calculate interval volatility
    print("\nCalculating interval volatility...")
    df_intervals = calculate_interval_volatility(df_minutes)
    print(f"✅ Analyzed {len(df_intervals):,} intervals")
    
    # Save interval data
    df_intervals.to_csv("btc_interval_volatility.csv", index=False)
    print(f"✅ Saved interval data to btc_interval_volatility.csv")
    
    # Run analyses
    interval_stats = analyze_volatility_by_interval(df_intervals)
    hourly_vol = analyze_volatility_by_hour(df_intervals)
    analyze_scaling_opportunities(df_intervals)
    
    # Summary
    print(f"\n{'=' * 100}")
    print("📋 SUMMARY")
    print(f"{'=' * 100}")
    
    # Find highest volatility interval
    max_vol_interval = interval_stats["range_pct_mean"].idxmax()
    min_vol_interval = interval_stats["range_pct_mean"].idxmin()
    
    print(f"\n  Highest volatility interval: {max_vol_interval} ({interval_stats.loc[max_vol_interval, 'range_pct_mean']:.3f}%)")
    print(f"  Lowest volatility interval: {min_vol_interval} ({interval_stats.loc[min_vol_interval, 'range_pct_mean']:.3f}%)")
    
    # Find most volatile hours
    max_vol_hour = hourly_vol["avg_range"].idxmax()
    min_vol_hour = hourly_vol["avg_range"].idxmin()
    
    print(f"\n  Most volatile hour: {max_vol_hour}:00 ET ({hourly_vol.loc[max_vol_hour, 'avg_range']:.3f}%)")
    print(f"  Least volatile hour: {min_vol_hour}:00 ET ({hourly_vol.loc[min_vol_hour, 'avg_range']:.3f}%)")
    
    print(f"\n{'=' * 100}")
    print("Data files saved:")
    print("  - btc_minute_data_60d.csv (raw minute candles)")
    print("  - btc_interval_volatility.csv (5-minute interval analysis)")
    print(f"{'=' * 100}")
    
    return df_minutes, df_intervals


if __name__ == "__main__":
    df_minutes, df_intervals = main()
