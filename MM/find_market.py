
import requests
import json

def find_markets():
    url = "https://gamma-api.polymarket.com/events"
    params = {
        "closed": "false",
        "limit": 50,
        "tag_id": "1" # Crypto? Or just search
    }
    
    try:
        # Fetch all open events and filter for "Bitcoin" and "Up or Down"
        resp = requests.get(url, params=params)
        events = resp.json()
        
        matches = []
        for e in events:
            if "Bitcoin" in e.get("title", "") and "Up or Down" in e.get("title", ""):
                slug = e.get("slug")
                markets = e.get("markets", [])
                if markets:
                    token_id = markets[0].get("clobTokenIds", [""])[0]
                    matches.append((slug, token_id))
        
        # Sort by slug to find the next one
        matches.sort()
        
        print("FOUND MARKETS:")
        for slug, token in matches:
            print(f"Slug: {slug}")
            print(f"Token: {token}")
            print("-" * 40)
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    find_markets()
