#!/usr/bin/env python3
"""
Hourly BTC Sniper Bot - Clean Implementation
============================================
Simple strategy: Buy cheap, sell for profit, don't get stuck.

Key Features:
- Tiered buying: 4 tiers × $3 = $12 max
- Tiered selling: Sell % based on profit margin
- Side locking: Once you pick YES or NO, stick with it
- No re-entry: Once sold, we're done for this market
"""

import asyncio
import time
import argparse
import logging
from typing import Optional
from dataclasses import dataclass, field

# Local imports (reuse existing utilities)
from client_wrapper import PolymarketClient
from data_feed import PolymarketWebSocket, BinancePriceMonitor, LocalOrderBook
from orders import create_quote_orders, batch_cancel_and_post, extract_order_ids
from pricing import CryptoHourlyPricer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class SniperConfig:
    """All configurable parameters in one place."""
    # Budget - conservative: 2 tiers × $3 = $6 max spend
    tier_budget: float = 3.00         # $ per tier
    num_tiers: int = 2                # Total tiers (HARD LIMIT - only 2 buys)
    
    # Buy zones (YES price thresholds)
    buy_zone_yes: float = 0.38        # Buy YES when price < 38%
    buy_zone_no: float = 0.62         # Buy NO when price > 62% (i.e., NO < 38%)
    
    # Timing
    tier_timer_seconds: int = 60      # Seconds between tier buys
    base_quote_interval: float = 2.0  # Default cycle time (FAST)
    
    # Sell thresholds: profit -> (name, sell_pct, cycle_time_seconds)
    # UPDATED CONFIG:
    # +10¢ = IMMEDIATE (0.5s), Others = 2.0s
    sell_tiers: dict = field(default_factory=lambda: {
        0.10: ("FULL_LIQ", 1.00, 0.5),   # +10¢ = 100% sell, IMMEDIATE
        0.08: ("TIER_8", 0.40, 2.0),     # +8¢ = 40% sell
        0.05: ("TIER_5", 0.25, 2.0),     # +5¢ = 25% sell
        0.03: ("TIER_3", 0.15, 2.0),     # +3¢ = 15% sell
        0.02: ("TIER_2", 0.10, 2.0),     # +2¢ = 10% sell
    })
    
    # Safety - Polymarket minimum is ~5 shares
    min_shares: float = 5.0           # Minimum order size for buying


# ============================================================================
# SNIPER BOT
# ============================================================================

class HourlySniper:
    """Clean, focused hourly BTC sniper bot."""
    
    def __init__(
        self,
        market_slug: str,
        token_id_yes: str,
        config: SniperConfig = None,
        dry_run: bool = False,
    ):
        self.market_slug = market_slug
        self.token_id_yes = token_id_yes
        self.token_id_no: Optional[str] = None
        self.config = config or SniperConfig()
        self.dry_run = dry_run
        self.running = False
        
        # State
        self.inv_yes: float = 0.0
        self.inv_no: float = 0.0
        self.avg_entry_yes: float = 0.0
        self.avg_entry_no: float = 0.0
        self._last_mid_price: float = 0.0
        self._last_price_update_time: float = 0.0
        
        # Tiered buying state
        self._locked_side: Optional[str] = None  # "yes" or "no" - once set, don't change
        self._tiers_used: int = 0
        self._last_tier_time: float = 0.0
        self._has_sold: bool = False  # Once we sell, we're done
        self._pending_order_side: Optional[str] = None  # Track pending buy orders
        
        # Order tracking
        self._current_order_ids: list = []
        
        # Initialize client and feeds
        self._init_client()
        self._fetch_no_token()
        self._init_feeds()
    
    def _init_client(self):
        """Initialize Polymarket client."""
        self.poly_client = PolymarketClient()
        self.client = self.poly_client.get_client()
        logger.info(f"Client initialized for {self.poly_client.address}")
    
    def _fetch_no_token(self):
        """Fetch the NO token ID from Gamma API."""
        import httpx
        import json
        try:
            resp = httpx.get(
                "https://gamma-api.polymarket.com/markets",
                params={"slug": self.market_slug}
            )
            markets = resp.json()
            if markets:
                # clobTokenIds is often a string "[\"123\", \"456\"]"
                tokens_raw = markets[0].get("clobTokenIds", [])
                
                if isinstance(tokens_raw, str):
                    tokens = json.loads(tokens_raw)
                else:
                    tokens = tokens_raw
                    
                for t in tokens:
                    if t != self.token_id_yes:
                        self.token_id_no = t
                        logger.info(f"Found NO token: {self.token_id_no[:20]}...")
                        return
        except Exception as e:
            logger.warning(f"Could not fetch NO token: {e}")
    
    def _init_feeds(self):
        """Initialize data feeds."""
        tokens = [self.token_id_yes]
        if self.token_id_no:
            tokens.append(self.token_id_no)
        
        self.poly_feed = PolymarketWebSocket(tokens)
        self.poly_feed.on_book_update = self._on_book_update
        
        self.binance_feed = BinancePriceMonitor(["btcusdt"])
        
        # Fallback: Fetch initial price via REST to avoid waiting
        self._fetch_initial_price()

    def _fetch_initial_price(self):
        """Fetch initial order book via REST."""
        try:
            logger.info("Fetching initial price snapshot...")
            book = self.client.get_order_book(self.token_id_yes)
            logger.info(f"BOOK RAW: Bids={len(book.bids)} Asks={len(book.asks)}")
            if book.bids:
                logger.info(f"Top Bid: {book.bids[0]}")
            if book.asks:
                logger.info(f"Top Ask: {book.asks[0]}")
                
            if book and book.bids and book.asks:
                best_bid = float(book.bids[0].price)
                best_ask = float(book.asks[0].price)
                mid = (best_bid + best_ask) / 2
                self._last_mid_price = mid
                self._last_price_update_time = time.time()
                logger.info(f"✅ Initial Price: {mid:.3f}")
            else:
                logger.warning("⚠️ Empty order book - no valid price available. Check token ID or market liquidity.")
        except Exception as e:
            logger.warning(f"Could not fetch initial price: {e}")
    
    def _on_book_update(self, book: LocalOrderBook):
        """Handle order book updates."""
        if book.token_id == self.token_id_yes and book.mid_price:
            self._last_mid_price = book.mid_price
            self._last_price_update_time = time.time()
    
    # ========================================================================
    # TIERED BUYING LOGIC
    # ========================================================================
    
    def _should_buy(self) -> tuple[bool, Optional[str], float]:
        """
        Determine if we should buy and which side.
        Returns: (should_buy, side, size_in_dollars)
        """
        # Already sold = done for this market
        if self._has_sold:
            return (False, None, 0)
        
        # All tiers used - SELL ONLY MODE
        if self._tiers_used >= self.config.num_tiers:
            logger.info(f"🛑 MAX {self.config.num_tiers} tiers used - SELL ONLY mode")
            return (False, None, 0)
        
        # Need price data
        if not self._last_mid_price:
            return (False, None, 0)
        
        mid = self._last_mid_price
        
        # Determine which side to buy (if any)
        buy_side = None
        if mid < self.config.buy_zone_yes:  # YES is cheap
            buy_side = "yes"
        elif mid > self.config.buy_zone_no:  # NO is cheap
            buy_side = "no"
        
        if not buy_side:
            return (False, None, 0)
        
        # Side locking: once committed, don't switch
        if self._locked_side and self._locked_side != buy_side:
            logger.warning(f"Side locked to {self._locked_side.upper()}, ignoring {buy_side.upper()} signal")
            return (False, None, 0)
        
        # Timer check: don't buy too fast (except first tier)
        if self._tiers_used > 0:
            elapsed = time.time() - self._last_tier_time
            if elapsed < self.config.tier_timer_seconds:
                remaining = self.config.tier_timer_seconds - elapsed
                logger.debug(f"Timer: {remaining:.0f}s until next tier")
                return (False, None, 0)
        
        logger.info(f"🎯 TIER {self._tiers_used + 1}/{self.config.num_tiers} TRIGGERED ({buy_side.upper()} @ {mid:.3f})")
        return (True, buy_side, self.config.tier_budget)
    
    def _record_buy(self, side: str):
        """Record that we bought a tier."""
        self._locked_side = side
        self._tiers_used += 1
        self._last_tier_time = time.time()
        logger.info(f"📊 TIER {self._tiers_used}/{self.config.num_tiers} used. Locked to: {side.upper()}")
    
    # ========================================================================
    # TIERED SELLING LOGIC
    # ========================================================================
    
    def _get_sell_info(self, side: str) -> tuple[str, float, float]:
        """
        Get sell tier, percentage, and cycle time based on profit.
        Returns: (tier_name, sell_percentage, cycle_time_seconds)
        """
        if side == "yes":
            inv = self.inv_yes
            entry = self.avg_entry_yes
            current = self._last_mid_price
        else:
            inv = self.inv_no
            entry = self.avg_entry_no
            current = 1.0 - self._last_mid_price
        
        if inv < 0.5 or entry <= 0:
            return ("NO_SELL", 0.0, self.config.base_quote_interval)
        
        profit = current - entry
        
        # Check each tier from highest to lowest
        for threshold, (name, pct, cycle) in sorted(self.config.sell_tiers.items(), reverse=True):
            if profit >= threshold:
                return (name, pct, cycle)
        
        return ("NO_SELL", 0.0, self.config.base_quote_interval)
    
    def _get_sell_size(self, side: str) -> float:
        """Calculate how many shares to sell based on tier."""
        tier_name, sell_pct, _ = self._get_sell_info(side)
        
        inv = self.inv_yes if side == "yes" else self.inv_no
        
        if sell_pct <= 0:
            return 0
        
        # Calculate shares
        shares = inv * sell_pct
        
        # Enforce Minimum Order Size (Polymarket requires ~5 shares)
        # With small balance, 10% might be < 5 shares, so we floor it at 5.
        shares = max(shares, self.config.min_shares)
        shares = min(shares, inv)
        
        logger.info(f"SELL: {tier_name} | {sell_pct:.0%} of {inv:.1f} = {shares:.1f} shares")
        return shares
    
    # ========================================================================
    # INVENTORY MANAGEMENT
    # ========================================================================
    
    def _refresh_inventory(self):
        """Fetch current inventory from chain."""
        old_yes = self.inv_yes
        old_no = self.inv_no
        
        try:
            self.inv_yes = self.poly_client.get_position(self.token_id_yes)
            if self.token_id_no:
                self.inv_no = self.poly_client.get_position(self.token_id_no)
            
            # Detect fills (inventory increased)
            if self.inv_yes > old_yes + 0.5:
                shares_filled = self.inv_yes - old_yes
                logger.info(f"✅ YES fill: {old_yes:.1f} → {self.inv_yes:.1f} (+{shares_filled:.1f})")
                if self.avg_entry_yes <= 0:
                    self.avg_entry_yes = self._last_mid_price or 0.5
                    logger.info(f"Set YES entry price: {self.avg_entry_yes:.3f}")
                # Record the tier if we have a pending buy order
                if self._pending_order_side == "yes":
                    self._record_buy("yes")
                    self._pending_order_side = None
            
            if self.inv_no > old_no + 0.5:
                shares_filled = self.inv_no - old_no
                logger.info(f"✅ NO fill: {old_no:.1f} → {self.inv_no:.1f} (+{shares_filled:.1f})")
                if self.avg_entry_no <= 0:
                    self.avg_entry_no = 1.0 - (self._last_mid_price or 0.5)
                    logger.info(f"Set NO entry price: {self.avg_entry_no:.3f}")
                # Record the tier if we have a pending buy order
                if self._pending_order_side == "no":
                    self._record_buy("no")
                    self._pending_order_side = None
            
            # Detect sells (inventory decreased)
            if old_yes > 0.5 and self.inv_yes < old_yes - 0.5:
                logger.info(f"💰 SOLD YES: {old_yes:.1f} → {self.inv_yes:.1f}")
                if self.inv_yes < 0.5:
                    self._has_sold = True
            
            if old_no > 0.5 and self.inv_no < old_no - 0.5:
                logger.info(f"💰 SOLD NO: {old_no:.1f} → {self.inv_no:.1f}")
                if self.inv_no < 0.5:
                    self._has_sold = True
            
            logger.info(f"🎒 Inventory: YES={self.inv_yes:.1f}@{self.avg_entry_yes:.3f} | NO={self.inv_no:.1f}@{self.avg_entry_no:.3f}")
            
        except Exception as e:
            logger.error(f"Inventory fetch failed: {e}")
    
    # ========================================================================
    # QUOTE GENERATION
    # ========================================================================
    
    def _generate_quotes(self) -> tuple[Optional[float], Optional[float], float, float, float]:
        """
        Generate bid/ask prices and sizes.
        Returns: (bid_price, ask_price, bid_size, ask_size, cycle_time)
        """
        mid = self._last_mid_price
        cycle_time = self.config.base_quote_interval
        
        if not mid:
            return (None, None, 0, 0, cycle_time)
        
        # Check if we should buy
        should_buy, buy_side, buy_budget = self._should_buy()
        
        bid = None
        ask = None
        bid_size = 0
        ask_size = 0
        
        # BUYING: Generate bid
        if should_buy and buy_side == "yes":
            bid = mid  # Aggressive bid at mid
            bid_size = buy_budget / max(mid, 0.10)
            self._pending_order_side = "yes"  # Track pending order
            logger.info(f"🎯 BUY YES @ {bid:.3f} ({bid_size:.1f} shares)")
        
        # SELLING: Generate ask if we have inventory
        if self.inv_yes > 0.5:
            tier_name, sell_pct, tier_cycle = self._get_sell_info("yes")
            if sell_pct > 0:
                cycle_time = min(cycle_time, tier_cycle) # Use faster cycle if selling
                ask_size = self._get_sell_size("yes")
                # AGGRESSIVE pricing for ALL sells (scalping strategy)
                ask = mid - 0.01  # TAKE liquidity - sell at mid-1¢
                logger.info(f"🚀 SELL YES @ {ask:.3f} ({tier_name}) [AGGRESSIVE]")
        
        return (bid, ask, bid_size, ask_size, cycle_time)
    
    def _generate_quotes_no(self) -> tuple[Optional[float], Optional[float], float, float, float]:
        """Generate quotes for NO side."""
        mid = self._last_mid_price
        mid_no = 1.0 - mid if mid else 0
        cycle_time = self.config.base_quote_interval
        
        should_buy, buy_side, buy_budget = self._should_buy()
        
        bid = None
        ask = None
        bid_size = 0
        ask_size = 0
        
        # BUYING NO
        if should_buy and buy_side == "no":
            bid = mid_no
            bid_size = buy_budget / max(mid_no, 0.10)
            self._pending_order_side = "no"  # Track pending order
            logger.info(f"🎯 BUY NO @ {bid:.3f} ({bid_size:.1f} shares)")
        
        # SELLING NO
        if self.inv_no > 0.5:
            tier_name, sell_pct, tier_cycle = self._get_sell_info("no")
            if sell_pct > 0:
                cycle_time = min(cycle_time, tier_cycle)
                ask_size = self._get_sell_size("no")
                # AGGRESSIVE pricing for ALL sells (scalping strategy)
                ask = mid_no - 0.01  # TAKE liquidity
                logger.info(f"🚀 SELL NO @ {ask:.3f} ({tier_name}) [AGGRESSIVE]")
        
        return (bid, ask, bid_size, ask_size, cycle_time)
    
    # ========================================================================
    # MAIN LOOP
    # ========================================================================
    
    async def run(self):
        """Main bot loop."""
        self.running = True
        logger.info("🚀 Starting Hourly Sniper...")
        
        # Start feeds
        feed_tasks = [
            asyncio.create_task(self.poly_feed.connect()),
            asyncio.create_task(self.binance_feed.connect()),
        ]
        
        # Initial inventory
        self._refresh_inventory()
        
        # Wait for price data
        await asyncio.sleep(2)
        
        last_inv_sync = 0.0
        
        try:
            while self.running:
                # Periodic inventory sync (every 3s for fast fill detection)
                if time.time() - last_inv_sync > 3:
                    self._refresh_inventory()
                    last_inv_sync = time.time()
                
                # Check time remaining
                try:
                    time_left = CryptoHourlyPricer.get_time_remaining()
                except:
                    time_left = 3600
                
                if time_left < 60:
                    logger.warning("⏰ Less than 1 minute left - stopping buys")
                
                # Skip if no price yet
                if not self._last_mid_price:
                    logger.info("⏳ Waiting for price data...")
                    await asyncio.sleep(2)
                    continue
                
                # Initialize all variables (FIX: prevents NameError)
                bid_yes = None
                ask_yes = None
                bid_size_yes = 0
                ask_size_yes = 0
                bid_no = None
                ask_no = None
                bid_size_no = 0
                ask_size_no = 0
                cycle_time = self.config.base_quote_interval
                
                # FORCE SELL in last 3 minutes (Panic Mode)
                if time_left < 180:
                    if self.inv_yes > 0.5:
                        logger.warning("🚨 < 3 MIN LEFT: PANIC SELL YES!")
                        ask_yes = self._last_mid_price - 0.02  # Dump it
                        ask_size_yes = self.inv_yes
                    if self.inv_no > 0.5:
                        logger.warning("🚨 < 3 MIN LEFT: PANIC SELL NO!")
                        ask_no = (1.0 - self._last_mid_price) - 0.02
                        ask_size_no = self.inv_no
                    cycle_time = 0.5  # Fast cycle in panic mode
                else:
                    # Normal quote generation
                    bid_yes, ask_yes, bid_size_yes, ask_size_yes, cycle_yes = self._generate_quotes()
                    bid_no, ask_no, bid_size_no, ask_size_no, cycle_no = self._generate_quotes_no()
                    
                    # Determine cycle time
                    cycle_time = min(cycle_yes, cycle_no)
                    
                    # Time-based overrides
                    if time_left < 600:  # < 10 min
                        cycle_time = min(cycle_time, 1.0)
                    elif time_left < 1200:  # < 20 min
                        cycle_time = min(cycle_time, 1.5)
                
                # Check for stale price (WS failure backup)
                if time.time() - self._last_price_update_time > 5.0:
                    logger.debug("⚠️ Price stale (>5s), fetching via REST...")
                    self._fetch_initial_price() # Re-use this method to update price
                
                # Check if we have anything to do
                has_action = (bid_yes or ask_yes or bid_no or ask_no)
                
                if not has_action and abs(self.inv_yes) < 0.5 and abs(self.inv_no) < 0.5:
                    # No inventory, no buy signal
                    if self._has_sold:
                        logger.info("✅ All sold! Mission complete. Shutting down...")
                        break
                    else:
                        mid = self._last_mid_price
                        logger.info(f"⏳ Waiting... YES @ {mid:.2f} (need <{self.config.buy_zone_yes:.2f} or >{self.config.buy_zone_no:.2f})")
                        await asyncio.sleep(self.config.base_quote_interval)
                        continue
                
                # Build orders (create separate orders for bid and ask with correct sizes)
                orders = []
                if bid_yes:
                    orders.extend(create_quote_orders(
                        self.client, self.token_id_yes, bid_yes, None, bid_size_yes
                    ))
                if ask_yes:
                    orders.extend(create_quote_orders(
                        self.client, self.token_id_yes, None, ask_yes, ask_size_yes
                    ))
                if self.token_id_no and bid_no:
                    orders.extend(create_quote_orders(
                        self.client, self.token_id_no, bid_no, None, bid_size_no
                    ))
                if self.token_id_no and ask_no:
                    orders.extend(create_quote_orders(
                        self.client, self.token_id_no, None, ask_no, ask_size_no
                    ))
                
                # Execute
                if self.dry_run:
                    logger.info(f"🧪 DRY RUN: YES {bid_yes}/{ask_yes} | NO {bid_no}/{ask_no}")
                else:
                    try:
                        result = batch_cancel_and_post(
                            self.client, self._current_order_ids, orders
                        )
                        if result["posted"]:
                            self._current_order_ids = extract_order_ids(result["posted"])
                        logger.info(f"📝 Orders: YES {bid_yes}/{ask_yes} | NO {bid_no}/{ask_no}")
                    except Exception as e:
                        logger.error(f"Order error: {e}")
                
                # Use dynamic cycle time
                await asyncio.sleep(cycle_time)
                
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            self.running = False
            # Cancel all orders
            try:
                self.client.cancel_all()
            except:
                pass
            # Cancel feed tasks
            for t in feed_tasks:
                t.cancel()


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Hourly BTC Sniper Bot")
    parser.add_argument("market_slug", help="Market slug (e.g., bitcoin-up-or-down-january-31-9am-et)")
    parser.add_argument("token_id", help="YES token ID")
    parser.add_argument("--dry-run", action="store_true", help="Don't place real orders")
    parser.add_argument("--tier-budget", type=float, default=3.0, help="$ per tier (default: 3)")
    parser.add_argument("--num-tiers", type=int, default=4, help="Number of tiers (default: 4)")
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("Hourly BTC Sniper Bot")
    print("=" * 60)
    
    config = SniperConfig(
        tier_budget=args.tier_budget,
        num_tiers=args.num_tiers,
    )
    
    sniper = HourlySniper(
        market_slug=args.market_slug,
        token_id_yes=args.token_id,
        config=config,
        dry_run=args.dry_run,
    )
    
    asyncio.run(sniper.run())


if __name__ == "__main__":
    main()
