#!/usr/bin/env python3
"""
Momentum Sniper Bot
===================
Strategy: Momentum Confirmation
- Scans market at 5Hz (200ms)
- Trigger: Buy Winning Token when Ask > 54¢
- Guard: Max Buy Price 55¢ (Slippage protection)
- Exit: Sell at Entry + 5¢
- Single-shot execution: Buys once, sells once, then shuts down.
"""

import asyncio
import time
import argparse
import logging
import json
import httpx
from enum import Enum
from dataclasses import dataclass
from typing import Optional, List, Dict
from concurrent.futures import ThreadPoolExecutor

# Reuse existing infrastructure
from client_wrapper import PolymarketClient
from data_feed import PolymarketWebSocket, UserWebSocket, FillEvent, LocalOrderBook
from py_clob_client.clob_types import OrderArgs, OrderType, PostOrdersArgs
from py_clob_client.order_builder.constants import BUY, SELL

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("MomentumSniper")

# Silence noisy libraries
for lib in ["httpx", "websockets", "urllib3"]:
    logging.getLogger(lib).setLevel(logging.WARNING)


class BotState(Enum):
    SCANNING = "scanning"
    BUYING = "buying"
    HOLDING = "holding"
    EXITING = "exiting"
    DONE = "done"


@dataclass
class Position:
    token_id: str
    side: str  # "YES" or "NO"
    entry_price: float
    size: float
    fill_time: float


@dataclass
class PendingBuy:
    token_id: str
    side: str
    price: float
    size: float
    start_time: float
    filled_size: float = 0.0
    avg_price: float = 0.0


class MomentumSniper:
    def __init__(
        self,
        market_slug: str,
        budget: float = 10.0,
        size: Optional[float] = None,
        dry_run: bool = False
    ):
        self.market_slug = market_slug
        self.budget = budget
        self.fixed_size = size
        self.dry_run = dry_run
        
        # Strategy Parameters
        self.TRIGGER_PRICE = 0.54
        self.MAX_BUY_PRICE = 0.55
        self.PROFIT_TARGET = 0.05
        self.MIN_SIZE = 5.0
        self.STALE_BOOK_SEC = 1.0
        self.BUY_CONFIRM_TIMEOUT = 2.0
        
        # State
        self.running = False
        self.state = BotState.SCANNING
        self.position: Optional[Position] = None
        
        # Data
        self.token_id_yes: Optional[str] = None
        self.token_id_no: Optional[str] = None
        self.last_ask_yes: float = 0.0
        self.last_ask_no: float = 0.0
        self.last_update: float = 0.0
        
        # Infrastructure
        self.poly_client = PolymarketClient()
        self.client = self.poly_client.get_client()
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.wallet_address = getattr(self.poly_client, 'address', '').lower()
        
        # Websockets
        self.poly_ws: Optional[PolymarketWebSocket] = None
        self.user_ws: Optional[UserWebSocket] = None
        
        # Order tracking
        self.pending_buy_order_id: Optional[str] = None
        self.buy_start_time: float = 0
        self.pending_buy: Optional[PendingBuy] = None

    async def _init_infrastructure(self):
        """Initialize tokens and websockets."""
        self._fetch_tokens()
        
        # Check Balance & Connection (User Verification)
        try:
            if not self.dry_run:
                logger.info(f"🔑 Wallet Connected: {self.wallet_address}")
        except Exception as e:
             logger.warning(f"⚠️ Balance Check Failed (Keys might be invalid?): {e}")

        # Public Feed
        self.poly_ws = PolymarketWebSocket([self.token_id_yes, self.token_id_no])
        self.poly_ws.on_book_update = self._on_book_update
        
        # User Feed (for fast fills) - Optional
        try:
            creds = self.poly_client.get_credentials()
            self.user_ws = UserWebSocket(creds, debug=True)
            self.user_ws.on_fill = self._on_fill
        except Exception as e:
            logger.warning(f"User WebSocket init failed (continuing without it): {e}")
            self.user_ws = None

    async def _check_position_rest(self):
        """Fallback: Check position via REST."""
        if self.dry_run:
            return

        try:
            def _apply_rest_position(token_id: str, side: str, pos_size: float) -> bool:
                if pos_size <= 0:
                    return False

                entry_price = None
                if self.pending_buy and self.pending_buy.token_id == token_id:
                    entry_price = self.pending_buy.avg_price if self.pending_buy.filled_size > 0 else self.pending_buy.price
                elif self.position and self.position.token_id == token_id and self.position.entry_price > 0:
                    entry_price = self.position.entry_price
                else:
                    entry_price = self.last_ask_yes if side == "YES" else self.last_ask_no

                if entry_price is None or entry_price <= 0:
                    entry_price = 0.0

                if self.pending_buy and pos_size < self.pending_buy.size:
                    logger.warning(f"⚠️ Partial fill detected via REST: {pos_size:.2f} / {self.pending_buy.size:.2f}")

                self.position = Position(token_id, side, entry_price, pos_size, time.time())
                self.state = BotState.HOLDING
                self.pending_buy = None
                return True

            if self.token_id_yes:
                pos_yes = float(self.poly_client.get_position(self.token_id_yes))
                if _apply_rest_position(self.token_id_yes, "YES", pos_yes):
                    logger.info(f"🔎 REST Check: YES Position confirmed: {pos_yes}")
                    return

            if self.token_id_no:
                pos_no = float(self.poly_client.get_position(self.token_id_no))
                if _apply_rest_position(self.token_id_no, "NO", pos_no):
                    logger.info(f"🔎 REST Check: NO Position confirmed: {pos_no}")
                    return
            
            # If we think we are holding but have 0 pos, we might have sold OR buy failed
            if self.state == BotState.HOLDING:
                if self.position:
                    # Check specific token
                    current_pos = float(self.poly_client.get_position(self.position.token_id))
                    if current_pos == 0:
                        logger.warning("🔎 REST Check: Position is 0. Buy failed or Sold?")
                        # If roughly same time as buy, probably failed FOK.
                        if time.time() - self.position.fill_time < 5:
                             logger.info("↩️  Assuming FOK Buy Failed -> Reverting to SCANNING")
                             self.state = BotState.SCANNING
                             self.position = None
                        else:
                             # Assume Sold if verified? Or maybe we just sold?
                             # Without User WS, we might miss the sell fill event.
                             # If we were holding, and now 0, likely sold.
                             logger.info("✅ Position Gone -> Assuming SOLD")
                             self.state = BotState.DONE
                             self.running = False
            
            if self.state == BotState.EXITING:
                 # Check if position is gone
                 if self.position:
                     current_pos = float(self.poly_client.get_position(self.position.token_id))
                     if current_pos == 0:
                         logger.info("✅ Position Gone -> SOLD")
                         self.state = BotState.DONE
                         self.running = False
                     else:
                         # Still holding; retry sell
                         self.state = BotState.HOLDING


        except Exception as e:
            logger.error(f"REST Position check failed: {e}")

    def _fetch_tokens(self):
        """Map market slug to token IDs."""
        try:
            resp = httpx.get(
                "https://gamma-api.polymarket.com/markets",
                params={"slug": self.market_slug},
                timeout=10.0
            )
            resp.raise_for_status()
            data = resp.json()
            
            if not data:
                raise ValueError("Market not found")
            
            market = data[0]
            tokens = json.loads(market.get("clobTokenIds", "[]"))
            outcomes = json.loads(market.get("outcomes", "[]"))
            
            # Map YES/NO correctness
            # Handle "Yes"/"No" and "Up"/"Down" cases
            outcome_0 = str(outcomes[0]).lower()
            if "yes" in outcome_0 or "up" in outcome_0:
                self.token_id_yes = tokens[0]
                self.token_id_no = tokens[1]
                logger.info(f"✅ Market Mapped: {market.get('question', self.market_slug)}")
                logger.info(f"   YES (Up/Yes):   {self.token_id_yes}")
                logger.info(f"   NO  (Down/No):  {self.token_id_no}")
            else:
                self.token_id_yes = tokens[1]
                self.token_id_no = tokens[0]
                logger.info(f"✅ Market Mapped: {market.get('question', self.market_slug)}")
                logger.info(f"   YES (Up/Yes):   {self.token_id_yes}")
                logger.info(f"   NO  (Down/No):  {self.token_id_no}")
            
        except Exception as e:
            logger.error(f"Failed to fetch market: {e}")
            raise

    # =========================================================================
    # WEBSOCKET HANDLERS
    # =========================================================================

    def _on_book_update(self, book: LocalOrderBook):
        """Update local best asks."""
        if not book.asks:
            return
            
        best_ask = float(book.asks[0].price)
        
        if book.token_id == self.token_id_yes:
            self.last_ask_yes = best_ask
        elif book.token_id == self.token_id_no:
            self.last_ask_no = best_ask
        
        self.last_update = time.time()

    def _on_fill(self, event: FillEvent):
        """Handle execution reports."""
        if event.token_id not in [self.token_id_yes, self.token_id_no]:
            return
            
        # Buying Fill
        if event.side.lower() == "buy":
            side = "YES" if event.token_id == self.token_id_yes else "NO"
            logger.info(f"⚡ BUY FILLED: {side} {event.size} @ {event.price:.3f}")

            # Update/accumulate position for partial fills
            if not self.position or self.position.token_id != event.token_id:
                self.position = Position(
                    token_id=event.token_id,
                    side=side,
                    entry_price=event.price,
                    size=event.size,
                    fill_time=time.time()
                )
            else:
                total_cost = self.position.entry_price * self.position.size + event.price * event.size
                new_size = self.position.size + event.size
                self.position.size = new_size
                self.position.entry_price = total_cost / new_size
                self.position.fill_time = time.time()

            # Track pending buy progress
            if self.pending_buy and self.pending_buy.token_id == event.token_id:
                pb = self.pending_buy
                total_cost = pb.avg_price * pb.filled_size + event.price * event.size
                pb.filled_size += event.size
                pb.avg_price = total_cost / pb.filled_size

            self.state = BotState.HOLDING
            self.pending_buy = None
        
        # Selling Fill
        elif event.side.lower() == "sell" and self.state == BotState.EXITING:
            logger.info(f"💰 SELL FILLED: {event.size} @ {event.price:.3f}")
            self.state = BotState.DONE
            self.running = False


    # =========================================================================
    # CORE LOGIC
    # =========================================================================

    async def _scan_and_execute(self):
        """5Hz Scan Loop."""
        if self.state != BotState.SCANNING:
            return
        if self.last_update == 0 or (time.time() - self.last_update) > self.STALE_BOOK_SEC:
            return

        # Check YES
        if self.last_ask_yes >= self.TRIGGER_PRICE and self.last_ask_yes <= self.MAX_BUY_PRICE:
            await self._execute_buy(self.token_id_yes, "YES", self.last_ask_yes)
            return

        # Check NO
        if self.last_ask_no >= self.TRIGGER_PRICE and self.last_ask_no <= self.MAX_BUY_PRICE:
            await self._execute_buy(self.token_id_no, "NO", self.last_ask_no)
            return

    async def _execute_buy(self, token_id: str, side: str, price: float):
        """Execute Single-Shot Buy."""
        price = round(price, 2)
        logger.info(f"🚀 MOMENTUM TRIGGER: Buying {side} @ {price:.2f}")
        self.state = BotState.BUYING
        self.buy_start_time = time.time()
        
        if self.fixed_size is not None:
            size = float(self.fixed_size)
            est_cost = size * price
            if self.budget > 0 and est_cost > self.budget:
                logger.warning(
                    f"⚠️ Fixed size {size:.2f} costs ~${est_cost:.2f} which exceeds budget ${self.budget:.2f}"
                )
        else:
            size = self.budget / price

        size = round(size, 2)
        if size < self.MIN_SIZE:
            logger.warning(f"⚠️ Budget ${self.budget} too small for price {price:.2f} (Size {size:.1f} < Min {self.MIN_SIZE})")
            # Stop to prevent spam.
            logger.error("STOPPING: Budget rule violation.")
            self.state = BotState.DONE 
            self.running = False
            return

        if self.dry_run:
            logger.info(f"🧪 DRY RUN: Bought {size:.1f} {side} @ {price:.3f}")
            self.position = Position(token_id, side, price, size, time.time())
            self.state = BotState.HOLDING
            return

        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=BUY
            )
            signed = self.client.create_order(order_args)
            resp = self.client.post_orders([
                PostOrdersArgs(
                    order=signed,
                    orderType=OrderType.FOK, # Fill or Kill
                    postOnly=False
                )
            ])
            
            if hasattr(resp, "orders") and resp.orders:
                self.pending_buy_order_id = resp.orders[0].id
                logger.info(f"📝 Buy Order Sent: {self.pending_buy_order_id}")
                # Track pending buy; wait for fill confirmation
                self.pending_buy = PendingBuy(token_id, side, price, size, time.time())
                
            elif isinstance(resp, dict) and resp.get("orderID"):
                self.pending_buy_order_id = resp.get("orderID")
                logger.info(f"📝 Buy Order Sent: {self.pending_buy_order_id}")
                self.pending_buy = PendingBuy(token_id, side, price, size, time.time())
            else:
                logger.error(f"❌ Buy Failed: {resp}")
                self.state = BotState.SCANNING # Reset if failed

        except Exception as e:
            logger.error(f"❌ Buy Exception: {e}")
            self.state = BotState.SCANNING

    async def _manage_position(self):
        """Manage Exit."""
        if self.state != BotState.HOLDING or not self.position:
            return

        # Target: Entry + 5¢
        if self.position.entry_price <= 0:
            # Avoid selling with a zero/unknown entry price
            logger.warning("⚠️ Missing entry price; skipping sell check until confirmed.")
            return
        target_price = self.position.entry_price + self.PROFIT_TARGET
        token_id = self.position.token_id
        
        # Current Market Price
        current_bid = 0.0
        book = self.poly_ws.get_order_book(token_id)
        if book and book.bids:
            current_bid = float(book.bids[0].price)
        
        # Check if we can sell
        if current_bid >= target_price:
            await self._execute_sell(token_id, current_bid, self.position.size)
        elif self.dry_run:
             # Fast forward dry run
             if time.time() - self.position.fill_time > 5:
                 logger.info(f"🧪 DRY RUN: Simulated target hit at {target_price:.3f}")
                 self.state = BotState.DONE
                 self.running = False


    async def _execute_sell(self, token_id: str, price: float, size: float):
        """Execute Sell."""
        self.state = BotState.EXITING
        logger.info(f"💰 SELLING @ {price:.3f} (Profit Target Hit)")
        
        if self.dry_run:
            logger.info("🧪 DRY RUN: Sold")
            self.state = BotState.DONE
            self.running = False
            return

        try:
            # Cancel any open orders first
            self.client.cancel_all()
            
            # Place limit sell
            order_args = OrderArgs(
                token_id=token_id,
                price=price, # Sell into the bid
                size=size,
                side=SELL
            )
            signed = self.client.create_order(order_args)
            self.client.post_orders([
                PostOrdersArgs(
                    order=signed,
                    orderType=OrderType.IOC, # Immediate or Cancel to take liquidity
                    postOnly=False
                )
            ])
            # Wait briefly for fill event or check
            await asyncio.sleep(0.5)
            
            # Check if still holding?
            # If done, state will be DONE (via _on_fill or _check_position_rest)
            # If still EXITING, we failed to sell or partial fill?
            if self.state == BotState.EXITING:
                # Check real position quickly
                pos = float(self.poly_client.get_position(token_id))
                if pos > 0:
                    logger.warning(f"⚠️ Sell IOC likely cancelled/failed (Pos: {pos}). Retrying...")
                    self.state = BotState.HOLDING
                else:
                    logger.info("✅ Sell Confirmed (Zero Pos)")
                    self.state = BotState.DONE
                    self.running = False
 
        except Exception as e:
            logger.error(f"❌ Sell Failed: {e}")
            self.state = BotState.HOLDING # Retry

    # =========================================================================
    # MAIN LOOP
    # =========================================================================

    async def run(self):
        self.running = True
        await self._init_infrastructure()
        
        logger.info("="*60)
        logger.info("MOMENTUM SNIPER ACTIVATED")
        logger.info(f"Market: {self.market_slug}")
        logger.info(f"Trigger: {self.TRIGGER_PRICE} - {self.MAX_BUY_PRICE}")
        logger.info(f"Budget: ${self.budget}")
        logger.info("="*60)
        
        # Start WS
        asyncio.create_task(self.poly_ws.connect())
        if self.user_ws:
             asyncio.create_task(self.user_ws.connect())
        
        await asyncio.sleep(2) # Warmup
        
        last_rest_check = 0
        
        try:
            loop_count = 0
            while self.running:
                loop_count += 1
                # Periodic REST Check (every 1s)
                if time.time() - last_rest_check > 1.0:
                    await self._check_position_rest()
                    last_rest_check = time.time()
                
                # Heartbeat every 1s (every ~5 loops)
                if loop_count % 5 == 0:
                    logger.info(f"💓 Scanning (Loop {loop_count})... YES: {self.last_ask_yes:.3f} | NO: {self.last_ask_no:.3f}")

                if self.state == BotState.SCANNING:
                    await self._scan_and_execute()
                elif self.state == BotState.HOLDING:
                    await self._manage_position()
                
                # 5Hz Loop
                await asyncio.sleep(0.2)
                
                # Check for buy timeout
                if self.state == BotState.BUYING and self.pending_buy:
                    if time.time() - self.pending_buy.start_time > self.BUY_CONFIRM_TIMEOUT:
                        logger.warning("⚠️ Buy confirm timeout - assuming no fill, resetting.")
                        self.state = BotState.SCANNING
                        self.pending_buy = None
                     
        except KeyboardInterrupt:
            logger.info("STOPPING")
        finally:
            self.running = False

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("slug", help="Market Slug")
    parser.add_argument("--budget", type=float, default=3.0)
    parser.add_argument("--size", type=float, default=None, help="Fixed size in shares (overrides budget sizing).")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    
    bot = MomentumSniper(args.slug, budget=args.budget, size=args.size, dry_run=args.dry_run)
    asyncio.run(bot.run())
