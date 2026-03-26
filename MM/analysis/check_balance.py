
import logging
import time
import os
from client_wrapper import PolymarketClient
from config import settings
import py_clob_client.http_helpers.helpers as _helpers

# Cloudflare bypass
def _patched(m, h):
    if h is None: h = dict()
    h['User-Agent'] = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120'
    h['Accept'] = '*/*'
    h['Content-Type'] = 'application/json'
    return h
_helpers.overloadHeaders = _patched

def check():
    print('='*60)
    print("POLYNARKET ADVANCED BALANCE CHECKER")
    print('='*60)
    
    try:
        pm = PolymarketClient()
        client = pm.get_client()
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        
        # Addresses
        eoa_address = client.get_address() # This returns the funder/proxy if set?
        proxy_address = settings.poly_proxy_address
        
        print(f"EOA/Trading Address: {eoa_address}")
        print(f"Settings Proxy:      {proxy_address}")
        
        # Check EOA Balance (No proxy sig)
        print("\n--- Checking Account Balance (Type: EOA) ---")
        data_eoa = client.get_balance_allowance(
            params=BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=0 # EOA
            )
        )
        bal_eoa = float(data_eoa.get('balance', 0)) / 1_000_000
        print(f"EOA USDC Balance: ${bal_eoa:,.2f}")
        
        # Check Proxy Balance if configured
        if proxy_address:
             print("\n--- Checking Proxy Balance (Type: Proxy) ---")
             try:
                 data_proxy = client.get_balance_allowance(
                     params=BalanceAllowanceParams(
                         asset_type=AssetType.COLLATERAL,
                         signature_type=2 # Proxy
                     )
                 )
                 bal_proxy = float(data_proxy.get('balance', 0)) / 1_000_000
                 print(f"Proxy USDC Balance: ${bal_proxy:,.2f}")
                 
                 # Check allowances for proxy
                 allowances = data_proxy.get('allowances', {})
                 print("\nProxy Allowances (Exchange Spenders):")
                 for spender, amt in allowances.items():
                      val = float(amt) / 1_000_000
                      # Cap at reasonable naming for display
                      display_amt = f"${val:,.0f}" if val > 1_000_000 else f"${val:,.2f}"
                      print(f" - {spender[:12]}...: {display_amt}")
                      
             except Exception as e:
                 print(f"Failed to fetch Proxy balance: {e}")
        else:
             print("\nNo Proxy configured in .env")

        print("\n" + "="*60)
        print("DIAGNOSIS:")
        if proxy_address and bal_eoa > 0 and bal_proxy == 0:
            print("❌ FUNDS ARE ON EOA, BUT BOT IS USING PROXY.")
            print("   Action: Either clear POLY_PROXY_ADDRESS in .env OR transfer USDC to Proxy.")
        elif bal_eoa == 0 and bal_proxy == 0:
            print("❌ NO FUNDS DETECTED IN EITHER ACCOUNT.")
        elif proxy_address and bal_proxy > 0:
            print("✅ FUNDS DETECTED ON PROXY. BOT SHOULD WORK.")
        elif not proxy_address and bal_eoa > 0:
            print("✅ FUNDS DETECTED ON EOA. EOA MODE ACTIVE.")
        print("="*60)
             
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check()
