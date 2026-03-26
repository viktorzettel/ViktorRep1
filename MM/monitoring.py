"""
Market Monitoring Module
Responsible for fetching historical data and calculating "Market Memory" metrics
(VWAP, RSI, Percentiles) for the Heatmap Strategy.
"""

import logging
import time
import requests
import numpy as np
from dataclasses import dataclass
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)

@dataclass
class MarketMetrics:
    current_price: float
    rsi_14: float
    vwap_session: float
    percentile_rank: float # 0-100
    
    # Raw Zones for debug
    p10: float
    p20: float
    p40: float
    p60: float
    p80: float
    p90: float

class MarketMonitor:
    def __init__(self, token_id: str, lookback_hours: int = 1):
        self.token_id = token_id
        self.lookback_hours = lookback_hours
        
    def fetch_history(self) -> List[Dict]:
        """Fetch 1m candles for the token."""
        try:
            now = int(time.time())
            start = now - (3600 * self.lookback_hours)
            
            # Use Fidelity=10 as discovered in research
            url = f"https://clob.polymarket.com/prices-history?interval=1m&market={self.token_id}&startTs={start}&endTs={now}&fidelity=10"
            
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                return data.get('history', [])
            else:
                logger.warning(f"Failed to fetch history: {resp.status_code}")
                return []
        except Exception as e:
            logger.error(f"Error fetching history: {e}")
            return []

    def calculate_metrics(self, ignore_last_mins: int = 3) -> Optional[MarketMetrics]:
        """
        Calculate implementation plan metrics.
        
        Args:
            ignore_last_mins: Exclude recent N minutes of data (Stability Filter).
                              Prevents "Expiry Chaos" from skewing the session stats.
        """
        history = self.fetch_history()
        # Sparse Data Handling: Allow 5 candles minimum (for young/illiquid markets)
        if not history or len(history) < 5: 
            return None
            
        # Filter History (Ignore last N minutes)
        # Assuming history is sorted by time (API usually does)
        # Check timestamps? 't' field.
        # history[-1] is latest.
        
        cutoff_index = len(history)
        if ignore_last_mins > 0:
            # removing last N candles
            if len(history) > ignore_last_mins + 15: # Ensure we keep enough data
                cutoff_index = -ignore_last_mins
            else:
                pass # Not enough data to filter, keep all
                
        # Parse Data (Sliced)
        # We use Full History for RSI (Needs recent momentum)
        # But we use Sliced History for Percentiles/VWAP (Needs stability)
        
        # Prices for RSI (All data to capture current crash/spike)
        prices_full = [float(x['p']) for x in history]
        current_price = prices_full[-1]
        
        # Prices for Stats (Lagged)
        prices_stats = prices_full[:cutoff_index]
        if not prices_stats: prices_stats = prices_full # Fallback
        
        # 1. RSI (14) - On FULL Data (We need to know if it's overbought NOW)
        rsi = self._calculate_rsi(prices_full, period=14)
        
        # 2. Percentiles (Session Range) - On STABLE Data
        p10 = np.percentile(prices_stats, 10)
        p20 = np.percentile(prices_stats, 20)
        p40 = np.percentile(prices_stats, 40)
        p60 = np.percentile(prices_stats, 60)
        p80 = np.percentile(prices_stats, 80)
        p90 = np.percentile(prices_stats, 90)
        
        # Calculate Rank (0-100) using STABLE distribution
        percentile_rank = self._calculate_percentile_rank(prices_stats, current_price)
        
        # 3. VWAP equivalent (Median) - On STABLE Data
        vwap = np.median(prices_stats) 
        
        if ignore_last_mins > 0:
            logger.debug(f"📉 Monitor: Stats Lag {ignore_last_mins}m applied. (Stats Len {len(prices_stats)} vs Full {len(prices_full)})")
        
        return MarketMetrics(
            current_price=current_price,
            rsi_14=rsi,
            vwap_session=vwap,
            percentile_rank=percentile_rank,
            p10=p10, p20=p20, p40=p40, p60=p60, p80=p80, p90=p90
        )

    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        if len(prices) < period + 1:
            return 50.0
            
        deltas = np.diff(prices)
        seed = deltas[:period+1]
        up = seed[seed >= 0].sum() / period
        down = -seed[seed < 0].sum() / period
        rs = up / down if down != 0 else 0
        rsi = 100 - (100 / (1 + rs))

        for i in range(period, len(prices)-1):    
            delta = prices[i+1] - prices[i]
            
            if delta > 0:
                upval = delta
                downval = 0.0
            else:
                upval = 0.0
                downval = -delta

            up = (up * (period - 1) + upval) / period
            down = (down * (period - 1) + downval) / period
            
            rs = up / down if down != 0 else 0
            rsi = 100 - (100 / (1 + rs))
            
        return rsi

    def _calculate_percentile_rank(self, prices: List[float], current: float) -> float:
        """Calculate what percentile the current price is in relative to history."""
        params = np.array(prices)
        return (params < current).mean() * 100.0
