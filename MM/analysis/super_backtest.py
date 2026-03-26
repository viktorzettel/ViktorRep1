import asyncio
import aiohttp
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import super_logic  # Our new library

async def fetch_candles(limit=1440*5): # 5 days of minutes
    # We need a LOT of 1m data to have history for each hour
    # Batch fetch
    url = "https://api.binance.com/api/v3/klines"
    all_candles = []
    
    # Fetch in chunks. Start from 5 days ago.
    start_ts = int((datetime.now() - timedelta(days=5)).timestamp() * 1000)
    end_ts = int(datetime.now().timestamp() * 1000)
    
    curr = start_ts
    while curr < end_ts:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
             params = {"symbol": "BTCUSDT", "interval": "1m", "startTime": curr, "limit": 1000}
             async with session.get(url, params=params) as resp:
                 batch = await resp.json()
                 if not batch: break
                 all_candles.extend(batch)
                 curr = batch[-1][6] + 1
                 
    df = pd.DataFrame(all_candles, columns=[
                "open_time", "open", "high", "low", "close", "vol", 
                "close_time", "q_vol", "trades", "taker_buy_vol", "taker_buy_q_vol", "ignore"
            ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close"] = df["close"].astype(float)
    return df

async def run_backtest():
    print("⏳ Fetching deep history (5 Days 1m candles)...")
    df = await fetch_candles()
    df = df.sort_values("open_time").reset_index(drop=True)
    
    print(f"✅ Loaded {len(df)} minute candles.")
    
    # Identify Hourly Events
    # Every top of the hour (XX:00) is a "Event Start".
    # We predict at XX:05.
    
    # Creates list of event indices
    # We need at least 60 mins of history before the event.
    
    results = []
    
    # Iterate through the DataFrame looking for xx:05 timestamps
    # This represents the "Prediction Time" (5 mins into the hour)
    
    total_bets = 0
    correct_bets = 0
    skipped = 0
    
    print("\n🚀 Running Super Backtest...")
    
    # Step through time 
    # We look for timestamps where minute == 5
    prediction_points = df[df["open_time"].dt.minute == 5]
    
    for idx, row in prediction_points.iterrows():
        # This is 05 mins after the hour.
        # Event Start was 5 mins ago.
        # Event End is 55 mins from now.
        
        pred_time = row["open_time"]
        event_start = pred_time - timedelta(minutes=5)
        event_end = pred_time + timedelta(minutes=55)
        
        # 1. Get History (Last 60 mins strictly BEFORE pred_time)
        # We use data up to row (inclusive) for RSI/Vol calcs?
        # Yes, at minute 5 we have the close of minute 5. 
        # So we take last 60 rows ending at idx.
        
        if idx < 60: continue
        
        history_slice = df.iloc[idx-60 : idx+1].copy() # 60 mins context
        
        current_price = row["close"]
        
        # Find the "Strike" (Open Price of the hour)
        # We need to find the candle at event_start.
        # Or simpler: just find the row in df where time = event_start
        strike_row = df[df["open_time"] == event_start]
        if len(strike_row) == 0: continue
        strike_price = float(strike_row.iloc[0]["open"])
        
        # Find the "Outcome" (Close Price at event_end)
        outcome_row = df[df["open_time"] == event_end - timedelta(minutes=1)] # The 59th minute candle
        if len(outcome_row) == 0: continue # Data ends before event resolved
        final_price = float(outcome_row.iloc[0]["close"])
        
        did_win_up = final_price > strike_price
        
        # 2. RUN SUPER PREDICTOR
        time_left = 55 # minutes
        
        result = super_logic.get_super_prediction(
            current_price, strike_price, strike_price, time_left, history_slice
        )
        
        if result["signal"] == "SKIP":
            skipped += 1
            continue
            
        prediction_up = (result["signal"] == "UP")
        is_correct = (prediction_up == did_win_up)
        
        if is_correct: correct_bets += 1
        total_bets += 1
        
        # print(f"{pred_time} | Signal: {result['signal']} | Correct: {is_correct}")
        
    print("-" * 80)
    print(f"Total Bets: {total_bets} (Skipped: {skipped})")
    if total_bets > 0:
        print(f"Accuracy:   {correct_bets/total_bets*100:.2f}%")
        print(f"Brier:      N/A (Signal based)")
    else:
        print("No bets taken.")

if __name__ == "__main__":
    asyncio.run(run_backtest())
