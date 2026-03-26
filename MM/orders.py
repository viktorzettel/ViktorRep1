"""
Order management module for Polymarket Market Maker.

Provides efficient batch operations for order cancellation and creation.
"""

import logging
from typing import Any, Optional

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import PostOrdersArgs


logger = logging.getLogger(__name__)

# API Constraints (Updated 2026)
MAX_BATCH_SIZE = 15  # Increased from 5 (Medium Article)


def batch_cancel_and_post(
    client: ClobClient,
    cancel_order_ids: Optional[list[str]] = None,
    new_orders: Optional[list[PostOrdersArgs]] = None,
) -> dict[str, Any]:
    """
    Cancel existing orders and post new orders with minimal latency.
    
    Uses batch API endpoints for efficiency:
    - cancel_orders() for batch cancellation (single API call)
    - post_orders() for batch order creation (single API call)
    
    Note: The Polymarket API does not support atomic cancel+post in a single
    call. Orders are cancelled first, then new orders are posted.
    
    Args:
        client: Authenticated ClobClient instance with L2 credentials.
        cancel_order_ids: List of order IDs to cancel. Pass None or empty list
            to skip cancellation.
        new_orders: List of PostOrdersArgs for new orders to create. Pass None
            or empty list to skip posting.
    
    Returns:
        dict: Results containing:
            - "cancelled": Response from cancel_orders(), or None if skipped
            - "posted": Response from post_orders(), or None if skipped
    
    Raises:
        Exception: If API calls fail (propagated from ClobClient).
    
    Example:
        >>> from py_clob_client.clob_types import OrderArgs, OrderType, PostOrdersArgs
        >>> from py_clob_client.order_builder.constants import BUY
        >>> 
        >>> # Create order args
        >>> order_args = OrderArgs(token_id="...", price=0.50, size=10.0, side=BUY)
        >>> signed_order = client.create_order(order_args)
        >>> new_orders = [PostOrdersArgs(order=signed_order, orderType=OrderType.GTC)]
        >>> 
        >>> # Cancel old orders and post new ones
        >>> result = batch_cancel_and_post(
        ...     client=client,
        ...     cancel_order_ids=["old_order_id_1", "old_order_id_2"],
        ...     new_orders=new_orders,
        ... )
    """
    results: dict[str, Any] = {
        "cancelled": None,
        "posted": None,
    }
    
    # Batch cancel existing orders
    if cancel_order_ids:
        logger.info(f"Cancelling {len(cancel_order_ids)} orders...")
        try:
            results["cancelled"] = client.cancel_orders(cancel_order_ids)
            logger.info(f"Successfully cancelled {len(cancel_order_ids)} orders")
        except Exception as e:
            logger.error(f"Failed to cancel orders: {e}")
            raise
    
    # Batch post new orders
    if new_orders:
        if len(new_orders) > MAX_BATCH_SIZE:
            logger.warning(f"⚠️ Batch size {len(new_orders)} > {MAX_BATCH_SIZE}. Truncating to limit.")
            new_orders = new_orders[:MAX_BATCH_SIZE]
            
        logger.info(f"Posting {len(new_orders)} new orders...")
        try:
            results["posted"] = client.post_orders(new_orders)
            
            # Check for error messages in response
            if isinstance(results["posted"], list):
                for i, order_result in enumerate(results["posted"]):
                    if isinstance(order_result, dict):
                        error_msg = order_result.get("errorMsg", "")
                        order_id = order_result.get("orderID", "")
                        if error_msg:
                            logger.warning(f"Order {i} error: {error_msg}")
                        elif order_id:
                            logger.info(f"Order {i} placed: {order_id[:16]}...")
            
            logger.info(f"Successfully posted {len(new_orders)} orders")
        except Exception as e:
            logger.error(f"Failed to post orders: {e}")
            raise
    
    return results


def create_quote_orders(
    client: ClobClient,
    token_id: str,
    bid_price: Optional[float],
    ask_price: Optional[float],
    size: float,
    post_only: bool = True,
    tick_size: float = 0.01,
) -> list[PostOrdersArgs]:
    """
    Create signed orders for a bid/ask quote.
    
    Args:
        client: Authenticated ClobClient instance.
        token_id: Token ID for the market outcome.
        bid_price: Bid price (None to skip bid).
        ask_price: Ask price (None to skip ask).
        size: Order size for each side.
        post_only: Whether to enforce Maker-only (default: True).
                  Set to False for bailouts/taking liquidity.
    
    Returns:
        List of PostOrdersArgs ready for post_orders().
    """
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY, SELL
    
    import math
    
    orders = []
    
    # Validation Helper
    def round_to_tick(price: float, tick: float) -> float:
        if price <= 0: return tick
        # Round 0.50000001 -> 0.50
        rounded = math.floor(price / tick) * tick
        return round(rounded, 6) # Float precision safety
        
    # Create bid order
    if bid_price is not None:
        # Tick Size Validation
        bid_price = round_to_tick(bid_price, tick_size)
    
        bid_args = OrderArgs(
            token_id=token_id,
            price=bid_price,
            size=size,
            side=BUY,
        )
        signed_bid = client.create_order(bid_args)
        logger.debug(f"Bid Order Maker (Signer): {signed_bid.order.maker}")
        orders.append(PostOrdersArgs(
            order=signed_bid, 
            orderType=OrderType.GTC,
            postOnly=post_only
        ))
        logger.debug(f"Created bid order: {bid_price:.4f} x {size}")
    
    # Create ask order
    if ask_price is not None:
        ask_args = OrderArgs(
            token_id=token_id,
            price=ask_price,
            size=size,
            side=SELL,
        )
        signed_ask = client.create_order(ask_args)
        orders.append(PostOrdersArgs(
            order=signed_ask, 
            orderType=OrderType.GTC,
            postOnly=post_only
        ))
        logger.debug(f"Created ask order: {ask_price:.4f} x {size}")
    
    return orders


def extract_order_ids(post_response: dict) -> list[str]:
    """
    Extract order IDs from post_orders response.
    
    Args:
        post_response: Response from client.post_orders().
    
    Returns:
        List of order IDs.
    """
    if not post_response:
        return []
    
    order_ids = []
    
    # Handle different response formats
    if isinstance(post_response, list):
        for item in post_response:
            if isinstance(item, dict) and "id" in item:
                order_ids.append(item["id"])
            elif isinstance(item, dict) and "orderID" in item:
                order_ids.append(item["orderID"])
    elif isinstance(post_response, dict):
        if "orderIDs" in post_response:
            order_ids = post_response["orderIDs"]
        elif "id" in post_response:
            order_ids.append(post_response["id"])
    
    return order_ids
