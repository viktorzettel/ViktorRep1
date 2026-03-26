import requests
import json

def find_active_market():
    print("🔍 Searching for ACTIVE Bitcoin markets...")
    url = "https://gamma-api.polymarket.com/events"
    params = {
        "closed": "false",
        "limit": 100,
        # "tag_id": "1" # Crypto - removed to be safe
    }
    
    try:
        resp = requests.get(url, params=params)
        events = resp.json()
        
        print(f"Fetched {len(events)} events. Scanning...")
        
        candidates = []
        
        for e in events:
            title = e.get("title", "").lower()
            # print(f"Checking: {title}") # Debug log
            
            if "bitcoin" in title or "btc" in title:
                 # Accept more variants
                slug = e.get("slug")
                markets = e.get("markets", [])
                if markets:
                    m = markets[0]
                    token_id = m.get("clobTokenIds", [""])[0]
                    vol = float(m.get("volume", 0) or 0)
                    liq = float(m.get("liquidity", 0) or 0)
                    
                    print(f"MATCH: {title} | Liquidity: {liq}")
                    
                    if liq > 0: # minimal check
                        candidates.append({
                            "title": title,
                            "slug": slug,
                            "token_id": token_id,
                            "volume": vol,
                            "liquidity": liq,
                            "end_date": m.get("endDate")
                        })

        # Sort by liquidity (highest first)
        candidates.sort(key=lambda x: x["liquidity"], reverse=True)
        
        print(f"\n✅ Found {len(candidates)} active candidates:\n")
        for c in candidates:
            print(f"Slug: {c['slug']}")
            print(f"Token: {c['token_id']}")
            print(f"Liquidity: ${c['liquidity']:.2f}")
            print(f"Volume: ${c['volume']:.2f}")
            print(f"Expires: {c['end_date']}")
            print("-" * 50)
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    find_active_market()
