import asyncio
import aiohttp
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import super_logic

# Reuse fetching logic or import it? Let's just copy the fetcher for standalone execution.
async def fetch_candles(limit=1440*5):
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
    print("⏳ Fetching deep history (5 Days)...")
    df = await fetch_candles()
    df = df.sort_values("open_time").reset_index(drop=True)
    print(f"✅ Loaded {len(df)} minute candles.")
    
    total_bets = 0
    correct_bets = 0
    
    print("\n🎲 Running Monte Carlo Backtest...")
    
    # Identify Prediction Points (Minute 5 of each hour)
    prediction_points = df[df["open_time"].dt.minute == 5]
    
    for idx, row in prediction_points.iterrows():
        pred_time = row["open_time"]
        event_start = pred_time - timedelta(minutes=5)
        event_end = pred_time + timedelta(minutes=55)
        
        # Need 60 mins context
        if idx < 60: continue
        history_slice = df.iloc[idx-60 : idx+1].copy()
        
        current_price = row["close"]
        
        # Find Strike (Open of hour)
        strike_row = df[df["open_time"] == event_start]
        if len(strike_row) == 0: continue
        strike_price = float(strike_row.iloc[0]["open"])
        
        # Find Outcome (Close of hour)
        outcome_row = df[df["open_time"] == event_end - timedelta(minutes=1)] 
        if len(outcome_row) == 0: continue
        final_price = float(outcome_row.iloc[0]["close"])
        did_win_up = final_price > strike_price
        
        # --- MONTE CARLO LOGIC ---
        # 1. Get Regime (Drift/Vol) from last 60 mins
        regime = super_logic.get_market_regime(history_slice)
        
        # 2. Run Sim
        mc_prob = super_logic.monte_carlo_simulation(
            current_price,
            strike_price,
            55, # time left
            regime['vol_min'],
            regime['drift_min'],
            num_sims=5000
        )
        
        # 3. Predict
        prediction_up = (mc_prob > 0.5)
        is_correct = (prediction_up == did_win_up)
        
        if is_correct: correct_bets += 1
        total_bets += 1
        
        # print(f"MC Prob: {mc_prob:.2f} | Result: {did_win_up} | Correct: {is_correct}")

    print("-" * 80)
    print(f"Total Bets: {total_bets}")
    if total_bets > 0:
        print(f"Accuracy:   {correct_bets/total_bets*100:.2f}%")
        
        # Is it signficant?
        import scipy.stats
        p_val = scipy.stats.binomtest(correct_bets, total_bets, 0.5, alternative='greater').pvalue
        print(f"P-Value:    {p_val:.4f}")
        
        if p_val < 0.05:
            print("✅ PASSED: Statistically Significant Alpha.")
        else:
            print("❌ FAILED: Indistinguishable from Random.")

if __name__ == "__main__":
    asyncio.run(run_backtest())
