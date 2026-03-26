import asyncio
import aiohttp
import numpy as np
import pandas as pd
import sys
import math

# =============================================================================
# MONTE CARLO ENGINE
# =============================================================================

def run_monte_carlo(current_price, strike, time_left_mins, history_df, num_simulations=10000):
    """
    Simulate future price paths using Geometric Brownian Motion parameters 
    derived from recent history.
    """
    # 1. Calculate History Stats (Log Returns)
    history_df['close'] = history_df['close'].astype(float)
    history_df['log_ret'] = np.log(history_df['close'] / history_df['close'].shift(1))
    
    # Drop first NaN
    returns = history_df['log_ret'].dropna()
    
    # Calculate Drift and Volatility (per minute)
    # We use per-minute because our simulation steps will be minutes (or seconds?)
    # Let's do minute steps for simplicity, or second steps for precision. 
    # Valid approximation: Minute steps is fine for <15 min outlook.
    
    mu_minute = returns.mean() 
    sigma_minute = returns.std()
    
    # If volatility is suspiciously low (flat market), force a minimum
    if sigma_minute == 0: sigma_minute = 0.0001
    
    print(f"   📊 Local Volatility (1m): {sigma_minute*100:.4f}%")
    print(f"   📊 Local Drift (1m):      {mu_minute*100:.5f}%")
    
    # 2. Setup Simulation
    # Time steps: 1 step per minute? 
    # Or 1 step per second? For 10 mins, 600 steps is fine.
    # Let's do 1 second steps for smoother granularity if time_left is small.
    
    dt = 1/60 # 1 second in minute units? No.
    # Let's standardize on MINUTES.
    # Time T = time_left_mins
    # Steps = time_left_mins (if 1 step/min)
    # Better: 60 steps per minute (Seconds).
    
    steps_per_min = 60
    total_steps = int(time_left_mins * steps_per_min)
    dt = 1 / steps_per_min # Delta T in minutes
    
    # Adjust mu/sigma to dt units?
    # GBM: dS = S * (mu*dt + sigma*epsilon*sqrt(dt))
    # Standard formula uses mu/sigma as annualized usually.
    # Here mu_minute/sigma_minute are per minute.
    # So for step of size 'dt' (fractions of minute):
    # drift_step = mu_minute * dt? 
    # vol_step = sigma_minute * sqrt(dt)
    
    # Simulation Matrix: [Simulations, Steps]
    # We only care about the FINAL price, but GBM is path dependent if we had barriers.
    # Here we just care about S_T.
    # S_T = S_0 * exp( (mu - 0.5*sigma^2)*T + sigma*sqrt(T)*Z )
    # We can jump straight to T if we assume checking only at expiry!
    # Much faster.
    
    # Projected Horizon T in minutes
    T = time_left_mins
    
    # Random Shock Z ~ N(0, 1) for each simulation
    Z = np.random.normal(0, 1, num_simulations)
    
    # S_T formula using 1-minute drift/vol paramters:
    # S_T = S_0 * exp( (mu_min - 0.5*sigma_min^2)*T + sigma_min*sqrt(T)*Z )
    
    drift_term = (mu_minute - 0.5 * sigma_minute**2) * T
    shock_term = sigma_minute * np.sqrt(T) * Z
    
    simulated_prices = current_price * np.exp(drift_term + shock_term)
    
    # 3. Analyze Results
    wins = np.sum(simulated_prices > strike)
    prob_up = wins / num_simulations
    
    return prob_up, simulated_prices.mean(), np.percentile(simulated_prices, [5, 95])


# =============================================================================
# DATA FETCHING
# =============================================================================

async def fetch_data():
    url = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=60"
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        async with session.get(url) as resp:
            data = await resp.json()
            df = pd.DataFrame(data, columns=[
                "open_time", "open", "high", "low", "close", "vol", 
                "close_time", "q_vol", "trades", "taker_buy_vol", "taker_buy_q_vol", "ignore"
            ])
            return df

async def get_current_price():
    url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        async with session.get(url) as resp:
             data = await resp.json()
             return float(data['price'])

# =============================================================================
# MAIN
# =============================================================================

async def main():
    print("\n🎲 MONTE CARLO PREDICTOR 🎲")
    print("-------------------------------")
    
    try:
        # Args
        if len(sys.argv) == 3:
            strike = float(sys.argv[1])
            time_left = float(sys.argv[2])
        else:
            s_input = input("🎯 Enter Strike: ").replace("$","").replace(",","")
            strike = float(s_input)
            t_input = input("⏰ Enter Time Left (mins): ")
            time_left = float(t_input)

        # 1. Fetch Data
        print("⏳ Fetching last 60m history & live price...")
        history = await fetch_data()
        current_price = await get_current_price()
        
        print(f"✅ Current Price: ${current_price:,.2f}")
        print(f"📜 History: {len(history)} candles used for Volatility/Drift")
        
        # 2. Run Simulation
        print(f"🎰 Simulating 100,000 paths for {time_left} mins...")
        prob_up, mean_price, conf_interval = run_monte_carlo(
            current_price, strike, time_left, history, num_simulations=100000
        )
        
        prob_down = 1.0 - prob_up
        
        # 3. Output
        print("\n📊 MONTE CARLO RESULTS")
        print("-----------------------")
        print(f"Gap to Strike: ${current_price - strike:,.2f}")
        print(f"Projected Mean: ${mean_price:,.2f}")
        print(f"90% Conf Range: ${conf_interval[0]:,.2f} - ${conf_interval[1]:,.2f}")
        print("-----------------------")
        print(f"📈 PROB UP:   {prob_up*100:.2f}%")
        print(f"📉 PROB DOWN: {prob_down*100:.2f}%")
        
        if prob_up > 0.60:
            print("\n🟢 SIGNAL: BET UP (YES)")
        elif prob_down > 0.60:
            print("\n🔴 SIGNAL: BET DOWN (NO)")
        else:
            print("\n🟡 SIGNAL: NO CLEAR EDGE")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
