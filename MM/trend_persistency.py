"""
Trend Persistence & Breakout Reliability Analysis

Deep dive into the 'Momentum' thesis.
1. Hurst Exponent: Quantify time-series momentum (H > 0.5) vs mean reversion (H < 0.5).
2. Breakout Reliability: Once price crosses X% threshold, how often does it fail?
3. Max Adverse Excursion (MAE): How much pain (drawdown) do we suffer after entry?

Usage:
    python trend_persistency.py
"""

import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path

DATA_FILE = Path('data/btc_1min_30d.json')
OUTPUT_DIR = Path('data/analysis')
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)

def load_data():
    with open(DATA_FILE) as f:
        klines = json.load(f)
    return klines

def calculate_hurst(series, max_lag=20):
    """
    Calculate Hurst Exponent using R/S analysis.
    H = 0.5: Random Walk (Geometric Brownian Motion)
    H > 0.5: Trending (Persistent)
    H < 0.5: Mean Reverting (Anti-persistent)
    """
    lags = range(2, max_lag)
    tau = [np.sqrt(np.std(np.subtract(series[lag:], series[:-lag]))) for lag in lags]
    poly = np.polyfit(np.log(lags), np.log(tau), 1)
    return poly[0]*2 

# Alternate simple Hurst for huge datasets (RS method is slow/complex, using variances method)
def hurst_variation(price_series, min_lag=2, max_lag=100):
    """
    Estimating H via Var(tau) ~ tau^(2H)
    """
    lags = np.unique(np.logspace(np.log10(min_lag), np.log10(max_lag), 20).astype(int))
    variances = []
    
    # Use log prices
    y = np.log(price_series)
    
    for lag in lags:
        # Differences at lag
        diffs = y[lag:] - y[:-lag]
        variances.append(np.var(diffs))
        
    # Fit line to log(lag) vs log(var)
    # log(Var) = 2H * log(lag) + C
    coeffs = np.polyfit(np.log(lags), np.log(variances), 1)
    H = coeffs[0] / 2
    return H, lags, variances


def analyze_breakout_reliability(candles_15m, threshold=0.0005):
    """
    Analyze what happens AFTER price first crosses +threshold or -threshold.
    
    Metrics:
    - Win Rate: Closes > 0 (or < 0 if short)
    - Strong Win: Closes > threshold
    - Reversal Fail: Closes < 0 (or > 0 if short)
    - MAE (Max Adverse Excursion): Max loss after entry
    - MFE (Max Favorable Excursion): Max profit after entry
    """
    
    results = {
        "total_triggers": 0,
        "wins": 0,          # Closed in direction
        "strong_wins": 0,    # Closed > threshold
        "losses": 0,        # Closed opposite
        "mae_values": [],   # Drawdown distribution
        "mfe_values": [],   # Max profit potential
    }
    
    for candle in candles_15m:
        entry_idx = -1
        direction = 0 # 1 = UP, -1 = DOWN
        
        # Check for first crossing
        for i, ret in enumerate(candle):
            if ret > threshold:
                entry_idx = i
                direction = 1
                break
            elif ret < -threshold:
                entry_idx = i
                direction = -1
                break
        
        if entry_idx != -1 and entry_idx < 14: # Ignore if crossing on last minute
            results["total_triggers"] += 1
            
            # Post-entry analysis
            post_entry_rets = candle[entry_idx+1:]
            final_ret = candle[-1]
            
            # Win/Loss
            if direction == 1:
                if final_ret > 0: results["wins"] += 1
                if final_ret > threshold: results["strong_wins"] += 1
                if final_ret <= 0: results["losses"] += 1
                
                # MAE: Mean min value after entry (how low did it go?)
                # We entered at ~threshold. Drawdown is (threshold - min_val) 
                # actually relative to entry roughly.
                # Let's count relative to Open (0).
                # Entry ~ threshold.
                mae = min(post_entry_rets) if post_entry_rets else final_ret
                mfe = max(post_entry_rets) if post_entry_rets else final_ret
                
                # Drawdown from entry point?
                # If we bought at 0.05%, and it drops to 0.01%, drawdown is 0.04%.
                # If it drops to -0.02%, drawdown is 0.07%.
                # MAE = Entry - Min Price
                drawdown = threshold - mae
                
            else: # DOWN entry
                if final_ret < 0: results["wins"] += 1
                if final_ret < -threshold: results["strong_wins"] += 1
                if final_ret >= 0: results["losses"] += 1
                
                # For short: MAE = Max Price - Entry
                # Entry ~ -threshold
                mae = max(post_entry_rets) if post_entry_rets else final_ret
                mfe = min(post_entry_rets) if post_entry_rets else final_ret
                
                drawdown = mae - (-threshold)

            results["mae_values"].append(max(0, drawdown))
            results["mfe_values"].append(abs(mfe)) # Simplification

    return results

def main():
    print("Loading data...")
    klines_1m = load_data()
    # Extract closes
    closes = np.array([k['close'] for k in klines_1m])
    
    print(f"Data points: {len(closes)}")
    
    # ---------------------------
    # 1. Hurst Exponent Analysis
    # ---------------------------
    print("\n--- Hurst Exponent Analysis ---")
    # Calculate H for different timescales
    # Short term: lags 2..20 (2 min to 20 min)
    # Medium term: lags 20..100 (20 min to 100 min)
    
    H_short, _, _ = hurst_variation(closes, min_lag=2, max_lag=15)
    H_medium, _, _ = hurst_variation(closes, min_lag=15, max_lag=60)
    H_long, _, _ = hurst_variation(closes, min_lag=60, max_lag=240)
    
    print(f"Hurst (2-15m):   {H_short:.3f}  (>0.5 = Momentum within candle?)")
    print(f"Hurst (15-60m):  {H_medium:.3f} (>0.5 = Trend continuation across candles?)")
    print(f"Hurst (1-4h):    {H_long:.3f}")
    
    interpretation = "Weak Momentum"
    if H_short > 0.6: interpretation = "STRONG Momentum"
    elif H_short < 0.45: interpretation = "Mean Reversion"
    print(f"Interpretation (Intra-candle): {interpretation}")
    
    # ---------------------------
    # 2. Breakout Reliability
    # ---------------------------
    print("\n--- Breakout Reliability (Threshold 0.05%) ---")
    
    # Reconstruct 15m candles
    candles_15m = []
    for i in range(0, len(klines_1m)-14, 15):
        block = klines_1m[i:i+15]
        open_p = block[0]['open']
        min_rets = [(c['close'] - open_p)/open_p for c in block]
        candles_15m.append(min_rets)
        
    res = analyze_breakout_reliability(candles_15m, threshold=0.0005)
    
    total = res["total_triggers"]
    win_rate = res["wins"] / total * 100
    strong_win_rate = res["strong_wins"] / total * 100
    avg_mae = np.mean(res["mae_values"]) * 100 # In percent
    
    # Calculate MAE percentiles
    mae_50 = np.percentile(res["mae_values"], 50) * 100
    mae_90 = np.percentile(res["mae_values"], 90) * 100
    
    print(f"Total Triggers: {total}")
    print(f"Win Rate (Close > 0):       {win_rate:.1f}%")
    print(f"Strong Win (Close > 0.05%): {strong_win_rate:.1f}%")
    print(f"Reversal Rate (Fail):       {100 - win_rate:.1f}%")
    print("\nDrawdown Analysis (Pain after entry):")
    print(f"Avg MAE: {avg_mae:.3f}% (If entry 0.05%, swings back {avg_mae:.3f}%)")
    print(f"Median MAE: {mae_50:.3f}%")
    print(f"90th % MAE: {mae_90:.3f}% (90% of trades draw down less than this)")
    
    # Plot MAE distribution
    plt.figure(figsize=(10, 6))
    plt.hist(np.array(res["mae_values"])*100, bins=50, color='salmon', edgecolor='black', alpha=0.7)
    plt.title("Max Adverse Excursion (Drawdown) Distribution after 0.05% Breakout")
    plt.xlabel("Drawdown % from Entry")
    plt.ylabel("Count")
    plt.axvline(avg_mae, color='red', linestyle='dashed', linewidth=1, label=f'Avg: {avg_mae:.3f}%')
    plt.legend()
    plt.savefig(OUTPUT_DIR / 'mae_distribution.png')
    print(f"\nMAE Plot saved to {OUTPUT_DIR}/mae_distribution.png")

if __name__ == "__main__":
    main()
