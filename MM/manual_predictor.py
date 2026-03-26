import asyncio
import aiohttp
import math
import sys

# =============================================================================
# LOGIC
# =============================================================================

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def calculate_fair_prob(current, strike, time_left_sec, vol):
    # Black-Scholes Binary Call
    if time_left_sec <= 0: 
        return 1.0 if current > strike else 0.0
    if vol <= 0: vol = 0.5

    T_years = time_left_sec / (365 * 24 * 3600)
    S = current
    K = strike
    sigma = vol

    numerator = math.log(S / K) - (0.5 * sigma**2 * T_years)
    denominator = sigma * math.sqrt(T_years)
    d2 = numerator / denominator
    
    return norm_cdf(d2)

async def get_btc_price():
    url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        async with session.get(url) as resp:
            data = await resp.json()
            return float(data["price"])

async def main():
    print("\n🔮 MANUAL MARKET PREDICTOR 🔮")
    print("-------------------------------")
    
    try:
        # 1. Get Live Price
        print("⏳ Fetching Live BTC Price...")
        current_price = await get_btc_price()
        print(f"✅ Current BTC Price: ${current_price:,.2f}")
        print("-------------------------------")


        # Check if args provided
        if len(sys.argv) == 3:
            strike_input = sys.argv[1]
            time_input = sys.argv[2]
            print(f"🎯 Strike: {strike_input}")
            print(f"⏰ Time: {time_input}m")
        else:
            # 2. User Inputs
            strike_input = input("🎯 Enter 'Price to Beat' (Strike): ").strip().replace("$", "").replace(",", "")
            time_input = input("⏰ Enter 'Time Remaining' (Minutes): ").strip()
        
        strike_price = float(strike_input.replace("$", "").replace(",", ""))
        time_left_mins = float(time_input)
        time_left_sec = time_left_mins * 60
        
        # 3. Calculate
        vol = 0.5 # Standard Vol assumption
        prob_up = calculate_fair_prob(current_price, strike_price, time_left_sec, vol)
        prob_down = 1.0 - prob_up
        
        # 4. Output
        print("\n📊 ANALYSIS")
        print(f"Gap to Strike: ${current_price - strike_price:,.2f}")
        
        print("\n🤖 MODEL PROBABILITY:")
        print(f"   UP (YES):   {prob_up*100:.2f}%")
        print(f"   DOWN (NO):  {prob_down*100:.2f}%")
        
        print("\n💡 RECOMMENDATION:")
        if prob_up > 0.60:
            print("   👉 BET UP (YES) 🟢")
            print("   (Strong signal. Look for shares < 60c)")
        elif prob_down > 0.60:
            print("   👉 BET DOWN (NO) 🔴")
            print("   (Strong signal. Look for shares < 60c)")
        else:
            print("   👉 SKIP / TOSS-UP 🟡")
            print("   (No clear edge. Save your money.)")
            
    except ValueError:
        print("❌ Invalid input. Please enter numbers only.")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
