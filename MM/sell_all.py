import logging
import json
from dotenv import load_dotenv
from client_wrapper import PolymarketClient
from py_clob_client.clob_types import OrderArgs, OrderType, PostOrdersArgs
from py_clob_client.order_builder.constants import SELL

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("SELL_ALL")

def sell_all(market_slug, token_yes, token_no=None):
    load_dotenv()
    poly_client = PolymarketClient()
    client = poly_client.get_client()
    logger.info(f"Initialized client for address: {poly_client.address}")

    tokens_to_check = [token_yes]
    if token_no:
        tokens_to_check.append(token_no)

    for token_id in tokens_to_check:
        logger.info(f"Checking balance for token: {token_id[:16]}...")
        
        # 1. Fetch Balance using the helper which handles decimals
        balance = poly_client.get_position(token_id)

        if balance <= 0.1:
            logger.info(f"No significant balance found ({balance}). Skipping.")
            continue

        logger.info(f"🚨 FOUND POSITION: {balance} units. DUMPING...")

        # 2. Cancel Existing Orders first
        try:
            client.cancel_all()
            logger.info("Cancelled all existing orders.")
        except Exception:
            pass

        # 3. Create Aggressive Sell Order (Taker)
        # Sell at 0.01 to hit any existing bid
        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=0.01,
                size=balance,
                side=SELL,
            )
            signed_order = client.create_order(order_args)
            
            # Post without postOnly to ensure it fills
            post_args = [PostOrdersArgs(order=signed_order, orderType=OrderType.GTC, postOnly=False)]
            resp = client.post_orders(post_args)
            
            logger.info(f"✅ SELL ORDER POSTED: {balance} units at 0.01 (Aggressive).")
            # Filter response for IDs
            logger.info(f"Response: {resp}")
        except Exception as e:
            logger.error(f"Failed to post sell order: {e}")

if __name__ == "__main__":
    # Default tokens for the current market
    YES_TOKEN = "31909393856053520018507280554368586128735607865481470209833276228711603596664"
    NO_TOKEN = "50501976685018753658119277763592993071747302155748969732018918933490316299806"
    
    sell_all("bitcoin-up-or-down-january-29-3pm-et", YES_TOKEN, NO_TOKEN)
