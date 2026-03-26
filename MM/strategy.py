"""
Strategy Module: Hourly Crypto Scalper

Logic:
    1. Identify Market (Asset, Expiry, Type) using CryptoParser.
    2. Get Real-Time Inputs (Price, Open, Vol) from DataFeed.
    3. Calculate Fair Probability using PricingEngine.
    4. Generate Quotes centered on Fair Probability.
"""

from dataclasses import dataclass
from typing import Optional
import logging
import time

from crypto_parser import CryptoParser, CryptoMarketData
from pricing import CryptoHourlyPricer
from crypto_parser import CryptoParser, CryptoMarketData
from pricing import CryptoHourlyPricer
from data_feed import BinancePriceMonitor, LocalOrderBook

logger = logging.getLogger(__name__)

# =============================================================================
# Data Structures (Compatible with main.py)
# =============================================================================

@dataclass
class Quote:
    bid: float
    ask: float
    
    @property
    def spread(self) -> float:
        return self.ask - self.bid
    
    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

@dataclass
class DualQuote:
    yes: Quote
    no: Quote
    metadata: Optional[dict] = None # Carries Zone info for Sizing logic

@dataclass
class StrategyConfig:
    min_spread: float = 0.02
    max_spread: float = 0.10
    skew_aggressiveness: float = 2.0  # How hard to lean into the trend
    vol_lookback: int = 600 # seconds for realized vol
    # Phase 5 Defenses
    use_basis_guard: bool = True    # Enable/Disable Basis Guard
    basis_risk_buffer: float = 0.02 # 2% extra spread for Oracle Mismatch
    gamma_guard_seconds: int = 120  # Stop quoting 2 mins before expiry if ATM
    
    # Phase 6: Active Manager
    min_volatility: float = 0.30    # Floor vol at 30% (Weekend Guard)
    trend_skew_strength: float = 0.5 # 0.0-1.0 factor for trend following
    
    # Phase 7: Advanced Signals
    # Phase 7: Advanced Signals
    volume_skew_strength: float = 0.2 # Impact of Volume Flow on Prob

    # Override
    fixed_strike: Optional[float] = None

# =============================================================================
# The Hourly Strategy
# =============================================================================

class CryptoHourlyStrategy:
    """
    Oracle-Driven Strategy for Hourly "Up/Down" Markets.
    """
    
    def __init__(
        self,
        market_title: str,
        binance_monitor: BinancePriceMonitor,
        config: Optional[StrategyConfig] = None
    ):
        self.config = config or StrategyConfig()
        self.binance = binance_monitor
        self.title = market_title
        
        # 1. Parse Market
        self.market_data: Optional[CryptoMarketData] = CryptoParser.parse_title(market_title)
        
        if self.market_data:
            logger.info(f"🧠 Strategy Initialized: {self.market_data.asset} {self.market_data.market_type} ({self.market_data.period})")
        else:
            logger.error(f"❌ Failed to parse market title: {market_title}")

    def get_dual_quotes(
        self,
        mid_price_yes: float,
        net_inventory: float, # YES - NO
        volatility_override: Optional[float] = None,
        order_book: Optional[LocalOrderBook] = None,
        metrics: Optional['MarketMetrics'] = None, # New dependency
    ) -> DualQuote:
        """
        Produce quotes using the Mirror Heatmap Strategy (Market Memory).
        """
        # Default Safety
        safe_bid = 0.01; safe_ask = 0.99
        safety_quote = DualQuote(Quote(safe_bid, safe_ask), Quote(safe_bid, safe_ask))

        if not self.market_data or not self.binance:
            return safety_quote

        # 2. Monitor Metrics Check (The Heatmap Logic)
        if metrics:
            return self._calculate_heatmap_quotes(mid_price_yes, net_inventory, metrics)
            
        # Fallback to Old Logic (Condensed) if Monitor fail
        anchor_price = mid_price_yes or 0.50
        spread = 0.05
        
        mid_yes = anchor_price - (net_inventory * 0.001)
        bid_yes = max(0.01, mid_yes - spread/2)
        ask_yes = min(0.99, mid_yes + spread/2)
        
        return DualQuote(
            yes=Quote(bid_yes, ask_yes),
            no=Quote(1-ask_yes, 1-bid_yes)
        )

    def _calculate_heatmap_quotes(
        self, 
        market_mid: float, 
        inventory: float, 
        metrics: 'MarketMetrics'
    ) -> DualQuote:
        """
        Core Mirror Logic: 5-Zone Asymmetric Session Grid.
        """
        rank = metrics.percentile_rank
        vwap = metrics.vwap_session
        
        # 1. SESSION ANCHORING (Improved Mean Reversion)
        # Use a blend of current market mid and session vwap (Gravity Center)
        if (0.05 < vwap < 0.95):
            # 50/50 Blend
            anchor = (market_mid * 0.5) + (vwap * 0.5)
        else:
            anchor = market_mid
        
        # 2. ASYMMETRIC MIRROR SPREADS
        # Default: Tight 1ct spreads
        spread_yes = 0.01
        spread_no = 0.01
        zone = "FAIR"

        if rank > 80:
            zone = "BUBBLE"
            spread_yes = 0.10
            spread_no = 0.01
        elif rank > 60:
            zone = "EXPENSIVE"
            spread_yes = 0.04
            spread_no = 0.01
        elif rank < 20:
            zone = "DEEP_VAL"
            spread_yes = 0.01
            spread_no = 0.10
        elif rank < 40:
            zone = "CHEAP"
            spread_yes = 0.01
            spread_no = 0.04

        # 3. FINAL QUOTE GENERATION
        inv_skew = inventory * 0.001
        final_mid_yes = anchor - inv_skew
        
        bid_yes = max(0.01, final_mid_yes - spread_yes/2)
        ask_yes = min(0.99, final_mid_yes + spread_yes/2)
        
        final_mid_no = 1.0 - final_mid_yes
        bid_no = max(0.01, final_mid_no - spread_no/2)
        ask_no = min(0.99, final_mid_no + spread_no/2)
        
        return DualQuote(
            yes=Quote(bid_yes, ask_yes),
            no=Quote(bid_no, ask_no),
            metadata={"zone": zone, "rank": rank}
        )

# Bridge for compatibility (if main.py imports old classes)
# We can alias or just replace usage in main.py
