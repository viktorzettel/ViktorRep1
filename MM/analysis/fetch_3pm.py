
import requests
import json

def fetch():
    try:
        url = "https://gamma-api.polymarket.com/events?slug=bitcoin-up-or-down-january-31-3pm-et"
        resp = requests.get(url)
        data = resp.json()
        
        if not data:
            print("No data found for slug")
            return

        m = data[0]["markets"][0]
        tokens_raw = m["clobTokenIds"]
        if isinstance(tokens_raw, str):
            tokens = json.loads(tokens_raw)
        else:
            tokens = tokens_raw
            
        token_id = tokens[0]  # YES token
        print(f"Token ID: {token_id}")
        print(f"Active: {m.get('active')}")
        print(f"Closed: {m.get('closed')}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    fetch()
