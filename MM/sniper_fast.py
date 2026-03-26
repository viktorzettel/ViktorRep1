#!/usr/bin/env python3
"""
Polymarket Mean-Reversion Scalp Bot v2.1 - Conservative Settings
Buys first token ≤46¢, sells ≥entry+4¢, $6 budget.
Minimal CLI: python scalp_bot.py <market_slug> <token_id>
"""

import asyncio
import time
import argparse
import logging
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, List
from concurrent.futures import ThreadPoolExecutor

from client_wrapper import PolymarketClient
from data_feed import (
    PolymarketWebSocket,
    LocalOrderBook,
    OrderBookLevel,
    UserWebSocket,
    FillEvent,
)
from py_clob_client.clob_types import OrderArgs, OrderType, PostOrdersArgs
from py_clob_client.order_builder.constants import BUY, SELL

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("ReversionScalp")

# Silence noisy libraries
for lib in ["httpx", "websockets", "urllib3"]:
    logging.getLogger(lib).setLevel(logging.WARNING)


class BotState(Enum):
    MONITORING = "monitoring"
    BUYING = "buying"
    HOLDING = "holding"
    EXITING = "exiting"
    DONE = "done"


@dataclass
class PositionTracker:
    bought_side: Optional[str] = None
    entry_price: float = 0.0
    inventory: float = 0.0
    fill_time: float = 0.0


@dataclass
class OrderTracker:
    order_id: str
    token_id: str
    side: str
    timestamp: float
    price: float


@dataclass
class BuyOrder:
    post_args: PostOrdersArgs
    price: float
    post_only: bool


@dataclass
class PendingBuy:
    token_id: str
    side_label: str
    price: float
    timestamp: float
    order_id: Optional[str] = None


class ReversionScalpBot:
    # CONSERVATIVE DEFAULTS - Embedded in code
    DEFAULT_BUDGET: float = 3.0           # Was 10.0
    DEFAULT_THRESHOLD: float = 0.45       # Was 0.45
    DEFAULT_PROFIT: float = 0.04          # Was 0.05
    DEFAULT_MIN_SIZE: float = 5.0
    DEFAULT_PRICE_FRESHNESS_SEC: float = 1.0
    DEFAULT_EXIT_TIMEOUT_SEC: float = 20.0
    
    def __init__(
        self,
        market_slug: str,
        token_id_yes: str,
        budget: Optional[float] = None,      # Now optional, uses default
        entry_threshold: Optional[float] = None,  # Now optional
        profit_target: Optional[float] = None,    # Now optional
        dry_run: bool = False
    ):
        self.market_slug = market_slug
        self.token_id_yes = token_id_yes
        self.token_id_no: Optional[str] = None
        
        # Apply defaults if not provided
        self.budget = budget if budget is not None else self.DEFAULT_BUDGET
        self.entry_threshold = entry_threshold if entry_threshold is not None else self.DEFAULT_THRESHOLD
        self.profit_target = profit_target if profit_target is not None else self.DEFAULT_PROFIT
        self.min_size = self.DEFAULT_MIN_SIZE
        self.price_freshness_sec = self.DEFAULT_PRICE_FRESHNESS_SEC
        self.exit_timeout_sec = self.DEFAULT_EXIT_TIMEOUT_SEC
        
        self.dry_run = dry_run
        
        self.running = False
        self.state = BotState.MONITORING
        self.pos = PositionTracker()
        
        self._last_yes_price: float = 0.0
        self._last_no_price: float = 0.0
        self._last_price_update: float = 0.0
        self._last_price_update_by_token: Dict[str, float] = {}
        self._last_yes_ask: Optional[float] = None
        self._last_no_ask: Optional[float] = None
        self._prev_yes_ask: Optional[float] = None
        self._prev_no_ask: Optional[float] = None
        self._armed_yes: bool = True
        self._armed_no: bool = True
        
        self._orders: List[OrderTracker] = []
        self._recent_trades: List[Dict] = []
        
        self._pending_buy: Optional[PendingBuy] = None
        self._buy_timeout: float = 3.0

        self._last_sell_attempt: float = 0.0
        self._sell_cooldown: float = 2.0

        self._last_position_check: float = 0.0
        self._position_check_interval: float = 0.6
        self._position_check_interval_buying: float = 0.2
        self._last_yes_pos: float = 0.0
        self._last_no_pos: float = 0.0
        self._last_total_pos: float = 0.0
        
        self._executor = ThreadPoolExecutor(max_workers=2)
        
        self.poly_client = PolymarketClient()
        self.client = self.poly_client.get_client()
        self.wallet_address = getattr(self.poly_client, 'address', '').lower()
        self.user_ws: Optional[UserWebSocket] = None
        self._user_ws_task: Optional[asyncio.Task] = None
        self._init_user_ws()
        
        self._fetch_tokens()
        self._init_feeds()

    def _fetch_tokens(self):
        """Fetch and map token IDs."""
        import httpx
        import json
        
        try:
            resp = httpx.get(
                "https://gamma-api.polymarket.com/markets",
                params={"slug": self.market_slug},
                timeout=10.0
            )
            resp.raise_for_status()
            markets = resp.json()
            
            if not markets:
                raise ValueError(f"Market {self.market_slug} not found")
            
            market_data = markets[0]
            tokens_raw = market_data.get("clobTokenIds", "[]")
            tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
            
            if len(tokens) < 2:
                raise ValueError("Market needs exactly 2 tokens")
            
            outcomes_raw = market_data.get('outcomes', '[]')
            outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
            
            if len(outcomes) >= 2:
                if 'yes' in str(outcomes[0]).lower():
                    self.token_id_yes = tokens[0]
                    self.token_id_no = tokens[1]
                else:
                    self.token_id_yes = tokens[1]
                    self.token_id_no = tokens[0]
            else:
                self.token_id_yes = tokens[0]
                self.token_id_no = tokens[1]
            
            logger.info(f"Tokens mapped - YES: {self.token_id_yes[:20]}... NO: {self.token_id_no[:20]}...")
            
        except Exception as e:
            logger.error(f"Failed to fetch tokens: {e}")
            raise SystemExit(1)

    def _init_feeds(self):
        """Initialize WebSocket data feeds."""
        if not self.token_id_yes or not self.token_id_no:
            raise ValueError("Tokens not initialized")
        
        tokens = [self.token_id_yes, self.token_id_no]
        self.poly_ws = PolymarketWebSocket(tokens)
        self.poly_ws.on_book_update = self._on_book_update
        self.poly_ws.on_trade = self._on_trade
        
        logger.info("WebSocket feeds initialized")

    def _init_user_ws(self):
        """Initialize authenticated user WebSocket for real-time fills."""
        creds = self.poly_client.get_credentials()
        if not creds:
            logger.warning("No API credentials available for user WebSocket")
            return
        try:
            self.user_ws = UserWebSocket(creds)
            self.user_ws.on_fill = self._on_fill
        except Exception as e:
            logger.warning(f"User WebSocket init failed: {e}")

    def _on_book_update(self, book: LocalOrderBook):
        """Handle order book updates from WebSocket."""
        now = time.time()
        
        if book.token_id == self.token_id_yes:
            self._last_yes_price = book.mid_price or 0.0
            self._last_price_update = now
            self._last_price_update_by_token[self.token_id_yes] = now
        elif book.token_id == self.token_id_no:
            self._last_no_price = book.mid_price or 0.0
            self._last_price_update = now
            self._last_price_update_by_token[self.token_id_no] = now

    def _on_trade(self, trade: Dict):
        """Track our own fills for accurate pricing."""
        maker = trade.get('maker_address', '').lower()
        taker = trade.get('taker_address', '').lower()
        
        if self.wallet_address and (self.wallet_address in [maker, taker]):
            self._recent_trades.append({
                'token_id': trade.get('token_id'),
                'price': float(trade.get('price', 0)),
                'side': 'buy' if taker == self.wallet_address else 'sell',
                'time': time.time()
            })
            self._recent_trades = self._recent_trades[-100:]

    def _on_fill(self, event: FillEvent) -> None:
        """Handle user-specific fill events to reduce fill detection latency."""
        token_id = event.token_id
        if token_id not in [self.token_id_yes, self.token_id_no]:
            return
        
        side = event.side.lower()
        label = "YES" if token_id == self.token_id_yes else "NO"
        
        if side == "buy":
            self._recent_trades.append({
                'token_id': token_id,
                'price': event.price,
                'side': 'buy',
                'time': time.time()
            })
            self._recent_trades = self._recent_trades[-100:]
            
            if self.pos.bought_side is None or self.pos.bought_side == label:
                self.pos.bought_side = label
                self.pos.entry_price = event.price
                self.pos.inventory = max(self.pos.inventory, 0.0) + event.size
                self.pos.fill_time = time.time()
                self.state = BotState.HOLDING
                self._pending_buy = None
                logger.info(f"🎯 {label} FILLED (WS): {event.size:.2f} @ {event.price:.3f}")
        
        elif side == "sell":
            if self.pos.bought_side == label:
                self.pos.inventory = max(0.0, self.pos.inventory - event.size)
                if self.pos.inventory < 0.5:
                    logger.info(f"✅ SOLD (WS): {label} @ {event.price:.3f}")
                    self.state = BotState.DONE
                    self.running = False

    def _get_fill_price(self, token_id: str, default: float) -> float:
        """Get actual fill price from recent trade history."""
        for trade in reversed(self._recent_trades):
            if trade['token_id'] == token_id and trade['side'] == 'buy':
                return trade['price']
        return default

    def _is_price_fresh(self, token_id: Optional[str] = None) -> bool:
        """Check if price data is recent (configurable freshness window)."""
        if token_id:
            last = self._last_price_update_by_token.get(token_id, 0.0)
            return (time.time() - last) < self.price_freshness_sec
        return (time.time() - self._last_price_update) < self.price_freshness_sec

    async def _get_position(self, token_id: str) -> float:
        """Get token balance from blockchain (non-blocking)."""
        if not token_id:
            return 0.0
        
        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(self._executor, self.poly_client.get_position, token_id),
                timeout=5.0
            )
            return float(result) if result else 0.0
        except Exception as e:
            logger.debug(f"Position fetch failed for {token_id[:15]}: {e}")
            return 0.0

    async def _check_positions(self) -> tuple[float, float, float]:
        """Check both token positions and fill state."""
        yes_pos = await self._get_position(self.token_id_yes)
        no_pos = await self._get_position(self.token_id_no)
        
        total_pos = yes_pos + no_pos
        
        if total_pos > 0.5 and self.pos.bought_side is None:
            if yes_pos > 0.5 and no_pos < 0.5:
                fallback_price = self._last_yes_price
                if self._pending_buy and self._pending_buy.token_id == self.token_id_yes:
                    fallback_price = self._pending_buy.price
                fill_price = self._get_fill_price(self.token_id_yes, fallback_price)
                self.pos.bought_side = 'YES'
                self.pos.inventory = yes_pos
                self.pos.entry_price = fill_price
                self.pos.fill_time = time.time()
                self.state = BotState.HOLDING
                self._pending_buy = None
                
                logger.info(f"🎯 YES FILLED: {yes_pos:.2f} shares @ {fill_price:.3f}")
                
            elif no_pos > 0.5 and yes_pos < 0.5:
                fallback_price = self._last_no_price
                if self._pending_buy and self._pending_buy.token_id == self.token_id_no:
                    fallback_price = self._pending_buy.price
                fill_price = self._get_fill_price(self.token_id_no, fallback_price)
                self.pos.bought_side = 'NO'
                self.pos.inventory = no_pos
                self.pos.entry_price = fill_price
                self.pos.fill_time = time.time()
                self.state = BotState.HOLDING
                self._pending_buy = None
                
                logger.info(f"🎯 NO FILLED: {no_pos:.2f} shares @ {fill_price:.3f}")
            else:
                logger.warning(f"Ambiguous position: YES={yes_pos:.2f}, NO={no_pos:.2f}")
        
        return yes_pos, no_pos, total_pos

    async def _cancel_orders_for_token(self, token_id: str):
        """Cancel all orders for specific token."""
        if self.dry_run or not token_id:
            return
        
        try:
            self.client.cancel_market_orders(asset_id=token_id)
            logger.debug(f"Cancelled orders for {token_id[:15]}")
        except Exception as e:
            logger.warning(f"Cancel failed for {token_id[:15]}: {e}")
        
        self._orders = [o for o in self._orders if o.token_id != token_id]

    async def _cancel_all_orders(self):
        """Cancel all open orders."""
        if self.dry_run:
            return
        
        try:
            self.client.cancel_all()
            self._orders = []
            logger.info("Cancelled all orders")
        except Exception as e:
            logger.warning(f"Cancel all failed: {e}")

    def _get_min_sell_price(self) -> float:
        """Calculate minimum sell price (entry + 4¢ profit)."""
        if self.pos.entry_price <= 0:
            return float('inf')
        return self.pos.entry_price + self.profit_target

    async def _should_sell(self) -> tuple[bool, float]:
        """Check if we should sell and at what price."""
        if self.state != BotState.HOLDING:
            return False, 0.0
        
        token_id = self.token_id_yes if self.pos.bought_side == 'YES' else self.token_id_no
        min_price = self._get_min_sell_price()
        
        book = self.poly_ws.get_order_book(token_id)
        if not book or not book.bids or not self._is_price_fresh(token_id):
            return False, 0.0
        
        best_bid = float(book.bids[0].price)
        
        if best_bid >= min_price:
            target = best_bid - 0.002
            return True, max(target, min_price)
        
        return False, 0.0

    async def _execute_sell(self) -> bool:
        """Execute sell at ≥ entry + 4¢."""
        if self.state == BotState.EXITING:
            return False
        
        self.state = BotState.EXITING
        start_time = time.time()
        
        token_id = self.token_id_yes if self.pos.bought_side == 'YES' else self.token_id_no
        label = self.pos.bought_side
        amount = self.pos.inventory
        min_price = self._get_min_sell_price()
        
        if amount < 0.5:
            logger.warning("No inventory to sell")
            self.state = BotState.HOLDING
            return False
        
        if self.dry_run:
            logger.info(f"🧪 DRY RUN: Would sell {label} {amount:.1f} @ ≥{min_price:.3f}")
            self.state = BotState.DONE
            self.running = False
            return True
        
        logger.info(f"💰 SELL START: {label} {amount:.1f} shares, min price {min_price:.3f}")
        
        attempt = 0
        while self.running and self.state == BotState.EXITING:
            if (time.time() - start_time) > self.exit_timeout_sec:
                logger.warning("Sell watchdog timeout - returning to HOLDING state")
                self.state = BotState.HOLDING
                return False
            attempt += 1
            
            should_sell, target_price = await self._should_sell()
            
            if not should_sell:
                if attempt % 10 == 0:
                    book = self.poly_ws.get_order_book(token_id)
                    if book and book.bids:
                        current_bid = float(book.bids[0].price)
                        logger.info(f"⏳ Waiting... Market {current_bid:.3f} < minimum {min_price:.3f}")
                
                await asyncio.sleep(0.5)
                continue
            
            try:
                await self._cancel_orders_for_token(token_id)
                
                sell_price = target_price
                logger.info(f"⚡ SELLING: {label} @ {sell_price:.3f} (market allows ≥{min_price:.3f})")
                
                order_args = OrderArgs(
                    token_id=token_id,
                    price=sell_price,
                    size=amount,
                    side=SELL
                )
                signed = self.client.create_order(order_args)
                
                resp = self.client.post_orders([
                    PostOrdersArgs(
                        order=signed,
                        orderType=OrderType.FOK,
                        postOnly=False
                    )
                ])
                
                order_id = self._extract_order_id(resp)
                if order_id:
                    self._orders.append(OrderTracker(
                        order_id=order_id,
                        token_id=token_id,
                        side='sell',
                        timestamp=time.time(),
                        price=sell_price
                    ))
                else:
                    logger.warning(f"Sell order not accepted: {self._resp_summary(resp)}")
                
                await asyncio.sleep(0.3)
                remaining = await self._get_position(token_id)
                
                if remaining < 0.5:
                    actual_profit = sell_price - self.pos.entry_price
                    logger.info(f"✅ SOLD: {amount:.1f} @ {sell_price:.3f}, profit +{actual_profit:.3f} per share")
                    self.pos.inventory = 0
                    self.state = BotState.DONE
                    self.running = False
                    return True
                
                logger.warning("FOK didn't fill immediately, retrying...")
                if order_id:
                    await self._cancel_order_by_id(order_id)
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Sell attempt {attempt} failed: {e}")
                await asyncio.sleep(1)
        
        logger.warning("Sell loop exited without completion")
        return False

    async def _cancel_order_by_id(self, order_id: str):
        """Cancel specific order."""
        if self.dry_run or not order_id:
            return
        
        try:
            self.client.cancel(order_id)
        except Exception as e:
            logger.debug(f"Cancel {order_id[:10]}... failed: {e}")

    def _extract_order_id(self, resp) -> Optional[str]:
        if hasattr(resp, "orders") and resp.orders:
            try:
                return resp.orders[0].id
            except Exception:
                return None
        if isinstance(resp, dict):
            return resp.get("orderID") or resp.get("orderId") or resp.get("id")
        return None

    def _resp_summary(self, resp) -> str:
        try:
            return repr(resp)
        except Exception:
            return "<unprintable response>"

    def _generate_buy_order(self, token_id: str, side_label: str, price: float, size: float) -> BuyOrder:
        """Generate buy order for token at the given price (FOK)."""
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=BUY
        )
        signed = self.client.create_order(order_args)
        
        return BuyOrder(
            post_args=PostOrdersArgs(
                order=signed,
                orderType=OrderType.FOK,
                postOnly=False
            ),
            price=price,
            post_only=False
        )

    def _get_best_ask(self, token_id: str) -> Optional[float]:
        book = self.poly_ws.get_order_book(token_id)
        if not book or not book.asks:
            return None
        return float(book.asks[0].price)

    def _get_best_ask_level(self, token_id: str) -> Optional[OrderBookLevel]:
        book = self.poly_ws.get_order_book(token_id)
        if not book or not book.asks:
            return None
        return book.asks[0]

    def _get_cumulative_ask_size(self, token_id: str, limit_price: float) -> Optional[float]:
        book = self.poly_ws.get_order_book(token_id)
        if not book or not book.asks:
            return None
        total = 0.0
        for level in book.asks:
            if float(level.price) <= limit_price:
                total += float(level.size)
            else:
                break
        return total

    def _compute_buy_size(self, price: float, available_size: Optional[float]) -> Optional[float]:
        desired = self.budget / price
        size = max(desired, self.min_size)
        if available_size is not None and available_size > 0:
            size = min(size, available_size)
        if size < self.min_size:
            return None
        return size

    def _update_last_asks(self) -> None:
        if self._is_price_fresh(self.token_id_yes):
            yes_ask = self._get_best_ask(self.token_id_yes)
            if yes_ask is not None:
                self._prev_yes_ask = self._last_yes_ask
                self._last_yes_ask = yes_ask
                if yes_ask > self.entry_threshold:
                    self._armed_yes = True
        if self._is_price_fresh(self.token_id_no):
            no_ask = self._get_best_ask(self.token_id_no)
            if no_ask is not None:
                self._prev_no_ask = self._last_no_ask
                self._last_no_ask = no_ask
                if no_ask > self.entry_threshold:
                    self._armed_no = True

    def _find_buy_candidate(self) -> Optional[tuple[str, str, float]]:
        """Pick a side to buy if price is at/below threshold."""
        yes_ask = self._last_yes_ask if self._is_price_fresh(self.token_id_yes) else None
        no_ask = self._last_no_ask if self._is_price_fresh(self.token_id_no) else None
        
        candidates: List[tuple[str, str, float]] = []
        if yes_ask is not None and yes_ask <= self.entry_threshold and self._armed_yes:
            candidates.append(("YES", self.token_id_yes, yes_ask))
        if no_ask is not None and no_ask <= self.entry_threshold and self._armed_no:
            candidates.append(("NO", self.token_id_no, no_ask))
        
        if not candidates:
            return None
        
        candidates.sort(key=lambda x: x[2])
        return candidates[0]

    async def _cleanup_stale_orders(self):
        """Remove order tracking older than 60 seconds."""
        now = time.time()
        stale = [o for o in self._orders if (now - o.timestamp) > 60]
        
        for o in stale:
            try:
                if not self.dry_run and o.side == 'buy':
                    self.client.cancel(o.order_id)
            except:
                pass
        
        self._orders = [o for o in self._orders if (now - o.timestamp) <= 60]

    async def _step(self):
        """Main execution step - runs every 100ms."""
        self._update_last_asks()
        
        now = time.time()
        check_interval = self._position_check_interval_buying if self.state == BotState.BUYING else self._position_check_interval
        if (now - self._last_position_check) >= check_interval:
            yes_pos, no_pos, total_pos = await self._check_positions()
            self._last_position_check = now
            self._last_yes_pos = yes_pos
            self._last_no_pos = no_pos
            self._last_total_pos = total_pos
        else:
            yes_pos, no_pos, total_pos = self._last_yes_pos, self._last_no_pos, self._last_total_pos
        
        if self.state == BotState.DONE:
            return
        
        if self.state == BotState.HOLDING:
            now = time.time()
            if now - self._last_sell_attempt >= self._sell_cooldown:
                self._last_sell_attempt = now
                
                should_sell, _ = await self._should_sell()
                if should_sell:
                    await self._execute_sell()
                    return
        
        if self.state == BotState.MONITORING:
            await self._cleanup_stale_orders()
            
            if total_pos < 0.5:
                candidate = self._find_buy_candidate()
                if candidate and self._pending_buy is None:
                    label, token_id, price = candidate
                    level = self._get_best_ask_level(token_id)
                    if not level:
                        return
                    current_price = float(level.price)
                    if current_price > self.entry_threshold:
                        return
                    
                    limit_price = self.entry_threshold
                    available_size = self._get_cumulative_ask_size(token_id, limit_price)
                    logger.info(
                        f"🔎 Buy check {label}: best_ask={current_price:.3f} "
                        f"cum_size≤{limit_price:.2f}={available_size or 0:.2f} "
                        f"budget=${self.budget:.2f}"
                    )
                    size = self._compute_buy_size(limit_price, available_size)
                    if size is None:
                        logger.info(f"⚠️ Not enough size ≤{limit_price:.3f} to meet minimum ({self.min_size})")
                        return
                    
                    if label == "YES":
                        self._armed_yes = False
                    else:
                        self._armed_no = False
                    
                    order = self._generate_buy_order(token_id, label, limit_price, size)
                    
                    if self.dry_run:
                        logger.info(f"🧪 DRY RUN: Would post TAKER for {label} {size:.2f} @ {order.price:.3f}")
                        self._pending_buy = PendingBuy(
                            token_id=token_id,
                            side_label=label,
                            price=order.price,
                            timestamp=time.time()
                        )
                        self.state = BotState.BUYING
                        return
                    
                    try:
                        await self._cancel_orders_for_token(token_id)
                        resp = self.client.post_orders([order.post_args])
                        
                        order_id = self._extract_order_id(resp)
                        if order_id:
                            self._orders.append(OrderTracker(
                                order_id=order_id,
                                token_id=token_id,
                                side='buy',
                                timestamp=time.time(),
                                price=order.price
                            ))
                            logger.info(f"Posted {label} order {size:.2f} @ {order.price:.3f}")
                        else:
                            logger.warning(f"Buy order not accepted: {self._resp_summary(resp)}")
                            if label == "YES":
                                self._armed_yes = True
                            else:
                                self._armed_no = True
                            return
                        
                        self._pending_buy = PendingBuy(
                            token_id=token_id,
                            side_label=label,
                            price=order.price,
                            timestamp=time.time(),
                            order_id=order_id
                        )
                        self.state = BotState.BUYING
                        return
                    
                    except Exception as e:
                        logger.error(f"Failed to post {label} order: {e}")
                        self._pending_buy = None
                        self.state = BotState.MONITORING
                        return
        
        if self.state == BotState.BUYING and self._pending_buy:
            if (time.time() - self._pending_buy.timestamp) > self._buy_timeout:
                if not self.dry_run and self._pending_buy.order_id:
                    await self._cancel_order_by_id(self._pending_buy.order_id)
                logger.info("⏳ Buy timed out without fill, resuming scan")
                self._pending_buy = None
                self.state = BotState.MONITORING

    async def run(self):
        """Main bot loop."""
        self.running = True
        
        logger.info("=" * 60)
        logger.info(f"🎯 Reversion Scalp Bot v2.1 - Conservative")
        logger.info(f"Market: {self.market_slug}")
        logger.info(f"Settings: Entry ≤{self.entry_threshold} | Profit +{self.profit_target} | Budget ${self.budget}")
        logger.info(f"Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
        logger.info("=" * 60)
        
        ws_task = asyncio.create_task(self.poly_ws.connect())
        if self.user_ws:
            async def _run_user_ws():
                try:
                    await self.user_ws.connect()
                except Exception as e:
                    logger.warning(f"User WebSocket disabled: {e}")
            self._user_ws_task = asyncio.create_task(_run_user_ws())
        await asyncio.sleep(3)
        
        yes_pos, no_pos, _ = await self._check_positions()
        if yes_pos > 0.5 or no_pos > 0.5:
            logger.info(f"⚠️ Restart with position: YES={yes_pos:.1f}, NO={no_pos:.1f}")
        
        cycle = 0
        
        try:
            while self.running:
                cycle += 1
                await self._step()
                
                if cycle % 50 == 0 and self.state != BotState.DONE:
                    if self.state == BotState.HOLDING:
                        min_sell = self._get_min_sell_price()
                        logger.info(f"State: HOLDING {self.pos.bought_side} | Entry: {self.pos.entry_price:.3f} | Min sell: {min_sell:.3f}")
                    else:
                        logger.debug(f"State: {self.state.value}")
                
                await asyncio.sleep(0.1)
                
        except KeyboardInterrupt:
            logger.info("Shutdown requested...")
        finally:
            self.running = False
            
            if not self.dry_run and self.pos.inventory > 0.5 and self.state != BotState.DONE:
                logger.info("🚨 Emergency shutdown with open position")
                min_sell = self._get_min_sell_price()
                logger.warning(f"Position: {self.pos.bought_side} {self.pos.inventory:.1f} @ entry {self.pos.entry_price:.3f}")
                logger.warning(f"Manual sell required: ≥{min_sell:.3f} for profit")
            
            try:
                await self._cancel_all_orders()
            except:
                pass
            
            ws_task.cancel()
            if self._user_ws_task:
                self._user_ws_task.cancel()
            self._executor.shutdown(wait=False)
            
            logger.info("Bot stopped")


def main():
    # MINIMAL CLI - Only market_slug and token_id required
    parser = argparse.ArgumentParser(
        description="Polymarket Reversion Scalp Bot - Conservative Settings",
        epilog="Defaults: threshold=0.46, profit=0.04, budget=6.0"
    )
    parser.add_argument("market_slug", help="Market slug (e.g., 'bitcoin-up-or-down-february-2-5am-et')")
    parser.add_argument("token_id", help="Reference YES token ID")
    
    # Optional overrides (rarely needed)
    parser.add_argument("--budget", type=float, default=None, help="Override budget (default: 6.0)")
    parser.add_argument("--threshold", type=float, default=None, help="Override entry threshold (default: 0.46)")
    parser.add_argument("--profit", type=float, default=None, help="Override profit target (default: 0.04)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without trading")
    
    args = parser.parse_args()
    
    bot = ReversionScalpBot(
        market_slug=args.market_slug,
        token_id_yes=args.token_id,
        budget=args.budget,           # None = use default 6.0
        entry_threshold=args.threshold,  # None = use default 0.46
        profit_target=args.profit,    # None = use default 0.04
        dry_run=args.dry_run
    )
    
    try:
        asyncio.run(bot.run())
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
