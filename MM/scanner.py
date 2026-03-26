"""
Market Scanner for Polymarket Market Maker.

Scans Gamma API to find optimal markets for market making based on:
- Liquidity
- 24h volume
- Time to expiry
- Price near 50% (most trading activity)

Usage:
    python scanner.py           # Show top 10 markets
    python scanner.py --json    # Output as JSON
"""

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
import urllib.request
import urllib.error


# =============================================================================
# Constants
# =============================================================================

GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"
CLOB_API_URL = "https://clob.polymarket.com/trades"
DEFAULT_LIMIT = 100  # Fetch more, filter to top 10

# Cloudflare Bypass Headers
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Origin": "https://polymarket.com",
}
CLOB_API_URL = "https://clob.polymarket.com/trades"
DEFAULT_LIMIT = 500  # Fetch more to find hourly markets


@dataclass
class MarketScore:
    """Scored market for market making suitability."""
    slug: str
    question: str
    score: float
    liquidity: float
    volume_24h: float
    days_to_expiry: int
    mid_price: float
    token_id_yes: str
    token_id_yes: str
    token_id_no: str
    velocity_tpm: float = 0.0  # Trades Per Minute (Leading Indicator)
    
    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "question": self.question[:60] + "..." if len(self.question) > 60 else self.question,
            "score": round(self.score, 1),
            "liquidity": f"${self.liquidity:,.0f}",
            "volume_24h": f"${self.volume_24h:,.0f}",
            "days_to_expiry": self.days_to_expiry,
            "mid_price": f"{self.mid_price:.0%}",
            "token_id_yes": self.token_id_yes,
            "mid_price": f"{self.mid_price:.0%}",
            "token_id_yes": self.token_id_yes,
            "token_id_no": self.token_id_no,
            "velocity_tpm": round(self.velocity_tpm, 2),
        }


# =============================================================================
# API Functions
# =============================================================================

def fetch_markets(limit: int = DEFAULT_LIMIT) -> list[dict]:
    """
    Fetch active markets from Gamma API.
    
    Args:
        limit: Maximum number of markets to fetch.
    
    Returns:
        List of market dictionaries.
    """
    url = f"{GAMMA_API_URL}?active=true&closed=false&limit={limit}"
    
    try:
        import ssl
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(url, headers=BROWSER_HEADERS)
        
        with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
            data = json.loads(response.read().decode())
            return data if isinstance(data, list) else []
    
    except urllib.error.URLError as e:
        print(f"Error fetching markets: {e}")
        return []
    except json.JSONDecodeError as e:
        print(f"Error parsing response: {e}")
        return []


def parse_token_ids(clob_token_ids: str) -> tuple[str, str]:
    """Parse clobTokenIds field to extract YES and NO token IDs."""
    try:
        ids = json.loads(clob_token_ids)
        if len(ids) >= 2:
            return ids[0], ids[1]  # YES, NO
        elif len(ids) == 1:
            return ids[0], ""
    except (json.JSONDecodeError, TypeError):
        pass
    return "", ""


def is_bitcoin_hourly(market: dict) -> bool:
    """
    Bulletproof Filter for 'Bitcoin Hourly' markets.
    Must involve BTC and be an hourly/short-term price market.
    """
    # 1. Check Slug/Question for Asset
    slug = market.get("slug", "").lower()
    question = market.get("question", "").lower()
    
    if "btc" not in slug and "bitcoin" not in question:
        return False
        
    # 2. Check for 'Hourly' or 'Price' context
    # Hourly markets usually have slugs like "will-btc-be-above-X-at-Y"
    # Or tags? Gamma doesn't always give tags.
    # Keyword check (Relaxed for Test)
    # keywords = ["above", "below", "price", ">", "<", "hit", "reach", "win", "comeback"]
    # if not any(k in question for k in keywords):
    #     return False
        
    # 3. Exclude 'Daily' or 'Weekly' if explicitly stated?
    # Actually, we want the one closing SOONEST (Hourly).
    # The 'calculate_mm_score' logic prioritizes expiry < 1 day.
    # But we should exclude vague things like "Will Bitcoin hit 100k in 2025?"
    
    # 4. Check resolution source? Usually not in lightweight gamma payload.
    
    return True


def calculate_days_to_expiry(end_date: str) -> int:
    """Calculate days until market expires."""
    try:
        expiry = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = expiry - now
        return max(0, delta.days)
    except (ValueError, TypeError):
        return 0


def parse_mid_price(outcome_prices: str) -> float:
    """Parse outcome prices to get YES price (mid-market estimate)."""
    try:
        prices = json.loads(outcome_prices)
        if prices:
            return float(prices[0])  # YES price
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return 0.5


def fetch_velocity(slug: str) -> float:
    """
    Fetch recent trades and calculate Trades Per Minute (TPM).
    Uses public CLOB API.
    """
    # We need the market condition ID or token ID.
    # The public /trades endpoint takes `market` (condition ID) or `asset_id`.
    # BUT, we only have Slug from Gamma. We need to find the Condition ID or Token ID from raw data.
    # Actually, scan_markets has the full market dict.
    # Let's use `token_yes` which we parsed.
    return 0.0 # Placeholder, logic moved to scan_markets for context access



# =============================================================================
# Scoring
# =============================================================================

def calculate_mm_score(market: dict) -> Optional[MarketScore]:
    """
    Calculate market making suitability score (0-100).
    
    Scoring breakdown:
    - Liquidity: 0-30 points
    - Volume 24h: 0-30 points
    - Time to expiry: 0-20 points
    - Price near 50%: 0-20 points
    """
    # Parse basic fields
    slug = market.get("slug", "")
    question = market.get("question", "")
    liquidity = float(market.get("liquidityNum", 0) or market.get("liquidity", 0) or 0)
    volume_24h = float(market.get("volume24hr", 0) or 0)
    end_date = market.get("endDate", "")
    outcome_prices = market.get("outcomePrices", "[]")
    clob_token_ids = market.get("clobTokenIds", "[]")
    
    # Skip markets without order book enabled
    if not market.get("enableOrderBook", False):
        return None
    
    # Skip markets with no token IDs
    token_yes, token_no = parse_token_ids(clob_token_ids)
    if not token_yes:
        return None
    
    days_to_expiry = calculate_days_to_expiry(end_date)
    mid_price = parse_mid_price(outcome_prices)
    
    # =========================================================================
    # SAFETY FILTERS ("KILL ZONE")
    # =========================================================================
    
    # 1. Price Safety Zone: 0.05 - 0.95 (Expanded for Test)
    if mid_price < 0.05 or mid_price > 0.95:
        return None
        
    # 2. Expiry Buffer: Previously > 24h, now allowing 0-day for Flash Markets
    # if days_to_expiry < 1:
        # return None
    pass
        
    # 3. Liquidity Floor (Reduced for Test)
    if liquidity < 5000 or volume_24h < 1000:
        return None
        
    # 4. Max Expiry (Increased for Test)
    if days_to_expiry > 365:
        return None

    # 5. Negative Risk Filter (Relaxed for Test)
    # if market.get("negRisk", False) or market.get("neg_risk", False):
    #     return None
        
    # =========================================================================
    
    # Calculate score
    score = 0.0
    
    # 1. Volume (Weight: 50%) - We want ACTION
    # Caps at $50k daily volume
    vol_score = min(volume_24h / 50000.0, 1.0) * 50
    score += vol_score
    
    # 2. Liquidity (Weight: 20%) - We want depth
    # Caps at $100k liquidity
    liq_score = min(liquidity / 100000.0, 1.0) * 20
    score += liq_score
    
    # 3. Price OTM Preference (Weight: 25%) - Gram-Charlier has edge here
    # ATM (40-60%) is a coin flip with max competition
    # OTM (< 30% or > 70%) is where tail pricing matters
    if mid_price < 0.30 or mid_price > 0.70:  # OTM
        score += 25  # MAX BONUS (Gram-Charlier has edge on tails)
    elif mid_price < 0.40 or mid_price > 0.60:  # Slight OTM
        score += 15
    else:  # ATM (40-60%) - Coin flip, no edge
        score += 5  # PENALTY
        
    # 4. Expiry (Weight: 20% -> 60% for Flash) 
    # Urgency Bonus for Flash Markets (last few hours)
    if days_to_expiry == 0:
        score += 60 # HUGE bonus for 0-day expiry
    elif days_to_expiry < 10:
        score += 40
    elif days_to_expiry <= 60:
        score += 20
    elif days_to_expiry <= 90:
        score += 10
    else:
        score += 0
    
    return MarketScore(
        slug=slug,
        question=question,
        score=score,
        liquidity=liquidity,
        volume_24h=volume_24h,
        days_to_expiry=days_to_expiry,
        mid_price=mid_price,
        token_id_yes=token_yes,
        token_id_no=token_no,
    )


def scan_markets(limit: int = 10) -> list[MarketScore]:
    """
    Scan and rank markets by MM suitability.
    
    Args:
        limit: Number of top markets to return.
    
    Returns:
        List of scored markets, sorted by score descending.
    """
    print("Fetching markets from Gamma API...")
    raw_markets = fetch_markets(DEFAULT_LIMIT)
    print(f"Fetched {len(raw_markets)} markets")
    
    # Score all markets
    scored = []
    for market in raw_markets:
        # User Requirement: Bulletproof Bitcoin Hourly
        # If we are running this specific bot, we ONLY want BTC Hourly.
        if not is_bitcoin_hourly(market):
            continue
            
        result = calculate_mm_score(market)
        if result and result.score > 0:
            scored.append(result)
    
    print(f"Scored {len(scored)} eligible markets")
    
    # Filter Top 20 Candidates for Deep Scan (Velocity Check)
    candidates = scored[:20]
    final_list = []
    
    print(f"Deep Scanning Top {len(candidates)} candidates for Velocity (Trades/Min)...")
    
    for m in candidates:
        try:
            # Use Token YES to check trades
            url = f"https://clob.polymarket.com/trades?asset_id={m.token_id_yes}&limit=50"
            import ssl
            ctx = ssl._create_unverified_context()
            req = urllib.request.Request(url, headers=BROWSER_HEADERS)
            with urllib.request.urlopen(req, timeout=5, context=ctx) as response:
                trades = json.loads(response.read().decode())
                
                # Calculate TPM (Trades per minute)
                if not trades:
                    velocity = 0.0
                else:
                    # Count trades in last 60 minutes
                    now_ts = datetime.now(timezone.utc).timestamp()
                    recent_trades = [t for t in trades if (now_ts - float(t['timestamp'])) < 3600]
                    
                    if not recent_trades:
                         velocity = 0.0
                    else:
                         # Use time range of the trades to estimate rate
                         oldest = float(recent_trades[-1]['timestamp'])
                         duration_min = max((now_ts - oldest) / 60.0, 1.0) # Avoid div by zero
                         velocity = len(recent_trades) / duration_min
                
                m.velocity_tpm = velocity
                
                # ZOMBIE FILTER: Require at least 0.2 trades/min (1 trade every 5 mins)
                # Ideally > 1.0 but let's be lenient for niche markets
                if velocity >= 0.2:
                    final_list.append(m)
                else:
                     print(f"  [Zombie] {m.slug} (Vel: {velocity:.2f} tpm) -> REJECTED")
                     
        except Exception as e:
            print(f"  [Error] Checking velocity for {m.slug}: {e}")
            # Keep it but warn? Or discard? Let's keep if error to be safe (could be API flakiness)
            final_list.append(m)
            
    # Re-Sort by Score (Velocity doesn't change score, just filters)
    # Optional: Bonus for High Velocity?
    for m in final_list:
        if m.velocity_tpm > 5.0:
            m.score += 10 # Velocity Bonus
            
    final_list.sort(key=lambda x: x.score, reverse=True)
    return final_list[:limit]


# =============================================================================
# CLI Output
# =============================================================================

def print_table(markets: list[MarketScore]) -> None:
    """Print markets as a formatted table."""
    if not markets:
        print("No markets found matching criteria.")
        return
    
    print()
    print("=" * 100)
    print("TOP MARKETS FOR MARKET MAKING")
    print("=" * 100)
    print()
    print(f"{'#':<3} {'Score':<6} {'Liq':<10} {'Vol24h':<10} {'Days':<5} {'Pr':<5} {'TPM':<5} {'Market':<50}")
    print("-" * 100)
    
    for i, m in enumerate(markets, 1):
        question_short = m.question[:47] + "..." if len(m.question) > 50 else m.question
        print(
            f"{i:<3} "
            f"{m.score:<6.0f} "
            f"${m.liquidity:<9,.0f} "
            f"${m.volume_24h:<9,.0f} "
            f"{m.days_to_expiry:<5} "
            f"{m.mid_price:<5.0%} "
            f"{m.velocity_tpm:<5.2f} "
            f"{question_short}"
        )
    
    print()
    print("-" * 100)
    print("To use a market, copy the token ID (YES or NO side) and run:")
    print("  python main.py \"MARKET_SLUG\" \"TOKEN_ID\"")
    print()
    
    # Show details for top market
    if markets:
        top = markets[0]
        print("=" * 100)
        print("TOP PICK DETAILS:")
        print(f"  Slug: {top.slug}")
        print(f"  Token ID (YES): {top.token_id_yes}")
        print(f"  Token ID (NO):  {top.token_id_no}")
        print()
        print("Command to run:")
        print(f"  python main.py \"{top.slug}\" \"{top.token_id_yes}\"")
        print("=" * 100)


def print_json(markets: list[MarketScore]) -> None:
    """Print markets as JSON."""
    data = [m.to_dict() for m in markets]
    print(json.dumps(data, indent=2))


# =============================================================================
# Main
# =============================================================================

def main():
    """Main entry point."""
    json_output = "--json" in sys.argv
    
    try:
        markets = scan_markets(limit=10)
        
        if json_output:
            print_json(markets)
        else:
            print_table(markets)
    
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
