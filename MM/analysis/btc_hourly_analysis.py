"""
Polymarket Hourly BTC Market Data Reconstruction

This script fetches historical BTC data and labels each hourly candle
exactly as Polymarket would resolve their "Up or Down" markets.

Polymarket Rules:
- Strike = BTC price at the START of the hour (Open)
- Resolution = BTC price at the END of the hour (Close)
- YES wins (label=1) if Close > Strike
- NO wins (label=0) if Close <= Strike

Data source: Binance BTCUSDT 1-hour candles
"""

import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

# ============================================================================
# CONFIG
# ============================================================================

BINANCE_API = "https://api.binance.com/api/v3/klines"
SYMBOL = "BTCUSDT"
INTERVAL = "1h"  # Hourly candles
LOOKBACK_DAYS = 14  # 2 weeks

# Time zones (UTC offset for ET: -5 hours standard, -4 hours DST)
# For simplicity, we'll work in UTC and note that ET = UTC-5 (EST) or UTC-4 (EDT)
UTC = timezone.utc
ET_OFFSET = -5  # EST (adjust to -4 for EDT if needed)


def fetch_binance_klines(symbol: str, interval: str, start_time: int, end_time: int) -> list:
    """Fetch klines from Binance API."""
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_time,
        "endTime": end_time,
        "limit": 1000,
    }
    resp = requests.get(BINANCE_API, params=params)
    resp.raise_for_status()
    return resp.json()


def process_klines(raw_klines: list) -> pd.DataFrame:
    """
    Process raw Binance klines into a DataFrame.
    
    Binance kline format:
    [0] Open time (ms)
    [1] Open price
    [2] High price
    [3] Low price
    [4] Close price
    [5] Volume
    [6] Close time (ms)
    ...
    """
    data = []
    for k in raw_klines:
        open_time_ms = k[0]
        close_time_ms = k[6]
        
        # Convert to datetime (UTC)
        open_time_utc = datetime.fromtimestamp(open_time_ms / 1000, tz=UTC)
        close_time_utc = datetime.fromtimestamp(close_time_ms / 1000, tz=UTC)
        
        # Convert to ET (simple offset)
        open_time_et = open_time_utc + timedelta(hours=ET_OFFSET)
        
        open_price = float(k[1])
        close_price = float(k[4])
        high_price = float(k[2])
        low_price = float(k[3])
        
        # Polymarket resolution logic:
        # Strike = Open price (start of hour)
        # YES wins if Close > Strike (strictly greater)
        # NO wins if Close <= Strike
        strike = open_price
        label = 1 if close_price > strike else 0
        
        # Calculate move magnitude
        move_pct = ((close_price - open_price) / open_price) * 100
        
        data.append({
            "open_time_utc": open_time_utc,
            "open_time_et": open_time_et,
            "hour_et": open_time_et.strftime("%Y-%m-%d %H:00"),
            "weekday": open_time_et.strftime("%A"),
            "hour_of_day": open_time_et.hour,
            "strike": strike,
            "close": close_price,
            "high": high_price,
            "low": low_price,
            "move_pct": move_pct,
            "label": label,  # 1 = UP (YES wins), 0 = DOWN (NO wins)
            "result": "UP" if label == 1 else "DOWN",
        })
    
    return pd.DataFrame(data)


def analyze_data(df: pd.DataFrame) -> dict:
    """Run basic statistical analysis on the labeled data."""
    total = len(df)
    up_count = df["label"].sum()
    down_count = total - up_count
    
    # Overall stats
    up_rate = up_count / total * 100
    down_rate = down_count / total * 100
    
    # By hour of day
    hourly_stats = df.groupby("hour_of_day")["label"].agg(["sum", "count"])
    hourly_stats["up_rate"] = (hourly_stats["sum"] / hourly_stats["count"] * 100).round(1)
    
    # By weekday
    weekday_stats = df.groupby("weekday")["label"].agg(["sum", "count"])
    weekday_stats["up_rate"] = (weekday_stats["sum"] / weekday_stats["count"] * 100).round(1)
    
    # Move magnitude stats
    avg_move = df["move_pct"].abs().mean()
    max_up = df["move_pct"].max()
    max_down = df["move_pct"].min()
    
    # Streak analysis
    df["streak_change"] = df["label"].diff().ne(0).cumsum()
    streaks = df.groupby("streak_change")["label"].agg(["first", "count"])
    up_streaks = streaks[streaks["first"] == 1]["count"]
    down_streaks = streaks[streaks["first"] == 0]["count"]
    
    return {
        "total_hours": total,
        "up_count": up_count,
        "down_count": down_count,
        "up_rate": up_rate,
        "down_rate": down_rate,
        "avg_move_pct": avg_move,
        "max_up_pct": max_up,
        "max_down_pct": max_down,
        "hourly_stats": hourly_stats,
        "weekday_stats": weekday_stats,
        "max_up_streak": up_streaks.max() if len(up_streaks) > 0 else 0,
        "max_down_streak": down_streaks.max() if len(down_streaks) > 0 else 0,
        "avg_up_streak": up_streaks.mean() if len(up_streaks) > 0 else 0,
        "avg_down_streak": down_streaks.mean() if len(down_streaks) > 0 else 0,
    }


def main():
    print("=" * 60)
    print("Polymarket Hourly BTC Data Reconstruction")
    print("=" * 60)
    
    # Calculate time range (last 2 weeks)
    end_time = datetime.now(UTC)
    start_time = end_time - timedelta(days=LOOKBACK_DAYS)
    
    print(f"\nFetching data from {start_time.strftime('%Y-%m-%d %H:%M')} to {end_time.strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"Lookback: {LOOKBACK_DAYS} days (~{LOOKBACK_DAYS * 24} hourly candles)")
    
    # Fetch data
    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)
    
    raw_klines = fetch_binance_klines(SYMBOL, INTERVAL, start_ms, end_ms)
    print(f"Fetched {len(raw_klines)} candles from Binance")
    
    # Process into labeled DataFrame
    df = process_klines(raw_klines)
    
    # Save raw data
    csv_path = "btc_hourly_labeled.csv"
    df.to_csv(csv_path, index=False)
    print(f"\n✅ Saved labeled data to {csv_path}")
    
    # Run analysis
    stats = analyze_data(df)
    
    # Print results
    print("\n" + "=" * 60)
    print("ANALYSIS RESULTS")
    print("=" * 60)
    
    print(f"\n📊 Overall Statistics ({stats['total_hours']} hours)")
    print(f"   UP (YES wins):   {stats['up_count']} ({stats['up_rate']:.1f}%)")
    print(f"   DOWN (NO wins):  {stats['down_count']} ({stats['down_rate']:.1f}%)")
    print(f"\n   Avg Move:        {stats['avg_move_pct']:.2f}%")
    print(f"   Max Up:          +{stats['max_up_pct']:.2f}%")
    print(f"   Max Down:        {stats['max_down_pct']:.2f}%")
    
    print(f"\n📈 Streak Analysis")
    print(f"   Max UP streak:   {stats['max_up_streak']} hours")
    print(f"   Max DOWN streak: {stats['max_down_streak']} hours")
    print(f"   Avg UP streak:   {stats['avg_up_streak']:.1f} hours")
    print(f"   Avg DOWN streak: {stats['avg_down_streak']:.1f} hours")
    
    print(f"\n🕐 UP Rate by Hour of Day (ET)")
    for hour in sorted(stats["hourly_stats"].index):
        row = stats["hourly_stats"].loc[hour]
        bar = "█" * int(row["up_rate"] / 5)
        print(f"   {hour:02d}:00  {row['up_rate']:5.1f}% {bar}")
    
    print(f"\n📅 UP Rate by Weekday")
    weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    for day in weekday_order:
        if day in stats["weekday_stats"].index:
            row = stats["weekday_stats"].loc[day]
            bar = "█" * int(row["up_rate"] / 5)
            print(f"   {day:10s}  {row['up_rate']:5.1f}% {bar}")
    
    # Show last 10 candles
    print(f"\n🔍 Last 10 Hourly Candles")
    print("-" * 80)
    for _, row in df.tail(10).iterrows():
        print(f"   {row['hour_et']} ET | Strike: ${row['strike']:,.2f} | Close: ${row['close']:,.2f} | {row['result']:4s} | {row['move_pct']:+.2f}%")
    
    print("\n" + "=" * 60)
    print("Analysis complete!")
    print("=" * 60)
    
    return df, stats


if __name__ == "__main__":
    df, stats = main()
