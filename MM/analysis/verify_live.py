
from monitoring import MarketMonitor
import logging
import time

# Verify YES Token (Up)
TOKEN_ID = "115503312506587728191870394329837684910063332390467143180568935293697158964005"

logging.basicConfig(level=logging.INFO)

def verify_live():
    print(f"--- Verifying Live Market Data for Token {TOKEN_ID} ---")
    monitor = MarketMonitor(token_id=TOKEN_ID, lookback_hours=1)
    
    print("Fetching History (1h)...")
    history = monitor.fetch_history()
    
    if not history:
        print("❌ ERROR: No history returned. Market might be too new or API issue.")
        return

    print(f"✅ History Fetched: {len(history)} candles.")
    if len(history) > 0:
        first = history[0]
        last = history[-1]
        print(f"   First: {time.ctime(first['t'])}")
        print(f"   Last:  {time.ctime(last['t'])}")
        print(f"   Price Range: {min([x['p'] for x in history])} - {max([x['p'] for x in history])}")
    
    print("\nCalculating Metrics...")
    metrics = monitor.calculate_metrics(ignore_last_mins=1) # 1m lag
    
    if metrics:
        print(f"✅ METRICS CALCULATED:")
        print(f"   Current Price: {metrics.current_price:.4f}")
        print(f"   VWAP (Median): {metrics.vwap_session:.4f}")
        print(f"   RSI (14):      {metrics.rsi_14:.1f}")
        print(f"   Percentile %:  {metrics.percentile_rank:.1f}%")
        
        # Interpret
        if metrics.percentile_rank < 20: 
             print("   -> ZONE: DEEP VALUE (Sniper Buy)")
        elif metrics.percentile_rank > 80:
             print("   -> ZONE: BUBBLE (Sniper Sell)")
        else:
             print("   -> ZONE: Neutral/Fair")
    else:
        print("❌ ERROR: Metric calculation failed (Not enough data?)")

if __name__ == "__main__":
    verify_live()
