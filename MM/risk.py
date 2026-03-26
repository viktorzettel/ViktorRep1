"""
Risk management module for Polymarket Market Maker.

Goal: Don't go broke.

Implements critical safety checks:
- max_inventory_check: Stop quoting one side if inventory exceeds limit
- kill_switch: Exit if daily PnL breaches loss limit
- sanity_check: Validate order parameters before submission
"""

import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Optional

from strategy import Quote


logger = logging.getLogger(__name__)


# =============================================================================
# Risk Enums and Types
# =============================================================================

class QuoteSide(Enum):
    """Which side(s) to quote."""
    BOTH = auto()
    BID_ONLY = auto()  # Reduce only when long
    ASK_ONLY = auto()  # Reduce only when short
    NONE = auto()      # Kill switch triggered


class RiskAction(Enum):
    """Actions the risk manager can take."""
    CONTINUE = auto()
    REDUCE_ONLY_BID = auto()
    REDUCE_ONLY_ASK = auto()
    KILL_SWITCH = auto()


@dataclass
class RiskConfig:
    """Risk management configuration."""
    # Inventory limits
    max_inventory_value: float = 20.0  # Increased for Cycle Mode (Target $15)
    
    # Daily PnL kill switch
    daily_loss_limit: float = -3.0  # -$3.00 daily loss limit
    
    # Sanity check bounds
    min_valid_price: float = 0.01
    max_valid_price: float = 0.99
    min_spread: float = 0.01  # Minimum 1 cent spread
    
    # Dual-Token Specific
    max_pair_cost: float = 0.99 # Ensure Bid YES + Bid NO <= 0.99
    # max_arb_bid_price: float = 0.49 # (Deprecated)


@dataclass
class RiskState:
    """Current risk state."""
    inventory: float = 0.0 # Net Exposure (YES - NO)
    inv_yes: float = 0.0
    inv_no: float = 0.0
    
    avg_entry_price: float = 0.0  # Net WAP
    
    inventory_value: float = 0.0  # Absolute Net Value
    daily_pnl: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    trades_today: int = 0
    kill_switch_triggered: bool = False
    last_check_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class RiskError(Exception):
    """Exception raised when risk checks fail."""
    pass


class KillSwitchError(Exception):
    """Exception raised when kill switch is triggered."""
    pass


class TradingHoursError(Exception):
    """Exception raised when trading outside allowed hours."""
    pass


# =============================================================================
# Weekend Trading Blackout
# =============================================================================

def is_weekend_blackout(now: Optional[datetime] = None) -> bool:
    """
    Check if current time is within weekend blackout period.
    
    Blackout: Friday 10PM UTC → Monday 6AM UTC
    
    This protects against thin-liquidity manipulation attacks like the
    @a4385 exploit that extracted $233K from market makers on a Saturday.
    
    Args:
        now: Current time (uses UTC now if None).
    
    Returns:
        True if within blackout period, False if safe to trade.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    
    weekday = now.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
    hour = now.hour
    
    # Friday after 10PM UTC
    if weekday == 4 and hour >= 22:
        return True
    
    # All of Saturday and Sunday
    if weekday in (5, 6):
        return True
    
    # Monday before 6AM UTC
    if weekday == 0 and hour < 6:
        return True
    
    return False


def get_next_trading_open(now: Optional[datetime] = None) -> datetime:
    """
    Get the next time trading will be allowed.
    
    Args:
        now: Current time (uses UTC now if None).
    
    Returns:
        Datetime when trading resumes (Monday 6AM UTC).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    
    weekday = now.weekday()
    
    # Calculate days until Monday
    if weekday == 4:  # Friday
        days_until_monday = 3
    elif weekday == 5:  # Saturday
        days_until_monday = 2
    elif weekday == 6:  # Sunday
        days_until_monday = 1
    else:  # Monday early morning
        days_until_monday = 0
    
    # Next Monday 6AM UTC
    from datetime import timedelta
    next_monday = now.replace(hour=6, minute=0, second=0, microsecond=0)
    next_monday += timedelta(days=days_until_monday)
    
    return next_monday


# =============================================================================
# Flow Imbalance Detection
# =============================================================================

class FlowTracker:
    """
    Tracks fill flow to detect one-sided trading (adverse selection signal).
    
    If 80%+ of recent fills are on one side, something is wrong:
    - Informed traders are picking you off
    - You're providing exit liquidity for manipulators
    
    Usage:
        tracker = FlowTracker()
        tracker.record_fill("sell")  # Record each fill
        
        if tracker.is_toxic():
            # Stop quoting or widen spreads
            pass
    """
    
    def __init__(
        self,
        window_size: int = 20,
        toxic_threshold: float = 0.80,
    ) -> None:
        """
        Initialize the flow tracker.
        
        Args:
            window_size: Number of recent fills to track.
            toxic_threshold: Imbalance ratio that triggers toxic signal (0.80 = 80%).
        """
        self.window_size = window_size
        self.toxic_threshold = toxic_threshold
        self._fills: list[str] = []  # "buy" or "sell"
    
    def record_fill(self, side: str) -> None:
        """
        Record a fill.
        
        Args:
            side: "buy" or "sell".
        """
        self._fills.append(side.lower())
        
        # Keep only recent fills
        if len(self._fills) > self.window_size:
            self._fills.pop(0)
    
    def get_imbalance(self) -> tuple[float, str]:
        """
        Get current flow imbalance.
        
        Returns:
            Tuple of (imbalance_ratio, dominant_side).
            Imbalance is 0.5 if balanced, up to 1.0 if one-sided.
        """
        if len(self._fills) < 5:
            return 0.5, "neutral"
        
        buys = self._fills.count("buy")
        sells = self._fills.count("sell")
        total = len(self._fills)
        
        if buys > sells:
            return buys / total, "buy"
        elif sells > buys:
            return sells / total, "sell"
        else:
            return 0.5, "neutral"
    
    def is_toxic(self) -> bool:
        """
        Check if flow is toxic (one-sided beyond threshold).
        
        Returns:
            True if 80%+ of fills are on one side.
        """
        imbalance, _ = self.get_imbalance()
        return imbalance >= self.toxic_threshold
    
    def get_status(self) -> dict:
        """Get current flow status."""
        imbalance, dominant = self.get_imbalance()
        return {
            "fills_tracked": len(self._fills),
            "imbalance": imbalance,
            "dominant_side": dominant,
            "is_toxic": self.is_toxic(),
        }
    
    def reset(self) -> None:
        """Clear all tracked fills."""
        self._fills.clear()


# =============================================================================
# Risk Manager
# =============================================================================

class RiskManager:
    """
    Manages all risk checks for the market maker.
    
    Safety features:
    1. max_inventory_check: Stops quoting one side if inventory > $500
    2. kill_switch: Cancels all orders and exits if daily PnL < -$50
    3. sanity_check: Validates bid < ask before any order
    
    Usage:
        risk = RiskManager()
        
        # Before quoting
        allowed = risk.max_inventory_check(inventory=100, mid_price=0.50)
        if allowed == QuoteSide.NONE:
            # Kill switch active
            pass
        
        # Before sending orders
        risk.sanity_check(bid=0.48, ask=0.52)
        
        # After each trade
        risk.update_pnl(pnl_change=-5.0)
    """
    
    def __init__(self, config: Optional[RiskConfig] = None) -> None:
        """
        Initialize the risk manager.
        
        Args:
            config: Risk configuration (uses defaults if None).
        """
        self.config = config or RiskConfig()
        self.state = RiskState()
        self._on_kill_switch: Optional[callable] = None
        
        logger.info(
            f"RiskManager initialized: "
            f"max_inventory=${self.config.max_inventory_value}, "
            f"daily_loss_limit=${self.config.daily_loss_limit}"
        )
    
    def set_kill_switch_callback(self, callback: callable) -> None:
        """Set callback to execute when kill switch triggers."""
        self._on_kill_switch = callback
    
    # =========================================================================
    # Core Risk Checks
    # =========================================================================
    
    def max_inventory_check(
        self,
        inventory: float,
        mid_price: float,
    ) -> QuoteSide:
        """
        Check if inventory exceeds limit and determine allowed quote sides.
        
        If inventory > $500 on one side:
        - Long (positive inventory): Stop bidding, ask only (reduce only)
        - Short (negative inventory): Stop asking, bid only (reduce only)
        
        Args:
            inventory: Current position (positive = long, negative = short).
            mid_price: Current mid-market price.
        
        Returns:
            QuoteSide indicating which sides can be quoted.
        
        Raises:
            KillSwitchError: If kill switch has been triggered.
        """
        if self.state.kill_switch_triggered:
            return QuoteSide.NONE
        
        # Calculate inventory value
        inventory_value = abs(inventory) * mid_price
        self.state.inventory = inventory
        self.state.inventory_value = inventory_value
        
        # Check if over limit
        if inventory_value > self.config.max_inventory_value:
            if inventory > 0:
                # Long position over limit - reduce only (ask only)
                logger.warning(
                    f"INVENTORY LIMIT: Long ${inventory_value:.2f} > "
                    f"${self.config.max_inventory_value}. ASK ONLY mode."
                )
                return QuoteSide.ASK_ONLY
            else:
                # Short position over limit - reduce only (bid only)
                logger.warning(
                    f"INVENTORY LIMIT: Short ${inventory_value:.2f} > "
                    f"${self.config.max_inventory_value}. BID ONLY mode."
                )
                return QuoteSide.BID_ONLY
        
        return QuoteSide.BOTH
    
    def kill_switch(self, daily_pnl: Optional[float] = None) -> bool:
        """
        Check if kill switch should be triggered based on daily PnL.
        
        If daily PnL < -$50:
        - Cancel all orders
        - Exit the process
        
        Args:
            daily_pnl: Override current daily PnL (uses stored value if None).
        
        Returns:
            True if kill switch triggered, False otherwise.
        
        Raises:
            KillSwitchError: When kill switch is triggered.
        """
        if daily_pnl is not None:
            self.state.daily_pnl = daily_pnl
        
        if self.state.daily_pnl < self.config.daily_loss_limit:
            self.state.kill_switch_triggered = True
            
            logger.critical(
                f"🚨 KILL SWITCH TRIGGERED 🚨 "
                f"Daily PnL: ${self.state.daily_pnl:.2f} < "
                f"${self.config.daily_loss_limit:.2f}"
            )
            
            # Execute callback if set
            if self._on_kill_switch:
                self._on_kill_switch()
            
            raise KillSwitchError(
                f"Daily PnL ${self.state.daily_pnl:.2f} breached "
                f"limit ${self.config.daily_loss_limit:.2f}"
            )
        
        return False
    
    def sanity_check(self, bid: float, ask: float) -> None:
        """
        Validate order parameters before submission.
        
        Asserts that:
        - bid < ask (no crossed quotes)
        - Prices are within valid range [0.01, 0.99]
        - Spread is at least minimum
        
        Args:
            bid: Proposed bid price.
            ask: Proposed ask price.
        
        Raises:
            RiskError: If any sanity check fails.
        """
        # Check bid < ask
        if bid >= ask:
            raise RiskError(
                f"SANITY CHECK FAILED: bid ({bid:.4f}) >= ask ({ask:.4f}). "
                "Crossed quotes would give away free money!"
            )
        
        # Check price bounds
        if bid < self.config.min_valid_price:
            raise RiskError(
                f"SANITY CHECK FAILED: bid ({bid:.4f}) < "
                f"min price ({self.config.min_valid_price})"
            )
        
        if ask > self.config.max_valid_price:
            raise RiskError(
                f"SANITY CHECK FAILED: ask ({ask:.4f}) > "
                f"max price ({self.config.max_valid_price})"
            )
        
        # Check minimum spread
        spread = ask - bid
        if spread < self.config.min_spread:
            raise RiskError(
                f"SANITY CHECK FAILED: spread ({spread:.4f}) < "
                f"minimum ({self.config.min_spread})"
            )
            
        # Check Max Bid (For Arb Strategy)
        # Replaced with Dynamic Pair Cost Check in Strategy
        pass
        
        logger.debug(f"Sanity check passed: bid={bid:.4f}, ask={ask:.4f}")
    
    def sanity_check_quote(self, quote: Quote) -> None:
        """
        Validate a Quote object.
        
        Args:
            quote: Quote to validate.
        
        Raises:
            RiskError: If sanity check fails.
        """
        self.sanity_check(quote.bid, quote.ask)
    
    # =========================================================================
    # PnL Tracking
    # =========================================================================
    
    def update_pnl(self, pnl_change: float) -> None:
        """
        Update daily PnL and check kill switch.
        
        Args:
            pnl_change: Change in PnL from a trade.
        """
        self.state.daily_pnl += pnl_change
        self.state.trades_today += 1
        
        logger.info(
            f"PnL update: {pnl_change:+.2f} | "
            f"Daily: ${self.state.daily_pnl:.2f} | "
            f"Trades: {self.state.trades_today}"
        )
        
        # Check kill switch after each update
        self.kill_switch()
    
    def update_unrealized_pnl(self, unrealized: float) -> None:
        """
        Update unrealized PnL from mark-to-market.
        
        Args:
            unrealized: Current unrealized PnL.
        """
        self.state.unrealized_pnl = unrealized
        total_pnl = self.state.realized_pnl + unrealized
        self.state.daily_pnl = total_pnl
        
        # Check kill switch
        self.kill_switch()
    
    def record_fill(self, side: str, price: float, size: float, token_side: str = "yes") -> None:
        """
        Record a fill and update realized PnL and WAP.
        
        Args:
            side: 'buy' or 'sell'.
            price: Fill price.
            size: Fill size.
            token_side: 'yes' or 'no'.
        """
        self.state.trades_today += 1
        
        # Update individual token inventory
        signed_size = size if side == "buy" else -size
        
        if token_side == "yes":
            self.state.inv_yes += signed_size
        else:
            self.state.inv_no += signed_size
            
        # Update Net Exposure (YES - NO)
        # Why? Because Short YES == Long NO. 
        # So Buying YES (+1) increases exposure.
        # Buying NO (+1) acts like Selling YES (-1) in terms of payout delta.
        # Net Exposure = YES - NO
        
        old_net_inv = self.state.inventory
        
        # Determine effective update to net inventory
        # Buy YES -> +1 Net
        # Sell YES -> -1 Net
        # Buy NO -> -1 Net (Hedge)
        # Sell NO -> +1 Net
        
        net_change = signed_size if token_side == "yes" else -signed_size
        new_net_inv = old_net_inv + net_change
        
        # ---------------------------------------------------------------------
        # WAP (Weighted Average Price) Tracking (SIMPLIFIED FOR NET)
        # ---------------------------------------------------------------------
        # Logic: Track average cost of the NET exposure
        
        is_increasing = (old_net_inv > 0 and net_change > 0) or \
                        (old_net_inv < 0 and net_change < 0) or \
                        (old_net_inv == 0)
                        
        if is_increasing:
            # Formula: (OldVal + NewVal) / NewQty
            old_val = abs(old_net_inv) * self.state.avg_entry_price
            new_val = size * price # Approximation using current fill price
            total_qty = abs(new_net_inv)
            
            if total_qty > 0:
                self.state.avg_entry_price = (old_val + new_val) / total_qty
            else:
                self.state.avg_entry_price = 0.0
                
        elif (old_net_inv > 0 and new_net_inv < 0) or (old_net_inv < 0 and new_net_inv > 0):
             # Flipped
            self.state.avg_entry_price = price
            
        # Update state inventory
        self.state.inventory = new_net_inv
        
        logger.info(
            f"Fill: {side.upper()} {token_side.upper()} {size}@{price:.4f} | "
            f"Net Inv: {old_net_inv:.1f} -> {new_net_inv:.1f} (Y:{self.state.inv_yes:.1f} N:{self.state.inv_no:.1f}) | "
            f"WAP: {self.state.avg_entry_price:.4f}"
        )
    
    # =========================================================================
    # Risk State Queries
    # =========================================================================
    
    def is_safe_to_quote(self) -> bool:
        """Check if it's safe to continue quoting."""
        return not self.state.kill_switch_triggered
    
    def get_allowed_sides(self, inventory: float, mid_price: float) -> QuoteSide:
        """Get which sides are allowed to quote."""
        return self.max_inventory_check(inventory, mid_price)
    
    def get_status(self) -> dict:
        """Get current risk status."""
        return {
            "inventory": self.state.inventory,
            "inventory_value": self.state.inventory_value,
            "daily_pnl": self.state.daily_pnl,
            "trades_today": self.state.trades_today,
            "kill_switch_triggered": self.state.kill_switch_triggered,
            "max_inventory_value": self.config.max_inventory_value,
            "daily_loss_limit": self.config.daily_loss_limit,
        }
    
    def reset_daily(self) -> None:
        """Reset daily counters (call at start of trading day)."""
        self.state.daily_pnl = 0.0
        self.state.realized_pnl = 0.0
        self.state.unrealized_pnl = 0.0
        self.state.trades_today = 0
        self.state.kill_switch_triggered = False
        logger.info("Daily risk counters reset")
