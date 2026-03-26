"""
Enhanced BTC Intra-Hour Analysis with Time/Price Matrix

Creates a 2D heatmap showing:
- Rows: Time windows (cumulative from open)
- Columns: Price move buckets (% from strike)
- Cells: Win rate (% that finished UP) and sample count

This reveals:
1. At which time + price combination is the signal strongest?
2. Where are the "danger zones" (unpredictable ~50%)?
3. How does signal strength evolve over time?
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
LOOKBACK_DAYS = 14
UTC = timezone.utc
ET_OFFSET = -5

# Time windows (in minutes) - cumulative from open
TIME_WINDOWS = [5, 10, 15, 20, 30, 45]

# Price buckets (% from strike)
PRICE_BUCKETS = [
    (-float('inf'), -1.0, "<-1%"),
    (-1.0, -0.5, "-1% to -0.5%"),
    (-0.5, -0.2, "-0.5% to -0.2%"),
    (-0.2, 0.0, "-0.2% to 0%"),
    (0.0, 0.2, "0% to +0.2%"),
    (0.2, 0.5, "+0.2% to +0.5%"),
    (0.5, 1.0, "+0.5% to +1%"),
    (1.0, float('inf'), ">+1%"),
]


def fetch_binance_klines(symbol: str, interval: str, start_time: int, end_time: int) -> list:
    """Fetch klines from Binance with pagination."""
    all_klines = []
    current_start = start_time
    
    while current_start < end_time:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_time,
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
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
        })
    
    return pd.DataFrame(data)


def get_price_bucket(pct_move: float) -> str:
    """Get the bucket label for a given % move."""
    for low, high, label in PRICE_BUCKETS:
        if low <= pct_move < high:
            return label
    return ">+1%"


def build_matrix_data(df_minutes: pd.DataFrame) -> tuple:
    """
    Build the time/price matrix data.
    
    Returns:
        - matrix_data: dict of {time_window: {price_bucket: {'count': N, 'up_count': N}}}
        - hourly_outcomes: list of all hourly outcomes for reference
    """
    df_minutes["hour_key"] = df_minutes["date"] + "_" + df_minutes["hour_et"].astype(str).str.zfill(2)
    
    matrix_data = defaultdict(lambda: defaultdict(lambda: {'count': 0, 'up_count': 0}))
    hourly_outcomes = []
    
    for hour_key, group in df_minutes.groupby("hour_key"):
        if len(group) < 55:
            continue
            
        group = group.sort_values("minute")
        strike = group.iloc[0]["open"]
        final_close = group.iloc[-1]["close"]
        label = 1 if final_close > strike else 0
        
        hourly_outcomes.append({
            "hour_key": hour_key,
            "strike": strike,
            "close": final_close,
            "label": label,
        })
        
        # Calculate % move at each time window
        for window_end in TIME_WINDOWS:
            subset = group[group["minute"] <= window_end]
            if len(subset) == 0:
                continue
                
            price_at_window = subset.iloc[-1]["close"]
            pct_move = ((price_at_window - strike) / strike) * 100
            bucket = get_price_bucket(pct_move)
            
            matrix_data[window_end][bucket]['count'] += 1
            matrix_data[window_end][bucket]['up_count'] += label
    
    return matrix_data, hourly_outcomes


def build_incremental_matrix(df_minutes: pd.DataFrame) -> dict:
    """
    Build matrix for INCREMENTAL moves (change within window, not from open).
    
    Windows: 0-5, 5-10, 10-15, 15-20, 20-30, 30-45
    """
    INCREMENTAL_WINDOWS = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 45)]
    
    df_minutes["hour_key"] = df_minutes["date"] + "_" + df_minutes["hour_et"].astype(str).str.zfill(2)
    
    matrix_data = defaultdict(lambda: defaultdict(lambda: {'count': 0, 'up_count': 0}))
    
    for hour_key, group in df_minutes.groupby("hour_key"):
        if len(group) < 55:
            continue
            
        group = group.sort_values("minute")
        final_close = group.iloc[-1]["close"]
        strike = group.iloc[0]["open"]
        label = 1 if final_close > strike else 0
        
        for start_min, end_min in INCREMENTAL_WINDOWS:
            start_subset = group[group["minute"] == start_min]
            end_subset = group[group["minute"] == end_min]
            
            if len(start_subset) == 0 or len(end_subset) == 0:
                continue
            
            start_price = start_subset.iloc[0]["close"]
            end_price = end_subset.iloc[0]["close"]
            
            # Incremental move within this window
            pct_move = ((end_price - start_price) / start_price) * 100
            bucket = get_price_bucket(pct_move)
            
            window_label = f"{start_min}-{end_min}"
            matrix_data[window_label][bucket]['count'] += 1
            matrix_data[window_label][bucket]['up_count'] += label
    
    return matrix_data


def print_matrix(matrix_data: dict, title: str, windows: list):
    """Print the matrix as a formatted table."""
    print(f"\n{'=' * 120}")
    print(f"📊 {title}")
    print(f"{'=' * 120}")
    
    # Header
    bucket_labels = [b[2] for b in PRICE_BUCKETS]
    header = f"{'Window':<12} |"
    for label in bucket_labels:
        header += f" {label:^14} |"
    print(header)
    print("-" * len(header))
    
    # Rows
    for window in windows:
        row = f"{str(window):>10}m |" if isinstance(window, int) else f"{window:>10} |"
        
        for _, _, bucket_label in PRICE_BUCKETS:
            data = matrix_data[window][bucket_label]
            count = data['count']
            up_count = data['up_count']
            
            if count >= 3:  # Only show if enough samples
                up_rate = (up_count / count) * 100
                # Color coding via symbols
                if up_rate >= 70:
                    indicator = "🟢"
                elif up_rate >= 55:
                    indicator = "🔵"
                elif up_rate <= 30:
                    indicator = "🔴"
                elif up_rate <= 45:
                    indicator = "🟠"
                else:
                    indicator = "⚪"
                row += f" {up_rate:5.1f}%{indicator}(n={count:2d}) |"
            else:
                row += f" {'---':^14} |"
        
        print(row)
    
    print("\nLegend: 🟢 ≥70% UP | 🔵 ≥55% UP | ⚪ ~50% | 🟠 ≤45% UP | 🔴 ≤30% UP")


def find_best_signals(matrix_data: dict, min_samples: int = 5) -> list:
    """Find the strongest predictive signals in the matrix."""
    signals = []
    
    for window, buckets in matrix_data.items():
        for bucket_label, data in buckets.items():
            count = data['count']
            up_count = data['up_count']
            
            if count >= min_samples:
                up_rate = (up_count / count) * 100
                edge = abs(up_rate - 50)
                
                if edge >= 15:  # At least 15% edge
                    signals.append({
                        'window': window,
                        'bucket': bucket_label,
                        'up_rate': up_rate,
                        'down_rate': 100 - up_rate,
                        'edge': edge,
                        'count': count,
                        'direction': 'UP' if up_rate > 50 else 'DOWN',
                    })
    
    # Sort by edge (strongest first)
    signals.sort(key=lambda x: x['edge'], reverse=True)
    return signals


def main():
    print("=" * 80)
    print("🔬 Enhanced BTC Intra-Hour Matrix Analysis")
    print("=" * 80)
    
    # Fetch data
    end_time = datetime.now(UTC)
    start_time = end_time - timedelta(days=LOOKBACK_DAYS)
    
    print(f"\nFetching 1-minute data for {LOOKBACK_DAYS} days...")
    
    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)
    
    raw_klines = fetch_binance_klines(SYMBOL, "1m", start_ms, end_ms)
    print(f"Fetched {len(raw_klines)} minute candles")
    
    df_minutes = process_minute_data(raw_klines)
    
    # Build CUMULATIVE matrix (from open)
    print("\nBuilding cumulative matrix (price vs strike at each checkpoint)...")
    cum_matrix, outcomes = build_matrix_data(df_minutes)
    print(f"Analyzed {len(outcomes)} complete hours")
    
    # Build INCREMENTAL matrix (change within window)
    print("Building incremental matrix (price change within each window)...")
    inc_matrix = build_incremental_matrix(df_minutes)
    
    # Print matrices
    print_matrix(cum_matrix, "CUMULATIVE MATRIX (% from Strike at Time T)", TIME_WINDOWS)
    print_matrix(inc_matrix, "INCREMENTAL MATRIX (% Change Within Window)", 
                 ["0-5", "5-10", "10-15", "15-20", "20-30", "30-45"])
    
    # Find best signals
    print(f"\n{'=' * 80}")
    print("🎯 STRONGEST PREDICTIVE SIGNALS (Edge ≥ 15%)")
    print(f"{'=' * 80}")
    
    cum_signals = find_best_signals(cum_matrix)
    inc_signals = find_best_signals(inc_matrix)
    
    print("\n📈 From Cumulative Matrix:")
    for sig in cum_signals[:10]:
        print(f"   At minute {sig['window']:>2}: {sig['bucket']:>14} → {sig['direction']} {max(sig['up_rate'], sig['down_rate']):.1f}% (edge={sig['edge']:.1f}%, n={sig['count']})")
    
    print("\n📉 From Incremental Matrix:")
    for sig in inc_signals[:10]:
        print(f"   Window {sig['window']:>5}: {sig['bucket']:>14} → {sig['direction']} {max(sig['up_rate'], sig['down_rate']):.1f}% (edge={sig['edge']:.1f}%, n={sig['count']})")
    
    # Actionable summary
    print(f"\n{'=' * 80}")
    print("💡 KEY TAKEAWAYS")
    print(f"{'=' * 80}")
    
    best_cum = cum_signals[0] if cum_signals else None
    best_inc = inc_signals[0] if inc_signals else None
    
    if best_cum:
        print(f"\n1. STRONGEST CUMULATIVE SIGNAL:")
        print(f"   At minute {best_cum['window']}, if price is {best_cum['bucket']},")
        print(f"   → {best_cum['direction']} wins {max(best_cum['up_rate'], best_cum['down_rate']):.1f}% of the time (n={best_cum['count']})")
    
    if best_inc:
        print(f"\n2. STRONGEST INCREMENTAL SIGNAL:")
        print(f"   In window {best_inc['window']} min, if price moves {best_inc['bucket']},")
        print(f"   → {best_inc['direction']} wins {max(best_inc['up_rate'], best_inc['down_rate']):.1f}% of the time (n={best_inc['count']})")
    
    # Save data
    print(f"\n{'=' * 80}")
    print("Analysis complete!")
    print(f"{'=' * 80}")
    
    return cum_matrix, inc_matrix


if __name__ == "__main__":
    cum_matrix, inc_matrix = main()
