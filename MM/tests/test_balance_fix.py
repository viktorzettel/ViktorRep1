
import logging
from client_wrapper import PolymarketClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
import py_clob_client.http_helpers.helpers as _helpers

# Cloudflare bypass
_helpers.overloadHeaders = lambda m, h: {**(h or {}), 'User-Agent': 'Mozilla/5.0'}

def test():
    pm = PolymarketClient()
    client = pm.get_client()
    
    # Example Token ID from user logs
    token_id = "82901799715930045311809212542225092826839455972178920747166234033810084508110"
    
    print(f"Testing Balance lookup for Token: {token_id[:16]}...")
    
    # Try get_balance_allowance with CONDITIONAL
    try:
        data = client.get_balance_allowance(
            params=BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=token_id,
                signature_type=2 # Proxy
            )
        )
        print(f"Success! Response: {data}")
    except Exception as e:
        print(f"Failed get_balance_allowance: {e}")

if __name__ == "__main__":
    test()
