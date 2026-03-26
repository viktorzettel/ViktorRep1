"""
Micro-Scalping Manager

Implements "Time-Based Inventory Decay" to prevent the "Inventory Trap".
Tracks inventory batches in FIFO order and calculates price adjustments
based on holding time.

Logic:
- Inventory Risk is a function of TIME, not just SIZE.
- The longer we hold, the more we aggressively lower our ASK (or raise BID) to exit.
"""

import time
import logging
from collections import deque
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class InventoryBatch:
    """A specific batch of tokens acquired at a specific time."""
    size: float
    price: float
    timestamp: float
    side: str  # 'YES' or 'NO'

class MicroScalpingManager:
    def __init__(
        self, 
        decay_rate_per_min: float = 0.01,  # Drop price 1 cent per minute
        grace_period_sec: float = 30.0,    # No decay for first 30s
        max_hold_sec: float = 300.0,       # 5 minutes max
        max_decay_cap: float = 0.10        # Max decay 10 cents
    ):
        """
        Initialize the manager.
        
        Args:
            decay_rate_per_min: How much to penalize price per minute of holding.
            grace_period_sec: Seconds before decay starts.
            max_hold_sec: Time after which decay accelerates.
            max_decay_cap: Hard limit on how much we shift price.
        """
        self.long_batches = deque()  # Batches of YES tokens
        self.short_batches = deque() # Batches of NO tokens (effectively Short YES)
        
        self.decay_rate = decay_rate_per_min
        self.grace_period = grace_period_sec
        self.max_hold = max_hold_sec
        self.max_decay_cap = max_decay_cap
        
        logger.info(
            f"MicroScalper Initialized: "
            f"Decay={self.decay_rate}/min, Grace={self.grace_period}s"
        )

    def on_fill(self, side: str, size: float, price: float, token_side: str = "yes"):
        """
        Register a new trade fill and update FIFO batches.
        
        Args:
            side: 'buy' or 'sell'
            size: Amount filled
            price: Fill price
            token_side: 'yes' or 'no'
        """
        now = time.time()
        
        # Normalize to YES exposure
        # Buying YES = Long YES
        # Selling NO = Long YES (Synthetically) -> We treat NO inventory distinct for now but logically connected
        
        # SIMPLIFICATION: We track YES and NO inventory separately for FIFO
        # This matches the physical tokens we hold in the wallet.
        
        target_queue = self.long_batches if token_side.lower() == 'yes' else self.short_batches
        opposite_queue = self.short_batches if token_side.lower() == 'yes' else self.long_batches
        
        if side.lower() == 'buy':
            # We are acquiring tokens -> Add to Batch
            target_queue.append(InventoryBatch(size, price, now, token_side.upper()))
            logger.info(f"MicroScalp: Acquired {size} {token_side.upper()} @ {price:.2f}")
            
        elif side.lower() == 'sell':
            # We are selling tokens -> Remove from Oldest Batch (FIFO)
            remaining_to_sell = size
            
            while remaining_to_sell > 0 and target_queue:
                oldest_batch = target_queue[0]
                
                if oldest_batch.size <= remaining_to_sell:
                    # Consumed entire batch
                    remaining_to_sell -= oldest_batch.size
                    target_queue.popleft()
                else:
                    # Partial consumption
                    oldest_batch.size -= remaining_to_sell
                    remaining_to_sell = 0
            
            if remaining_to_sell > 0:
                logger.warning(f"MicroScalp: Sold {size} {token_side} but only had matches for part of it. (External fill?)")

    def get_decay_adjustment(self, token_side: str = "yes") -> float:
        """
        Calculate the price adjustment (decay) for a specific side.
        
        Returns:
            Float representing the price DROP (positive value).
            e.g. 0.02 means "Lower price by 2 cents".
        """
        now = time.time()
        queue = self.long_batches if token_side.lower() == 'yes' else self.short_batches
        
        if not queue:
            return 0.0
            
        # Decay is driven by the OLDEST batch
        oldest_batch = queue[0]
        age_seconds = now - oldest_batch.timestamp
        
        if age_seconds < self.grace_period:
            return 0.0
            
        # Linear Decay Calculation
        minutes_over = (age_seconds - self.grace_period) / 60.0
        decay = minutes_over * self.decay_rate
        
        # Acceleration if over max hold
        if age_seconds > self.max_hold:
            decay *= 2.0  # Double penalty for toxic waste
            
        # Cap it
        final_decay = min(decay, self.max_decay_cap)
        
        if final_decay > 0.005: # Only log significant decay
             logger.debug(f"MicroScalp {token_side}: Age {age_seconds:.0f}s -> Decay {final_decay:.4f}")
             
        return final_decay

    def get_status(self) -> dict:
        """Get diagnostic status."""
        return {
            "yes_batches": len(self.long_batches),
            "no_batches": len(self.short_batches),
            "yes_oldest_age": (time.time() - self.long_batches[0].timestamp) if self.long_batches else 0,
        }
