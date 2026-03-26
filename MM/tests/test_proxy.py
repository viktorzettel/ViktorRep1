
import os
import time
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
import py_clob_client.http_helpers.helpers as _helpers

# Cloudflare bypass configuration
def _patched(m, h):
    if h is None: h = dict()
    h['User-Agent'] = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120'
    h['Accept'] = '*/*'
    h['Content-Type'] = 'application/json'
    return h
_helpers.overloadHeaders = _patched

load_dotenv()

def test_proxy_order():
    pk = os.getenv("POLY_PRIVATE_KEY")
    proxy = os.getenv("POLY_PROXY_ADDRESS")
    host = "https://clob.polymarket.com"
    chain_id = 137
    
    # Token ID from the user's previous attempt (Seahawks YES)
    token_id = "91737931954079461205792748723730956466398437395923414328893692961489566016241"
    
    print(f"Testing Proxy Order for {proxy}")
    print(f"Signer: {pk[:6]}...")
    
    # Try different signature types
    # 0 = EOA (Standard)
    # 1 = Gnosis Safe (PolyProxy) ?
    # 2 = ?
    
    sig_types = [0, 1, 2]
    
    for sig_type in sig_types:
        print(f"\n===========================================")
        print(f"Testing signature_type={sig_type}")
        print(f"===========================================")
        
        try:
            client = ClobClient(
                host, 
                key=pk, 
                chain_id=chain_id, 
                funder=proxy, 
                signature_type=sig_type
            )
            
            # Ensure we have creds
            creds = client.create_or_derive_api_creds()
            client.set_api_creds(creds)
            
            print("Credentials derived and set.")
            
            # Create a dummy order
            # Price 0.01 (very low to avoid fill, or just limit)
            # Size 5.0 (min $1 value? price 0.01 -> size needs to be > 100? No, min size is usually 1, min val $1)
            # If price is 0.01, size 200 = $2.00
            
            order_args = OrderArgs(
                price=0.01,
                size=120.0,
                side="BUY",
                token_id=token_id,
            )
            
            print("Placing order...")
            resp = client.create_and_post_order(order_args)
            print(f"✅ SUCCESS! Response: {resp}")
            
            # If successful, cancel immediately
            if resp and resp.get('orderID'):
                print("Cancelling order...")
                client.cancel(resp['orderID'])
                
            break # Stop if successful
            
        except Exception as e:
            print(f"❌ Failed with signature_type={sig_type}")
            print(f"Error: {e}")

if __name__ == "__main__":
    test_proxy_order()
