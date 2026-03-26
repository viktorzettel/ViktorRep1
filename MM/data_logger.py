"""
Data logging module for Polymarket Market Maker.

Logs all trading activity to CSV files for analysis and improvement:
- quotes.csv: Every quote generated
- orders.csv: Every order submitted
- events.csv: Risk events, fills, state changes

Usage:
    from data_logger import DataLogger
    
    logger = DataLogger()
    logger.log_quote(bid=0.48, ask=0.52, inventory=10, mid_price=0.50)
    logger.log_order(side="buy", price=0.48, size=10, order_id="abc123")
"""

import csv
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# =============================================================================
# Configuration
# =============================================================================

DEFAULT_DATA_DIR = "data"


@dataclass
class LogConfig:
    """Configuration for data logging."""
    data_dir: str = DEFAULT_DATA_DIR
    quotes_file: str = "quotes.csv"
    orders_file: str = "orders.csv"
    events_file: str = "events.csv"
    daily_summary_file: str = "daily_summary.csv"


# =============================================================================
# Data Logger
# =============================================================================

class DataLogger:
    """
    Logs trading activity to CSV files.
    
    Creates/appends to:
    - data/quotes.csv: All generated quotes
    - data/orders.csv: All submitted orders
    - data/events.csv: Risk events, fills, errors
    """
    
    def __init__(self, config: Optional[LogConfig] = None) -> None:
        """Initialize the data logger."""
        self.config = config or LogConfig()
        self._ensure_data_dir()
        self._init_files()
        
        # Session tracking
        self.session_start = datetime.now(timezone.utc)
        self.quotes_count = 0
        self.orders_count = 0
        self.events_count = 0
    
    def _ensure_data_dir(self) -> None:
        """Create data directory if it doesn't exist."""
        Path(self.config.data_dir).mkdir(parents=True, exist_ok=True)
    
    def _get_path(self, filename: str) -> str:
        """Get full path for a data file."""
        return os.path.join(self.config.data_dir, filename)
    
    def _init_files(self) -> None:
        """Initialize CSV files with headers if they don't exist."""
        # Quotes file
        quotes_path = self._get_path(self.config.quotes_file)
        if not os.path.exists(quotes_path):
            with open(quotes_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "market_id", "token_id",
                    "bid", "ask", "spread", "mid_price",
                    "inventory", "daily_pnl", "market_type"
                ])
        
        # Orders file
        orders_path = self._get_path(self.config.orders_file)
        if not os.path.exists(orders_path):
            with open(orders_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "market_id", "token_id",
                    "side", "price", "size", "order_id", "status"
                ])
        
        # Events file
        events_path = self._get_path(self.config.events_file)
        if not os.path.exists(events_path):
            with open(events_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "event_type", "details", "market_id"
                ])
    
    def _now(self) -> str:
        """Get current timestamp as ISO string."""
        return datetime.now(timezone.utc).isoformat()
    
    # =========================================================================
    # Quote Logging
    # =========================================================================
    
    def log_quote(
        self,
        bid: Optional[float],
        ask: Optional[float],
        mid_price: float,
        inventory: float,
        daily_pnl: float,
        market_id: str = "",
        token_id: str = "",
        market_type: str = "",
    ) -> None:
        """
        Log a generated quote.
        
        Args:
            bid: Bid price (None if not quoting bid).
            ask: Ask price (None if not quoting ask).
            mid_price: Current mid-market price.
            inventory: Current inventory.
            daily_pnl: Current daily P&L.
            market_id: Market slug.
            token_id: Token ID being traded.
            market_type: "crypto_15m" or "standard".
        """
        spread = (ask - bid) if (bid and ask) else 0
        
        path = self._get_path(self.config.quotes_file)
        with open(path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                self._now(), market_id, token_id,
                bid or "", ask or "", f"{spread:.4f}", f"{mid_price:.4f}",
                f"{inventory:.2f}", f"{daily_pnl:.2f}", market_type
            ])
        
        self.quotes_count += 1
    
    # =========================================================================
    # Order Logging
    # =========================================================================
    
    def log_order(
        self,
        side: str,
        price: float,
        size: float,
        order_id: str = "",
        status: str = "submitted",
        market_id: str = "",
        token_id: str = "",
    ) -> None:
        """
        Log a submitted order.
        
        Args:
            side: "buy" or "sell".
            price: Order price.
            size: Order size.
            order_id: Polymarket order ID.
            status: "submitted", "filled", "cancelled", "error".
            market_id: Market slug.
            token_id: Token ID.
        """
        path = self._get_path(self.config.orders_file)
        with open(path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                self._now(), market_id, token_id,
                side, f"{price:.4f}", f"{size:.2f}", order_id, status
            ])
        
        self.orders_count += 1
    
    # =========================================================================
    # Event Logging
    # =========================================================================
    
    def log_event(
        self,
        event_type: str,
        details: str,
        market_id: str = "",
    ) -> None:
        """
        Log a risk or system event.
        
        Args:
            event_type: Type of event (e.g., "KILL_SWITCH", "FLOW_TOXIC", "WEEKEND_BLACKOUT").
            details: Human-readable details.
            market_id: Market slug (if applicable).
        """
        path = self._get_path(self.config.events_file)
        with open(path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                self._now(), event_type, details, market_id
            ])
        
        self.events_count += 1
    
    def log_fill(
        self,
        side: str,
        price: float,
        size: float,
        market_id: str = "",
    ) -> None:
        """Log a fill event."""
        self.log_event(
            event_type="FILL",
            details=f"{side.upper()} {size}@{price:.4f}",
            market_id=market_id,
        )
    
    def log_risk_event(
        self,
        event_type: str,
        details: str,
        market_id: str = "",
    ) -> None:
        """Log a risk management event."""
        self.log_event(event_type, details, market_id)
    
    # =========================================================================
    # Summary
    # =========================================================================
    
    def get_session_summary(self) -> dict:
        """Get summary of current session."""
        duration = datetime.now(timezone.utc) - self.session_start
        return {
            "session_start": self.session_start.isoformat(),
            "duration_minutes": duration.total_seconds() / 60,
            "quotes_logged": self.quotes_count,
            "orders_logged": self.orders_count,
            "events_logged": self.events_count,
        }
    
    def log_session_end(self, final_pnl: float = 0.0) -> None:
        """Log end of trading session."""
        summary = self.get_session_summary()
        self.log_event(
            event_type="SESSION_END",
            details=f"Duration: {summary['duration_minutes']:.1f}m, "
                   f"Quotes: {summary['quotes_logged']}, "
                   f"Orders: {summary['orders_logged']}, "
                   f"P&L: ${final_pnl:.2f}",
        )
