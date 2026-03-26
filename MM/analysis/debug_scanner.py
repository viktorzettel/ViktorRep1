import json
import urllib.request
import argparse

GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Origin": "https://polymarket.com",
}

def check_liquidity():
    import ssl
    context = ssl._create_unverified_context()
    # Find Bitcoin/Ethereum Price markets (Usually hourly/daily)
    url = f"{GAMMA_API_URL}?active=true&closed=false&limit=100&search=Bitcoin+Price"
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(req, context=context) as response:
        markets = json.loads(response.read().decode())
    
    print(f"Fetched {len(markets)} markets.")
    
    for m in markets:
        slug = m.get('slug', 'no-slug')
        liq = float(m.get('liquidityNum', 0) or m.get('liquidity', 0) or 0)
        vol = float(m.get('volume24hr', 0) or 0)
        neg = m.get('negRisk', False) or m.get('neg_risk', False)
        book = m.get('enableOrderBook', False)
        
        # Check if it's a crypto market
        # if not any(x in slug.upper() for x in ["BTC", "ETH", "BITCOIN", "ETHEREUM", "SOL"]):
        #      # print(f"Skipping {slug}")
        #      continue
            
        mid_price = m.get('outcomePrices', "[]")
        tokens = m.get('clobTokenIds', "[]")
        end_date = m.get('endDate', '')
        
        # Simple date parse
        try:
            from datetime import datetime, timezone
            expiry = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            days = (expiry - datetime.now(timezone.utc)).days
        except:
            days = -1

        if "BTC" in slug.upper() or "ETH" in slug.upper() or "BITCOIN" in slug.upper() or "PRICE" in slug.upper():
            print(f"[{slug}] Liq: ${liq:,.0f} | Vol: ${vol:,.0f} | Days: {days} | Price: {mid_price}")

if __name__ == "__main__":
    check_liquidity()
