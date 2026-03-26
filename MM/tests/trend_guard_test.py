import asyncio
import logging
from data_feed import PolymarketWebSocket, LocalOrderBook

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("TrendGuardTest")

# CONFIG
TOKEN_ID = "21742633143463906290569050155826241533067272736897614382246205612454604771328" # Bitcoin > 90k (Example)
# User can replace this token ID or I will make it an arg.

import sys

async def main():
    if len(sys.argv) < 2:
        print("Usage: python trend_guard_test.py <TOKEN_ID>")
        # Default to a know active token or ask user
        print("Please provide a Token ID (Yes or No side).")
        return
        
    token_id = sys.argv[1]
    
    print(f"🛡️ STARTING TREND GUARD TEST for {token_id[:15]}...")
    
    ws = PolymarketWebSocket(token_ids=[token_id])
    
    def on_update(book: LocalOrderBook):
        if not book.bids or not book.asks: return
        
        # Trend Guard Logic
        bid_vol = sum(b.size for b in book.bids[:3])
        ask_vol = sum(a.size for a in book.asks[:3])
        
        bid_vol = max(bid_vol, 1.0)
        ask_vol = max(ask_vol, 1.0)
        
        ratio = ask_vol / bid_vol
        mid = book.mid_price
        
        status = "✅ SAFE"
        if ratio > 3.0: status = "⛔ STOP BUYING (Crash Risk)"
        if ratio < 0.33: status = "⛔ STOP SELLING (Pump Risk)"
        
        print(f"Price: {mid:.3f} | Bids: ${bid_vol:,.0f} | Asks: ${ask_vol:,.0f} | Ratio: {ratio:.2f} | {status}")

    ws.on_book_update = on_update
    
    await ws.connect()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
