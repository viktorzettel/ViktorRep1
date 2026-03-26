import json
import urllib.request
import ssl

def get_market_details(slug):
    url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }
    ctx = ssl._create_unverified_context()
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, context=ctx) as response:
            data = json.loads(response.read().decode())
            if data and isinstance(data, list):
                market = data[0]
                print(f"Slug: {market.get('slug')}")
                print(f"Question: {market.get('question')}")
                print(f"Token IDs: {market.get('clobTokenIds')}")
                
                tokens = json.loads(market.get('clobTokenIds', '[]'))
                if tokens:
                    print(f"\n🚀 COMMAND TO RUN:")
                    print(f"python3 main.py \"{slug}\" \"{tokens[0]}\"")
            else:
                print("Market not found.")
    except Exception as e:
        print(f"Error: {e}")

import sys

if __name__ == "__main__":
    if len(sys.argv) > 1:
        get_market_details(sys.argv[1])
    else:
        # Default fallback
        get_market_details("bitcoin-up-or-down-january-28-5pm-et")
