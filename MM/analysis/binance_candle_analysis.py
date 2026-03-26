import requests

def check_market_fees(event_slug):
    # 1. Try slug query if it looks like a slug, otherwise try direct ID
    if event_slug.isdigit():
        url = f"https://gamma-api.polymarket.com/events/{event_slug}"
    else:
        url = f"https://gamma-api.polymarket.com/events?slug={event_slug}"
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        # Handle list response from slug query
        event_data = data[0] if isinstance(data, list) else data
        
        print(f"Event: {event_data.get('title', 'Unknown Title')}")
        
        # 2. Iterate through the markets in this event (usually just one for Hourly BTC)
        markets = event_data.get("markets", [])
        
        if not markets:
            print("❌ No markets found for this event.")
            return

        for market in markets:
            question = market.get("question", "Unknown Market")
            
            # 3. Extract Fee Fields
            # Fees are in Basis Points (bps). 1 bps = 0.01%
            # So 100 bps = 1.00%
            taker_fee_bps = market.get("takerBaseFee", 0)
            maker_fee_bps = market.get("makerBaseFee", 0)
            
            print(f"\nMarket: {question}")
            print(f"--------------------------------")
            print(f"💰 Taker Fee (Market Orders): {taker_fee_bps} bps ({taker_fee_bps / 100}%)")
            print(f"🏦 Maker Fee (Limit Orders):  {maker_fee_bps} bps ({maker_fee_bps / 100}%)")
            
            if taker_fee_bps == 0 and maker_fee_bps == 0:
                print("✅ RESULT: NO FEES. You keep 100% of the spread.")
            else:
                print("⚠️ RESULT: FEES APPLY. Factor this into your profit calc.")

    except requests.exceptions.HTTPError as e:
        print(f"Error fetching data: {e}")

# --- INPUT ---
# From URL: https://polymarket.com/event/bitcoin-up-or-down-february-3-12pm-et
target_slug = "bitcoin-up-or-down-february-3-12pm-et"

check_market_fees(target_slug)

