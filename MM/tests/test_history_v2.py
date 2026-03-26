import requests
import time
import json
import logging

logging.basicConfig(level=logging.INFO)

def fetch_token_history():
    token_id_yes = "31909393856053520018507280554368586128735607865481470209833276228711603596664"
    
    # Try different intervals/fidelities
    # CLOB API usually wants start/end times timestamps
    now = int(time.time())
    start = now - 3600 * 4 # Last 4 hours
    
    # Try Gamma API endpoint which is friendlier
    # https://gamma-api.polymarket.com/prices-history?market=...&interval=1m&fidelity=1
    
    # Let's try CLOB with start/end
    # GET /prices-history?interval=1m&market=...&startTs=...&endTs=...
    
    url = f"https://clob.polymarket.com/prices-history?interval=1m&market={token_id_yes}&startTs={start}&endTs={now}"
    
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
             
        # Fallback: Gamma API
        # https://gamma-api.polymarket.com/events?id=... (No, that gives market info)
        # Often simpler to just track it locally if API is hard.
             
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    fetch_token_history()
