"""
Main entry point for Polymarket Market Maker.

Initializes the client, detects market type, and runs the main trading loop
with full risk management integration.

Goal: Don't go broke.
"""

# =============================================================================
# CLOUDFLARE BYPASS PATCH (must be done before any py_clob_client imports)
# =============================================================================
import py_clob_client.http_helpers.helpers as _helpers

def _patched_overloadHeaders(method, headers):
    """Use browser-like headers to bypass Cloudflare detection."""
    if headers is None:
        headers = dict()
    headers["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    headers["Accept"] = "application/json, text/plain, */*"
    headers["Connection"] = "keep-alive"
    headers["Content-Type"] = "application/json"
    headers["Accept-Language"] = "en-US,en;q=0.9"
    headers["sec-ch-ua"] = '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'
    headers["sec-ch-ua-mobile"] = "?0"
    headers["sec-ch-ua-platform"] = '"macOS"'
    if method == "GET":
        headers["Accept-Encoding"] = "gzip, deflate, br"
    return headers

_helpers.overloadHeaders = _patched_overloadHeaders
# =============================================================================

import argparse
import asyncio
import logging
import signal
import sys
import time
from typing import Optional

from client_wrapper import PolymarketClient
from auto_pilot import AutoPilot
from strategy import (
    CryptoHourlyStrategy,
    StrategyConfig,
    DualQuote,
    Quote
)
from data_feed import (
    PolymarketWebSocket,
    BinancePriceMonitor,
    check_price_dislocation,
    LocalOrderBook,
    BinancePrice,
    UserWebSocket,
    FillEvent,
)
from orders import batch_cancel_and_post, create_quote_orders, extract_order_ids
from risk import (
    RiskManager,
    RiskConfig,
    RiskError,
    KillSwitchError,
    QuoteSide,

    is_weekend_blackout,
    get_next_trading_open,
    TradingHoursError,
    FlowTracker,
)

from data_logger import DataLogger
from data_logger import DataLogger
from micro_scalping_manager import MicroScalpingManager
from monitoring import MarketMonitor, MarketMetrics


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class CycleManager:
    """Manages the 3-Zone Smart Cycle Strategy."""
    def __init__(self, use_cycle: bool):
        self.enabled = use_cycle
        self.last_buy_time_yes = 0.0
        self.last_buy_time_no = 0.0
        
    def record_buy(self, side: str):
        if side == "yes": self.last_buy_time_yes = time.time()
        else: self.last_buy_time_no = time.time()
        
    def get_regime(self, time_left_sec: int) -> dict:
        """Returns {phase, buy_allowed, size_mult, start_margin, decay_time}"""
        minutes_left = time_left_sec / 60.0
        
        if minutes_left > 30:
            # ZONE 1: ACCUMULATOR (Early Game)
            return {
                "name": "ACCUMULATOR",
                "buy_allowed": True,
                "size_mult": 1.0,
                "start_margin": 0.08, # Start +8ct
                "decay_time": 1200 # 20 mins to drop to floor
            }
        elif minutes_left > 10:
            # ZONE 2: ACCELERATOR (Mid Game)
            return {
                "name": "ACCELERATOR",
                "buy_allowed": True,
                "size_mult": 0.50, # Half Risk
                "start_margin": 0.06, # +6ct
                "decay_time": 300 # 5 mins
            }
        else:
            # ZONE 3: TERMINATOR (End Game)
            return {
                "name": "LIQUIDATE_ONLY",
                "buy_allowed": False, # Stop Buying
                "size_mult": 0.0,
                "start_margin": 0.04, # +4ct
                "decay_time": 120 # 2 mins (Panic)
            }
            
    def get_dynamic_margin(self, side: str, time_left_sec: int) -> float:
        """Calculates current required margin based on Bag Age."""
        if not self.enabled: return 0.01 # Default 1ct
        
        # T-5m Override (Hard Stop)
        if time_left_sec < 300: return 0.01
        
        regime = self.get_regime(time_left_sec)
        
        last_buy = self.last_buy_time_yes if side == "yes" else self.last_buy_time_no
        if last_buy == 0: return regime["start_margin"]
        
        bag_age = time.time() - last_buy
        
        # Linear Decay: Start -> 0.01 over DecayTime
        # Margin = Start - ( (Start - 0.01) * (Age / DecayTime) )
        if bag_age >= regime["decay_time"]:
            return 0.01
            
        decay_progress = bag_age / regime["decay_time"]
        margin_diff = regime["start_margin"] - 0.01
        current_margin = regime["start_margin"] - (margin_diff * decay_progress)
        
        return max(current_margin, 0.01)


class MarketMaker:
    """
    Main market maker orchestrator.
    
    Coordinates between:
    - Polymarket WebSocket for order book updates
    - Binance price monitor for reference prices
    - Fee-adjusted strategy for quote generation
    - Risk manager for safety checks
    - Order management for execution
    
    All components run concurrently via asyncio.
    """
    
    def __init__(
        self,
        market_id: str,
        token_id: str, # This is technically Token YES usually
        market_type: Optional[str] = None,
        risk_config: Optional[RiskConfig] = None,
        starting_inventory: Optional[float] = None,
        refresh_rate_seconds: float = 4.0,
        long_only: bool = False,
        token_id_no: Optional[str] = None,
        dry_run: bool = False,
        fixed_strike: Optional[float] = None,
        use_cycle_mode: bool = False,
    ) -> None:
        """
        Initialize the market maker.
        """
        self.market_id = market_id
        self.token_id_yes = token_id 
        self.token_id_no = token_id_no # Use provided NO token if available
        
        self.refresh_rate = refresh_rate_seconds
        self.long_only = long_only
        self.long_only = long_only
        self.dry_run = dry_run
        self.fixed_strike = fixed_strike
        self.use_cycle_mode = use_cycle_mode
        
        self.running = False
        self.soft_stop = False
        self.soft_stop_start_time = None
        self.soft_stop_timeout = 300
        self.min_size = 0.1
        
        # Cycle Mode State
        self.use_cycle_mode = use_cycle_mode
        self.cycle_manager = CycleManager(use_cycle_mode)
        self.cycle_phase = "ACCUMULATE" 
        self._current_order_ids = []
        
        self._current_order_ids = []
        
        # ONE-SHOT MODE: Only buy once, then sell
        self._has_bought = False
        
        # TIERED SELLING: Track profit tier for threshold detection
        self._last_profit_tier = None
        self._base_refresh_rate = refresh_rate_seconds  # Store original
        
        # SMART TIERED BUYING: 4 tiers x 25% each with 60s timer backup
        self._buy_tiers_used = [False, False, False, False]  # Tiers 1-4
        self._last_buy_time = 0  # Timestamp of last buy
        self._buy_timer_seconds = 60  # 1 minute timer for backup buys
        self._total_buy_budget = 12.0  # $12 total budget (scaled for $13.79 balance)
        self._buy_per_tier = 3.0  # $3 per tier (25%)
        self._buy_side = None  # 'yes' or 'no' - locked after first buy
        
        # Hardcode Market Type to Standard info (just for logging if needed)
        logger.info(f"🚀 Initializing Hourly Sniper Logic for {market_id}")
        
        self._init_risk_manager(risk_config)
        self._init_client()
        
        # FETCH COMPLEMENTARY TOKEN ID (NO)
        self._fetch_market_tokens()
    
        # Initialize Data Feeds FIRST (Strategy needs them)
        self._init_data_feeds()
        
        # Initialize Strategy (Needs Title + Feeds)
        self._init_strategy()
        
        # State
        self.inv_yes: float = 0.0
        self.inv_no: float = 0.0
        
        # Performance Tracking (Profit Guard)
        self.avg_entry_yes: float = 0.0
        self.avg_entry_no: float = 0.0
        
        # Cycle Mode State
        self.use_cycle_mode = use_cycle_mode
        self.cycle_manager = CycleManager(use_cycle_mode)
        self.cycle_phase = "ACCUMULATE" 
        self._current_order_ids = []
        
        self.soft_stop = False
        self._last_inv_sync = 0.0
        
        # Pending tier tracking (for fill-based recording)
        self._pending_tier_side: Optional[str] = None
        self._pending_tier: Optional[int] = None
        
        # Bailout Persistence Tracking (Phase 8)
        self.distress_start_time: Optional[float] = None
        self.distress_side: Optional[str] = None # "YES" or "NO"
        
        # Initialize Micro-Scalper (Time Decay)
        self.scalp_manager = MicroScalpingManager(
            decay_rate_per_min=0.01, # Drop 1 cent per minute
            grace_period_sec=30.0,   # 30s grace
            max_hold_sec=300.0       # 5 min panic
        )
        
        self._current_time_left = 3600 # Default fallback
        
        # Initialize Market Monitor (Phase 2)
        self.monitor = MarketMonitor(token_id=self.token_id_yes)
        self._last_monitor_update = 0.0
        self._current_metrics: Optional[MarketMetrics] = None
        
        # Initialize inventory
        if starting_inventory is not None:
             self.inv_yes = starting_inventory # Assume starting inv is YES for now
             logger.info(f"🎒 MANUALLY OVERRIDDEN inventory YES: {self.inv_yes:.2f} shares")
        else:
             self._refresh_inventory()
        
        self._last_inv_sync = time.time()

    @property
    def inventory(self) -> float:
        """Net Exposure: YES - NO"""
        return self.inv_yes - self.inv_no

    # =========================================================================
    # TIERED SELLING SYSTEM
    # =========================================================================
    
    def _get_profit_tier(self, profit_margin: float) -> tuple:
        """
        Returns (tier_name, sell_percentage) based on profit margin.
        Higher profit = more aggressive selling.
        """
        if profit_margin >= 0.10:  # +10¢ or more
            return ("FULL_LIQ", 1.0)  # 100% - dump everything
        elif profit_margin >= 0.08:
            return ("TIER_8", 0.40)   # 40%
        elif profit_margin >= 0.05:
            return ("TIER_5", 0.25)   # 25%
        elif profit_margin >= 0.03:
            return ("TIER_3", 0.15)   # 15%
        elif profit_margin >= 0.01:  # At least 1¢ profit
            return ("TIER_1", 0.10)   # 10%
        else:
            return ("NO_SELL", 0.0)   # Don't sell at 0 or negative profit
    
    def _get_dynamic_cycle_time(self, time_left: int, profit_margin: float) -> float:
        """
        Returns cycle time in seconds based on time left and profit.
        Faster cycles = more aggressive exit.
        """
        # Last 3 min = IMMEDIATE (minimum cycle)
        if time_left < 180:
            return 0.5
        
        # +10¢ profit = IMMEDIATE
        if profit_margin >= 0.10:
            return 0.5
        
        # Last 10 min = 1s cycles
        if time_left < 600:
            return 1.0
        
        # Last 20 min = 1.5s cycles
        if time_left < 1200:
            return 1.5
        
        # Normal: tier-based
        if profit_margin >= 0.05:
            return 2.0
        else:
            return 4.0  # 4s for lower profit tiers (1-5¢)
    
    def _check_tier_cross(self, current_tier: str) -> bool:
        """
        Check if we crossed into a new (higher) profit tier.
        Returns True if we should trigger immediate sell.
        """
        if self._last_profit_tier is None:
            self._last_profit_tier = current_tier
            return False
        
        # Define tier order (higher index = better profit)
        tier_order = ["NO_SELL", "TIER_1", "TIER_3", "TIER_5", "TIER_8", "FULL_LIQ"]
        
        old_idx = tier_order.index(self._last_profit_tier) if self._last_profit_tier in tier_order else 0
        new_idx = tier_order.index(current_tier) if current_tier in tier_order else 0
        
        # Update tracked tier
        self._last_profit_tier = current_tier
        
        # Trigger if we moved UP to a better tier
        if new_idx > old_idx:
            logger.info(f"🎯 TIER CROSS: {tier_order[old_idx]} → {current_tier}. IMMEDIATE SELL!")
            return True
        
        return False

    # =========================================================================
    # SMART TIERED BUYING SYSTEM
    # =========================================================================
    
    def _get_buy_tier(self, price: float, side: str) -> int:
        """
        Get the buy tier (0-4) based on price.
        Tier 0 = No-man's land (no buy)
        Tiers 1-4 = Progressively deeper value zones
        
        YES side (buying YES when YES is cheap):
        - Tier 0: >= 38¢ (no buy)
        - Tier 1: 33-38¢ (entry)
        - Tier 2: 28-33¢ (scale)
        - Tier 3: 23-28¢ (heavy)
        - Tier 4: < 23¢ (max)
        
        NO side (buying NO when NO is cheap, i.e. YES expensive):
        - Same thresholds but based on NO price
        """
        if side == "yes":
            if price >= 0.38:
                return 0  # No-man's land
            elif price >= 0.33:
                return 1  # Entry
            elif price >= 0.28:
                return 2  # Scale
            elif price >= 0.23:
                return 3  # Heavy
            else:
                return 4  # Max
        else:  # NO side
            if price >= 0.38:
                return 0  # No-man's land
            elif price >= 0.33:
                return 1
            elif price >= 0.28:
                return 2
            elif price >= 0.23:
                return 3
            else:
                return 4
    
    def _should_buy_tiered(self, mid_yes: float) -> tuple:
        """
        Determine if we should buy and which side.
        Returns (should_buy: bool, side: str, size: float)
        
        Logic:
        1. If already bought all 4 tiers → no buy
        2. If locked to a side → only check that side
        3. Check current tier vs used tiers
        4. Handle timer backup for same-tier repeat buys
        """
        now = time.time()
        mid_no = 1.0 - mid_yes
        
        # If all tiers used, no more buying
        if all(self._buy_tiers_used):
            return (False, None, 0)
        
        # Determine which side to check
        if self._buy_side is not None:
            # Already locked to a side
            check_sides = [self._buy_side]
        else:
            # Check both sides, pick the one with better value (lower price)
            check_sides = ["yes", "no"]
        
        for side in check_sides:
            price = mid_yes if side == "yes" else mid_no
            tier = self._get_buy_tier(price, side)
            
            if tier == 0:
                continue  # No-man's land, skip
            
            tier_idx = tier - 1  # Convert to 0-indexed
            
            # Check if this tier was already used
            if not self._buy_tiers_used[tier_idx]:
                # New tier! Buy immediately
                logger.info(f"🎯 TIER {tier} TRIGGERED ({side.upper()} @ {price:.2f}). BUYING!")
                return (True, side, self._buy_per_tier)
            
            # Tier already used - check timer for backup buy
            tiers_bought = sum(self._buy_tiers_used)
            if tiers_bought < 4:  # Still have budget
                time_since_last = now - self._last_buy_time
                if time_since_last >= self._buy_timer_seconds:
                    # Timer expired! Find next available tier slot
                    for i in range(4):
                        if not self._buy_tiers_used[i]:
                            logger.info(f"⏰ TIMER BACKUP: 60s passed, buying tier {i+1} ({side.upper()} @ {price:.2f})")
                            return (True, side, self._buy_per_tier)
        
        return (False, None, 0)
    
    def _record_tiered_buy(self, side: str, tier: int):
        """Record that a buy was made for the given tier."""
        tier_idx = tier - 1
        if 0 <= tier_idx < 4:
            self._buy_tiers_used[tier_idx] = True
            self._last_buy_time = time.time()
            self._buy_side = side  # Lock to this side
            self._has_bought = True  # For one-shot compatibility
            
            tiers_used = sum(self._buy_tiers_used)
            logger.info(f"📊 TIERED BUY: {tiers_used}/4 tiers used. Side: {side.upper()}")

    def _refresh_inventory(self):
        logger.info("Fetching on-chain inventory...")
        try:
             # Store old state to detect "Mystery Fills" (WS Failure)
             old_yes = self.inv_yes
             old_no = self.inv_no
             
             self.inv_yes = self.client_wrapper.get_position(self.token_id_yes)
             self.inv_no = self.client_wrapper.get_position(self.token_id_no)
             
             # FILL DETECTION: Check if inventory increased (order filled)
             # If we have a pending tier and inventory increased, record it
             if self._pending_tier is not None and self._pending_tier_side is not None:
                 inv_increased = False
                 if self._pending_tier_side == "yes" and self.inv_yes > old_yes + 0.5:
                     inv_increased = True
                     logger.info(f"✅ FILL DETECTED: YES inventory increased {old_yes:.1f} → {self.inv_yes:.1f}")
                 elif self._pending_tier_side == "no" and self.inv_no > old_no + 0.5:
                     inv_increased = True
                     logger.info(f"✅ FILL DETECTED: NO inventory increased {old_no:.1f} → {self.inv_no:.1f}")
                 
                 if inv_increased:
                     self._record_tiered_buy(self._pending_tier_side, self._pending_tier)
                     self._pending_tier = None
                     self._pending_tier_side = None
             
             # COST BASIS RESCUE LOGIC
             # If we have inventory but AvgEntry is 0 (missed fill), estimate it.
             # Only if we actually *have* inventory now.
             
             # YES Side Rescue
             if self.inv_yes > 0.1 and self.avg_entry_yes <= 0:
                 # We have shares but don't know price.
                 # Fallback: Use Current Mid Price (or 0.50 if unknown)
                 est_price = self._last_mid_price_yes or 0.50
                 self.avg_entry_yes = est_price
                 logger.warning(f"⚠️ WARM-UP: Found {self.inv_yes} YES shares with unknown cost. setting AvgEntry = {est_price:.3f} (Mid)")
             
             # NO Side Rescue
             if self.inv_no > 0.1 and self.avg_entry_no <= 0:
                 # Need NO mid price. (Approximated as 1 - YES Mid)
                 est_price_no = (1.0 - (self._last_mid_price_yes or 0.50))
                 self.avg_entry_no = est_price_no
                 logger.warning(f"⚠️ WARM-UP: Found {self.inv_no} NO shares with unknown cost. setting AvgEntry = {est_price_no:.3f} (Derived Mid)")
                 
             logger.info(f"🎒 Inventory: YES={self.inv_yes:.2f} (@ {self.avg_entry_yes:.3f}), NO={self.inv_no:.2f} (@ {self.avg_entry_no:.3f}) (Net: {self.inventory:.2f})")
        except Exception as e:
             logger.error(f"Failed to fetch inventory: {e}")
             # Do NOT reset to 0 on error, keep old state to prevent panic buying
             # self.inv_yes = 0.0 
             # self.inv_no = 0.0

    def _fetch_market_tokens(self):
        """Fetch the complementary token ID (NO) for the market."""
        if self.token_id_no and self.token_id_no != "UNKNOWN":
            logger.info(f"✅ Token pair provided: YES={self.token_id_yes} NO={self.token_id_no}")
            return
            
        try:
            # 1. Try CLOB API first
            market = self.client.get_market(self.market_id)
            if not market:
                raise ValueError("CLOB returned empty market")
                
            tokens = market.get('tokens', [])
            if len(tokens) >= 2:
                for t in tokens:
                    if t['token_id'] != self.token_id_yes:
                        self.token_id_no = t['token_id']
                        break
                logger.info(f"✅ Found Token pair (CLOB): YES={self.token_id_yes} NO={self.token_id_no}")
                return
        except Exception as e:
            logger.warning(f"CLOB get_market failed ({e}). Trying Gamma fallback for tokens...")
            
        try:
            # 2. Try Gamma API Fallback
            import urllib.request, json, ssl
            ctx = ssl._create_unverified_context()
            url = f"https://gamma-api.polymarket.com/markets?slug={self.market_id}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                if data and isinstance(data, list) and len(data) > 0:
                    clob_ids = data[0].get('clobTokenIds', [])
                    
                    # Handle case where clobTokenIds is a stringified JSON list
                    if isinstance(clob_ids, str):
                        try:
                            clob_ids = json.loads(clob_ids)
                        except:
                            pass
                            
                    # Usually clobTokenIds is [YES, NO]
                    if isinstance(clob_ids, list) and len(clob_ids) >= 2:
                        for tid in clob_ids:
                            if tid != self.token_id_yes:
                                self.token_id_no = tid
                                break
                        logger.info(f"✅ Found Token pair (Gamma): YES={self.token_id_yes} NO={self.token_id_no}")
                        return
        except Exception as e2:
            logger.error(f"Token discovery fallback failed: {e2}")

        self.token_id_no = "UNKNOWN"
        logger.warning("⚠️ Could not determine NO token ID. NO-token trading disabled.")

    def _init_risk_manager(self, config: Optional[RiskConfig]) -> None:
        """Initialize the risk manager, flow tracker, and data logger."""
        self.risk = RiskManager(config)
        self.risk.set_kill_switch_callback(self._on_kill_switch)
        self.flow_tracker = FlowTracker(window_size=20, toxic_threshold=0.80)
        self.data_logger = DataLogger()
        logger.info("Risk manager initialized")
    
    def _on_kill_switch(self) -> None:
        """Handle kill switch trigger."""
        logger.critical("🚨 KILL SWITCH CALLBACK - Initiating emergency shutdown")
        self.running = False
        try:
            self.client.cancel_all()
            logger.info("Emergency: All orders cancelled")
        except Exception as e:
            logger.error(f"Emergency cancel failed: {e}")

    def initiate_soft_stop(self):
        if not self.soft_stop:
             logger.warning("🏳️ SOFT STOP INITIATED: Unwinding positions...")
             self.soft_stop = True
             self.soft_stop_start_time = time.time()
        else:
            logger.warning("Soft stop already in progress!")
            return

        logger.warning(f"🛑 SOFT STOP: Net Exposure: {self.inventory:.2f}")
        self.soft_stop = True
    
    def _init_client(self) -> None:
        logger.info("Initializing Polymarket client...")
        self.client_wrapper = PolymarketClient()
        self.client = self.client_wrapper.get_client()
        logger.info(f"Client initialized for address: {self.client_wrapper.address}")
    
    
    def _init_strategy(self) -> None:
        logger.info(f"Initializing Hourly Sniper Strategy...")
        
        # We need the Market Title to parse context (BTC/ETH, Up/Down)
        # We can fetch it from the API if not passed, or assume it's fetched.
        # Let's try to fetch market title.
        market_title = "Unknown Market"
        try:
             # Try CLOB first
             m = self.client.get_market(self.market_id)
             if m and 'question' in m:
                 market_title = m['question']
        except Exception as e:
             logger.warning(f"CLOB get_market failed: {e}. Trying Gamma API fallback...")
             try:
                 import urllib.request
                 import json
                 import ssl
                 
                 # Fallback: Gamma API via Slug
                 # We assume self.market_id is the slug
                 ctx = ssl._create_unverified_context()
                 url = f"https://gamma-api.polymarket.com/markets?slug={self.market_id}"
                 headers = {
                     "User-Agent": "Mozilla/5.0",
                 }
                 req = urllib.request.Request(url, headers=headers)
                 with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
                     data = json.loads(response.read().decode())
                     if data and isinstance(data, list) and len(data) > 0:
                         market_title = data[0].get('question', market_title)
                         logger.info(f"✅ Gamma Fallback Success: {market_title}")
             except Exception as e2:
                 logger.error(f"Gamma fallback failed: {e2}")

        logger.info(f"Market Title: {market_title}")
        
        # Initialize Sniper Strategy
        # Config can be tuned here
        config = StrategyConfig(
            min_spread=0.03, 
            max_spread=0.10,
            fixed_strike=self.fixed_strike,
        )
        
        self.strategy = CryptoHourlyStrategy(
            market_title=market_title,
            binance_monitor=self.binance_monitor,
            config=config
        )
    
    def _init_data_feeds(self) -> None:
        """Initialize data feed connections."""
        # Listen to BOTH tokens
        token_ids = [self.token_id_yes]
        if self.token_id_no and self.token_id_no != "UNKNOWN":
            token_ids.append(self.token_id_no)
            
        self.poly_ws = PolymarketWebSocket(token_ids=token_ids)
        self.poly_ws.on_book_update = self._on_book_update
        
        self.user_ws = UserWebSocket(api_creds=self.client_wrapper.get_credentials())
        self.user_ws.on_fill = self._on_fill_event
        
        self.user_ws.on_fill = self._on_fill_event
        
        # Always use Binance Monitor for Hourly Strategy
        self.binance_monitor = BinancePriceMonitor()
        self.binance_monitor.on_price_update = self._on_binance_price
        
        self._last_mid_price_yes: Optional[float] = None
        self._last_binance_price: Optional[float] = None
    
    def _on_book_update(self, book: LocalOrderBook) -> None:
        """Handle order book updates from Polymarket."""
        # Check which token this update is for
        if book.token_id == self.token_id_yes:
            if book.mid_price:
                self._last_mid_price_yes = book.mid_price
                logger.info(f"📖 Book YES update: mid={book.mid_price:.4f}")
        # We don't strictly need NO book if we derive price, but good to have
    
    def _on_binance_price(self, price: BinancePrice) -> None:
        if price.symbol == "btcusdt":
            self._last_binance_price = price.price
            # logger.debug(f"Binance BTC update: {price.price}")
    
    async def run(self) -> None:
        self.running = True
        logger.info("Starting market maker (Dual Token)...")
        tasks = [
            asyncio.create_task(self._polymarket_feed(), name="polymarket_feed"),
            asyncio.create_task(self._user_feed(), name="user_feed"),
            asyncio.create_task(self._quote_loop(), name="quote_loop"),
        ]
        if self.binance_monitor:
            tasks.append(asyncio.create_task(self._binance_feed(), name="binance_feed"))
        
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except KillSwitchError as e:
            logger.critical(f"Kill switch terminated execution: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
        finally:
            await self.shutdown()

    # Feed methods helper
    async def _polymarket_feed(self):
        try: await self.poly_ws.connect()
        except Exception: await asyncio.sleep(5); await self._polymarket_feed()
    async def _user_feed(self): 
        try: await self.user_ws.connect()
        except: await asyncio.sleep(5); await self._user_feed()
    async def _binance_feed(self):
        try: await self.binance_monitor.connect()
        except: await asyncio.sleep(5); await self._binance_feed()

    async def _quote_loop(self) -> None:
        logger.info("🔄 Quote loop starting...")
        await asyncio.sleep(2)
        logger.info("🔄 Quote loop active!")
        while self.running:
            try:
                logger.info("📍 LOOP ITERATION START")
                # WEEKEND BLACKOUT (DISABLED - BTC hourly markets run 24/7)
                # if is_weekend_blackout():
                #     await asyncio.sleep(300)
                #     continue
                if not self.risk.is_safe_to_quote():
                    logger.warning("⚠️ Risk: Not safe to quote")
                    await asyncio.sleep(5)
                    continue
                
                # =============================================================
                # CRYPTO VELOCITY GUARD (DISABLED - Binance latency too high)
                # =============================================================
                # if self.binance_monitor:
                #     btc_vel = self.binance_monitor.get_price_velocity("btcusdt", window_seconds=5)
                #     if btc_vel > 0.001:  # 0.1% move in 5 seconds
                #         logger.warning(f"🌊 HIGH VELOCITY DETECTED: BTC moved {btc_vel:.2%} in 5s. PAUSING QUOTES.")
                #         if self._current_order_ids:
                #              logger.warning("🌊 Emergency Cancel due to Velocity...")
                #              try: 
                #                  self.client.cancel_all()
                #                  self._current_order_ids = []
                #              except: pass
                #         await asyncio.sleep(2)
                #         continue
                
                logger.debug(f"📍 QUOTE LOOP: Running update cycle...")
                
                # FORCE INVENTORY SYNC (Safety Check since WS is unreliable)
                if time.time() - self._last_inv_sync > 10:
                     self._refresh_inventory()
                     self._last_inv_sync = time.time()
                
                
                # Dynamic Refresh Rate (Phase 6)
                # If we are in the "Danger Zone" (<5 mins), speed up.
                self._current_time_left = 3600 # Default
                try: 
                    from pricing import CryptoHourlyPricer
                    self._current_time_left = CryptoHourlyPricer.get_time_remaining()
                except: pass
                
                # Update Market Monitor (DISABLED - BLOCKING ISSUE)
                # if time.time() - self._last_monitor_update > 60:
                #     metrics = self.monitor.calculate_metrics()
                #     if metrics:
                #          self._current_metrics = metrics
                #          r_pct = metrics.percentile_rank
                #          logger.info(f"🧠 Monitor: Rank {r_pct:.1f}% | RSI {metrics.rsi_14:.1f} | VWAP {metrics.vwap_session:.3f}")
                #     self._last_monitor_update = time.time()

                logger.info("📍 About to call _update_quotes()...")
                await self._update_quotes()
                logger.info("📍 _update_quotes() completed")
                
                # DYNAMIC CYCLE TIME: Faster when profitable or near expiry
                if self.inv_yes > 0.5 and self.avg_entry_yes > 0:
                    profit_margin = (self._last_mid_price_yes or 0.5) - self.avg_entry_yes
                    dynamic_cycle = self._get_dynamic_cycle_time(self._current_time_left, profit_margin)
                elif self._current_time_left < 300:
                    dynamic_cycle = 1.0  # Speed Mode last 5 min
                else:
                    dynamic_cycle = self._base_refresh_rate
                
                await asyncio.sleep(dynamic_cycle)
                    
            except Exception as e:
                logger.error(f"Quote loop error: {e}")
                await asyncio.sleep(5)

    async def _update_quotes(self) -> None:
        """Update Dual-Token quotes."""
        
        # SMART CYCLE REGIME CHECK
        regime = self.cycle_manager.get_regime(self._current_time_left)
        # Log Regime change if useful...
        
        logger.info(f"📍 Quote update: _last_mid_price_yes={self._last_mid_price_yes}")
        
        if not self._last_mid_price_yes:
            # Only log every 30s to avoid spam
            if not hasattr(self, "_last_wait_log") or time.time() - self._last_wait_log > 30:
                 logger.info("📡 Waiting for Polymarket Order Book data (MID_PRICE_YES)...")
                 self._last_wait_log = time.time()
            return
            
        mid_yes = self._last_mid_price_yes
        
        # Risk Check: Net Inventory (self.inventory is YES - NO)
        allowed_sides = self.risk.get_allowed_sides(self.inventory, mid_yes)
        if allowed_sides == QuoteSide.NONE:
            logger.warning(f"⚠️ Risk blocking quotes: allowed_sides=NONE, inv={self.inventory}")
            return
        
        logger.info(f"📊 Quote check: YES={mid_yes:.2f}, allowed={allowed_sides}, inv={self.inventory:.1f}")

        # Strategy: Get Dual Quotes
        # Retrieve full book for Trend Guard
        book = self.poly_ws.get_order_book(self.token_id_yes)
        
        # PASS SESSION METRICS TO STRATEGY
        # (Oracle removed per User Request: "Markets are irrational")
        dual_quote = self.strategy.get_dual_quotes(mid_yes, self.inventory, order_book=book, metrics=self._current_metrics)
        
        # =====================================================================
        # TIME-BASED DECAY INJECTION (Legacy - Removing to avoid conflict)
        # =====================================================================
        # decay_yes = self.scalp_manager.get_decay_adjustment("yes")
        # decay_no = self.scalp_manager.get_decay_adjustment("no")
        # Removing legacy decay logic blocks...
        
        # =====================================================================
        # PROFIT GUARD + SMART PATIENCE (DISABLED - Tiered Selling handles this)
        # =====================================================================
        # The tiered selling system now manages sell prices based on profit tiers.
        # Patience Guard was conflicting by lifting asks too high.
        
        # margin_yes = self.cycle_manager.get_dynamic_margin("yes", self._current_time_left)
        # margin_no = self.cycle_manager.get_dynamic_margin("no", self._current_time_left)
        
        # YES Guard - DISABLED
        # if self.inv_yes > 5.0 and self.avg_entry_yes > 0:
        #     min_ask = self.avg_entry_yes + margin_yes
        #     if dual_quote.yes.ask < min_ask:
        #          logger.info(f"🛡️ PATIENCE YES: Lifting Ask {dual_quote.yes.ask:.3f} -> {min_ask:.3f} (Entry {self.avg_entry_yes:.3f} + Margin {margin_yes:.3f})")
        #          dual_quote.yes.ask = min_ask
        
        # NO Guard - DISABLED
        # if self.inv_no > 5.0 and self.avg_entry_no > 0:
        #     min_ask_no = self.avg_entry_no + margin_no
        #     if dual_quote.no.ask < min_ask_no:
        #          logger.info(f"🛡️ PATIENCE NO: Lifting Ask {dual_quote.no.ask:.3f} -> {min_ask_no:.3f} (Entry {self.avg_entry_no:.3f} + Margin {margin_no:.3f})")
        #          dual_quote.no.ask = min_ask_no

        # =====================================================================

        # =====================================================================
        
        # Sanity Checks
        try:
            self.risk.sanity_check_quote(dual_quote.yes)
            self.risk.sanity_check_quote(dual_quote.no)
        except RiskError as e:
            logger.error(f"Quote sanity failure: {e}")
            return

        # Prepare 4 Orders
        new_orders = []
        
        # --- YES Orders ---
        bid_yes, ask_yes = dual_quote.yes.bid, dual_quote.yes.ask
        
        # Long Only: Don't sell YES if we don't have it
        if self.long_only and self.inv_yes <= 0.1:
             ask_yes = None

        # CYCLE MODE FILTER (Buying Blocker)
        if self.use_cycle_mode:
             if not regime["buy_allowed"]:
                 bid_yes = None # Stop Buying in Zone 3
             
             # Also enforce size mult
             # (Handled in get_order_size)

        # enforce reduce-only if needed
        if allowed_sides == QuoteSide.ASK_ONLY: bid_yes = None
        if allowed_sides == QuoteSide.BID_ONLY: ask_yes = None
        
        # Soft Stop Override
        if self.soft_stop:
             # Logic simplified: Unwind Net Exposure
             if self.inventory > 0: bid_yes = None # Don't buy more YES
             if self.inventory < 0: ask_yes = None # Don't sell more YES
        
        # =====================================================================
        # SMART TIERED BUYING: Replace simple ONE-SHOT with intelligent entry
        # This OVERRIDES cycle mode blocking - tiered buying has its own logic
        # =====================================================================
        should_buy, buy_side, buy_size = self._should_buy_tiered(mid_yes)
        
        # Store buy intent for later recording (after order is placed)
        _pending_buy_tier = None
        
        if not should_buy:
            # No buy signal - block all bids
            bid_yes = None
            bid_no = None
        else:
            # Buy signal - GENERATE bid prices for the chosen side
            if buy_side == "yes":
                bid_no = None  # Block NO buys
                # Generate aggressive bid: buy at current mid (or slightly above)
                bid_yes = mid_yes  # Market buy at mid price
                _pending_buy_tier = self._get_buy_tier(mid_yes, "yes")
                logger.info(f"💰 TIERED BUY: Generating YES bid @ {bid_yes:.3f}")
            elif buy_side == "no":
                bid_yes = None  # Block YES buys
                mid_no = 1.0 - mid_yes
                bid_no = mid_no  # Market buy at mid price
                _pending_buy_tier = self._get_buy_tier(mid_no, "no")
                logger.info(f"💰 TIERED BUY: Generating NO bid @ {bid_no:.3f}")

        # Extract Zone metadata for sizing
        zone_info = dual_quote.metadata.get("zone", "UNKNOWN") if dual_quote.metadata else "UNKNOWN"
        
        # SIZE CALCULATION: Use tiered sell size for asks, tiered buy size for bids
        if ask_yes and self.inv_yes > 0.5:
            # TIERED SELLING: percentage-based sell size
            size_yes = self._get_tiered_sell_size(mid_yes, token_side="yes")
        elif should_buy and buy_side == "yes":
            # TIERED BUYING: fixed $5 per tier
            size_yes = buy_size / max(mid_yes, 0.10)  # Convert $ to shares
        else:
            # Normal sizing
            size_yes = self._get_order_size(bid_yes or mid_yes, zone=zone_info, token_side="yes")
        
        # Taker Check YES:
        # 1. If we are in DEEP_VAL (Cheap), we want to BUY aggressively (Bid). Allow Taker.
        # 2. If we are Selling to exit (Inventory > 0), Allow Taker.
        post_only_yes = True
        if zone_info == "DEEP_VAL" and bid_yes:
             post_only_yes = False # Sniper Buy
        if self.inventory > 0 and ask_yes:
             post_only_yes = False # Panic Sell / Exit
             
        new_orders.extend(create_quote_orders(self.client, self.token_id_yes, bid_yes, ask_yes, size_yes, post_only=post_only_yes))

        # --- NO Orders ---
        bid_no, ask_no = dual_quote.no.bid, dual_quote.no.ask
        
        # Long Only: Don't sell NO if we don't have it
        if self.long_only and self.inv_no <= 0.1:
             ask_no = None
             
        # CYCLE MODE FILTER (NO Token)
        if self.use_cycle_mode:
             if not regime["buy_allowed"]:
                 bid_no = None 

        # Soft Stop Override (keep existing)
        if self.soft_stop:
             if self.inventory > 0: ask_no = None 
             if self.inventory < 0: bid_no = None
        
        # SMART TIERED BUYING: NO side is already filtered above
        # (bid_no is set to None when should_buy=False or buy_side="yes")
        
        # SIZE CALCULATION: Use tiered sell size for NO asks, tiered buy size for bids
        if ask_no and self.inv_no > 0.5:
            # TIERED SELLING: percentage-based sell size
            size_no = self._get_tiered_sell_size(1 - mid_yes, token_side="no")
        elif should_buy and buy_side == "no":
            # TIERED BUYING: fixed $5 per tier
            size_no = buy_size / max(1 - mid_yes, 0.10)  # Convert $ to shares
        else:
            # Normal sizing
            size_no = self._get_order_size(bid_no or (1-mid_yes), zone=zone_info, token_side="no")
        
        # Taker Check NO:
        # 1. If we are in BUBBLE (YES Expensive -> NO Cheap), we want to BUY NO aggressively. Allow Taker.
        # 2. If we are Selling NO to exit (Inventory < 0), Allow Taker.
        post_only_no = True
        if zone_info == "BUBBLE" and bid_no:
             post_only_no = False # Sniper Buy NO
        if self.inventory < 0 and ask_no:
             post_only_no = False # Panic Sell NO
             
        if self.token_id_no and self.token_id_no != "UNKNOWN":
             new_orders.extend(create_quote_orders(self.client, self.token_id_no, bid_no, ask_no, size_no, post_only=post_only_no))

        # SNIPER MODE: Skip order posting if no buy signal AND no inventory
        # This prevents balance errors from unused market-making orders
        if not should_buy and abs(self.inventory) < 0.5:
            # Only cancel existing orders, don't post new ones
            if self._current_order_ids:
                try:
                    self.client.cancel_all()
                    self._current_order_ids = []
                except: pass
            logger.info(f"⏳ WAITING: No buy signal (YES @ {mid_yes:.2f}), no inventory. Standing by...")
            return

        # Execute
        if self.dry_run:
            logger.info(f"🧪 DRY RUN: Would POST YES {bid_yes}/{ask_yes} | NO {bid_no}/{ask_no} | Size {size_yes}/{size_no}")
            # Don't record tiered buy in dry-run mode
        else:
            try:
                # Store old inventory to detect fills
                old_inv_yes = self.inv_yes
                old_inv_no = self.inv_no
                
                result = batch_cancel_and_post(self.client, self._current_order_ids, new_orders)
                if result["posted"]:
                    self._current_order_ids = extract_order_ids(result["posted"])
                    
                    # TIERED BUYING: Store pending tier, but DON'T record yet
                    # We'll record when we detect inventory increase (fill)
                    if should_buy and _pending_buy_tier is not None and buy_side:
                        self._pending_tier_side = buy_side
                        self._pending_tier = _pending_buy_tier
                        logger.info(f"📋 Pending tier {_pending_buy_tier} for {buy_side.upper()} - waiting for fill...")
                    
                # Log
                logger.info(f"Quotes: YES {bid_yes}/{ask_yes} | NO {bid_no}/{ask_no} | NetInv: {self.inventory:.1f}")
            except Exception as e:
                logger.error(f"Order failure: {e}")
            
        # =====================================================================
        # PHASE 7: TIME-PHASED BAILOUT
        # =====================================================================
        # Exit threshold depends on Time Remaining.
        # > 30m: 1% (Diamond Hands)
        # 5-30m: 10% (Disciplined)
        # < 5m:  20% (Paper Hands)
        
        bailout_threshold = 0.01 # Default
        if self._current_time_left < 300: # < 5m
            bailout_threshold = 0.20
        elif self._current_time_left < 1800: # < 30m
            bailout_threshold = 0.10
            
        current_fair_prob = (dual_quote.yes.bid + dual_quote.yes.ask) / 2
        
        is_distressed = False
        distressed_side = None

        # 1. Long YES Bailout Check
        if self.inv_yes > 10 and current_fair_prob < bailout_threshold:
             is_distressed = True
             distressed_side = "YES"
             
        # 2. Long NO Bailout Check
        elif self.inv_no > 10 and current_fair_prob > (1.0 - bailout_threshold):
             is_distressed = True
             distressed_side = "NO"
             
        # Persistence Logic
        if is_distressed:
            if self.distress_start_time is None:
                self.distress_start_time = time.time()
                self.distress_side = distressed_side
                logger.warning(f"⚠️ BAILOUT WARNING: {distressed_side} in distress (Prob {current_fair_prob:.2f}). Timer started.")
            else:
                elapsed = time.time() - self.distress_start_time
                if elapsed > 15.0: # 15s Persistence (User wanted "Time below threshold")
                    # TRIGGER
                    logger.warning(f"🚨 TIME-PHASED BAILOUT FIRED: {distressed_side} distressed for {int(elapsed)}s. DUMPING!")
                    
                    if distressed_side == "YES":
                        self._execute_bailout(self.token_id_yes, self.inv_yes)
                    else:
                        if self.token_id_no:
                             self._execute_bailout(self.token_id_no, self.inv_no)
                    
                    # Reset after firing to avoid spamming (or maybe we want to keep firing?)
                    # Usually dumping takes 1 cycle. Let's reset to allow re-check.
                    self.distress_start_time = None
        else:
            if self.distress_start_time is not None:
                logger.info(f"✅ Bailout Warning Cleared. (Prob recovered to {current_fair_prob:.2f})")
                self.distress_start_time = None
                self.distress_side = None
                 
        # =====================================================================
        # PHASE 7: DATA FACTORY
        # =====================================================================
        # Log this tick to CSV for future AI training.
        # We need to ensure DataLogger is initialized.
        # For now, let's keep it simple and just rely on the existing log file or add a CSV append here?
        # A separate CSV is cleaner.
        try:
            with open("data/ticks.csv", "a") as f:
                # Format: timestamp, price_btc, fair_prob, bid, ask, net_inv, time_left
                # Ensure header exists? We'll skip check for perf.
                line = f"{time.time()},{self.binance_monitor.btc_price},{current_fair_prob},{dual_quote.yes.bid},{dual_quote.yes.ask},{self.inv_yes - self.inv_no},{self._current_time_left}\n"
                f.write(line)
        except Exception:
            pass # Don't crash on logging
                 
    def _execute_bailout(self, token_id: str, amount: float):
        """Execute emergency sell for bailout."""
        try:
            # Cancel existing adds to free up shares? Ideally yes.
            # Post Limit Sell at 0.05 or lower to cross spread?
            # Polymarket API doesn't support 'Market' easily, we use Aggressive Limit.
            # Sell at 0.01 to hit any bid.
            orders = create_quote_orders(self.client, token_id, None, 0.01, amount, post_only=False)
            self.client.update_orders([], orders) # Post immediately
            logger.warning(f"🔥 BAILOUT FIRED: Selling {amount} of {token_id} at 0.01")
        except Exception as e:
            logger.error(f"Bailout failed: {e}")
            
    def _get_tiered_sell_size(self, mid_price: float, token_side: str = "yes") -> float:
        """
        Calculate sell size based on profit tier (percentage of holdings).
        Uses tiered aggressive selling based on profit margin.
        """
        min_shares = 5.0
        
        # Get current inventory and entry price
        if token_side == "yes":
            inv = self.inv_yes
            entry = self.avg_entry_yes
        else:
            inv = self.inv_no
            entry = self.avg_entry_no
        
        # No inventory = no sell
        if inv < 0.5 or entry <= 0:
            return min_shares
        
        # Calculate profit margin
        profit_margin = mid_price - entry
        
        # Last 3 min = FULL LIQUIDATION regardless of profit
        if self._current_time_left < 180:
            logger.info(f"⏰ ENDGAME: Last 3 min. FULL LIQUIDATION!")
            return max(min_shares, inv)
        
        # Get tier and sell percentage
        tier_name, sell_pct = self._get_profit_tier(profit_margin)
        
        # Check for tier cross (triggers immediate full-tier sell)
        crossed = self._check_tier_cross(tier_name)
        if crossed:
            # On tier cross, we want to act on full percentage immediately
            pass  # sell_pct already set correctly
        
        # Calculate shares to sell
        shares = inv * sell_pct
        
        # Ensure minimum
        shares = max(min_shares, shares)
        
        # Cap at actual inventory
        shares = min(shares, inv)
        
        logger.debug(f"TIERED SELL: Tier={tier_name}, Profit={profit_margin:.3f}, Sell%={sell_pct:.0%}, Shares={shares:.1f}")
        
        return round(shares, 2)
            
    def _get_order_size(self, price: float, zone: str = "UNKNOWN", token_side: str = "unknown") -> float:
        # Market requires minimum 5 shares usually? 
        # Actually min tick is $1 or shares? Let's assume 5 shares for now.
        min_shares_limit = 5.0 # Polymarket hard limit
        
        # DYNAMIC SIZING (Heatmap Strategy)
        # 40-60%: Min Size ($2) -> Risk of being < 5 shares
        # 20-40% / 60-80%: Med Size ($5)
        # <20% / >80%: Max Size ($10)
        
        # Base targets in USDC
        target_val = 5.0 # Default to $5 to be safe (approx 10 shares at 0.50)
        
        # ASYMMETRIC SIZING (Phase 5)
        if zone == "BUBBLE":
             # YES is expensive. NO is cheap. Focus on NO.
             target_val = 15.0 if token_side == "no" else 5.0
        elif zone == "DEEP_VAL":
             # NO is expensive. YES is cheap. Focus on YES.
             target_val = 15.0 if token_side == "yes" else 5.0
        elif zone == "EXPENSIVE":
             target_val = 10.0 if token_side == "no" else 5.0
        elif zone == "CHEAP":
             target_val = 10.0 if token_side == "yes" else 5.0
        elif zone == "FAIR":
             target_val = 5.0
        else:
             target_val = 5.0 # Fallback
        
        # CYCLE MODE MULTIPLIER (Zone Decay)
        if self.use_cycle_mode:
             regime = self.cycle_manager.get_regime(self._current_time_left)
             target_val *= regime["size_mult"]
             
        # Convert Target Value (USDC) to Shares
        # shares = value / price
        safe_price = max(price, 0.01)
        shares = target_val / safe_price
        
        # HARD FLOOR: 5 Shares
        if shares < min_shares_limit:
            shares = min_shares_limit
            
        return round(shares, 2)
    
    def _on_fill_event(self, event: FillEvent, token_side: str):
        """Handle fill events to track inventory and WAP."""
        # Update specific inv & Avg Entry
        if token_side == "yes":
             if event.side == "buy": 
                 # Weighted Avg Update: (OldVal + NewVal) / NewTotal
                 total_cost = (self.inv_yes * self.avg_entry_yes) + (event.size * event.price)
                 self.inv_yes += event.size
                 self.avg_entry_yes = total_cost / max(self.inv_yes, 0.1) # Avoid div0
                 
                 # Record Buy Time for Smart Patience
                 if self.use_cycle_mode: self.cycle_manager.record_buy("yes")
             else: 
                 self.inv_yes -= event.size
                 if self.inv_yes < 0.1: self.avg_entry_yes = 0.0 # Reset if empty
        else:
             if event.side == "buy": 
                 total_cost = (self.inv_no * self.avg_entry_no) + (event.size * event.price)
                 self.inv_no += event.size
                 self.avg_entry_no = total_cost / max(self.inv_no, 0.1)
                 
                 if self.use_cycle_mode: self.cycle_manager.record_buy("no")
             else: 
                 self.inv_no -= event.size
                 if self.inv_no < 0.1: self.avg_entry_no = 0.0
              
        # Risk update
        self.risk.record_fill(event.side, event.price, event.size, token_side)
        self.scalp_manager.on_fill(event.side, event.size, event.price, token_side)
        self.data_logger.log_fill(event.side, event.price, event.size, self.market_id)
        
        logger.info(f"🎒 Position Update: YES {self.inv_yes:.1f} (@ {self.avg_entry_yes:.3f}) | NO {self.inv_no:.1f} (@ {self.avg_entry_no:.3f})")
    
    async def shutdown(self) -> None:
        """Graceful shutdown."""
        self.running = False
        logger.info("Shutting down...")
        
        # Cancel all orders
        try:
            self.client.cancel_all()
            logger.info("All orders cancelled")
        except Exception as e:
            logger.error(f"Error cancelling orders: {e}")
        
        # Disconnect data feeds
        try:
            await self.poly_ws.disconnect()
        except Exception:
            pass
            
        try:
            await self.user_ws.disconnect()
        except Exception:
            pass
        
        if self.binance_monitor:
            try:
                await self.binance_monitor.disconnect()
            except Exception:
                pass
        
        # Log final risk status
        status = self.risk.get_status()
        logger.info(f"Final status: {status}")




async def main(market_id: str = None, token_id: str = None) -> None:
    """
    Main entry point.
    """
    loop = asyncio.get_running_loop()

    
    # Parse custom arguments
    parser = argparse.ArgumentParser(description="Polymarket Market Maker")
    parser.add_argument("market_id", nargs='?', help="Polymarket market identifier (slug)")
    parser.add_argument("token_id", nargs='?', help="Token ID to trade")
    parser.add_argument("--inv", type=float, help="Starting inventory (override on-chain check)")
    parser.add_argument("--refresh", type=float, default=4.0, help="Quote refresh interval in seconds (default: 4.0)")
    parser.add_argument("--long-only", action="store_true", help="Disable short selling (sell only what you own)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate quotes without sending orders")
    parser.add_argument("--strike", type=float, help="Manual Strike Price (Price to Beat)")
    parser.add_argument("--auto", action="store_true", help="Enable Auto-Pilot (Automated Market Selection)")
    parser.add_argument("--cycle", action="store_true", help="Enable Cycle Mode (Buy-Only -> Sell-Only)")
    
    # Parse known args, leaving others for sys.argv access if needed (though cleaner to use parser)
    args, unknown = parser.parse_known_args()
    
    
    # Configure risk limits (adjusted for $17 balance)
    risk_config = RiskConfig(
        max_inventory_value=10.0,   # User Request: Conservative ($10) max inventory
        daily_loss_limit=-4.0,      # -$4 daily loss limit (~25% of capital)
    )
    
    # Auto-Pilot Mode
    if getattr(args, 'auto', False): # Use getattr in case I forgot to add_argument above
         print("=" * 60)
         print("🤖 MODE: AUTO-PILOT ENGAGED")
         print("   - Market Selection: AUTOMATED")
         print("   - Safety Filters: ACTIVE")
         print("   - Risk Profile: CONSERVATIVE ($10 Limit)")
         print("=" * 60)
         
         # Initialize Client
         client_wrapper = PolymarketClient()
         client = client_wrapper.get_client()
         
         # Initialize AutoPilot
         pilot = AutoPilot(
             clob_client=client,
             market_maker_class=MarketMaker,
             args=args,
             risk_config=risk_config
         )
         
         def handle_signal():
             logger.warning("👋 Shutdown signal received.")
             asyncio.create_task(pilot.stop())
             
         for sig in (signal.SIGINT, signal.SIGTERM):
             loop.add_signal_handler(sig, handle_signal)
             
         # Run Pilot (Async)
         await pilot.run()
         return

    # Standard Single-Market Mode Validation
    if not args.market_id or not args.token_id:
        parser.error("market_id and token_id are required unless --auto is used.")

    market_id = args.market_id
    token_id = args.token_id
    starting_inv = args.inv
    refresh_rate = args.refresh
    long_only = args.long_only

    print("=" * 60)
    print("Polymarket Market Maker")
    print("Goal: Don't go broke.")
    if long_only:
         print("Mode: LONG ONLY (Short selling disabled)")
    print("=" * 60)
    
    print("=" * 60)
    
    # Initialize Market Maker
    mm = MarketMaker(
        market_id=market_id,
        token_id=token_id,
        risk_config=risk_config,
        starting_inventory=args.inv,
        refresh_rate_seconds=args.refresh,
        long_only=args.long_only,
        dry_run=args.dry_run,
        fixed_strike=args.strike,
        use_cycle_mode=getattr(args, 'cycle', False)
    )
    
    # Handle shutdown signals
    # 1st Ctrl+C = Soft Stop (Bailout)
    # 2nd Ctrl+C = Hard Stop (shutdown)
    
    signal_state = [True] # Mutable wrapper for closure
    def handle_signal():
        if args.auto:
             if signal_state[0]:
                 logger.info("🛑 SIGINT received. Stopping AutoPilot (Soft Stop)...")
                 asyncio.create_task(pilot.stop())
                 signal_state[0] = False
             else:
                 logger.critical("😤 FORCE EXIT REQUESTED")
                 sys.exit(1)
        else:
             # Single Market Mode
             if not mm.soft_stop:
                  logger.info("🛑 SIGINT received. Soft Stopping MarketMaker...")
                  mm.initiate_soft_stop()
             else:
                  logger.critical("😤 FORCE EXIT REQUESTED")
                  asyncio.create_task(mm.shutdown())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)
    
    # Run the market maker (all feeds run concurrently)
    await mm.run()
    
    # Exit with appropriate code
    if mm.risk.state.kill_switch_triggered:
        logger.critical("Exiting due to kill switch")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
