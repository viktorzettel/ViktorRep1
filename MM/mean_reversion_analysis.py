"""
Intra-Candle Mean Reversion Analysis

Tests the hypothesis: if BTC is X% above/below the strike at minute T,
does it tend to revert by candle close?

This is critical for evaluating the proposed strategy:
- Wait for token to drop below 40¢ (price moved away from strike)
- Buy cheap token expecting mean reversion
- Sell at entry + 4¢

Uses cached 1-minute data from btc_data_analysis.py
"""

import json
import time
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy import stats

DATA_DIR = Path(__file__).parent / "data"
CACHE_FILE = DATA_DIR / "btc_1min_30d.json"


def load_1min_data() -> list[dict]:
    """Load cached 1-minute data."""
    if not CACHE_FILE.exists():
        print("❌ Run btc_data_analysis.py first to download data")
        exit(1)
    
    with open(CACHE_FILE) as f:
        return json.load(f)


def build_15min_intracandle(candles_1m: list[dict]) -> list[dict]:
    """
    Build 15-minute candles with intra-candle snapshots.
    
    For each 15-min candle, record:
    - open price (minute 0 = strike)
    - price at each minute (1-14)
    - close price (minute 14)
    - whether candle closed UP or DOWN
    """
    candles = []
    
    for i in range(0, len(candles_1m) - 14, 15):
        block = candles_1m[i:i+15]
        
        open_price = block[0]["open"]
        close_price = block[-1]["close"]
        
        # Intra-candle prices at each minute
        minute_prices = [b["close"] for b in block]
        minute_returns = [(p - open_price) / open_price for p in minute_prices]
        
        candles.append({
            "timestamp": block[0]["timestamp"],
            "open": open_price,
            "close": close_price,
            "outcome": "up" if close_price > open_price else "down",
            "close_return": (close_price - open_price) / open_price,
            "minute_prices": minute_prices,
            "minute_returns": minute_returns,
        })
    
    return candles


def analyze_conditional_probabilities(candles: list[dict]):
    """
    Core analysis: Given price is X% from strike at minute T,
    what's P(close > strike)?
    """
    print("\n" + "="*70)
    print("🔬 INTRA-CANDLE CONDITIONAL PROBABILITY ANALYSIS")
    print("="*70)
    print("\nQuestion: If price is X% from strike at minute T,")
    print("what % of the time does the candle close ABOVE the strike?\n")
    
    # Check minutes: 5, 7, 10, 12 (common decision points)
    check_minutes = [3, 5, 7, 10, 12]
    
    # Return buckets (% from strike)
    buckets = [
        (-999, -0.50, "< -0.50%"),
        (-0.50, -0.30, "-0.50 to -0.30%"),
        (-0.30, -0.15, "-0.30 to -0.15%"),
        (-0.15, -0.05, "-0.15 to -0.05%"),
        (-0.05, 0.05, "-0.05 to +0.05%"),
        (0.05, 0.15, "+0.05 to +0.15%"),
        (0.15, 0.30, "+0.15 to +0.30%"),
        (0.30, 0.50, "+0.30 to +0.50%"),
        (0.50, 999, "> +0.50%"),
    ]
    
    for minute in check_minutes:
        print(f"\n{'─'*70}")
        print(f"AT MINUTE {minute} (of 15):")
        print(f"{'─'*70}")
        print(f"{'Mid-candle return':>22} {'N':>6} {'Close UP':>10} {'Close DOWN':>12} {'Continues':>10}")
        
        for lo, hi, label in buckets:
            # Find candles where price at this minute is in this bucket
            matching = []
            for c in candles:
                if minute < len(c["minute_returns"]):
                    mid_return = c["minute_returns"][minute] * 100  # to percent
                    if lo <= mid_return < hi:
                        matching.append(c)
            
            if len(matching) < 10:
                continue
            
            n = len(matching)
            n_up = sum(1 for c in matching if c["outcome"] == "up")
            pct_up = n_up / n * 100
            pct_down = 100 - pct_up
            
            # "Continues" = if mid > 0, did it close up? If mid < 0, close down?
            mid_center = (lo + hi) / 2
            if mid_center > 0:
                continues = f"{pct_up:.0f}%"
            elif mid_center < 0:
                continues = f"{pct_down:.0f}%"
            else:
                continues = "—"
            
            print(f"  {label:>20} {n:>6} {pct_up:>8.1f}%  {pct_down:>10.1f}%  {continues:>10}")


def analyze_reversion_for_strategy(candles: list[dict]):
    """
    Directly test the proposed strategy:
    - Token drops below 40¢ → means one direction is dominant
    - Buy cheap token → expect reversion
    - Sell at entry + 4¢
    
    Token at 40¢ ≈ market thinks P(outcome) = 40%
    For YES token at 40¢: BTC is below strike, market says 40% chance of closing UP
    
    We simulate: at each minute, if mid-candle return implies P(up) ≈ 40%,
    does price ever revert enough for the token to reach 44¢ before expiry?
    """
    print("\n" + "="*70)
    print("💰 STRATEGY SIMULATION: Mean Reversion Token Trading")
    print("="*70)
    print("\nScenario: YES token drops to ~40¢ (BTC is below strike)")
    print("Strategy: Buy YES at 40¢, sell at 44¢ (10% return)")
    print("Risk: Token goes to 0¢ at expiry if BTC stays below\n")
    
    # For each candle, at each minute, check:
    # 1. Is current return negative enough that YES ≈ 40¢?
    # 2. After this point, does price ever come back close enough to strike
    #    that YES would reach 44¢+?
    # 3. Does the candle ultimately close UP or DOWN?
    
    # Token price ≈ probability. YES at 40¢ means P(up) ≈ 40%.
    # For a rough mapping: if current return = -X% with Y minutes remaining,
    # we need to estimate what token prices look like.
    
    # Simpler approach: just check mid-candle return buckets and whether
    # the return got closer to zero (reversion) at any point after
    
    print("Testing: When BTC is X% BELOW strike at minute T,")
    print("does it come back closer to strike before candle close?\n")
    
    check_minutes = [3, 5, 7, 10]
    thresholds = [
        (-0.15, "0.15% below"),
        (-0.20, "0.20% below"),
        (-0.30, "0.30% below"),
        (-0.50, "0.50% below"),
    ]
    
    for minute in check_minutes:
        print(f"\n{'─'*70}")
        print(f"ENTRY AT MINUTE {minute}:")
        print(f"{'─'*70}")
        
        for threshold, label in thresholds:
            trades = []
            
            for c in candles:
                if minute >= len(c["minute_returns"]):
                    continue
                
                mid_return = c["minute_returns"][minute]
                
                # Check if below threshold (BTC is below strike)
                if mid_return > threshold or mid_return < threshold * 2:
                    continue
                
                entry_return = mid_return
                
                # Track what happens after entry
                max_reversion = 0  # How much did it come back toward zero?
                final_return = c["close_return"]
                
                for future_min in range(minute + 1, 15):
                    if future_min < len(c["minute_returns"]):
                        future_return = c["minute_returns"][future_min]
                        # Reversion = moved closer to zero
                        reversion = abs(entry_return) - abs(future_return)
                        max_reversion = max(max_reversion, reversion)
                
                # Did it ever revert by at least 0.05% (≈ 4¢ on token)?
                reverted_4ct = max_reversion > 0.0005  # 0.05% ≈ ~$35
                reverted_any = max_reversion > 0
                
                # Did the candle close UP (winning for YES buyer)?
                closed_up = c["outcome"] == "up"
                
                trades.append({
                    "entry_return": entry_return,
                    "final_return": final_return,
                    "max_reversion": max_reversion,
                    "reverted_4ct": reverted_4ct,
                    "reverted_any": reverted_any,
                    "closed_up": closed_up,
                })
            
            if len(trades) < 5:
                continue
            
            n = len(trades)
            pct_revert_any = sum(1 for t in trades if t["reverted_any"]) / n * 100
            pct_revert_4ct = sum(1 for t in trades if t["reverted_4ct"]) / n * 100
            pct_closed_up = sum(1 for t in trades if t["closed_up"]) / n * 100
            avg_max_rev = np.mean([t["max_reversion"] * 100 for t in trades])
            
            # P/L simulation: buy YES at 40¢
            # Win if revert enough (sell at 44¢): +4¢
            # Lose if candle closes DOWN (token → 0): -40¢
            wins = sum(1 for t in trades if t["reverted_4ct"])
            losses_at_expiry = sum(1 for t in trades if not t["reverted_4ct"] and not t["closed_up"])
            partial_wins = sum(1 for t in trades if not t["reverted_4ct"] and t["closed_up"])
            
            profit = wins * 4 - losses_at_expiry * 40 + partial_wins * 60  # token → $1 if up
            avg_profit_per_trade = profit / n
            
            print(f"\n  BTC {label} strike ({n} trades):")
            print(f"    Reverts at all:     {pct_revert_any:.0f}%")
            print(f"    Reverts ≥0.05%:     {pct_revert_4ct:.0f}%")
            print(f"    Avg max reversion:  {avg_max_rev:.3f}%")
            print(f"    Closes UP:          {pct_closed_up:.0f}%")
            print(f"    Estimated P/L per trade: {avg_profit_per_trade:+.1f}¢")


def plot_reversion_curves(candles: list[dict], output_dir: Path):
    """
    Plot average price path after BTC moves X% from strike.
    Shows whether reversion happens and how strong it is.
    """
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    fig.suptitle("Intra-Candle Price Behavior After Moving From Strike", fontsize=14, fontweight='bold')
    
    check_minute = 5  # Check at minute 5
    
    # Group candles by their return at minute 5
    groups = {
        "below_-0.20%": [],
        "-0.20% to -0.10%": [],
        "-0.10% to 0%": [],
        "0% to +0.10%": [],
        "+0.10% to +0.20%": [],
        "above_+0.20%": [],
    }
    
    for c in candles:
        if check_minute >= len(c["minute_returns"]):
            continue
        mid_ret = c["minute_returns"][check_minute] * 100
        
        if mid_ret < -0.20:
            groups["below_-0.20%"].append(c)
        elif mid_ret < -0.10:
            groups["-0.20% to -0.10%"].append(c)
        elif mid_ret < 0:
            groups["-0.10% to 0%"].append(c)
        elif mid_ret < 0.10:
            groups["0% to +0.10%"].append(c)
        elif mid_ret < 0.20:
            groups["+0.10% to +0.20%"].append(c)
        else:
            groups["above_+0.20%"].append(c)
    
    # Plot 1: Average return paths for DOWN moves
    ax = axes[0]
    ax.set_title(f"After BTC is BELOW strike at minute {check_minute}")
    colors_down = ['#c0392b', '#e74c3c', '#f39c12']
    
    for (label, group), color in zip(
        [("below_-0.20%", groups["below_-0.20%"]),
         ("-0.20% to -0.10%", groups["-0.20% to -0.10%"]),
         ("-0.10% to 0%", groups["-0.10% to 0%"])],
        colors_down
    ):
        if len(group) < 10:
            continue
        
        paths = np.array([c["minute_returns"] for c in group]) * 100
        avg_path = np.mean(paths, axis=0)
        
        ax.plot(range(15), avg_path, linewidth=2, label=f"{label} (n={len(group)})", color=color)
    
    ax.axhline(0, color='gray', linestyle='--', alpha=0.5)
    ax.axvline(check_minute, color='blue', linestyle=':', alpha=0.3, label=f'Check point (min {check_minute})')
    ax.set_xlabel("Minute within 15-min candle")
    ax.set_ylabel("Return from open (%)")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    
    # Plot 2: Average return paths for UP moves
    ax = axes[1]
    ax.set_title(f"After BTC is ABOVE strike at minute {check_minute}")
    colors_up = ['#27ae60', '#2ecc71', '#f39c12']
    
    for (label, group), color in zip(
        [("above_+0.20%", groups["above_+0.20%"]),
         ("+0.10% to +0.20%", groups["+0.10% to +0.20%"]),
         ("0% to +0.10%", groups["0% to +0.10%"])],
        colors_up
    ):
        if len(group) < 10:
            continue
        
        paths = np.array([c["minute_returns"] for c in group]) * 100
        avg_path = np.mean(paths, axis=0)
        
        ax.plot(range(15), avg_path, linewidth=2, label=f"{label} (n={len(group)})", color=color)
    
    ax.axhline(0, color='gray', linestyle='--', alpha=0.5)
    ax.axvline(check_minute, color='blue', linestyle=':', alpha=0.3, label=f'Check point (min {check_minute})')
    ax.set_xlabel("Minute within 15-min candle")
    ax.set_ylabel("Return from open (%)")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    
    plot_path = output_dir / "mean_reversion_analysis.png"
    plt.savefig(plot_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"\n📊 Reversion plot saved to {plot_path}")
    return plot_path


def main():
    output_dir = DATA_DIR / "analysis"
    output_dir.mkdir(exist_ok=True)
    
    # Load data
    print("📂 Loading cached 1-minute data...")
    candles_1m = load_1min_data()
    print(f"   {len(candles_1m):,} one-minute candles loaded")
    
    # Build 15-min candles with intra-candle data
    candles = build_15min_intracandle(candles_1m)
    print(f"   {len(candles)} fifteen-minute candles built")
    
    # Analysis 1: Conditional probabilities
    analyze_conditional_probabilities(candles)
    
    # Analysis 2: Strategy simulation
    analyze_reversion_for_strategy(candles)
    
    # Analysis 3: Reversion plots
    plot_reversion_curves(candles, output_dir)
    
    print("\n" + "="*70)
    print("✅ ANALYSIS COMPLETE")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
