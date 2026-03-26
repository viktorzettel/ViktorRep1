"""
Comprehensive BTC Market Analysis for Polymarket Hourly Markets

This script performs deep analysis on:
1. Market Opening Patterns (US, EU, Asia)
2. Intra-Hour Dynamics (minute-by-minute)
3. Early Signal Strength (predictive power of early moves)
4. Optimal Entry Timing
5. Volatility Analysis by Hour
6. Mean Reversion Patterns

Data: Binance BTCUSDT 1-minute candles for 14 days
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

# Time zones (UTC offsets)
UTC = timezone.utc
ET_OFFSET = -5  # EST

# Market Opening Times (in ET)
MARKET_SESSIONS = {
    "asia_open": 20,      # 8 PM ET = 9 AM Tokyo next day
    "asia_close": 4,      # 4 AM ET = 5 PM Tokyo
    "europe_open": 3,     # 3 AM ET = 8 AM London
    "europe_close": 11,   # 11 AM ET = 4 PM London
    "us_premarket": 4,    # 4 AM ET
    "us_open": 9,         # 9:30 AM ET (we use 9 for hourly)
    "us_close": 16,       # 4 PM ET
}


def fetch_binance_klines(symbol: str, interval: str, start_time: int, end_time: int) -> list:
    """Fetch klines from Binance API with pagination for large datasets."""
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
        current_start = klines[-1][6] + 1  # Close time + 1ms
        
        if len(klines) < 1000:
            break
    
    return all_klines


def process_minute_data(raw_klines: list) -> pd.DataFrame:
    """Process raw 1-minute klines into DataFrame."""
    data = []
    for k in raw_klines:
        open_time_ms = k[0]
        open_time_utc = datetime.fromtimestamp(open_time_ms / 1000, tz=UTC)
        open_time_et = open_time_utc + timedelta(hours=ET_OFFSET)
        
        data.append({
            "timestamp_utc": open_time_utc,
            "timestamp_et": open_time_et,
            "hour_et": open_time_et.hour,
            "minute": open_time_et.minute,
            "weekday": open_time_et.strftime("%A"),
            "date": open_time_et.strftime("%Y-%m-%d"),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })
    
    return pd.DataFrame(data)


def build_hourly_windows(df_minutes: pd.DataFrame) -> pd.DataFrame:
    """
    Build hourly windows with detailed intra-hour metrics.
    Each row represents one hour with strike, close, and minute-by-minute data.
    """
    # Group by date and hour
    df_minutes["hour_key"] = df_minutes["date"] + "_" + df_minutes["hour_et"].astype(str).str.zfill(2)
    
    hourly_data = []
    
    for hour_key, group in df_minutes.groupby("hour_key"):
        if len(group) < 55:  # Skip incomplete hours
            continue
            
        group = group.sort_values("minute")
        
        # Strike = Open at minute 0
        strike = group.iloc[0]["open"]
        
        # Close = Close at last minute (59)
        final_close = group.iloc[-1]["close"]
        
        # Polymarket resolution
        label = 1 if final_close > strike else 0
        
        # Intra-hour metrics at key checkpoints
        checkpoints = {}
        for minute in [1, 5, 10, 15, 20, 30, 45]:
            subset = group[group["minute"] <= minute]
            if len(subset) > 0:
                checkpoint_price = subset.iloc[-1]["close"]
                checkpoint_pct = ((checkpoint_price - strike) / strike) * 100
                checkpoints[f"pct_at_{minute}m"] = checkpoint_pct
                checkpoints[f"price_at_{minute}m"] = checkpoint_price
        
        # Volatility metrics
        high_in_hour = group["high"].max()
        low_in_hour = group["low"].min()
        range_pct = ((high_in_hour - low_in_hour) / strike) * 100
        
        # Volume
        total_volume = group["volume"].sum()
        
        # Did price cross strike during hour?
        crossed_above = (group["high"] > strike).any() and (group["low"] < strike).any()
        
        # Max drawdown from strike
        max_above = ((group["high"].max() - strike) / strike) * 100
        max_below = ((strike - group["low"].min()) / strike) * 100
        
        hourly_data.append({
            "hour_key": hour_key,
            "date": group.iloc[0]["date"],
            "hour_et": group.iloc[0]["hour_et"],
            "weekday": group.iloc[0]["weekday"],
            "strike": strike,
            "close": final_close,
            "move_pct": ((final_close - strike) / strike) * 100,
            "label": label,
            "result": "UP" if label == 1 else "DOWN",
            "high": high_in_hour,
            "low": low_in_hour,
            "range_pct": range_pct,
            "volume": total_volume,
            "crossed_strike": crossed_above,
            "max_above_pct": max_above,
            "max_below_pct": max_below,
            **checkpoints,
        })
    
    return pd.DataFrame(hourly_data)


def analyze_market_sessions(df: pd.DataFrame) -> dict:
    """Analyze UP/DOWN rates by market session."""
    results = {}
    
    # Pre-US (before 9 AM ET)
    pre_us = df[df["hour_et"] < 9]
    results["pre_us_market"] = {
        "hours": len(pre_us),
        "up_rate": (pre_us["label"].sum() / len(pre_us) * 100) if len(pre_us) > 0 else 0,
    }
    
    # US Market Hours (9 AM - 4 PM ET)
    us_hours = df[(df["hour_et"] >= 9) & (df["hour_et"] < 16)]
    results["us_market_hours"] = {
        "hours": len(us_hours),
        "up_rate": (us_hours["label"].sum() / len(us_hours) * 100) if len(us_hours) > 0 else 0,
    }
    
    # After US Close (4 PM - 8 PM ET)
    after_us = df[(df["hour_et"] >= 16) & (df["hour_et"] < 20)]
    results["after_us_close"] = {
        "hours": len(after_us),
        "up_rate": (after_us["label"].sum() / len(after_us) * 100) if len(after_us) > 0 else 0,
    }
    
    # Asia (8 PM - 4 AM ET)
    asia = df[(df["hour_et"] >= 20) | (df["hour_et"] < 4)]
    results["asia_session"] = {
        "hours": len(asia),
        "up_rate": (asia["label"].sum() / len(asia) * 100) if len(asia) > 0 else 0,
    }
    
    # Europe (3 AM - 11 AM ET)
    europe = df[(df["hour_et"] >= 3) & (df["hour_et"] < 11)]
    results["europe_session"] = {
        "hours": len(europe),
        "up_rate": (europe["label"].sum() / len(europe) * 100) if len(europe) > 0 else 0,
    }
    
    # US Open Hour specifically (9 AM)
    us_open_hour = df[df["hour_et"] == 9]
    results["us_open_hour_9am"] = {
        "hours": len(us_open_hour),
        "up_rate": (us_open_hour["label"].sum() / len(us_open_hour) * 100) if len(us_open_hour) > 0 else 0,
    }
    
    # Europe Open Hour (3 AM ET)
    eu_open_hour = df[df["hour_et"] == 3]
    results["europe_open_hour_3am"] = {
        "hours": len(eu_open_hour),
        "up_rate": (eu_open_hour["label"].sum() / len(eu_open_hour) * 100) if len(eu_open_hour) > 0 else 0,
    }
    
    return results


def analyze_early_signals(df: pd.DataFrame) -> dict:
    """
    Analyze predictive power of early price movements.
    Key question: If price is up X% at minute Y, what's the final outcome?
    """
    results = {}
    
    # Thresholds to test
    thresholds = [0.1, 0.2, 0.3, 0.5, 0.75, 1.0]
    checkpoints = [1, 5, 10, 15, 20, 30]
    
    for minute in checkpoints:
        col = f"pct_at_{minute}m"
        if col not in df.columns:
            continue
            
        minute_results = {}
        
        for thresh in thresholds:
            # If UP by threshold at this minute
            up_early = df[df[col] >= thresh]
            if len(up_early) > 0:
                up_finish = up_early["label"].sum() / len(up_early) * 100
            else:
                up_finish = None
            
            # If DOWN by threshold at this minute
            down_early = df[df[col] <= -thresh]
            if len(down_early) > 0:
                down_finish = (1 - down_early["label"].sum() / len(down_early)) * 100
            else:
                down_finish = None
            
            minute_results[f"up_{thresh}pct"] = {
                "count": len(up_early),
                "finish_up_pct": up_finish,
            }
            minute_results[f"down_{thresh}pct"] = {
                "count": len(down_early),
                "finish_down_pct": down_finish,
            }
        
        results[f"minute_{minute}"] = minute_results
    
    return results


def analyze_volatility(df: pd.DataFrame) -> dict:
    """Analyze volatility patterns by hour and identify high-volatility periods."""
    results = {}
    
    # Volatility by hour
    hourly_vol = df.groupby("hour_et")["range_pct"].agg(["mean", "std", "max"])
    hourly_vol.columns = ["avg_range", "std_range", "max_range"]
    results["by_hour"] = hourly_vol.to_dict()
    
    # Most volatile hours (top 5)
    most_volatile = hourly_vol.nlargest(5, "avg_range")
    results["most_volatile_hours"] = most_volatile.index.tolist()
    
    # Least volatile hours (bottom 5)
    least_volatile = hourly_vol.nsmallest(5, "avg_range")
    results["least_volatile_hours"] = least_volatile.index.tolist()
    
    # Volatility by weekday
    weekday_vol = df.groupby("weekday")["range_pct"].mean()
    results["by_weekday"] = weekday_vol.to_dict()
    
    # Correlation between volatility and outcome
    df["high_vol"] = df["range_pct"] > df["range_pct"].median()
    high_vol_up_rate = df[df["high_vol"]]["label"].mean() * 100
    low_vol_up_rate = df[~df["high_vol"]]["label"].mean() * 100
    results["high_vol_up_rate"] = high_vol_up_rate
    results["low_vol_up_rate"] = low_vol_up_rate
    
    return results


def analyze_mean_reversion(df: pd.DataFrame) -> dict:
    """
    Analyze mean reversion patterns:
    - After a big early move, does price tend to revert?
    - What's the optimal time to fade an early move?
    """
    results = {}
    
    # Define "big early move" as >0.5% at minute 10
    if "pct_at_10m" not in df.columns:
        return results
    
    big_move_up = df[df["pct_at_10m"] >= 0.5]
    big_move_down = df[df["pct_at_10m"] <= -0.5]
    
    # When up big at 10m, did it finish higher or lower than at 10m?
    if len(big_move_up) > 0:
        reverted = (big_move_up["move_pct"] < big_move_up["pct_at_10m"]).sum()
        reversion_rate_up = reverted / len(big_move_up) * 100
        results["up_big_at_10m"] = {
            "count": len(big_move_up),
            "reverted_pct": reversion_rate_up,
            "still_up_at_close": (big_move_up["label"].sum() / len(big_move_up) * 100),
        }
    
    if len(big_move_down) > 0:
        reverted = (big_move_down["move_pct"] > big_move_down["pct_at_10m"]).sum()
        reversion_rate_down = reverted / len(big_move_down) * 100
        results["down_big_at_10m"] = {
            "count": len(big_move_down),
            "reverted_pct": reversion_rate_down,
            "still_down_at_close": ((1 - big_move_down["label"].sum() / len(big_move_down)) * 100),
        }
    
    # Optimal fade entry: at which minute does reversion signal become strongest?
    for fade_minute in [15, 20, 30]:
        col = f"pct_at_{fade_minute}m"
        if col not in df.columns:
            continue
            
        big_up_at_min = df[df[col] >= 0.5]
        if len(big_up_at_min) > 0:
            # Did these eventually close DOWN?
            faded_to_down = big_up_at_min[big_up_at_min["label"] == 0]
            results[f"fade_signal_{fade_minute}m_up"] = {
                "big_up_count": len(big_up_at_min),
                "ended_down_pct": len(faded_to_down) / len(big_up_at_min) * 100,
            }
    
    return results


def analyze_optimal_entry(df: pd.DataFrame) -> dict:
    """
    Find optimal entry timing.
    Question: At which minute should we enter to maximize win rate?
    """
    results = {}
    
    # For each checkpoint, if we bought when UP at that point, what was win rate?
    # (This simulates momentum strategy)
    checkpoints = [1, 5, 10, 15, 20, 30]
    
    momentum_results = {}
    fade_results = {}
    
    for minute in checkpoints:
        col = f"pct_at_{minute}m"
        if col not in df.columns:
            continue
        
        # Momentum: Buy YES when UP at minute X
        up_at = df[df[col] > 0]
        if len(up_at) > 0:
            momentum_win = up_at["label"].sum() / len(up_at) * 100
            momentum_results[minute] = {
                "count": len(up_at),
                "win_rate": momentum_win,
            }
        
        # Fade: Buy NO when UP at minute X (contrarian)
        if len(up_at) > 0:
            fade_win = (1 - up_at["label"].sum() / len(up_at)) * 100
            fade_results[minute] = {
                "count": len(up_at),
                "win_rate": fade_win,
            }
    
    results["momentum_strategy"] = momentum_results
    results["fade_strategy"] = fade_results
    
    # Simple baseline: always buy YES
    baseline_up = df["label"].sum() / len(df) * 100
    results["baseline_yes_rate"] = baseline_up
    
    return results


def print_comprehensive_report(df: pd.DataFrame, 
                                session_results: dict,
                                early_signal_results: dict,
                                volatility_results: dict,
                                mean_reversion_results: dict,
                                entry_results: dict):
    """Print a comprehensive analysis report."""
    
    print("\n" + "=" * 80)
    print("🔬 COMPREHENSIVE BTC HOURLY MARKET ANALYSIS")
    print("=" * 80)
    print(f"\nData Period: {df['date'].min()} to {df['date'].max()}")
    print(f"Total Hours Analyzed: {len(df)}")
    
    # ========== MARKET SESSIONS ==========
    print("\n" + "-" * 80)
    print("📊 MARKET SESSION ANALYSIS")
    print("-" * 80)
    
    print("\n  Session                    | Hours | UP Rate |  Insight")
    print("  " + "-" * 70)
    
    session_order = [
        ("asia_session", "Asia (8PM-4AM ET)"),
        ("europe_session", "Europe (3AM-11AM ET)"),
        ("europe_open_hour_3am", "EU Open (3AM ET)"),
        ("pre_us_market", "Pre-US (<9AM ET)"),
        ("us_open_hour_9am", "US Open (9AM ET)"),
        ("us_market_hours", "US Market (9AM-4PM)"),
        ("after_us_close", "After US (4PM-8PM)"),
    ]
    
    for key, name in session_order:
        data = session_results.get(key, {})
        hours = data.get("hours", 0)
        rate = data.get("up_rate", 0)
        
        # Generate insight
        if rate > 55:
            insight = "✅ BULLISH"
        elif rate < 45:
            insight = "🔴 BEARISH"
        else:
            insight = "⚪ NEUTRAL"
        
        print(f"  {name:27s} | {hours:5d} | {rate:5.1f}%  |  {insight}")
    
    # ========== EARLY SIGNAL STRENGTH ==========
    print("\n" + "-" * 80)
    print("🎯 EARLY SIGNAL STRENGTH (Predictive Power)")
    print("-" * 80)
    print("\n  'If price is UP X% at minute Y, what % of the time does hour finish UP?'\n")
    
    for minute in [5, 10, 15, 20]:
        key = f"minute_{minute}"
        if key not in early_signal_results:
            continue
            
        data = early_signal_results[key]
        print(f"  At Minute {minute}:")
        
        for thresh in [0.2, 0.5, 1.0]:
            up_key = f"up_{thresh}pct"
            down_key = f"down_{thresh}pct"
            
            up_data = data.get(up_key, {})
            down_data = data.get(down_key, {})
            
            up_count = up_data.get("count", 0)
            up_finish = up_data.get("finish_up_pct")
            
            down_count = down_data.get("count", 0)
            down_finish = down_data.get("finish_down_pct")
            
            if up_count > 3:
                print(f"    +{thresh}% → Finishes UP {up_finish:.1f}% of time (n={up_count})")
            if down_count > 3:
                print(f"    -{thresh}% → Finishes DOWN {down_finish:.1f}% of time (n={down_count})")
        print()
    
    # ========== VOLATILITY ANALYSIS ==========
    print("-" * 80)
    print("📈 VOLATILITY ANALYSIS")
    print("-" * 80)
    
    print(f"\n  Most Volatile Hours (ET): {volatility_results.get('most_volatile_hours', [])}")
    print(f"  Least Volatile Hours (ET): {volatility_results.get('least_volatile_hours', [])}")
    
    print(f"\n  High Volatility Hours → UP Rate: {volatility_results.get('high_vol_up_rate', 0):.1f}%")
    print(f"  Low Volatility Hours → UP Rate: {volatility_results.get('low_vol_up_rate', 0):.1f}%")
    
    print("\n  Average Range by Hour (ET):")
    by_hour = volatility_results.get("by_hour", {}).get("avg_range", {})
    for hour in sorted(by_hour.keys()):
        rng = by_hour[hour]
        bar = "█" * int(rng * 20)
        print(f"    {hour:02d}:00  {rng:.2f}% {bar}")
    
    # ========== MEAN REVERSION ==========
    print("\n" + "-" * 80)
    print("🔄 MEAN REVERSION ANALYSIS")
    print("-" * 80)
    
    if "up_big_at_10m" in mean_reversion_results:
        data = mean_reversion_results["up_big_at_10m"]
        print(f"\n  When UP ≥0.5% at minute 10 (n={data['count']}):")
        print(f"    → Price reverted lower by close: {data['reverted_pct']:.1f}%")
        print(f"    → Still finished UP at close: {data['still_up_at_close']:.1f}%")
    
    if "down_big_at_10m" in mean_reversion_results:
        data = mean_reversion_results["down_big_at_10m"]
        print(f"\n  When DOWN ≥0.5% at minute 10 (n={data['count']}):")
        print(f"    → Price reverted higher by close: {data['reverted_pct']:.1f}%")
        print(f"    → Still finished DOWN at close: {data['still_down_at_close']:.1f}%")
    
    # Fade signals
    for minute in [15, 20, 30]:
        key = f"fade_signal_{minute}m_up"
        if key in mean_reversion_results:
            data = mean_reversion_results[key]
            print(f"\n  Fade Signal at Minute {minute}:")
            print(f"    When UP ≥0.5% at {minute}m, ended DOWN: {data['ended_down_pct']:.1f}% (n={data['big_up_count']})")
    
    # ========== OPTIMAL ENTRY ==========
    print("\n" + "-" * 80)
    print("⏱️  OPTIMAL ENTRY TIMING")
    print("-" * 80)
    
    print(f"\n  Baseline (always buy YES): {entry_results.get('baseline_yes_rate', 0):.1f}%")
    
    print("\n  Momentum Strategy (Buy YES when already UP at minute X):")
    mom = entry_results.get("momentum_strategy", {})
    for minute in sorted(mom.keys()):
        data = mom[minute]
        edge = data["win_rate"] - entry_results.get("baseline_yes_rate", 50)
        print(f"    Minute {minute:2d}: Win Rate = {data['win_rate']:.1f}% | Edge = {edge:+.1f}% | (n={data['count']})")
    
    print("\n  Fade Strategy (Buy NO when UP at minute X):")
    fade = entry_results.get("fade_strategy", {})
    for minute in sorted(fade.keys()):
        data = fade[minute]
        print(f"    Minute {minute:2d}: Win Rate = {data['win_rate']:.1f}% | (n={data['count']})")
    
    # ========== KEY INSIGHTS ==========
    print("\n" + "=" * 80)
    print("💡 KEY INSIGHTS & RECOMMENDATIONS")
    print("=" * 80)
    
    insights = []
    
    # Find best session
    best_session = max(session_results.items(), key=lambda x: x[1].get("up_rate", 0))
    worst_session = min(session_results.items(), key=lambda x: x[1].get("up_rate", 0))
    insights.append(f"Best session for YES: {best_session[0]} ({best_session[1]['up_rate']:.1f}% UP)")
    insights.append(f"Best session for NO: {worst_session[0]} ({100-worst_session[1]['up_rate']:.1f}% DOWN)")
    
    # Find strongest early signal
    for minute in [10, 15, 20]:
        key = f"minute_{minute}"
        if key in early_signal_results:
            data = early_signal_results[key]
            thresh_05 = data.get("up_0.5pct", {})
            if thresh_05.get("count", 0) > 5 and thresh_05.get("finish_up_pct"):
                if thresh_05["finish_up_pct"] > 70:
                    insights.append(f"STRONG SIGNAL: +0.5% at min {minute} → {thresh_05['finish_up_pct']:.0f}% finish UP")
                elif thresh_05["finish_up_pct"] < 40:
                    insights.append(f"FADE SIGNAL: +0.5% at min {minute} → Only {thresh_05['finish_up_pct']:.0f}% finish UP")
    
    print()
    for i, insight in enumerate(insights, 1):
        print(f"  {i}. {insight}")
    
    print("\n" + "=" * 80)


def main():
    print("=" * 80)
    print("🔬 Starting Comprehensive BTC Market Analysis")
    print("=" * 80)
    
    # Calculate time range
    end_time = datetime.now(UTC)
    start_time = end_time - timedelta(days=LOOKBACK_DAYS)
    
    print(f"\nFetching 1-minute data for {LOOKBACK_DAYS} days...")
    print(f"Expected: ~{LOOKBACK_DAYS * 24 * 60} minute candles")
    
    # Fetch minute-level data
    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)
    
    raw_klines = fetch_binance_klines(SYMBOL, "1m", start_ms, end_ms)
    print(f"Fetched {len(raw_klines)} minute candles")
    
    # Process into DataFrame
    df_minutes = process_minute_data(raw_klines)
    print(f"Processed minute data: {len(df_minutes)} rows")
    
    # Build hourly windows with intra-hour metrics
    print("Building hourly windows with intra-hour analysis...")
    df_hourly = build_hourly_windows(df_minutes)
    print(f"Built {len(df_hourly)} complete hourly windows")
    
    # Save data
    df_hourly.to_csv("btc_hourly_comprehensive.csv", index=False)
    print("✅ Saved to btc_hourly_comprehensive.csv")
    
    # Run analyses
    print("\nRunning analyses...")
    
    session_results = analyze_market_sessions(df_hourly)
    print("  ✓ Market session analysis")
    
    early_signal_results = analyze_early_signals(df_hourly)
    print("  ✓ Early signal strength analysis")
    
    volatility_results = analyze_volatility(df_hourly)
    print("  ✓ Volatility analysis")
    
    mean_reversion_results = analyze_mean_reversion(df_hourly)
    print("  ✓ Mean reversion analysis")
    
    entry_results = analyze_optimal_entry(df_hourly)
    print("  ✓ Optimal entry analysis")
    
    # Print comprehensive report
    print_comprehensive_report(
        df_hourly,
        session_results,
        early_signal_results,
        volatility_results,
        mean_reversion_results,
        entry_results
    )
    
    return df_hourly


if __name__ == "__main__":
    df = main()
