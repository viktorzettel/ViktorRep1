"""
Enhanced BTC Analysis: First 30 Minutes with Statistical Validation

Features:
1. Larger dataset (60 days)
2. Focus on first 30 minutes (5-minute intervals)
3. Statistical tests (p-values, confidence intervals)
4. Distance-from-strike transition probabilities
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import math
import warnings
warnings.filterwarnings('ignore')

# ============================================================================
# CONFIG
# ============================================================================

BINANCE_API = "https://api.binance.com/api/v3/klines"
SYMBOL = "BTCUSDT"
LOOKBACK_DAYS = 60  # Increased from 14 to 60 days
UTC = timezone.utc
ET_OFFSET = -5

# Focus on first 30 minutes with 5-minute granularity
TIME_CHECKPOINTS = [5, 10, 15, 20, 25, 30]

# Finer price buckets for better granularity
PRICE_BUCKETS = [
    (-float('inf'), -0.5, "<-0.5%"),
    (-0.5, -0.3, "-0.5% to -0.3%"),
    (-0.3, -0.2, "-0.3% to -0.2%"),
    (-0.2, -0.1, "-0.2% to -0.1%"),
    (-0.1, 0.0, "-0.1% to 0%"),
    (0.0, 0.1, "0% to +0.1%"),
    (0.1, 0.2, "+0.1% to +0.2%"),
    (0.2, 0.3, "+0.2% to +0.3%"),
    (0.3, 0.5, "+0.3% to +0.5%"),
    (0.5, float('inf'), ">+0.5%"),
]


def fetch_binance_klines_paginated(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """Fetch klines with pagination for large datasets."""
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
            
        # Progress indicator
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
            "timestamp_et": open_time_et,
            "hour_et": open_time_et.hour,
            "minute": open_time_et.minute,
            "date": open_time_et.strftime("%Y-%m-%d"),
            "open": float(k[1]),
            "close": float(k[4]),
        })
    
    return pd.DataFrame(data)


def get_price_bucket(pct_move: float) -> str:
    """Get bucket label for a % move."""
    for low, high, label in PRICE_BUCKETS:
        if low <= pct_move < high:
            return label
    return ">+0.5%"


def calculate_statistics(successes: int, trials: int) -> dict:
    """
    Calculate statistical measures for a proportion.
    
    Returns:
        - proportion (win rate)
        - standard_error
        - confidence_interval_95 (lower, upper)
        - p_value (test against 50%)
        - is_significant (p < 0.05)
    """
    if trials == 0:
        return None
    
    p = successes / trials
    n = trials
    
    # Standard error
    se = np.sqrt(p * (1 - p) / n) if n > 0 else 0
    
    # 95% confidence interval (Wilson score for small samples)
    if n >= 5:
        z = 1.96
        denominator = 1 + z**2 / n
        center = (p + z**2 / (2 * n)) / denominator
        margin = (z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))) / denominator
        ci_lower = max(0, center - margin)
        ci_upper = min(1, center + margin)
    else:
        ci_lower = ci_upper = p
    
    # P-value: use normal approximation for binomial test against p=0.5
    # Z = (observed - expected) / sqrt(n * p0 * (1-p0))
    # For large n, this is a good approximation
    if n >= 10:
        expected = n * 0.5
        std_dev = np.sqrt(n * 0.5 * 0.5)
        z_score = abs(successes - expected) / std_dev
        # Two-tailed p-value from normal distribution (approximation)
        # P(|Z| > z) = 2 * (1 - Phi(z))
        # Using error function approximation
        p_value = 2 * (1 - 0.5 * (1 + math.erf(z_score / np.sqrt(2))))
    else:
        # For small samples, use conservative estimate
        p_value = 1.0
    
    return {
        'proportion': p,
        'trials': n,
        'successes': successes,
        'standard_error': se,
        'ci_lower': ci_lower,
        'ci_upper': ci_upper,
        'p_value': p_value,
        'is_significant': p_value < 0.05,
    }


def build_distance_matrix(df_minutes: pd.DataFrame) -> dict:
    """
    Build the distance-from-strike matrix with statistics.
    
    For each time checkpoint and price bucket:
    - Count how many hours had that distance at that time
    - Count how many of those finished UP
    - Calculate statistical significance
    """
    df_minutes["hour_key"] = df_minutes["date"] + "_" + df_minutes["hour_et"].astype(str).str.zfill(2)
    
    # Store raw counts
    matrix_counts = defaultdict(lambda: defaultdict(lambda: {'total': 0, 'up': 0}))
    
    total_hours = 0
    
    for hour_key, group in df_minutes.groupby("hour_key"):
        if len(group) < 55:
            continue
            
        group = group.sort_values("minute")
        strike = group.iloc[0]["open"]
        final_close = group.iloc[-1]["close"]
        label = 1 if final_close > strike else 0
        total_hours += 1
        
        for checkpoint in TIME_CHECKPOINTS:
            subset = group[group["minute"] <= checkpoint]
            if len(subset) == 0:
                continue
                
            price_at_checkpoint = subset.iloc[-1]["close"]
            distance_pct = ((price_at_checkpoint - strike) / strike) * 100
            bucket = get_price_bucket(distance_pct)
            
            matrix_counts[checkpoint][bucket]['total'] += 1
            matrix_counts[checkpoint][bucket]['up'] += label
    
    # Calculate statistics for each cell
    matrix_stats = {}
    for checkpoint in TIME_CHECKPOINTS:
        matrix_stats[checkpoint] = {}
        for _, _, bucket_label in PRICE_BUCKETS:
            data = matrix_counts[checkpoint][bucket_label]
            stats_result = calculate_statistics(data['up'], data['total'])
            matrix_stats[checkpoint][bucket_label] = stats_result
    
    return matrix_stats, total_hours


def print_statistical_matrix(matrix_stats: dict, total_hours: int):
    """Print matrix with statistical annotations."""
    print(f"\n{'=' * 140}")
    print(f"📊 DISTANCE FROM STRIKE MATRIX (First 30 Minutes) - {total_hours} Hours Analyzed")
    print(f"{'=' * 140}")
    
    # Simplified bucket labels for display
    bucket_labels = [b[2] for b in PRICE_BUCKETS]
    
    # Header
    header = f"{'Time':^8} |"
    for label in bucket_labels:
        header += f" {label:^13} |"
    print(header)
    print("-" * len(header))
    
    for checkpoint in TIME_CHECKPOINTS:
        row = f"{checkpoint:>5} min |"
        
        for _, _, bucket_label in PRICE_BUCKETS:
            stats_data = matrix_stats[checkpoint][bucket_label]
            
            if stats_data and stats_data['trials'] >= 5:
                p = stats_data['proportion'] * 100
                n = stats_data['trials']
                sig = "**" if stats_data['is_significant'] else ""
                
                # Visual indicator
                if p >= 65:
                    indicator = "🟢"
                elif p >= 55:
                    indicator = "🔵"
                elif p <= 35:
                    indicator = "🔴"
                elif p <= 45:
                    indicator = "🟠"
                else:
                    indicator = "⚪"
                
                row += f" {p:4.0f}%{indicator}{sig:2}n={n:<3} |"
            else:
                row += f" {'---':^13} |"
        
        print(row)
    
    print("\nLegend: 🟢≥65% UP | 🔵≥55% UP | ⚪50% | 🟠≤45% UP | 🔴≤35% UP | ** = p<0.05")


def print_significant_signals(matrix_stats: dict, min_edge: float = 10.0):
    """Print only statistically significant signals."""
    print(f"\n{'=' * 100}")
    print("🎯 STATISTICALLY SIGNIFICANT SIGNALS (p < 0.05, edge ≥ 10%)")
    print(f"{'=' * 100}")
    
    signals = []
    
    for checkpoint in TIME_CHECKPOINTS:
        for _, _, bucket_label in PRICE_BUCKETS:
            stats_data = matrix_stats[checkpoint][bucket_label]
            
            if stats_data and stats_data['is_significant']:
                p = stats_data['proportion'] * 100
                edge = abs(p - 50)
                
                if edge >= min_edge:
                    signals.append({
                        'time': checkpoint,
                        'bucket': bucket_label,
                        'win_rate': p,
                        'direction': 'UP' if p > 50 else 'DOWN',
                        'edge': edge,
                        'n': stats_data['trials'],
                        'p_value': stats_data['p_value'],
                        'ci_lower': stats_data['ci_lower'] * 100,
                        'ci_upper': stats_data['ci_upper'] * 100,
                    })
    
    # Sort by edge
    signals.sort(key=lambda x: x['edge'], reverse=True)
    
    print(f"\n{'Minute':<8} | {'Distance Zone':<18} | {'Outcome':<6} | {'Rate':^8} | {'95% CI':^16} | {'p-value':^10} | {'n':^5}")
    print("-" * 90)
    
    for sig in signals:
        outcome_rate = sig['win_rate'] if sig['direction'] == 'UP' else (100 - sig['win_rate'])
        ci_str = f"[{sig['ci_lower']:.1f}%, {sig['ci_upper']:.1f}%]"
        print(f"{sig['time']:>5} min | {sig['bucket']:<18} | {sig['direction']:<6} | {outcome_rate:>5.1f}%  | {ci_str:^16} | {sig['p_value']:>8.4f} | {sig['n']:>5}")
    
    if not signals:
        print("  No signals meet significance criteria. Need more data.")
    
    return signals


def analyze_transition_probabilities(df_minutes: pd.DataFrame):
    """
    Analyze: Given distance D at time T1, what's P(UP) at time T2?
    This shows how probabilities evolve as time passes.
    """
    print(f"\n{'=' * 100}")
    print("📈 TRANSITION ANALYSIS: How does signal strength change over time?")
    print(f"{'=' * 100}")
    
    df_minutes["hour_key"] = df_minutes["date"] + "_" + df_minutes["hour_et"].astype(str).str.zfill(2)
    
    # Track: If UP at time T1, what's P(UP) at end?
    transitions = defaultdict(lambda: {'total': 0, 'still_up': 0})
    
    for hour_key, group in df_minutes.groupby("hour_key"):
        if len(group) < 55:
            continue
            
        group = group.sort_values("minute")
        strike = group.iloc[0]["open"]
        final_close = group.iloc[-1]["close"]
        final_up = final_close > strike
        
        for checkpoint in TIME_CHECKPOINTS:
            subset = group[group["minute"] <= checkpoint]
            if len(subset) == 0:
                continue
                
            price = subset.iloc[-1]["close"]
            currently_up = price > strike
            
            key = (checkpoint, currently_up)
            transitions[key]['total'] += 1
            if final_up:
                transitions[key]['still_up'] += 1
    
    print("\n  'If price is [above/below] strike at minute T, what's the final outcome?'\n")
    print(f"  {'Minute':<8} | {'Currently UP':^30} | {'Currently DOWN':^30}")
    print(f"  {'-'*8}-+-{'-'*30}-+-{'-'*30}")
    
    for checkpoint in TIME_CHECKPOINTS:
        up_data = transitions[(checkpoint, True)]
        down_data = transitions[(checkpoint, False)]
        
        up_str = "---"
        down_str = "---"
        
        if up_data['total'] >= 10:
            up_rate = up_data['still_up'] / up_data['total'] * 100
            up_str = f"→ {up_rate:.1f}% finish UP (n={up_data['total']})"
        
        if down_data['total'] >= 10:
            down_rate = (1 - down_data['still_up'] / down_data['total']) * 100
            down_str = f"→ {down_rate:.1f}% finish DOWN (n={down_data['total']})"
        
        print(f"  {checkpoint:>5} min | {up_str:^30} | {down_str:^30}")


def main():
    print("=" * 100)
    print("🔬 Enhanced BTC Analysis: First 30 Minutes with Statistical Validation")
    print("=" * 100)
    
    # Fetch larger dataset
    end_time = datetime.now(UTC)
    start_time = end_time - timedelta(days=LOOKBACK_DAYS)
    
    print(f"\nDataset: {LOOKBACK_DAYS} days ({start_time.strftime('%Y-%m-%d')} to {end_time.strftime('%Y-%m-%d')})")
    print(f"Expected: ~{LOOKBACK_DAYS * 24 * 60:,} minute candles, ~{LOOKBACK_DAYS * 24} hourly windows")
    
    print("\nFetching data (this may take a minute for 60 days)...")
    
    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)
    
    raw_klines = fetch_binance_klines_paginated(SYMBOL, "1m", start_ms, end_ms)
    print(f"✅ Fetched {len(raw_klines):,} minute candles")
    
    df_minutes = process_minute_data(raw_klines)
    
    # Build matrix with statistics
    print("\nBuilding distance-from-strike matrix with statistical tests...")
    matrix_stats, total_hours = build_distance_matrix(df_minutes)
    print(f"✅ Analyzed {total_hours} complete hourly windows")
    
    # Print matrix
    print_statistical_matrix(matrix_stats, total_hours)
    
    # Print significant signals
    significant_signals = print_significant_signals(matrix_stats)
    
    # Transition analysis
    analyze_transition_probabilities(df_minutes)
    
    # Summary
    print(f"\n{'=' * 100}")
    print("📋 SUMMARY")
    print(f"{'=' * 100}")
    print(f"\n  Dataset: {total_hours} hours ({LOOKBACK_DAYS} days)")
    print(f"  Significant signals found: {len(significant_signals)}")
    
    if significant_signals:
        best = significant_signals[0]
        print(f"\n  🏆 STRONGEST SIGNAL:")
        print(f"     At minute {best['time']}, if distance is {best['bucket']}")
        print(f"     → {best['direction']} wins {best['win_rate']:.1f}% (p={best['p_value']:.4f}, n={best['n']})")
    
    print(f"\n{'=' * 100}")
    
    return matrix_stats, significant_signals


if __name__ == "__main__":
    matrix_stats, signals = main()
