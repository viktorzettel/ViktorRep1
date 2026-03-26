"""
Crypto Parser Module

Responsibility: Turn unstructured Polymarket question titles into structured financial data.
Target: BTC and ETH Daily/Weekly markets.

Example Inputs:
- "Will BTC hit $105,000 by Friday?"
- "Will ETH be above $3,200 on Jan 26?"

Output strategy:
- Asset: BTC or ETH
- Strike: Float (e.g. 105000.0)
- Expiry: Date interpretation (handled loosely for now, or strict if date provided)
"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple
from datetime import datetime

@dataclass
class CryptoMarketData:
    asset: str      # 'BTC' or 'ETH'
    strike: float   # e.g. 95000.0 (Optional if Up/Down)
    direction: str  # 'above' or 'up'
    raw_date: str   # Extracted date string
    market_type: str = "FIXED_STRIKE" # 'FIXED_STRIKE' or 'UP_DOWN'
    period: str = "" # '1H', '4H', etc.

class CryptoParser:
    # Regex Patterns: Fixed Strike
    PRICE_PATTERN = re.compile(r"\$([0-9,]+(\.[0-9]+)?)([kK]?)")
    ASSET_PATTERN = re.compile(r"\b(BTC|ETH|Bitcoin|Ethereum|SOL|Solana)\b", re.IGNORECASE)
    
    # Regex Patterns: Hourly Up/Down
    # "Will BTC be up or down at 5pm?"
    # "Will ETH close positive in the 1h candle?" -> TBD
    UP_DOWN_PATTERN = re.compile(r"\b(up|down|positive|negative|higher|lower|above|below)\b", re.IGNORECASE)

    
    @staticmethod
    def parse_title(title: str) -> Optional[CryptoMarketData]:
        """
        Parse a market title to extract crypto parameters.
        Returns None if not a valid/supported crypto binary.
        """
        # 1. Identify Asset
        asset_match = CryptoParser.ASSET_PATTERN.search(title)
        if not asset_match:
            return None
            
        raw_asset = asset_match.group(1).upper()
        if "BITCOIN" in raw_asset or "BTC" in raw_asset: asset = "BTC"
        elif "ETHEREUM" in raw_asset or "ETH" in raw_asset: asset = "ETH"
        elif "SOL" in raw_asset: asset = "SOL"
        else: asset = "BTC" # Default
        
        # 2. Check for "Up/Down" (Hourly) Market
        up_down_match = CryptoParser.UP_DOWN_PATTERN.search(title)
        if up_down_match:
             # It is an Hourly/Up-Down Market
             # We might extract expiry from text, but usually logic is implied
             return CryptoMarketData(
                 asset=asset,
                 strike=0.0, # Strike is "Open Price" (Unknown here)
                 direction=up_down_match.group(1).lower(),
                 raw_date="Hourly",
                 market_type="UP_DOWN",
                 period="1H" # Assumption for now
             )

        # 3. Identify Strike Price (Fixed Strike)

        # Look for $...
        price_match = CryptoParser.PRICE_PATTERN.search(title)
        if not price_match:
            # Fallback: sometimes purely numeric "above 100000"? 
            # For safety, we insist on "$" for now to avoid parsing years (2025) as price.
            return None
            
        raw_price = price_match.group(1).replace(",", "")
        multiplier = price_match.group(3)
        
        try:
            strike = float(raw_price)
            if multiplier and multiplier.lower() == 'k':
                strike *= 1000.0
        except ValueError:
            return None
            
        # 3. Identify Date (extract everything after "by" or "on")
        # heuristic: "by Friday", "on Jan 25"
        date_str = ""
        date_match = re.search(r"\b(by|on)\s+(.*)", title, re.IGNORECASE)
        if date_match:
            date_str = date_match.group(2).strip()
            # Remove trailing '?'
            if date_str.endswith("?"):
                date_str = date_str[:-1]
        
        return CryptoMarketData(
            asset=asset,
            strike=strike,
            direction="above", # Binary options are almost always "Will X be > Y" (Calls)
            raw_date=date_str,
            market_type="FIXED_STRIKE"
        )

# Quick verification if run directly
if __name__ == "__main__":
    examples = [
        "Will BTC hit $105,000 by Friday?",
        "Will Ethereum be above $3,200 on Jan 26?",
        "Will BTC be up or down?",
        "Will SOL close higher?", 
        "Will the Chiefs win the Superbowl?", # Should fail

        "Will BTC hit $95k?",
    ]
    
    for ex in examples:
        print(f"'{ex}' -> {CryptoParser.parse_title(ex)}")
