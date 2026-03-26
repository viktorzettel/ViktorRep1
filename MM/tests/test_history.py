import requests
import time
import json

def fetch_token_history(token_id):
    # Polymarket Gamma API endpoint for history?
    # Usually: https://gamma-api.polymarket.com/history?ticker=... works for markets
    # Or Clob candles?
    # Let's try the common endpoint for CLOB candles
    
    # Endpoint: https://clob.polymarket.com/prices-history?interval=1m&market=... or token?
    # Let's try to reverse engineer or use known endpoints.
    
    # Gamma API often uses:
    # https://gamma-api.polymarket.com/prices-history?market=...&interval=...
    
    # We need the Condition ID or Market ID usually.
    # Let's try with the Token ID provided in the prompt examples.
    
    token_id_yes = "31909393856053520018507280554368586128735607865481470209833276228711603596664"
    market_slug = "bitcoin-up-or-down-january-29-3pm-et"
    
    # Method 1: CLOB Prices History (Official)
    # GET /prices-history?interval=1m&market=<token_id>
    url = f"https://clob.polymarket.com/prices-history?interval=1m&market={token_id_yes}"
    
    print(f"Testing CLOB History for Token {token_id_yes[:10]}...")
    try:
        resp = requests.get(url)
        if resp.status_code == 200:
            data = resp.json()
            print(f"✅ Success! Got {len(data.get('history', []))} candles.")
            if data.get('history'):
                print(f"Sample: {data['history'][0]}")
                # Analyze range
                prices = [x['p'] for x in data['history']]
                print(f"Range: Low {min(prices)} - High {max(prices)}")
        else:
            print(f"❌ Failed: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    fetch_token_history(None)
