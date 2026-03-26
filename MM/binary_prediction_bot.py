"""
Bitcoin Binary Options Prediction Bot

Dry-run implementation for testing prediction accuracy on Polymarket 15-minute
BTC Up/Down markets. Makes 8 decisions per candle (every 30s from T-4:00 to T-0:30).

Architecture:
- HAR-RV volatility model for baseline forecast
- 5-second high-frequency polling in decision window
- Signal-specific rolling windows (micro-vol=2min, OFI=3min, momentum=5min)
- Multi-decision mode with dry-run logging
"""

import asyncio
import json
import logging
import math
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiohttp
import numpy as np

# =============================================================================
# CONFIGURATION
# =============================================================================

POSITION_SIZE = 4.0  # $4 fixed for testing
MIN_EDGE = 0.05      # 5% edge required to trade
DRY_RUN = True       # Log decisions but don't execute

# Decision timing (seconds before candle close)
# Bot activates at T-6:00, collects data, first decision at T-5:00
# Then every 30 seconds until T-0:30
DECISION_POINTS = [300, 270, 240, 210, 180, 150, 120, 90, 60, 30]  # 10 decisions
DECISION_WINDOW_START = 360  # Start 6 min before close for data collection

# Rolling window sizes (seconds)
WINDOW_MICRO_VOL = 120   # 2 minutes
WINDOW_OFI = 180         # 3 minutes  
WINDOW_MOMENTUM = 300    # 5 minutes
PREFETCH_MINUTES = 15    # Historical data to fetch at activation (increased from 10)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("BinaryBot")

# Data directory for logs
DATA_DIR = Path(__file__).parent / "data" / "binary_bot_logs"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class PriceSnapshot:
    """Single 5-second price/book snapshot."""
    timestamp: float
    price: float
    bids: list = field(default_factory=list)  # [(price, size), ...]
    asks: list = field(default_factory=list)


@dataclass 
class Decision:
    """Single decision point result."""
    decision_num: int
    seconds_before_close: int
    timestamp: float
    p_up: float
    yes_price: float
    no_price: float
    target: str  # "yes" or "no" or "skip"
    action: str  # "BUY" or "SKIP"
    edge: float
    ev: float
    reason: str = ""


@dataclass
class CandleResult:
    """Result for one complete candle."""
    candle_id: str
    open_price: float
    close_price: float
    outcome: str  # "up" or "down"
    decisions: list = field(default_factory=list)
    final_prediction_correct: bool = False


# =============================================================================
# DATA FETCHING
# =============================================================================

async def fetch_binance_price() -> float:
    """Fetch current BTC price from Binance."""
    url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        async with session.get(url) as resp:
            data = await resp.json()
            return float(data["price"])


# -----------------------------------------------------------------------------
# CHAINLINK-ALIGNED PRICE PROVIDERS
# Polymarket uses Chainlink Data Streams. Binance runs ~$80 higher than Chainlink.
# These providers (Coinbase, Bitstamp, Gemini) are closer to the Chainlink aggregate.
# -----------------------------------------------------------------------------

async def fetch_coinbase_price() -> float:
    """Fetch BTC price from Coinbase."""
    url = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        async with session.get(url) as resp:
            data = await resp.json()
            return float(data["data"]["amount"])


async def fetch_bitstamp_price() -> float:
    """Fetch BTC price from Bitstamp."""
    url = "https://www.bitstamp.net/api/v2/ticker/btcusd"
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        async with session.get(url) as resp:
            data = await resp.json()
            return float(data["last"])


async def fetch_gemini_price() -> float:
    """Fetch BTC price from Gemini."""
    url = "https://api.gemini.com/v1/pubticker/btcusd"
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        async with session.get(url) as resp:
            data = await resp.json()
            return float(data["last"])


async def fetch_chainlink_aligned_price() -> tuple[float, str]:
    """
    Fetch BTC price from Chainlink-aligned provider.
    
    Based on 2-minute live testing (24 samples):
    - Bitstamp: $3.34 avg deviation (WINNER - closest to Chainlink)
    - Gemini: $3.86 avg deviation
    - Coinbase: $16.81 avg deviation
    - Binance: $81.75 avg deviation
    
    Returns:
        (price, source_description)
    """
    # Try Bitstamp first (best match to Chainlink)
    try:
        price = await fetch_bitstamp_price()
        return (price, "Bitstamp")
    except Exception as e:
        logger.warning(f"Bitstamp failed: {e}")
    
    # Fallback to Gemini (second-best)
    try:
        price = await fetch_gemini_price()
        return (price, "Gemini")
    except Exception as e:
        logger.warning(f"Gemini failed: {e}")
    
    # Fallback to Coinbase
    try:
        price = await fetch_coinbase_price()
        return (price, "Coinbase")
    except Exception as e:
        logger.warning(f"Coinbase failed: {e}")
    
    # Last resort: Binance (but warn because it's ~$80 higher)
    logger.warning("All Chainlink-aligned providers failed, using Binance (+$80 vs oracle)")
    price = await fetch_binance_price()
    return (price, "Binance (fallback)")


async def fetch_binance_klines(interval: str = "1m", limit: int = 1440) -> list:
    """
    Fetch 1-minute klines from Binance.
    Default: last 1440 bars = 24 hours.
    """
    url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval={interval}&limit={limit}"
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        async with session.get(url) as resp:
            data = await resp.json()
            # Return list of close prices
            return [float(candle[4]) for candle in data]


async def fetch_binance_order_book(depth: int = 10) -> dict:
    """Fetch BTC order book from Binance."""
    url = f"https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit={depth}"
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        async with session.get(url) as resp:
            data = await resp.json()
            return {
                "bids": [(float(b[0]), float(b[1])) for b in data["bids"]],
                "asks": [(float(a[0]), float(a[1])) for a in data["asks"]]
            }


async def fetch_binance_agg_trades(start_time: int, end_time: int) -> list:
    """
    Fetch aggregated trades from Binance for historical prefetch.
    Times in milliseconds.
    """
    url = f"https://api.binance.com/api/v3/aggTrades?symbol=BTCUSDT&startTime={start_time}&endTime={end_time}&limit=1000"
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        async with session.get(url) as resp:
            data = await resp.json()
            return data


async def fetch_funding_rate() -> float:
    """Fetch BTC perpetual funding rate from Binance."""
    url = "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1"
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(url) as resp:
                data = await resp.json()
                if data:
                    return float(data[0]["fundingRate"])
                return 0.0
    except Exception as e:
        logger.warning(f"Failed to fetch funding rate: {e}")
        return 0.0


async def fetch_polymarket_prices(token_id_yes: str) -> tuple[float, float]:
    """
    Fetch Yes/No token prices from Polymarket CLOB.
    Returns (yes_price, no_price).
    """
    if not token_id_yes:
        return (0.50, 0.50)
    
    try:
        url = f"https://clob.polymarket.com/books/{token_id_yes}"
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(url) as resp:
                data = await resp.json()
                
                # Get best bid/ask
                bids = data.get("bids", [])
                asks = data.get("asks", [])
                
                if bids and asks:
                    yes_bid = float(bids[0]["price"])
                    yes_ask = float(asks[0]["price"])
                    yes_mid = (yes_bid + yes_ask) / 2
                else:
                    yes_mid = 0.50
                
                return (yes_mid, 1 - yes_mid)
    except Exception as e:
        logger.warning(f"Failed to fetch Polymarket prices: {e}")
        return (0.50, 0.50)


async def fetch_active_btc_market() -> Optional[dict]:
    """
    Scan Polymarket for active BTC 15-minute Up/Down markets.
    Returns market info with token IDs and expiry time.
    
    NOTE: This API approach has reliability issues. For production,
    use parse_market_url_epoch() with the direct market URL.
    """
    try:
        url = "https://gamma-api.polymarket.com/events"
        params = {"active": "true", "limit": 200}
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",  # Avoid brotli
        }
        
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(url, params=params, headers=headers) as resp:
                events = await resp.json()
        
        now = datetime.now(timezone.utc)
        candidates = []
        
        for e in events:
            title = str(e.get("title", "")).lower()
            # Look for BTC up/down markets
            if not ("bitcoin" in title and "up or down" in title):
                continue
            
            for m in e.get("markets", []):
                end_date = m.get("endDate")
                if not end_date:
                    continue
                
                try:
                    end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                except Exception:
                    continue
                
                mins_left = (end_dt - now).total_seconds() / 60.0
                if mins_left < 0 or mins_left > 20:  # Only within 20 minutes
                    continue
                
                # Parse token IDs
                tokens_raw = m.get("clobTokenIds", [])
                if isinstance(tokens_raw, str):
                    import json as json_lib
                    try:
                        tokens = json_lib.loads(tokens_raw)
                    except Exception:
                        tokens = []
                else:
                    tokens = tokens_raw
                
                yes_token = tokens[0] if len(tokens) > 0 else None
                no_token = tokens[1] if len(tokens) > 1 else None
                
                candidates.append({
                    "title": e.get("title"),
                    "end_time": end_dt,
                    "minutes_left": mins_left,
                    "yes_token": yes_token,
                    "no_token": no_token,
                })
        
        if not candidates:
            return None
        
        # Sort by closest to expiry
        candidates.sort(key=lambda x: x["minutes_left"])
        return candidates[0]
        
    except Exception as e:
        logger.error(f"Failed to scan Polymarket: {e}")
        return None


def parse_market_url_epoch(url: str) -> Optional[int]:
    """
    Parse epoch timestamp from Polymarket market URL.
    
    Example URL: https://polymarket.com/event/btc-updown-15m-1770375600
    Returns: 1770375600 (Unix timestamp of candle close)
    """
    import re
    match = re.search(r'btc-updown-15m-(\d+)', url)
    if match:
        return int(match.group(1))
    return None


# =============================================================================
# HAR-RV VOLATILITY MODEL
# =============================================================================

def compute_har_rv(minute_prices: list) -> float:
    """
    Compute HAR-RV volatility forecast.
    
    Returns annualized volatility (decimal, e.g., 0.50 = 50%).
    Uses realized variance at different time scales.
    """
    if len(minute_prices) < 60:  # Need at least 1 hour
        logger.warning("Insufficient data for HAR-RV, using default vol")
        return 0.50
    
    # Compute log returns
    prices = np.array(minute_prices)
    returns = np.diff(np.log(prices))
    n = len(returns)
    
    # Realized volatility (annualized) at different scales
    # RV = std(returns) * sqrt(periods_per_year)
    # For 1-minute data: periods_per_year = 365 * 24 * 60 = 525,600
    
    sqrt_annual = np.sqrt(525600)  # Annualization factor for 1-min data
    
    # Daily component (last ~24h or available)
    n_day = min(1440, n)
    rv_day = np.std(returns[-n_day:]) * sqrt_annual
    
    # Weekly component (use all available data as proxy)
    rv_week = np.std(returns) * sqrt_annual
    
    # HAR weighted average
    # Simple weighting: mostly recent data
    har_vol = 0.5 * rv_day + 0.3 * rv_week + 0.2 * rv_week
    
    return min(max(har_vol, 0.10), 2.0)  # Clamp between 10% and 200%


# =============================================================================
# SIGNAL COMPUTATION
# =============================================================================

def compute_micro_volatility(buffer: list[PriceSnapshot], window_sec: int = WINDOW_MICRO_VOL) -> float:
    """
    Compute realized volatility over last 2 minutes.
    Short window captures current regime.
    """
    now = time.time()
    recent = [s.price for s in buffer if s.timestamp > now - window_sec]
    
    if len(recent) < 10:
        return float('nan')
    
    returns = np.diff(np.log(recent))
    # Scale to window duration
    return np.std(returns) * np.sqrt(len(recent))


def compute_cumulative_ofi(buffer: list[PriceSnapshot], window_sec: int = WINDOW_OFI) -> float:
    """
    Compute cumulative Order Flow Imbalance over last 3 minutes.
    
    OFI = Σ(Δ bid_volume - Δ ask_volume)
    Positive = buying pressure, Negative = selling pressure.
    """
    now = time.time()
    recent = [s for s in buffer if s.timestamp > now - window_sec]
    
    if len(recent) < 2:
        return 0.0
    
    total_ofi = 0.0
    for i in range(1, len(recent)):
        prev, curr = recent[i-1], recent[i]
        
        if not prev.bids or not curr.bids:
            continue
            
        # Sum top 5 levels
        prev_bid_vol = sum(b[1] for b in prev.bids[:5])
        curr_bid_vol = sum(b[1] for b in curr.bids[:5])
        prev_ask_vol = sum(a[1] for a in prev.asks[:5])
        curr_ask_vol = sum(a[1] for a in curr.asks[:5])
        
        bid_delta = curr_bid_vol - prev_bid_vol
        ask_delta = curr_ask_vol - prev_ask_vol
        
        total_ofi += bid_delta - ask_delta
    
    return total_ofi


def compute_momentum(buffer: list[PriceSnapshot], window_sec: int = WINDOW_MOMENTUM) -> float:
    """
    Compute momentum as normalized linear regression slope over last 5 minutes.
    Returns score in [-1, +1].
    """
    now = time.time()
    recent = [(s.timestamp, s.price) for s in buffer if s.timestamp > now - window_sec]
    
    if len(recent) < 20:
        return 0.0
    
    x = np.array([r[0] for r in recent])
    y = np.array([r[1] for r in recent])
    
    # Linear regression slope
    slope = np.polyfit(x, y, 1)[0]
    
    # Normalize by standard deviation
    std_y = np.std(y)
    if std_y == 0:
        return 0.0
    
    return np.clip(slope / std_y, -1, 1)


def compute_funding_signal(funding_rate: float) -> float:
    """
    Convert funding rate to directional signal.
    
    High positive funding = longs overcrowded → bearish
    High negative funding = shorts overcrowded → bullish
    """
    if funding_rate > 0.0005:  # > 0.05%
        return -0.05  # Bearish adjustment
    elif funding_rate < -0.0005:  # < -0.05%
        return 0.05   # Bullish adjustment
    return 0.0


# =============================================================================
# PROBABILITY ESTIMATION
# =============================================================================

def norm_cdf(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def estimate_p_up(
    current_price: float,
    open_price: float,
    har_volatility: float,
    micro_vol: float,
    ofi: float,
    momentum: float,
    funding_signal: float,
    time_remaining_minutes: float
) -> float:
    """
    Estimate probability that close > open.
    
    Combines Black-Scholes base probability with signal adjustments.
    """
    # Current return from open
    current_return = (current_price - open_price) / open_price
    
    # Use micro-vol if valid, else HAR
    vol = micro_vol if not np.isnan(micro_vol) and micro_vol > 0 else har_volatility
    
    # Time decay factor
    time_factor = np.sqrt(time_remaining_minutes / 15)  # For 15-min candle
    
    # Base probability (Black-Scholes style)
    if vol * time_factor > 0.0001:
        z_score = current_return / (vol * time_factor)
        p_base = norm_cdf(z_score)
    else:
        p_base = 1.0 if current_return > 0 else 0.0
    
    # Signal adjustments (weights calibrated empirically)
    alpha_ofi = 0.05
    alpha_momentum = 0.10
    alpha_funding = 0.03
    
    # OFI uses tanh to bound effect
    ofi_adjustment = alpha_ofi * np.tanh(ofi / 100)
    momentum_adjustment = alpha_momentum * momentum
    funding_adjustment = alpha_funding * funding_signal
    
    p_adjusted = p_base + ofi_adjustment + momentum_adjustment + funding_adjustment
    
    # Clamp to avoid extremes
    return np.clip(p_adjusted, 0.05, 0.95)


# =============================================================================
# DECISION LOGIC
# =============================================================================

def make_decision(
    p_up: float,
    yes_price: float,
    min_edge: float = MIN_EDGE
) -> Decision:
    """
    Decide whether to trade and which direction.
    """
    # Determine direction
    if p_up > 0.5:
        target = "yes"
        model_prob = p_up
        market_prob = yes_price
    else:
        target = "no"
        model_prob = 1 - p_up
        market_prob = 1 - yes_price
    
    # Calculate edge
    edge = model_prob - market_prob
    
    # Expected value
    if edge > 0:
        ev = model_prob * (1 - market_prob) - (1 - model_prob) * market_prob
    else:
        ev = 0
    
    # Decision
    if edge > min_edge:
        action = "BUY"
        reason = f"Edge {edge:.1%} > threshold {min_edge:.1%}"
    else:
        action = "SKIP"
        reason = f"Edge {edge:.1%} below threshold {min_edge:.1%}"
    
    return Decision(
        decision_num=0,
        seconds_before_close=0,
        timestamp=time.time(),
        p_up=p_up,
        yes_price=yes_price,
        no_price=1 - yes_price,
        target=target,
        action=action,
        edge=edge,
        ev=ev,
        reason=reason
    )


# =============================================================================
# HISTORICAL DATA PREFETCH
# =============================================================================

async def prefetch_historical_data(lookback_minutes: int = PREFETCH_MINUTES) -> list[PriceSnapshot]:
    """
    Fetch historical 5-second data at decision window activation.
    
    Uses Binance aggTrades and resamples to 5-second bars.
    """
    end_time = int(time.time() * 1000)
    start_time = end_time - (lookback_minutes * 60 * 1000)
    
    logger.info(f"Prefetching {lookback_minutes} minutes of historical data...")
    
    try:
        trades = await fetch_binance_agg_trades(start_time, end_time)
        
        if not trades:
            logger.warning("No historical trades fetched")
            return []
        
        # Resample to 5-second bars
        snapshots = []
        current_bucket = None
        bucket_prices = []
        
        for trade in trades:
            trade_time = trade["T"] / 1000  # Convert to seconds
            bucket = int(trade_time // 5) * 5  # 5-second bucket
            
            if bucket != current_bucket:
                if bucket_prices:
                    # Save previous bucket's VWAP or last price
                    snapshots.append(PriceSnapshot(
                        timestamp=current_bucket,
                        price=bucket_prices[-1],  # Use last price in bucket
                        bids=[],
                        asks=[]
                    ))
                current_bucket = bucket
                bucket_prices = []
            
            bucket_prices.append(float(trade["p"]))
        
        # Don't forget last bucket
        if bucket_prices and current_bucket:
            snapshots.append(PriceSnapshot(
                timestamp=current_bucket,
                price=bucket_prices[-1],
                bids=[],
                asks=[]
            ))
        
        logger.info(f"Prefetched {len(snapshots)} historical snapshots")
        return snapshots
        
    except Exception as e:
        logger.error(f"Failed to prefetch historical data: {e}")
        return []


# =============================================================================
# MAIN BOT LOOP
# =============================================================================

class BinaryPredictionBot:
    """
    Main bot for 15-minute BTC binary options prediction.
    """
    
    def __init__(self, token_id_yes: str = "", dry_run: bool = True):
        self.token_id_yes = token_id_yes
        self.dry_run = dry_run
        self.buffer: list[PriceSnapshot] = []
        self.har_volatility: float = 0.50
        self.funding_rate: float = 0.0
        self.current_candle: Optional[CandleResult] = None
    
    async def calibrate_har_rv(self):
        """Calibrate HAR-RV model from 1-minute data."""
        logger.info("Calibrating HAR-RV volatility model...")
        
        # Fetch 7 days of 1-minute data (max from Binance single call is 1000)
        klines_24h = await fetch_binance_klines("1m", 1440)
        
        self.har_volatility = compute_har_rv(klines_24h)
        logger.info(f"HAR-RV calibrated: {self.har_volatility:.1%} annualized vol")
    
    async def update_funding_rate(self):
        """Update funding rate signal."""
        self.funding_rate = await fetch_funding_rate()
        logger.info(f"Funding rate: {self.funding_rate:.4%}")
    
    async def run_decision_window(self, candle_close_time: float, open_price: float):
        """
        Run 8 decisions from T-4:00 to T-0:30.
        
        Args:
            candle_close_time: Unix timestamp when 15-min candle closes
            open_price: BTC price at candle open
        """
        candle_id = datetime.fromtimestamp(candle_close_time, tz=timezone.utc).strftime("%Y%m%d_%H%M")
        
        self.current_candle = CandleResult(
            candle_id=candle_id,
            open_price=open_price,
            close_price=0,
            outcome=""
        )
        
        logger.info(f"=== Decision Window Started | Candle: {candle_id} | Open: ${open_price:,.2f} ===")
        
        # Prefetch historical data (15 minutes of 5-second data)
        logger.info("Prefetching 15 minutes of historical data...")
        self.buffer = await prefetch_historical_data(PREFETCH_MINUTES)
        logger.info(f"Buffer initialized with {len(self.buffer)} snapshots")
        
        # 1-minute data collection phase before first decision
        logger.info("Collecting real-time data for 1 minute...")
        collection_end = candle_close_time - DECISION_POINTS[0]  # First decision time
        while time.time() < collection_end:
            try:
                current_price, _ = await fetch_chainlink_aligned_price()
                order_book = await fetch_binance_order_book(10)
                self.buffer.append(PriceSnapshot(
                    timestamp=time.time(),
                    price=current_price,
                    bids=order_book["bids"],
                    asks=order_book["asks"]
                ))
            except Exception as e:
                logger.warning(f"Data collection error: {e}")
            await asyncio.sleep(5)  # 5-second polling
        
        logger.info(f"Data collection complete. Buffer has {len(self.buffer)} snapshots")
        
        # Fetch Polymarket prices (placeholder for now)
        yes_price, no_price = await fetch_polymarket_prices(self.token_id_yes)
        
        # Run decision loop
        for i, seconds_before in enumerate(DECISION_POINTS):
            target_time = candle_close_time - seconds_before
            
            # Wait until decision time
            now = time.time()
            if now < target_time:
                await asyncio.sleep(target_time - now)
            
            # Fetch current data
            try:
                # Use Chainlink-aligned price (median of Coinbase/Bitstamp/Gemini)
                # This matches Polymarket's oracle better than Binance (~$88 difference)
                current_price, price_source = await fetch_chainlink_aligned_price()
                order_book = await fetch_binance_order_book(10)
                
                # Add to buffer
                self.buffer.append(PriceSnapshot(
                    timestamp=time.time(),
                    price=current_price,
                    bids=order_book["bids"],
                    asks=order_book["asks"]
                ))
                
                # Compute signals
                micro_vol = compute_micro_volatility(self.buffer)
                ofi = compute_cumulative_ofi(self.buffer)
                momentum = compute_momentum(self.buffer)
                funding_signal = compute_funding_signal(self.funding_rate)
                
                time_remaining = seconds_before / 60  # Convert to minutes
                
                # Estimate P_up
                p_up = estimate_p_up(
                    current_price=current_price,
                    open_price=open_price,
                    har_volatility=self.har_volatility,
                    micro_vol=micro_vol,
                    ofi=ofi,
                    momentum=momentum,
                    funding_signal=funding_signal,
                    time_remaining_minutes=time_remaining
                )
                
                # Make decision
                decision = make_decision(p_up, yes_price)
                decision.decision_num = i + 1
                decision.seconds_before_close = seconds_before
                
                self.current_candle.decisions.append(decision)
                
                # Log decision
                delta = (current_price - open_price) / open_price * 100
                logger.info(
                    f"[{i+1}/8] T-{seconds_before//60}:{seconds_before%60:02d} | "
                    f"Price: ${current_price:,.0f} ({delta:+.2f}%) | "
                    f"P_up: {p_up:.1%} | "
                    f"Momentum: {momentum:+.2f} | "
                    f"OFI: {ofi:+.1f} | "
                    f"→ {decision.action} {decision.target.upper() if decision.action == 'BUY' else ''}"
                )
                
            except Exception as e:
                logger.error(f"Decision {i+1} failed: {e}")
        
        # Wait for candle close
        now = time.time()
        if now < candle_close_time:
            logger.info(f"Waiting {candle_close_time - now:.1f}s for candle close...")
            await asyncio.sleep(candle_close_time - now + 2)  # 2s buffer
        
        # Fetch close price (using Chainlink-aligned for consistency)
        close_price, _ = await fetch_chainlink_aligned_price()
        self.current_candle.close_price = close_price
        self.current_candle.outcome = "up" if close_price > open_price else "down"
        
        # Check final prediction
        if self.current_candle.decisions:
            final_decision = self.current_candle.decisions[-1]
            if final_decision.action == "BUY":
                predicted_up = final_decision.target == "yes"
                actual_up = self.current_candle.outcome == "up"
                self.current_candle.final_prediction_correct = predicted_up == actual_up
        
        # Log result
        result_emoji = "✅" if self.current_candle.final_prediction_correct else "❌"
        logger.info(
            f"=== Candle Closed | "
            f"Close: ${close_price:,.2f} | "
            f"Outcome: {self.current_candle.outcome.upper()} | "
            f"Prediction: {result_emoji} ==="
        )
        
        # Save to file
        self._save_candle_result()
        
        return self.current_candle
    
    def _save_candle_result(self):
        """Save candle result to JSON file."""
        if not self.current_candle:
            return
        
        filename = DATA_DIR / f"candle_{self.current_candle.candle_id}.json"
        
        result_dict = {
            "candle_id": self.current_candle.candle_id,
            "open_price": self.current_candle.open_price,
            "close_price": self.current_candle.close_price,
            "outcome": self.current_candle.outcome,
            "final_prediction_correct": self.current_candle.final_prediction_correct,
            "decisions": [
                {
                    "decision_num": d.decision_num,
                    "seconds_before_close": d.seconds_before_close,
                    "timestamp": d.timestamp,
                    "p_up": d.p_up,
                    "yes_price": d.yes_price,
                    "target": d.target,
                    "action": d.action,
                    "edge": d.edge,
                    "ev": d.ev,
                    "reason": d.reason
                }
                for d in self.current_candle.decisions
            ]
        }
        
        with open(filename, "w") as f:
            json.dump(result_dict, f, indent=2)
        
        logger.info(f"Saved result to {filename}")


# =============================================================================
# ENTRY POINT
# =============================================================================

async def main():
    """Main entry point for testing."""
    bot = BinaryPredictionBot(dry_run=True)
    
    # Initial calibration
    await bot.calibrate_har_rv()
    await bot.update_funding_rate()
    
    print("\n" + "="*60)
    print("🤖 BINARY PREDICTION BOT - DRY RUN MODE")
    print("="*60)
    print(f"HAR-RV Volatility: {bot.har_volatility:.1%}")
    print(f"Funding Rate: {bot.funding_rate:.4%}")
    print(f"Position Size: ${POSITION_SIZE}")
    print(f"Min Edge: {MIN_EDGE:.1%}")
    print("="*60 + "\n")
    
    # Prompt user for strike price
    # The "strike" is the CANDLE OPEN PRICE set by Polymarket at the start of the 15-min window.
    # This is the "price to beat" shown on the market page.
    print("📋 MANUAL INPUT REQUIRED")
    print("   Enter the CANDLE OPEN PRICE shown on Polymarket.")
    print("   This is the 'price to beat' for the Up/Down market.")
    print("   (It's set at the start of the 15-min candle using Chainlink oracle)\n")
    
    while True:
        try:
            strike_input = input("   Strike price (e.g., 65850): $")
            strike_price = float(strike_input.replace(",", "").strip())
            
            # Sanity check
            current_price, _ = await fetch_chainlink_aligned_price()
            diff = abs(current_price - strike_price)
            
            if diff > 5000:
                print(f"   ⚠️  Warning: Strike ${strike_price:,.2f} is ${diff:,.0f} from current price")
                confirm = input("   Continue anyway? (y/n): ")
                if confirm.lower() != 'y':
                    continue
            
            print(f"   ✅ Strike set to ${strike_price:,.2f}\n")
            break
            
        except ValueError:
            print("   ❌ Invalid price format. Enter a number like 65850")
    
    # Wait for decision window (T-5:00 before candle close)
    now = time.time()
    current_15m = int(now // 900) * 900
    next_15m_close = current_15m + 900
    
    time_to_decision_window = next_15m_close - DECISION_WINDOW_START - now
    
    if time_to_decision_window > 0:
        print(f"⏳ Decision window starts in {time_to_decision_window/60:.1f} minutes...")
        print(f"   Candle close at {datetime.fromtimestamp(next_15m_close).strftime('%H:%M:%S')}")
        print(f"   T-6:00: Data collection starts")
        print(f"   T-5:00: First decision, then every 30s until T-0:30")
        print("   (Press Ctrl+C to exit)\n")
        await asyncio.sleep(time_to_decision_window)
    
    # Run decision window with user-provided strike price
    await bot.run_decision_window(
        candle_close_time=next_15m_close,
        open_price=strike_price
    )


if __name__ == "__main__":
    asyncio.run(main())
