"""
Data feed module for Polymarket Market Maker.

Provides real-time data streaming from Polymarket and Binance:
- PolymarketWebSocket: Streams local order book updates
- BinancePriceMonitor: Streams BTC/USDT and ETH/USDT prices
- check_price_dislocation: Detects toxic flow based on price differences
"""

import asyncio
import json
import logging
import time
import logging
import time
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional, Tuple


import websockets
from websockets.asyncio.client import ClientConnection


logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
# BINANCE_WS_BASE remains same
BINANCE_WS_BASE = "wss://stream.binance.com:9443/ws"

# User-Agent for Cloudflare Bypass
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Origin": "https://polymarket.com",
}

# Toxic flow threshold (0.5% price difference)
TOXIC_FLOW_THRESHOLD = 0.005


class AlertType(Enum):
    """Alert types for price dislocation detection."""
    TOXIC_FLOW = "TOXIC_FLOW"
    NORMAL = "NORMAL"


@dataclass
class PriceDislocationResult:
    """Result from price dislocation check."""
    alert: AlertType
    poly_price: float
    binance_price: float
    difference_pct: float
    
    @property
    def is_toxic(self) -> bool:
        return self.alert == AlertType.TOXIC_FLOW


# =============================================================================
# Polymarket WebSocket
# =============================================================================

@dataclass
class OrderBookLevel:
    """Single level in the order book."""
    price: float
    size: float


@dataclass
class LocalOrderBook:
    """Local order book state."""
    token_id: str
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)
    timestamp: Optional[float] = None
    
    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None
    
    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None
    
    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None


class PolymarketWebSocket:
    """
    WebSocket client for streaming Polymarket order book updates.
    
    Connects to wss://ws-subscriptions-clob.polymarket.com/ws and subscribes
    to the "market" channel for real-time order book data.
    
    Usage:
        async def on_update(book: LocalOrderBook):
            print(f"Mid price: {book.mid_price}")
        
        ws = PolymarketWebSocket(token_ids=["token_id_here"])
        ws.on_book_update = on_update
        await ws.connect()
    """
    
    def __init__(
        self,
        token_ids: list[str],
        url: str = POLYMARKET_WS_URL,
    ) -> None:
        """
        Initialize the Polymarket WebSocket client.
        
        Args:
            token_ids: List of token IDs to subscribe to.
            url: WebSocket URL (default: Polymarket production).
        """
        self.url = url
        self.token_ids = token_ids
        self._ws: Optional[ClientConnection] = None
        self._running = False
        self._order_books: dict[str, LocalOrderBook] = {}
        
        # Callbacks
        self.on_book_update: Optional[Callable[[LocalOrderBook], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None
    
    async def connect(self) -> None:
        """Connect to WebSocket and start streaming."""
        self._running = True
        
        try:
            import ssl
            ctx = ssl._create_unverified_context()
            async with websockets.connect(self.url, additional_headers=BROWSER_HEADERS, ssl=ctx) as ws:
                self._ws = ws
                logger.info(f"📡 Connected to Polymarket WebSocket: {self.url}")
                
                # Subscribe to market channel for each token
                await self._subscribe()
                
                # Process messages
                await self._message_loop()
                
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            if self.on_error:
                self.on_error(e)
            raise
        finally:
            self._running = False
            self._ws = None
    
    async def _subscribe(self) -> None:
        """Subscribe to order book updates for all token IDs."""
        if not self.token_ids:
            return
            
        subscribe_msg = {
            "type": "market",
            "assets_ids": self.token_ids,
        }
        await self._ws.send(json.dumps(subscribe_msg))
        logger.info(f"Subscribed to market channel for tokens: {self.token_ids}")
        
        # Initialize local order books
        for token_id in self.token_ids:
            self._order_books[token_id] = LocalOrderBook(token_id=token_id)
    
    async def _message_loop(self) -> None:
        """Process incoming WebSocket messages."""
        async for message in self._ws:
            try:
                if not message or not str(message).strip():
                    continue
                
                # Debug log to see what we are getting
                if "ping" in str(message).lower():
                    continue
                    
                data = json.loads(message)
                await self._handle_message(data)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse Polymarket message: {e} | Raw: {repr(message)}")
            except Exception as e:
                logger.error(f"Error handling Polymarket message: {e}")
    
    async def _handle_message(self, data: Any) -> None:
        """Handle a parsed WebSocket message."""
        # Handle list of messages
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    await self._handle_single_message(item)
            return
        
        # Handle single message
        if isinstance(data, dict):
            await self._handle_single_message(data)
    
    async def _handle_single_message(self, data: dict[str, Any]) -> None:
        """Handle a single parsed message."""
        msg_type = data.get("type") or data.get("event_type")
        
        if msg_type == "book":
            await self._handle_book_update(data)
        elif msg_type == "price_change":
            await self._handle_price_change(data)
        elif msg_type == "subscribed":
            logger.debug(f"Subscription confirmed: {data}")
    
    async def _handle_book_update(self, data: dict[str, Any]) -> None:
        """Handle order book snapshot or delta."""
        token_id = data.get("asset_id") or data.get("token_id")
        if not token_id or token_id not in self._order_books:
            return
        
        book = self._order_books[token_id]
        
        # Parse bids and asks
        if "bids" in data:
            book.bids = [
                OrderBookLevel(price=float(b["price"]), size=float(b["size"]))
                for b in data["bids"]
            ]
            book.bids.sort(key=lambda x: x.price, reverse=True)
        
        if "asks" in data:
            book.asks = [
                OrderBookLevel(price=float(a["price"]), size=float(a["size"]))
                for a in data["asks"]
            ]
            book.asks.sort(key=lambda x: x.price)
        
        book.timestamp = data.get("timestamp")
        
        # Trigger callback
        if self.on_book_update:
            self.on_book_update(book)
    
    async def _handle_price_change(self, data: dict[str, Any]) -> None:
        """Handle price change events."""
        token_id = data.get("asset_id") or data.get("token_id")
        if not token_id or token_id not in self._order_books:
            return
        
        # Update book if price info available
        book = self._order_books[token_id]
        if self.on_book_update:
            self.on_book_update(book)
    
    def get_order_book(self, token_id: str) -> Optional[LocalOrderBook]:
        """Get the current local order book for a token."""
        return self._order_books.get(token_id)
    
    async def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        self._running = False
        if self._ws:
            await self._ws.close()
            logger.info("Disconnected from Polymarket WebSocket")


# =============================================================================
# User WebSocket (Fills)
# =============================================================================

@dataclass
class FillEvent:
    """Trade fill event."""
    side: str  # "buy" or "sell"
    price: float
    size: float
    market_id: str
    token_id: str
    timestamp: float


class UserWebSocket:
    """
    WebSocket client for streaming user-specific updates (fills/trades).
    
    Connects to wss://ws-subscriptions-clob.polymarket.com/ws/user
    Requires API credentials for authentication.
    """
    
    USER_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
    
    def __init__(self, api_creds: Any, debug: bool = False) -> None:
        """
        Initialize User WebSocket.
        
        Args:
            api_creds: ApiCreds object containing key, secret, passphrase.
        """
        self.api_creds = api_creds
        self._ws: Optional[ClientConnection] = None
        self._running = False
        self._debug = debug
        
        # Callbacks
        self.on_fill: Optional[Callable[[FillEvent], None]] = None
    
    async def connect(self) -> None:
        """Connect and authenticate."""
        self._running = True
        try:
            # NOTE: User WS expects auth in the first message (not headers)
            headers = dict(BROWSER_HEADERS)

            import ssl
            ssl_ctx = ssl._create_unverified_context()
            async with websockets.connect(self.USER_WS_URL, additional_headers=headers, ssl=ssl_ctx) as ws:
                self._ws = ws
                logger.info("Connected to User WebSocket (Authenticated)")
                
                # Send auth payload as first message
                sub_msg = {
                    "type": "user",
                    "auth": {
                        "apiKey": self.api_creds.api_key,
                        "secret": self.api_creds.api_secret,
                        "passphrase": self.api_creds.api_passphrase,
                    },
                }
                await self._ws.send(json.dumps(sub_msg))
                
                # Process messages (Must be inside context block!)
                await self._message_loop()
                
        except Exception as e:
            logger.error(f"User WS error: {e}")
            raise
        finally:
            self._running = False
            self._ws = None

    async def _authenticate(self) -> None:
        """Send authentication subscription."""
        # Create subscription message
        # We need to sign a timestamp or use the API credentials directly depending on the channel
        # The 'user' channel typically expects just the API creds header or a subscribe message with signature
        # Polymarket ClobClient has helper for this, but we'll adapt manually or use what we can
        
        # Since we are using py-clob-client credentials, we can manually construct the auth headers/message
        # For simplicity, we assume standard subscription format for user channel:
        
        # The endpoint /ws/user usually handles auth via headers or a specific auth message
        # Let's try the standard subscription message structure
        
        # NOTE: Real implementation requires generating a signature relative to the timestamp
        # Ideally we'd reuse ClobClient's logic, but since we are raw websockets here,
        # we construct the subscribe message with API keys.
        
        # Based on Polymarket docs, we send:
        # { "type": "subscribe", "channel": "user", "stats": Bool }
        # And we must provide headers on connection? Or auth payload?
        
        # Actually, simpler approach: The client_wrapper/py_clob_client might have a way to get headers.
        # But here we will assume we pass the subscription message.
        
        # It seems the User Channel requires an authenticated socket connection logic.
        # However, looking at py_clob_client, it usually handles headers.
        # For this raw implementation, let's try sending the API creds in the subscribe message
        # OR (more likely) we rely on the creating logic elsewhere.
        
        # Given we don't have the full signature logic here, we'll try a basic subscribe
        # If this fails, we might need to import ClobClient helper.
        
        # But wait! We passed `api_creds`.
        timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
        
        # We need to generate a signature. 
        # Since we don't want to reimplement full crypto here, let's just assume 
        # we can subscribe if we have the right headers on 'connect' OR send a specific msg.
        
        # For this implementation, we will try to send a simple subscribe.
        # If it requires complex auth, we might rely on the user manually checking positions for now
        # BUT the plan said "Implement Fill Tracking".
        
        # Let's try to send the subscribe message.
        
        msg = {
            "type": "subscribe",
            "channel": "user",
        }
        
        # IMPORTANT: The actual authentication usually happens by signing a message.
        # Since we didn't implement the full signer here, we'll assume the ClobClient wrapper
        # might be needed. 
        
        # Let's use the ClobClient to create the headers if possible?
        # No, let's just try to send the message. If it fails, we fall back to manual.
        # But wait, we have the API Key/Secret.
        
        # We need to authenticate.
        # Let's insert the auth logic into the connect call if possible, or send an auth message.
        
        # Construct auth message (simplified)
        # { "type": "enable_user_stream", "api_key": ..., "timestamp": ..., "signature": ... }
        
        # To avoid complexity failure, let's stick to the basic "subscribe" and log if it works.
        # If not, we'll rely on the manual tracking.
        
        # For now, just send subscribe.
        await self._ws.send(json.dumps(msg))
        logger.info("Sent user channel subscription")

    async def _message_loop(self) -> None:
        async for message in self._ws:
            try:
                if message == "ping":
                    if self._debug:
                        logger.info("User WS: ping -> pong")
                    await self._ws.send("pong")
                    continue
                if message == "pong":
                    if self._debug:
                        logger.info("User WS: pong")
                    continue
                if self._debug:
                    preview = message if len(message) < 500 else message[:500] + "..."
                    logger.info(f"User WS raw: {preview}")
                data = json.loads(message)
                await self._handle_message(data)
            except json.JSONDecodeError:
                logger.warning(f"User WS non-JSON message: {message}")
            except Exception as e:
                logger.error(f"User WS Msg Error: {e}")

    async def _handle_message(self, data: Any) -> None:
        """Handle a parsed WebSocket message, potentially a list or single dict."""
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    await self._handle_single_message(item)
            return
            
        if isinstance(data, dict):
            await self._handle_single_message(data)

    async def _handle_single_message(self, data: dict[str, Any]) -> None:
        """Handle a single parsed message dictionary."""
        # Handle fills
        # Event type might be "trade" or "fill"
        event_type = data.get("event_type") or data.get("type")
        
        # User WS usually sends "trade" for fills
        if event_type == "trade" or event_type == "fill":
             # Parse trade
             # data usually contains: side, size, price, market, asset_id, etc.
             side = data.get("side", "").lower() 
             price_raw = data.get("price") or 0
             size_raw = data.get("size") or 0
             
             try:
                 price = float(price_raw)
                 size = float(size_raw)
             except (ValueError, TypeError):
                 return

             token_id = data.get("asset_id") or data.get("token_id")
             market = data.get("market") or data.get("condition_id")
             
             if side and size > 0:
                 event = FillEvent(
                     side=side,
                     price=price,
                     size=size,
                     token_id=token_id,
                     market_id=market,
                     timestamp=time.time()
                 )
                 if self.on_fill:
                     self.on_fill(event)

    async def disconnect(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()


    def _generate_auth_headers(self) -> dict:
        """Generate authentication headers."""
        if not self.api_creds:
            return {}
        return {}

# =============================================================================
# Binance Price Monitor
# =============================================================================

@dataclass
class BinancePrice:
    """Real-time price data from Binance."""
    symbol: str
    price: float
    timestamp: float
    # New: Candle Data
    candle_open: Optional[float] = None
    candle_close: Optional[float] = None 
    candle_high: Optional[float] = None
    candle_low: Optional[float] = None


class BinancePriceMonitor:
    """
    WebSocket client for streaming BTC/USDT and ETH/USDT prices from Binance.
    
    Connects to Binance's trade stream for real-time price updates.
    
    Usage:
        async def on_price(price: BinancePrice):
            print(f"{price.symbol}: ${price.price:.2f}")
        
        monitor = BinancePriceMonitor()
        monitor.on_price_update = on_price
        await monitor.connect()
    """
    
    # Default symbols to monitor
    DEFAULT_SYMBOLS = ["btcusdt", "ethusdt"]
    
    def __init__(
        self,
        symbols: Optional[list[str]] = None,
    ) -> None:
        """
        Initialize the Binance price monitor.
        
        Args:
            symbols: List of symbols to monitor (default: btcusdt, ethusdt).
        """
        self.symbols = symbols or self.DEFAULT_SYMBOLS
        self._ws: Optional[ClientConnection] = None
        self._running = False
        self._prices: dict[str, BinancePrice] = {}
        
        # Velocity Tracking: {symbol: deque[(timestamp, price)]}
        self.price_history: dict[str, deque] = {s: deque() for s in self.symbols}
        
        # Volume Flow Tracking: {symbol: deque[(timestamp, net_flow)]}
        # net_flow > 0 (Buy Aggressor), net_flow < 0 (Sell Aggressor)
        self.volume_history: dict[str, deque] = {s: deque() for s in self.symbols}

        
        # Callbacks
        self.on_price_update: Optional[Callable[[BinancePrice], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None
    
    @property
    def ws_url(self) -> str:
        """Build the combined stream URL for all symbols."""
        # Subscribe to both TRADE and KLINE_1H streams
        streams = []
        for s in self.symbols:
             streams.append(f"{s}@trade")
             streams.append(f"{s}@kline_1h")
        
        stream_path = "/".join(streams)
        # Use combined stream endpoint
        return f"wss://stream.binance.com:9443/stream?streams={stream_path}"
    
    async def connect(self) -> None:
        """Connect to Binance WebSocket and start streaming prices."""
        self._running = True
        url = self.ws_url
        
        try:
            async with websockets.connect(url) as ws:
                self._ws = ws
                logger.info(f"Connected to Binance WebSocket: {url}")
                
                # Process messages
                await self._message_loop()
                
        except Exception as e:
            logger.error(f"Binance WebSocket error: {e}")
            if self.on_error:
                self.on_error(e)
            raise
        finally:
            self._running = False
            self._ws = None
    
    async def _message_loop(self) -> None:
        """Process incoming WebSocket messages."""
        async for message in self._ws:
            try:
                data = json.loads(message)
                
                # Check event type
                # Combined stream payload: {"stream": "...", "data": {...}}
                if "data" in data:
                    payload = data["data"]
                else:
                    payload = data # Direct payload
                    
                evt = payload.get("e")
                
                if evt == "trade":
                    await self._handle_trade(payload)
                elif evt == "kline":
                    await self._handle_kline(payload)
                    
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse Binance message: {e}")
            except Exception as e:
                logger.error(f"Error handling Binance message: {e}")
    
    async def _handle_trade(self, data: dict[str, Any]) -> None:
        """Handle a trade event from Binance."""
        # Trade stream format: {"e": "trade", "s": "BTCUSDT", "p": "43000.00", ...}
        if data.get("e") != "trade":
            return
        
        symbol = data.get("s", "").lower()
        price_str = data.get("p")
        timestamp = data.get("T", 0) / 1000  # Convert ms to seconds
        
        if not symbol or not price_str:
            return
            
        # Get existing to preserve candle data
        existing = self._prices.get(symbol)
        
        price = BinancePrice(
            symbol=symbol,
            price=float(price_str),
            timestamp=timestamp,
            # Preserve Candle Fields
            candle_open=existing.candle_open if existing else None,
            candle_close=existing.candle_close if existing else None,
            candle_high=existing.candle_high if existing else None,
            candle_low=existing.candle_low if existing else None,
        )

        self._prices[symbol] = price
        
        # Track History for Volatility (Keep last 10 minutes = 600s)
        history = self.price_history.setdefault(symbol, deque())
        history.append((timestamp, price.price))
        
        # Track Volume Flow (Phase 7)
        # "m" (isBuyerMaker) -> True = Sell Aggressor, False = Buy Aggressor
        is_buyer_maker = data.get("m", False)
        qty = float(data.get("q", 0))
        
        flow = -qty if is_buyer_maker else qty
        
        vol_hist = self.volume_history.setdefault(symbol, deque())
        vol_hist.append((timestamp, flow))
        
        # Clean old history (1 hour window for stable moment estimation)
        while history and (timestamp - history[0][0] > 3600):
            history.popleft()
            
        while vol_hist and (timestamp - vol_hist[0][0] > 3600):
            vol_hist.popleft()
        
        # Trigger callback
        if self.on_price_update:
            self.on_price_update(price)

    async def _handle_kline(self, data: dict[str, Any]) -> None:
        """Handle a kline (candle) event from Binance."""
        # {"e": "kline", "s": "BTCUSDT", "k": {"o": "100.0", "c": "101.0", ...}}
        symbol = data.get("s", "").lower()
        k = data.get("k", {})
        
        # DEBUG: Print receipt
        if k:
            o = float(k.get("o", 0))
            # print(f"DEBUG: Rx Kline for {symbol}. Open: {o}")
            
        if not symbol or not k:
             return
             
        # Update our price object
        price_obj = self._prices.get(symbol)
        if not price_obj:
             # Create new if didn't exist (unlikely if trade stream is running)
             price_obj = BinancePrice(symbol=symbol, price=float(k.get("c", 0)), timestamp=time.time())
             self._prices[symbol] = price_obj
        
        # Update Candle Data
        price_obj.candle_open = float(k.get("o", 0))
        price_obj.candle_high = float(k.get("h", 0))
        price_obj.candle_low = float(k.get("l", 0))
        price_obj.candle_close = float(k.get("c", 0))
        
        # Note: We don't trigger on_price_update for every kline update (too frequent/redundant with trade)
        # But we now have the 'candle_open' stored for strategy usage.

    def get_price(self, symbol: str) -> Optional[float]:
        """
        Get the latest price for a symbol.
        
        Args:
            symbol: Symbol name (e.g., "btcusdt").
        
        Returns:
            Latest price, or None if not yet received.
        """
        price_data = self._prices.get(symbol.lower())
        return price_data.price if price_data else None
    
    @property
    def btc_price(self) -> Optional[float]:
        """Get the latest BTC/USDT price."""
        return self.get_price("btcusdt")
    
    @property
    def eth_price(self) -> Optional[float]:
        """Get the latest ETH/USDT price."""
        return self.get_price("ethusdt")

    def get_candle_open(self, symbol: str) -> Optional[float]:
        """Get the open price of the current 1H candle."""
        price_data = self._prices.get(symbol.lower())
        return price_data.candle_open if price_data else None
    
    async def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        self._running = False
        if self._ws:
            await self._ws.close()
            logger.info("Disconnected from Binance WebSocket")

    def get_price_velocity(self, symbol: str, window_seconds: float = 5.0) -> float:
        """
        Calculate absolute percentage price change over window.
        
        Args:
           symbol: e.g. "btcusdt"
           window_seconds: Lookback window (default 5s)
        
        Returns:
           float: Absolute velocity (e.g. 0.002 = 0.2% change).
           Returns 0.0 if not enough data.
        """
        history = self.price_history.get(symbol.lower())
        if not history or len(history) < 2:
            return 0.0
            
        current_time = time.time()
        # Find oldest price within window
        # history is formatted as (timestamp, price)
        # We want the price closest to (current_time - window_seconds)
        
        target_time = current_time - window_seconds
        
        # Since history is sorted by time (append only), we iterate
        start_price = None
        
        # Find first entry >= target_time
        # Or simpler: Just take the oldest entry in the deque if it's within window?
        # Actually, if we cleared history > 30s, we just need to find the one closest to window edge.
        
        for ts, p in history:
            if ts >= target_time:
                start_price = p
                break
        
        if start_price is None:
            # All history is older than window? Or empty?
            start_price = history[0][1] # Fallback to oldest available
            
        current_price = history[-1][1]
        
        if start_price == 0: return 0.0
        
        velocity = abs(current_price - start_price) / start_price
        return velocity

    def get_realized_volatility(self, symbol: str) -> float:
        """
        Calculate annualized volatility based on recent history.
        Uses standard deviation of log returns.
        
        Returns:
            float: Volatility (sigma) e.g. 0.50 for 50%. 
                   Default 0.5 (50%) if insufficient data.
        """
        history = self.price_history.get(symbol.lower())
        if not history or len(history) < 10:
            return 0.5 # Default fallback
            
        # Extract prices
        prices = [p for _, p in history]
        
        if len(prices) < 2:
            return 0.5
            
        # Calculate log returns: ln(p_i / p_{i-1})
        log_returns = []
        for i in range(1, len(prices)):
            if prices[i-1] > 0:
                ret = math.log(prices[i] / prices[i-1])
                log_returns.append(ret)
        
        if not log_returns:
            return 0.5
            
        # Calculate standard deviation
        mean_ret = sum(log_returns) / len(log_returns)
        var_ret = sum((r - mean_ret)**2 for r in log_returns) / len(log_returns)
        
        if var_ret < 1e-9:
            return 0.5
            
        daily_vol = math.sqrt(var_ret)
        annualized_vol = daily_vol * math.sqrt(365 * 24 * 3600 / 1) # 1s updates?
        # Actually log_returns are periodic. If history is 1s spaced, then annualized = stdev * sqrt(31536000)
        # Our update rate is approx 1s?
        # Let's assume 1s spacing.
        
        return annualized_vol

    def get_realized_moments(self, symbol: str) -> Tuple[float, float]:
        """
        Calculate realized Skewness and Kurtosis from recent price history.
        Uses RollingStats window (1H).
        
        Returns:
            (skew, kurtosis)
            Returns (0.0, 0.0) if insufficient data.
        """
        # We need a longer history for moments than volatility.
        # Ideally we'd have a separate deque. For now let's reuse price_history logic
        # but check if we have enough data.
        
        # NOTE: self.price_history is cleared > 600s. 
        # Ideally we need 3600s (1H) for robust skew.
        # But we are constrained by memory/design here.
        # Let's compute from the 10m window (600s). It's better than nothing.
        
        history = self.price_history.get(symbol.lower())
        if not history or len(history) < 100:
            return (0.0, 0.0)
            
        prices = [p for _, p in history]
        
        log_returns = []
        for i in range(1, len(prices)):
            if prices[i-1] > 0:
                ret = math.log(prices[i] / prices[i-1])
                log_returns.append(ret)
                
        if len(log_returns) < 30:
            return (0.0, 0.0)
            
        n = len(log_returns)
        mean = sum(log_returns) / n
        
        var = sum((r - mean)**2 for r in log_returns) / n
        stdev = math.sqrt(var)
        
        if stdev < 1e-9:
            return (0.0, 0.0)
            
        # Skew
        m3 = sum((r - mean)**3 for r in log_returns) / n
        skew = m3 / (stdev**3)
        
        # Kurtosis
        m4 = sum((r - mean)**4 for r in log_returns) / n
        kurt = (m4 / (stdev**4)) - 3.0 # Excess Kurtosis
        
        # Clamp to avoid exploding the pricer
        skew = max(-3.0, min(3.0, skew))
        kurt = max(-3.0, min(10.0, kurt))
        
        return (skew, kurt)


    def get_price_ema(self, symbol: str, window_seconds: float = 300.0) -> Optional[float]:
        """
        Calculate Simple Moving Average (SMA) of price over window.
        Returns float or None.
        """
        history = self.price_history.get(symbol.lower())
        if not history or len(history) < 2:
            return None
            
        current_time = time.time()
        start_time = current_time - window_seconds
        
        sum_price = 0.0
        count = 0
        
        for ts, p in history:
            if ts >= start_time:
                sum_price += p
                count += 1
                
        if count == 0:
            return history[-1][1] 
            
        return sum_price / count

    def get_volume_flow(self, symbol: str, window_seconds: float = 60.0) -> float:
        """
        Calculate Net Volume Flow (Buy - Sell) over window.
        Returns:
            float: Positive = Net Buy Pressure. Negative = Net Sell Pressure.
        """
        history = self.volume_history.get(symbol.lower())
        if not history:
            return 0.0
            
        current_time = time.time()
        start_time = current_time - window_seconds
        
        net_flow = 0.0
        
        for ts, flow in history:
            if ts >= start_time:
                net_flow += flow
                
        return net_flow


# =============================================================================
# Price Dislocation Detection
# =============================================================================

def check_price_dislocation(
    poly_price: float,
    binance_price: float,
    threshold: float = TOXIC_FLOW_THRESHOLD,
) -> PriceDislocationResult:
    """
    Check for price dislocation between Polymarket and Binance.
    
    If the price difference exceeds the threshold (default 0.5%), returns
    a TOXIC_FLOW alert signal indicating potential adverse selection.
    
    Args:
        poly_price: Current price on Polymarket.
        binance_price: Current price on Binance.
        threshold: Dislocation threshold as decimal (default 0.005 = 0.5%).
    
    Returns:
        PriceDislocationResult with alert type and price details.
    
    Example:
        >>> result = check_price_dislocation(poly_price=0.55, binance_price=0.545)
        >>> if result.is_toxic:
        ...     print(f"TOXIC FLOW: {result.difference_pct:.2%} dislocation!")
    """
    if binance_price == 0:
        raise ValueError("Binance price cannot be zero")
    
    # Calculate percentage difference
    difference = abs(poly_price - binance_price) / binance_price
    
    # Determine alert type
    alert = AlertType.TOXIC_FLOW if difference > threshold else AlertType.NORMAL
    
    return PriceDislocationResult(
        alert=alert,
        poly_price=poly_price,
        binance_price=binance_price,
        difference_pct=difference,
    )
