import asyncio
import json
import math
import aiohttp
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# =============================================================================
# PRICING ENGINE (Simplified from pricing.py)
# =============================================================================

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def calculate_fair_prob(current_price, open_price, time_left_sec, vol):
    if time_left_sec <= 0:
        return 1.0 if current_price > open_price else 0.0
    if vol <= 0: vol = 0.5

    T_years = time_left_sec / (365 * 24 * 3600)
    S = current_price
    K = open_price
    sigma = vol

    numerator = math.log(S / K) - (0.5 * sigma**2 * T_years)
    denominator = sigma * math.sqrt(T_years)
    d2 = numerator / denominator
    
    return norm_cdf(d2)

# =============================================================================
# DATA FETCHING
# =============================================================================

async def fetch_candles(symbol, interval, limit=1000):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        async with session.get(url, params=params) as resp:
            data = await resp.json()
            # Format: [Open Time, Open, High, Low, Close, Vol, Close Time, ...]
            df = pd.DataFrame(data, columns=[
                "open_time", "open", "high", "low", "close", "vol", 
                "close_time", "q_vol", "trades", "taker_buy_vol", "taker_buy_q_vol", "ignore"
            ])
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
            df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
            df["open"] = df["open"].astype(float)
            df["close"] = df["close"].astype(float)
            df["high"] = df["high"].astype(float)
            df["low"] = df["low"].astype(float)
            return df

# =============================================================================
# SIMULATION ENGINE
# =============================================================================

async def run_simulation():
    print("⏳ Fetching historical data (last ~40 days)...")
    
    # 1. Fetch Hourly Candles (Events)
    # 1000 hours ~= 41 days
    df_1h = await fetch_candles("BTCUSDT", "1h", limit=1000)
    
    # 2. Fetch 1m Candles (Intra-hour price action)
    # Note: Binance limit 1000 mins is only ~16 hours. 
    # For a proper backtest we need to batch fetch or just test recent history.
    # Let's test the LAST 120 HOURS (5 Days) to reach >100 samples for significance.
    # We will fetch 1m candles for the time range covered by the last 120 1h candles.
    
    last_120h = df_1h.tail(120)
    start_time = int(last_120h.iloc[0]["open_time"].timestamp() * 1000)
    end_time = int(last_120h.iloc[-1]["close_time"].timestamp() * 1000)
    
    print(f"📊 Backtesting Period: {last_120h.iloc[0]['open_time']} to {last_120h.iloc[-1]['close_time']} (~{len(last_120h)} samples)")
    
    # Fetch 1m candles in batches
    all_1m = []
    curr_start = start_time
    while curr_start < end_time:
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": "BTCUSDT", 
            "interval": "1m", 
            "startTime": curr_start, 
            "limit": 1000
        }
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(url, params=params) as resp:
                batch = await resp.json()
                if not batch: break
                all_1m.extend(batch)
                curr_start = batch[-1][6] + 1 # Close time + 1ms
                
    df_1m = pd.DataFrame(all_1m, columns=[
                "open_time", "open", "high", "low", "close", "vol", 
                "close_time", "q_vol", "trades", "taker_buy_vol", "taker_buy_q_vol", "ignore"
            ])
    df_1m["open_time"] = pd.to_datetime(df_1m["open_time"], unit="ms")
    df_1m["close"] = df_1m["close"].astype(float)
    
    # =========================================================================
    # REPLAY LOOP
    # =========================================================================
    
    results = []
    
    # print("\n🚀 Running Simulation...")
    # print("-" * 80)
    # print(f"{'Time':<20} | {'Open':<8} | {'Outcome':<8} | {'Avg Prob PREDICT':<10} | {'Brier':<8} | {'Correct?'}")
    # print("-" * 80)
    
    correct_bets = 0
    total_bets = 0
    total_brier = 0.0
    
    # Iterate through each Hour (Event)
    for idx, row in last_120h.iterrows():
        event_open_time = row["open_time"]
        event_close_time = row["close_time"]
        strike_price = row["open"]
        final_price = row["close"]
        
        did_yes_win = 1.0 if final_price > strike_price else 0.0
        
        # Filter 1m candles for this hour
        intra_candles = df_1m[(df_1m["open_time"] >= event_open_time) & (df_1m["open_time"] < event_close_time)]
        
        if len(intra_candles) == 0: continue
        
        # =====================================================================
        # CRITICAL CHANGE: ONLY LOOK AT MINUTE 5
        # =====================================================================
        # We want to see if the model knew the answer EARLY.
        # Let's take the candle that starts 5 minutes after open.
        
        # Calculate minutes since open for each candle
        # Select candles roughly between min 0 and min 10 (Early Phase)
        # Note: 1m candles have open_time. 
        # Candle 0: Open at XX:00. Close at XX:01.
        # Candle 9: Open at XX:09. Close at XX:10. 
        # We want to use price information available at ~XX:05 - XX:10.
        # Filter for candles where (open_time - event_open) is between 0 and 600 seconds (10 mins)
        
        intra_candles = intra_candles.copy()
        
        # Ensure we are comparing same timezones (both should be usually tz-naive from binance API if parsed that way)
        # Check alignment
        
        delta_seconds = (intra_candles["open_time"] - event_open_time).dt.total_seconds()
        early_candles = intra_candles[(delta_seconds >= 0) & (delta_seconds < 600)] # First 10 mins
        
        if len(early_candles) == 0:
             # logger.warning(f"No early data for event starting {event_open_time}")
             continue
             
        # Calculate Model Probabilities for this EARLY phase
        probs = []
        
        for midx, mrow in early_candles.iterrows():
            current_price = mrow["close"]
            # Time left in seconds
            time_left = (event_close_time - mrow["open_time"]).total_seconds()
            
            # Simple Realized Vol (Using standard 50% for now or estimating?)
            # Let's estimate from recent 10 mins of that hour if possible, else 50%
            vol = 0.5 
            
            prob = calculate_fair_prob(current_price, strike_price, time_left, vol)
            probs.append(prob)
            
        # Average probability the model assigned to the WINNING outcome in the FIRST 10 MINS
        avg_prob = np.mean(probs)
        
        # Brier Score: (Prob - Outcome)^2
        # Lower is better. 0 = Perfect, 0.25 = Random guessing (0.5)
        brier = (avg_prob - did_yes_win) ** 2
        total_brier += brier
        
        # Prediction Accuracy (Did mean prob align with outcome?)
        prediction = 1.0 if avg_prob > 0.5 else 0.0
        is_correct = (prediction == did_yes_win)
        
        if is_correct: correct_bets += 1
        total_bets += 1
        
        outcome_str = "UP" if did_yes_win else "DOWN"
        correct_str = "✅" if is_correct else "❌"
        
        # print(f"{event_open_time} | {strike_price:<8.2f} | {outcome_str:<8} | {avg_prob:.4f}     | {brier:.4f}   | {correct_str}")
        
    print("-" * 80)
    print(f"Total Hours Tested: {total_bets}")
    print(f"Model Accuracy:     {correct_bets/total_bets*100:.1f}%")
    print(f"Avg Brier Score:    {total_brier/total_bets:.4f} (Lower is better, <0.25 is skill)")
    
    if (correct_bets/total_bets) > 0.55:
        print("\n✅ PASSED: Model shows potential alpha (>55% accuracy).")
    else:
        print("\n❌ FAILED: Model is indistinguishable from random guessing.")
        
    # Write summary to a markdown file
    with open("backtest_results_summary.md", "w") as f:
        f.write("# Backtest Results Summary\n\n")
        f.write(f"- Total Hours Tested: {total_bets}\n")
        f.write(f"- Model Accuracy:     {correct_bets/total_bets*100:.1f}%\n")
        f.write(f"- Avg Brier Score:    {total_brier/total_bets:.4f}\n")
        f.write("\n")
        if (correct_bets/total_bets) > 0.55:
            f.write("✅ PASSED: Model shows potential alpha (>55% accuracy).\n")
        else:
            f.write("❌ FAILED: Model is indistinguishable from random guessing.\n")

if __name__ == "__main__":
    asyncio.run(run_simulation())
