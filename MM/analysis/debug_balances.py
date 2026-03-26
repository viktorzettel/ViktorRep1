import logging
import json
from dotenv import load_dotenv
from client_wrapper import PolymarketClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DEBUG")

def debug_balances():
    load_dotenv()
    poly = PolymarketClient()
    client = poly.get_client()
    
    logger.info(f"ADDRESS: {poly.address}")
    
    YES_TOKEN = "31909393856053520018507280554368586128735607865481470209833276228711603596664"
    NO_TOKEN = "50501976685018753658119277763592993071747302155748969732018918933490316299806"
    
    for name, tid in [("YES", YES_TOKEN), ("NO", NO_TOKEN)]:
        logger.info(f"Testing {name} ({tid[:10]}...):")
        
        # Test with and without proxy
        for sig_type in [0, 2]:
            try:
                resp = client.get_balance_allowance(
                    params=BalanceAllowanceParams(
                        asset_type=AssetType.CONDITIONAL,
                        token_id=tid,
                        signature_type=sig_type
                    )
                )
                logger.info(f"  SigType {sig_type}: {resp}")
            except Exception as e:
                logger.error(f"  SigType {sig_type} Error: {e}")

if __name__ == "__main__":
    debug_balances()
