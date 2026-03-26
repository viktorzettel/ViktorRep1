
import os
import time
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
import py_clob_client.http_helpers.helpers as _helpers

def _patched(m, h):
    if h is None: h = dict()
    h['User-Agent'] = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120'
    h['Accept'] = '*/*'
    h['Content-Type'] = 'application/json'
    return h
_helpers.overloadHeaders = _patched

load_dotenv()

def simple_buy():
    pk = os.getenv("POLY_PRIVATE_KEY")
    proxy = os.getenv("POLY_PROXY_ADDRESS")
    host = "https://clob.polymarket.com"
    chain_id = 137
    # Starbucks CEO market or whatever, just a valid token ID
    token_id = "91737931954079461205792748723730956466398437395923414328893692961489566016241"

    print("--- SIMPLE BUY TEST ---")
    print(f"Using Proxy: {proxy}")
    
    # Initialize Client
    client = ClobClient(
        host, 
        key=pk, 
        chain_id=chain_id, 
        funder=proxy, 
        signature_type=2 # PROXY
    )
    
    print("Deriving credentials...")
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    
    try:
        # Buy 3 shares at $0.40 = $1.20 (exceeds $1 min)
        price = 0.40
        size = 3.0
        
        print(f"Creating Order: BUY {size} @ {price}...")
        order_args = OrderArgs(price=price, size=size, side="BUY", token_id=token_id)
        signed = client.create_order(order_args)
        
        # Just try to post it
        print("Posting order...")
        resp = client.post_order(signed, OrderType.GTC)
        print(f"Response: {resp}")
        
        if resp.get('success') or (resp.get('orderID') and not resp.get('errorMsg')):
            print("✅ SUCCESS! Order placed.")
            print("Cancelling...")
            client.cancel(resp['orderID'])
            print("Cancelled.")
        else:
            print("❌ FAILURE in Response.")
            
    except Exception as e:
        print(f"❌ EXCEPTION: {e}")

if __name__ == "__main__":
    simple_buy()
