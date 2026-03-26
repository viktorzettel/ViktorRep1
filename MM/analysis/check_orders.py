
import os
import time
from dotenv import load_dotenv
from client_wrapper import PolymarketClient
import py_clob_client.http_helpers.helpers as _helpers

# Cloudflare bypass (just in case)
def _patched(m, h):
    if h is None: h = dict()
    h['User-Agent'] = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120'
    h['Accept'] = '*/*'
    h['Content-Type'] = 'application/json'
    return h
_helpers.overloadHeaders = _patched

def check_orders():
    print("Initializing client...")
    pm = PolymarketClient()
    client = pm.get_client()
    
    print(f"Checking Open Orders for {client.get_address()}...")
    
    try:
        orders = client.get_orders()
        print(f"Found {len(orders)} open orders.")
        
        total_locked = 0.0
        
        for o in orders:
            # Polymarket API returns orders with 'side', 'price', 'original_size', 'size' (remaining)
            side = o.get('side', '?')
            price = float(o.get('price', 0))
            size = float(o.get('size', 0)) # Remaining size
            
            value = 0
            if side == 'BUY':
                value = price * size
            else:
                # For SELL, it locks shares, not USDC, unless it's a short sell? 
                # Polymarket is usually fully funded. Selling inventory doesn't lock USDC.
                # But buying locks USDC.
                pass
                
            token_id = o.get('asset_id', 'unknown')
            print(f"- Order {o.get('orderID')}: {side} {size} @ {price} (Locked: ${value:.2f})")
            
            if side == 'BUY':
                total_locked += value
                
        print(f"\nTotal USDC Locked in Orders: ${total_locked:.2f}")
        
    except Exception as e:
        print(f"Error fetching orders: {e}")

if __name__ == "__main__":
    check_orders()
