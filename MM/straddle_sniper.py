#!/usr/bin/env python3
"""
Sniper Straddle Bot - FIXED VERSION v2.3
Fixes: Token-specific cancels, robust response handling, outcomes parsing, retries for posts/sells,
       WS cancel after emergency, enhanced dry-run logs, retry logic.
"""

import asyncio
import time
import argparse
import logging
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, List
from concurrent.futures import ThreadPoolExecutor

# Additional imports for approvals
from web3 import Web3
from web3.constants import MAX_INT
from web3.middleware import geth_poa_middleware

# Local imports (assuming these are in separate files as per original)
from client_wrapper import PolymarketClient
from data_feed import PolymarketWebSocket, LocalOrderBook
from orders import create_quote_orders, extract_order_ids
from py_clob_client.clob_types import OrderArgs, OrderType, PostOrdersArgs, MarketOrderArgs
from py_clob_client.order_builder.constants import BUY, SELL

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("StraddleSniper")

# Silence noisy libraries
for lib in ["httpx", "websockets", "urllib3"]:
    logging.getLogger(lib).setLevel(logging.WARNING)

class BotState(Enum):
    BUYING = "buying"
    ACQUIRING = "acquiring"
    EXITING = "exiting"
    DONE = "done"

@dataclass
class PositionTracker:
    has_yes: bool = False
    has_no: bool = False
    pending_yes: bool = False
    pending_no: bool = False
    yes_fill_price: float = 0.0
    no_fill_price: float = 0.0
    exit_triggered: bool = False
    inventory_yes: float = 0.0
    inventory_no: float = 0.0

@dataclass
class OrderTracker:
    order_id: str
    token_id: str
    side: str
    timestamp: float

class StraddleSniper:
    def __init__(
        self,
        market_slug: str,
        token_id_yes: str,
        budget_per_side: float = 4.5,
        entry_threshold: float = 0.45,
        dry_run: bool = False,
        max_slippage: float = 0.10
    ):
        self.market_slug = market_slug
        self.token_id_yes = token_id_yes
        self.token_id_no: Optional[str] = None
        
        self.budget_per_side = budget_per_side
        self.entry_threshold = entry_threshold
        self.dry_run = dry_run
        self.max_slippage = max_slippage
        
        self.running = False
        self.state = BotState.BUYING
        self.pos = PositionTracker()
        
        self._last_yes_price: float = 0.0
        self._last_no_price: float = 0.0
        self._last_price_update: float = 0.0
        self._orders: List[OrderTracker] = []
        self._recent_trades: List[Dict] = []
        
        self._executor = ThreadPoolExecutor(max_workers=2)
        
        self.poly_client = PolymarketClient()
        self.client = self.poly_client.get_client()
        self.wallet_address = getattr(self.poly_client, 'address', '').lower()
        self.private_key = self.poly_client.private_key  # Assuming this exists in PolymarketClient
        
        self._fetch_tokens()
        self._set_allowances()
        self._init_feeds()

    def _fetch_tokens(self):
        """Fetch both token IDs and map to YES/NO properly."""
        import httpx
        import json
        try:
            resp = httpx.get(
                "https://gamma-api.polymarket.com/markets",
                params={"slug": self.market_slug},
                timeout=5.0
            )
            markets = resp.json()
            if markets:
                market_data = markets[0]
                tokens_raw = market_data.get("clobTokenIds", [])
                tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
                
                if len(tokens) >= 2:
                    outcomes_raw = market_data.get('outcomes', [])
                    outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
                    if len(outcomes) >= 2:
                        if 'yes' in outcomes[0].lower():
                            self.token_id_yes = tokens[0]
                            self.token_id_no = tokens[1]
                        else:
                            self.token_id_yes = tokens[1]
                            self.token_id_no = tokens[0]
                        logger.info(f"Tokens loaded - YES: {self.token_id_yes[:15]}... NO: {self.token_id_no[:15]}...")
                    else:
                        logger.error("Could not map outcomes to tokens")
                        raise SystemExit
                else:
                    logger.error("Market needs 2 tokens")
                    raise SystemExit
        except Exception as e:
            logger.error(f"Failed to fetch tokens: {e}")
            raise

    def _set_allowances(self):
        """Set approvals for USDC and CTF tokens."""
        if self.dry_run:
            logger.info("🧪 DRY RUN: Skipping allowances")
            return

        try:
            rpc_url = "https://polygon-rpc.com"
            web3 = Web3(Web3.HTTPProvider(rpc_url))
            web3.middleware_onion.inject(geth_poa_middleware, layer=0)

            usdc_address = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e on Polygon
            ctf_address = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"   # Conditional Tokens

            erc20_abi = [{"constant": False, "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"}]
            erc1155_abi = [{"inputs": [{"internalType": "address", "name": "operator", "type": "address"}, {"internalType": "bool", "name": "approved", "type": "bool"}], "name": "setApprovalForAll", "outputs": [], "type": "function"}]

            usdc = web3.eth.contract(address=usdc_address, abi=erc20_abi)
            ctf = web3.eth.contract(address=ctf_address, abi=erc1155_abi)

            targets = [
                "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",  # CTF Exchange
                "0xC5d563A36AE78145C45a50134d48A1215220f80a",  # Neg Risk CTF Exchange
                "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"   # Neg Risk Adapter
            ]

            nonce = web3.eth.get_transaction_count(self.wallet_address)
            for target in targets:
                # Approve USDC
                tx = usdc.functions.approve(target, int(MAX_INT, 16)).build_transaction({
                    "chainId": 137, "from": self.wallet_address, "nonce": nonce, "gas": 200000, "gasPrice": web3.to_wei('50', 'gwei')
                })
                signed_tx = web3.eth.account.sign_transaction(tx, self.private_key)
                web3.eth.send_raw_transaction(signed_tx.rawTransaction)
                web3.eth.wait_for_transaction_receipt(signed_tx.hash)
                nonce += 1

                # Set CTF approval
                tx = ctf.functions.setApprovalForAll(target, True).build_transaction({
                    "chainId": 137, "from": self.wallet_address, "nonce": nonce, "gas": 200000, "gasPrice": web3.to_wei('50', 'gwei')
                })
                signed_tx = web3.eth.account.sign_transaction(tx, self.private_key)
                web3.eth.send_raw_transaction(signed_tx.rawTransaction)
                web3.eth.wait_for_transaction_receipt(signed_tx.hash)
                nonce += 1

            logger.info("✅ Allowances set for USDC and CTF tokens.")
        except Exception as e:
            logger.error(f"Failed to set allowances: {e}")
            raise SystemExit

    def _init_feeds(self):
        """Initialize WebSocket feeds."""
        if not self.token_id_yes or not self.token_id_no:
            raise ValueError("Tokens not initialized")
            
        tokens = [self.token_id_yes, self.token_id_no]
        self.poly_ws = PolymarketWebSocket(tokens)
        self.poly_ws.on_book_update = self._on_book_update
        self.poly_ws.on_trade = self._on_trade

    def _on_book_update(self, book: LocalOrderBook):
        """Update local price state."""
        now = time.time()
        if book.token_id == self.token_id_yes:
            self._last_yes_price = book.mid_price or 0.0
            self._last_price_update = now
        elif book.token_id == self.token_id_no:
            self._last_no_price = book.mid_price or 0.0

    def _on_trade(self, trade: Dict):
        """Capture our own fills for accurate pricing."""
        maker = trade.get('maker_address', '').lower()
        taker = trade.get('taker_address', '').lower()
        
        if self.wallet_address in [maker, taker]:
            self._recent_trades.append({
                'token_id': trade.get('token_id'),
                'price': float(trade.get('price', 0)),
                'side': 'buy' if taker == self.wallet_address else 'sell',
                'time': time.time()
            })
            self._recent_trades = self._recent_trades[-50:]

    def _get_fill_price(self, token_id: str, default: float) -> float:
        """Get actual fill price from recent trades."""
        for trade in reversed(self._recent_trades):
            if trade['token_id'] == token_id and trade['side'] == 'buy':
                return trade['price']
        return default

    def _is_price_fresh(self) -> bool:
        """Check if price data is recent (< 3 seconds old)."""
        return (time.time() - self._last_price_update) < 3.0

    async def _get_positions(self) -> tuple[float, float]:
        """Non-blocking position fetch using thread pool."""
        loop = asyncio.get_event_loop()
        
        tasks = [
            loop.run_in_executor(self._executor, self.poly_client.get_position, self.token_id_yes),
        ]
        if self.token_id_no:
            tasks.append(loop.run_in_executor(self._executor, self.poly_client.get_position, self.token_id_no))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        yes_pos = 0.0
        no_pos = 0.0
        
        if isinstance(results[0], Exception):
            logger.warning(f"YES position fetch failed: {results[0]}")
        else:
            yes_pos = float(results[0]) if results[0] else 0.0
            
        if len(results) > 1:
            if isinstance(results[1], Exception):
                logger.warning(f"NO position fetch failed: {results[1]}")
            else:
                no_pos = float(results[1]) if results[1] else 0.0
                
        return yes_pos, no_pos

    async def _cancel_orders_for_token(self, token_id: str):
        """Cancel orders for specific token using SDK method."""
        if self.dry_run:
            logger.info(f"🧪 DRY RUN: Would cancel orders for token {token_id[:10]}")
            return

        try:
            # Use token-specific cancel
            self.client.cancel_market_orders(asset_id=token_id)
            logger.info(f"Cancelled orders for token {token_id[:10]}")
        except Exception as e:
            logger.warning(f"Cancel failed for {token_id[:10]}: {e}")
        # Clear tracked orders regardless
        self._orders = [o for o in self._orders if o.token_id != token_id]

    async def _check_fills(self):
        """Check fills with race condition protection."""
        try:
            yes_pos, no_pos = await self._get_positions()
            
            if not self.pos.has_yes and yes_pos > 0.5:
                fill_price = self._get_fill_price(self.token_id_yes, self._last_yes_price)
                logger.info(f"🎯 YES FILLED: {yes_pos:.2f} shares @ {fill_price:.3f}")
                await self._cancel_orders_for_token(self.token_id_yes)
                self.pos.has_yes = True
                self.pos.pending_yes = False
                self.pos.inventory_yes = yes_pos
                self.pos.yes_fill_price = fill_price
                self._update_state()
            
            if not self.pos.has_no and no_pos > 0.5:
                fill_price = self._get_fill_price(self.token_id_no, self._last_no_price)
                logger.info(f"🎯 NO FILLED: {no_pos:.2f} shares @ {fill_price:.3f}")
                await self._cancel_orders_for_token(self.token_id_no)
                self.pos.has_no = True
                self.pos.pending_no = False
                self.pos.inventory_no = no_pos
                self.pos.no_fill_price = fill_price
                self._update_state()
                
        except Exception as e:
            logger.error(f"Fill check error: {e}")

    def _update_state(self):
        """State transitions."""
        if self.pos.has_yes and self.pos.has_no:
            if self.state not in [BotState.EXITING, BotState.DONE]:
                asyncio.create_task(self._transition_to_exiting())
        elif self.pos.has_yes or self.pos.has_no:
            self.state = BotState.ACQUIRING

    async def _transition_to_exiting(self):
        """Wait for profit target (YES + NO >= $0.995) then execute exit."""
        if self.state == BotState.EXITING:
            return
        
        logger.info("🎯 STRADDLE COMPLETE - Both legs filled. Monitoring for profit target...")
        logger.info(f"Entry prices: YES@{self.pos.yes_fill_price:.3f}, NO@{self.pos.no_fill_price:.3f}")
        logger.info("Waiting for YES_bid + NO_bid >= $0.995...")
        
        profit_reached = False
        check_count = 0
        
        while self.running and not profit_reached:
            try:
                book_yes = self.poly_ws.get_order_book(self.token_id_yes)
                book_no = self.poly_ws.get_order_book(self.token_id_no)
                
                if book_yes and book_no and book_yes.bids and book_no.bids and self._is_price_fresh():
                    yes_bid = float(book_yes.bids[0].price)
                    no_bid = float(book_no.bids[0].price)
                    total_exit = yes_bid + no_bid
                    
                    if total_exit >= 0.995:
                        logger.info(f"💰 PROFIT TARGET: YES({yes_bid:.3f}) + NO({no_bid:.3f}) = {total_exit:.3f}")
                        profit_reached = True
                        break
                    else:
                        check_count += 1
                        if check_count % 50 == 0:
                            logger.info(f"⏳ Current exit value: ${total_exit:.3f} (need $0.995)")
            except Exception as e:
                logger.error(f"Profit check error: {e}")
                
            await asyncio.sleep(0.1)
        
        if not profit_reached:
            logger.warning("Shutdown before profit - exiting at market anyway")
            
        self.state = BotState.EXITING
        
        if not self.dry_run:
            try:
                self.client.cancel_all()
                self._orders = []
            except Exception as e:
                logger.warning(f"Cancel during exit: {e}")
                
        await self._execute_exit()

    async def _execute_exit(self, emergency: bool = False):
        """Market sell with safety checks."""
        if self.pos.exit_triggered:
            return
        self.pos.exit_triggered = True

        if self.dry_run:
            logger.info("🧪 DRY RUN: Would sell everything (YES: {self.pos.inventory_yes:.1f}, NO: {self.pos.inventory_no:.1f})")
            self.state = BotState.DONE
            self.running = False
            return

        success_yes = False
        success_no = False

        if self.pos.inventory_yes > 0.5:
            success_yes = await self._safe_market_sell(
                self.token_id_yes, 
                self.pos.inventory_yes, 
                "YES",
                min_price=self.pos.yes_fill_price * (1 - self.max_slippage),
                bypass_slippage=emergency
            )
            if success_yes:
                self.pos.inventory_yes = 0

        if self.pos.inventory_no > 0.5:
            success_no = await self._safe_market_sell(
                self.token_id_no,
                self.pos.inventory_no,
                "NO",
                min_price=self.pos.no_fill_price * (1 - self.max_slippage),
                bypass_slippage=emergency
            )
            if success_no:
                self.pos.inventory_no = 0

        if success_yes and success_no:
            logger.info(f"🏁 COMPLETE - P&L: YES@{self.pos.yes_fill_price:.2f}, NO@{self.pos.no_fill_price:.2f}")
            self.state = BotState.DONE
            self.running = False
        else:
            logger.warning("Exit incomplete - Check positions manually")

    async def _safe_market_sell(self, token_id: str, amount: float, label: str, 
                                 min_price: float, bypass_slippage: bool = False) -> bool:
        """Sell with slippage protection using market order and retries."""
        retries = 3
        for attempt in range(retries):
            try:
                book = self.poly_ws.get_order_book(token_id)
                if not book or not book.bids:
                    logger.error(f"❌ No bids for {label}! Cannot exit.")
                    return False
                
                best_bid = float(book.bids[0].price)
                
                if not bypass_slippage and best_bid < min_price:
                    logger.warning(f"⚠️ {label} bid {best_bid:.2f} below min {min_price:.2f}. Holding.")
                    return False
                
                mo_args = MarketOrderArgs(
                    token_id=token_id,
                    amount=amount,
                    side=SELL,
                    order_type=OrderType.FOK
                )
                signed = self.client.create_market_order(mo_args)
                resp = self.client.post_order(signed, OrderType.FOK)
                
                if isinstance(resp, dict) and 'id' in resp:
                    logger.info(f"Market sell order posted for {label}: {amount:.1f} shares")
                    # Poll for confirmation
                    for _ in range(10):  # Up to 10 seconds
                        await asyncio.sleep(1)
                        pos = await self.poly_client.get_position(token_id)
                        if float(pos or 0) < 0.5:
                            logger.info(f"🎯 {label} SOLD: {amount:.1f} shares @ ~{best_bid:.3f}")
                            return True
                    logger.warning(f"{label} sell not filled after wait")
                    return False
                else:
                    logger.error(f"Failed to post market sell: {resp}")
                    if attempt < retries - 1:
                        await asyncio.sleep(1)  # Backoff
                        continue
                    return False
                
            except Exception as e:
                logger.error(f"Failed to sell {label} (attempt {attempt+1}): {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(1)
                    continue
                return False
        return False

    def _generate_order(self, token_id: str, has_pos: bool, pending: bool) -> Optional[PostOrdersArgs]:
        """Generate entry order."""
        if has_pos or pending or not token_id or not self._is_price_fresh():
            return None
        
        book = self.poly_ws.get_order_book(token_id)
        if not book or not book.asks:
            return None
        
        best_ask = float(book.asks[0].price)
        
        # TAKER: Price already at or below threshold
        if best_ask <= self.entry_threshold:
            logger.info(f"⚡ TAKER {token_id[:10]} @ {best_ask:.3f}")
            size = self.budget_per_side / best_ask
            size = max(size, 5.0)
            
            order_args = OrderArgs(
                token_id=token_id, 
                price=best_ask,
                size=size, 
                side=BUY
            )
            signed = self.client.create_order(order_args)
            return PostOrdersArgs(order=signed, orderType=OrderType.FOK, postOnly=False)
        
        # MAKER: Post bid at threshold
        else:
            size = self.budget_per_side / self.entry_threshold
            size = max(size, 5.0)
            order_args = OrderArgs(
                token_id=token_id,
                price=self.entry_threshold,
                size=size,
                side=BUY
            )
            signed = self.client.create_order(order_args)
            return PostOrdersArgs(order=signed, orderType=OrderType.GTC, postOnly=True)

    async def _cleanup_old_orders(self):
        """Cancel orders older than 30 seconds if in BUYING state."""
        now = time.time()
        old_orders = [o for o in self._orders if (now - o.timestamp) > 30]
        if old_orders and self.state == BotState.BUYING:
            logger.info(f"Cleaning {len(old_orders)} stale orders")
            for o in old_orders:
                try:
                    if not self.dry_run:
                        self.client.cancel(o.order_id)
                except Exception as e:
                    logger.warning(f"Failed to cancel stale order {o.order_id}: {e}")
            self._orders = [o for o in self._orders if (now - o.timestamp) <= 30]

    async def _step(self):
        """Main execution step."""
        await self._check_fills()
        
        if self.state in [BotState.EXITING, BotState.DONE]:
            return
        
        await self._cleanup_old_orders()
        
        yes_order = None
        no_order = None
        
        if not self.pos.has_yes and not self.pos.pending_yes:
            yes_order = self._generate_order(self.token_id_yes, self.pos.has_yes, self.pos.pending_yes)
        if not self.pos.has_no and not self.pos.pending_no:
            no_order = self._generate_order(self.token_id_no, self.pos.has_no, self.pos.pending_no)
        
        orders_to_post = []
        tokens_to_cancel = []
        
        if yes_order:
            if not yes_order.postOnly:
                tokens_to_cancel.append(self.token_id_yes)
            orders_to_post.append((yes_order, "YES"))
            self.pos.pending_yes = True
        
        if no_order:
            if not no_order.postOnly:
                tokens_to_cancel.append(self.token_id_no)
            orders_to_post.append((no_order, "NO"))
            self.pos.pending_no = True
        
        if not orders_to_post:
            return
        
        if self.dry_run:
            for _, label in orders_to_post:
                logger.info(f"🧪 DRY RUN: Would post {label} order")
            # Simulate pending reset after "fill" in dry run
            if yes_order:
                self.pos.pending_yes = False
            if no_order:
                self.pos.pending_no = False
            return
        
        retries = 3
        for attempt in range(retries):
            try:
                for token_id in tokens_to_cancel:
                    await self._cancel_orders_for_token(token_id)
                
                post_args = [o for o, _ in orders_to_post]
                if post_args:
                    resp = self.client.post_orders(post_args)
                    
                    if hasattr(resp, 'orders') and isinstance(resp.orders, list):
                        for i, order_data in enumerate(resp.orders):
                            token = self.token_id_yes if orders_to_post[i][1] == "YES" else self.token_id_no
                            self._orders.append(OrderTracker(
                                order_id=order_data.id,
                                token_id=token,
                                side='buy',
                                timestamp=time.time()
                            ))
                        logger.info(f"Posted {len(post_args)} orders")
                        break  # Success, exit retry
                    else:
                        logger.error(f"Unexpected response from post_orders: {resp}")
                        if attempt < retries - 1:
                            await asyncio.sleep(1)
                            continue
                        # Reset pending on final failure
                        if yes_order:
                            self.pos.pending_yes = False
                        if no_order:
                            self.pos.pending_no = False
            except Exception as e:
                logger.error(f"Order post failed (attempt {attempt+1}): {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(1)
                    continue
                # Reset pending on failure
                if yes_order:
                    self.pos.pending_yes = False
                if no_order:
                    self.pos.pending_no = False

    async def run(self):
        """Main loop."""
        self.running = True
        logger.info(f"🎯 Straddle Sniper on {self.market_slug}")
        logger.info(f"Entry: ≤{self.entry_threshold} | Budget: ${self.budget_per_side}/side (${self.budget_per_side * 2} total)")
        
        ws_task = asyncio.create_task(self.poly_ws.connect())
        await asyncio.sleep(2)
        
        cycle_count = 0
        
        try:
            while self.running:
                cycle_count += 1
                await self._step()
                
                if cycle_count % 10 == 0:
                    logger.debug(f"State: {self.state.value} | YES:{self.pos.inventory_yes:.1f} NO:{self.pos.inventory_no:.1f}")
                
                await asyncio.sleep(0.1)
                
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            self.running = False
            
            # Emergency exit BEFORE canceling WS (to use book data)
            if not self.dry_run and (self.pos.inventory_yes > 0.5 or self.pos.inventory_no > 0.5):
                logger.info("🚨 EMERGENCY EXIT - Selling immediately at market...")
                self.state = BotState.EXITING
                
                try:
                    await asyncio.wait_for(self._execute_exit(emergency=True), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.error("❌ Emergency exit timed out - positions may remain!")
                except Exception as e:
                    logger.error(f"❌ Emergency exit failed: {e}")
            
            # Now safe to cancel WS
            ws_task.cancel()
            self._executor.shutdown(wait=False)

def main():
    parser = argparse.ArgumentParser(description="Straddle Sniper Bot")
    parser.add_argument("market_slug", help="Market slug")
    parser.add_argument("token_id", help="YES token ID (initial, will be mapped)")
    parser.add_argument("--budget", type=float, default=4.5, help="Budget per side (default: $4.50)")
    parser.add_argument("--threshold", type=float, default=0.45, help="Entry threshold (default: 0.45)")
    parser.add_argument("--dry-run", action="store_true", help="Dry run mode (no real trades)")
    parser.add_argument("--slippage", type=float, default=0.10, help="Max exit slippage (default: 0.10 = 10%)")
    
    args = parser.parse_args()
    
    bot = StraddleSniper(
        market_slug=args.market_slug,
        token_id_yes=args.token_id,
        budget_per_side=args.budget,
        entry_threshold=args.threshold,
        dry_run=args.dry_run,
        max_slippage=args.slippage
    )
    
    asyncio.run(bot.run())

if __name__ == "__main__":
    main()