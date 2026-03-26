import requests
import time
import json
import logging

logging.basicConfig(level=logging.INFO)

def fetch_token_history():
    token_id_yes = "31909393856053520018507280554368586128735607865481470209833276228711603596664"
    
    # Error said: minimum 'fidelity' for '1m' range is 10
    # Let's try fidelity=10
    
    now = int(time.time())
    start = now - 3600 * 4 
    
    url = f"https://clob.polymarket.com/prices-history?interval=1m&market={token_id_yes}&startTs={start}&endTs={now}&fidelity=10"
    
    print(f"Testing URL: {url}")
    try:
        resp = requests.get(url)
        if resp.status_code == 200:
            data = resp.json()
            history = data.get('history', [])
            print(f"✅ Success! Got {len(history)} candles.")
            if history:
                 prices = [float(x.get('p', 0)) for x in history]
                 print(f"Stats: Min={min(prices):.4f}, Max={max(prices):.4f}, Current={prices[-1]:.4f}")
        else:
             print(f"❌ CLOB Failed: {resp.text}")
             
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    fetch_token_history()
