#!/usr/bin/env python3
"""
Polymarket Arbitrage Opportunity Logger v2.1

Monitors 200+ markets (all liquidity tiers) for arbitrage opportunities
where Sum(Ask_Yes + Ask_No) < THRESHOLD.

Key insight: On Polymarket's CLOB, the SELL price for YES = 1 - BUY price
for NO (they are derived from the complementary token). So we only need
to check BUY (ask) prices for both tokens; the spread is simply
1.0 - sum(asks).

Usage:
    python arb_logger.py                       # Default: 200 markets, 5 min
    python arb_logger.py --markets 500         # Scan 500 markets
    python arb_logger.py --threshold 0.97      # Stricter threshold
    python arb_logger.py --min-volume 0        # Include zero-volume markets
    python arb_logger.py --duration 600        # 10 minutes
    python arb_logger.py --verbose             # Show every price check
"""

import argparse
import csv
import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# =============================================================================
# Configuration
# =============================================================================

GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"
CLOB_API_URL = "https://clob.polymarket.com"

DEFAULT_THRESHOLD = 0.98
DEFAULT_MARKETS = 200
DEFAULT_DURATION_SEC = 300   # 5 minutes
DEFAULT_MIN_VOLUME = 0       # Include all by default
DEFAULT_MIN_LIQUIDITY = 100  # $100 minimum liquidity
POLL_INTERVAL_SEC = 2.0

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Origin": "https://polymarket.com",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("ArbLogger")


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class Market:
    """A Polymarket binary market with its CLOB token IDs."""
    slug: str
    question: str
    token_id_yes: str
    token_id_no: str
    liquidity: float = 0.0
    volume_24h: float = 0.0


@dataclass
class PriceCheck:
    """Result of checking a market's prices."""
    timestamp: str
    market_slug: str
    ask_yes: float          # Price to BUY Yes tokens
    ask_no: float           # Price to BUY No tokens
    sum_asks: float         # ask_yes + ask_no (should be ~1.0)
    gap: float              # 1.0 - sum_asks (theoretical margin)
    gap_after_fee: float    # gap minus 1% taker fee on both sides
    liquidity: float
    volume_24h: float


# =============================================================================
# API Functions
# =============================================================================

def _make_request(url: str, timeout: int = 10) -> Optional[dict]:
    """Make HTTP GET with error handling. Returns None on failure."""
    try:
        ctx = ssl._create_unverified_context()
        req = urllib.request.Request(url, headers=BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def fetch_markets(limit: int, min_volume: float, min_liquidity: float) -> List[Market]:
    """
    Fetch active binary markets from Gamma API.
    Fetches across ALL liquidity tiers via pagination.
    """
    all_markets = []
    batch_size = 100
    offset = 0

    while len(all_markets) < limit:
        url = (
            f"{GAMMA_API_URL}?active=true&closed=false"
            f"&limit={batch_size}&offset={offset}"
        )

        data = _make_request(url, timeout=15)
        if not data or not isinstance(data, list) or len(data) == 0:
            break

        for m in data:
            # Binary markets only
            outcomes_raw = m.get("outcomes", "[]")
            try:
                outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
            except json.JSONDecodeError:
                continue
            if len(outcomes) != 2:
                continue

            # Must have token IDs
            tokens_raw = m.get("clobTokenIds", "[]")
            try:
                tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
            except json.JSONDecodeError:
                continue
            if len(tokens) < 2:
                continue

            # Parse financials
            try:
                liquidity = float(m.get("liquidity", 0) or 0)
                volume_24h = float(m.get("volume24hr", 0) or 0)
            except (ValueError, TypeError):
                liquidity, volume_24h = 0.0, 0.0

            if liquidity < min_liquidity or volume_24h < min_volume:
                continue

            all_markets.append(Market(
                slug=m.get("slug", ""),
                question=m.get("question", "")[:80],
                token_id_yes=tokens[0],
                token_id_no=tokens[1],
                liquidity=liquidity,
                volume_24h=volume_24h,
            ))

        offset += batch_size
        if len(data) < batch_size:
            break

    all_markets = all_markets[:limit]
    logger.info(f"Fetched {len(all_markets)} binary markets (liq>=${min_liquidity:.0f}, vol>=${min_volume:.0f})")
    return all_markets


def get_ask_price(token_id: str) -> Optional[float]:
    """Get the best ask (BUY price) for a token from CLOB."""
    url = f"{CLOB_API_URL}/price?token_id={token_id}&side=BUY"
    data = _make_request(url, timeout=5)
    if data and data.get("price"):
        try:
            return float(data["price"])
        except (ValueError, TypeError):
            pass
    return None


def check_market(market: Market) -> Optional[PriceCheck]:
    """
    Check a market's Yes+No ask sum.
    Only needs 2 API calls (one per token, BUY side only).
    """
    ask_yes = get_ask_price(market.token_id_yes)
    ask_no = get_ask_price(market.token_id_no)

    if ask_yes is None or ask_no is None:
        return None

    sum_asks = ask_yes + ask_no
    gap = 1.0 - sum_asks
    # Fee = 1% taker on each leg = cost * 0.01
    fee_cost = sum_asks * 0.01
    gap_after_fee = gap - fee_cost

    return PriceCheck(
        timestamp=datetime.now(timezone.utc).isoformat(),
        market_slug=market.slug,
        ask_yes=ask_yes,
        ask_no=ask_no,
        sum_asks=sum_asks,
        gap=gap,
        gap_after_fee=gap_after_fee,
        liquidity=market.liquidity,
        volume_24h=market.volume_24h,
    )


# =============================================================================
# Scanner
# =============================================================================

def run_scanner(
    markets: List[Market],
    threshold: float,
    duration_sec: int,
    csv_path: str,
    verbose: bool = False,
) -> Dict:
    """Main scanner loop. Returns run statistics."""
    stats = {
        "markets_monitored": len(markets),
        "total_checks": 0,
        "api_errors": 0,
        "opportunities_found": 0,
        "unique_arb_markets": set(),
        "profitable_after_fee": 0,
        "best_opportunity": None,
        "start_time": time.time(),
        "cycle_count": 0,
    }

    end_time = time.time() + duration_sec

    with open(csv_path, "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "timestamp", "market_slug",
            "ask_yes", "ask_no", "sum_asks",
            "gap", "gap_after_fee",
            "liquidity", "volume_24h",
        ])

        logger.info(f"{'='*70}")
        logger.info(f"SCANNING {len(markets)} markets | threshold={threshold} | {duration_sec}s")
        logger.info(f"Output: {csv_path}")
        logger.info(f"{'='*70}")

        while time.time() < end_time:
            cycle_start = time.time()
            stats["cycle_count"] += 1
            cycle_arbs = 0

            for i, market in enumerate(markets):
                check = check_market(market)

                if check is None:
                    stats["api_errors"] += 1
                    continue

                stats["total_checks"] += 1

                if verbose:
                    icon = "🟢" if check.sum_asks >= 1.0 else ("🟡" if check.sum_asks >= threshold else "🔴")
                    logger.info(
                        f"  {icon} [{i+1}/{len(markets)}] "
                        f"Sum={check.sum_asks:.4f} gap={check.gap:.4f} | "
                        f"Y={check.ask_yes:.4f} N={check.ask_no:.4f} | "
                        f"{market.slug[:40]}"
                    )

                if check.sum_asks < threshold:
                    stats["opportunities_found"] += 1
                    stats["unique_arb_markets"].add(market.slug)
                    cycle_arbs += 1

                    if check.gap_after_fee > 0:
                        stats["profitable_after_fee"] += 1

                    if (stats["best_opportunity"] is None or
                            check.sum_asks < stats["best_opportunity"].sum_asks):
                        stats["best_opportunity"] = check

                    fee_status = "✅ PROFIT after fee" if check.gap_after_fee > 0 else "❌ fee eats profit"
                    logger.warning(
                        f"🚨 ARB: Sum={check.sum_asks:.4f} | "
                        f"Gap={check.gap:.4f} ({check.gap*100:.1f}%) | "
                        f"After 1% fee: {check.gap_after_fee:.4f} [{fee_status}] | "
                        f"Y={check.ask_yes:.4f} N={check.ask_no:.4f} | "
                        f"L=${check.liquidity:,.0f} V=${check.volume_24h:,.0f} | "
                        f"{market.slug[:40]}"
                    )

                    writer.writerow([
                        check.timestamp, check.market_slug,
                        check.ask_yes, check.ask_no, round(check.sum_asks, 6),
                        round(check.gap, 6), round(check.gap_after_fee, 6),
                        check.liquidity, check.volume_24h,
                    ])
                    csvfile.flush()

            elapsed = time.time() - cycle_start
            remaining = max(0, end_time - time.time())

            logger.info(
                f"Cycle {stats['cycle_count']} done in {elapsed:.1f}s | "
                f"{cycle_arbs} arbs | "
                f"{stats['opportunities_found']} total ({stats['profitable_after_fee']} profitable) | "
                f"{int(remaining)}s left"
            )

            sleep_time = max(0, POLL_INTERVAL_SEC - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    stats["runtime_sec"] = time.time() - stats["start_time"]
    stats["unique_arb_markets"] = len(stats["unique_arb_markets"])
    return stats


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Arbitrage Logger v2.1"
    )
    parser.add_argument("--markets", type=int, default=DEFAULT_MARKETS,
                        help=f"Markets to scan (default: {DEFAULT_MARKETS})")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"Arb threshold (default: {DEFAULT_THRESHOLD})")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION_SEC,
                        help=f"Duration in seconds (default: {DEFAULT_DURATION_SEC})")
    parser.add_argument("--min-volume", type=float, default=DEFAULT_MIN_VOLUME,
                        help=f"Min 24h volume (default: {DEFAULT_MIN_VOLUME})")
    parser.add_argument("--min-liquidity", type=float, default=DEFAULT_MIN_LIQUIDITY,
                        help=f"Min liquidity (default: {DEFAULT_MIN_LIQUIDITY})")
    parser.add_argument("--output", type=str, default="arb_opportunities.csv")
    parser.add_argument("--verbose", action="store_true",
                        help="Show every price check")

    args = parser.parse_args()

    logger.info("=" * 70)
    logger.info("  Polymarket Arbitrage Logger v2.1")
    logger.info("=" * 70)
    logger.info(f"  markets={args.markets} threshold={args.threshold} "
                f"duration={args.duration}s min_vol=${args.min_volume} min_liq=${args.min_liquidity}")
    logger.info("")

    markets = fetch_markets(args.markets, args.min_volume, args.min_liquidity)
    if not markets:
        logger.error("No markets found. Exiting.")
        return

    # Show sample
    logger.info("Sample markets:")
    for m in markets[:5]:
        logger.info(f"  L=${m.liquidity:>10,.0f} V=${m.volume_24h:>10,.0f} | {m.slug[:50]}")
    if len(markets) > 5:
        logger.info(f"  ... and {len(markets) - 5} more")
    logger.info("")

    stats = run_scanner(
        markets=markets,
        threshold=args.threshold,
        duration_sec=args.duration,
        csv_path=args.output,
        verbose=args.verbose,
    )

    # Summary
    logger.info("")
    logger.info("=" * 70)
    logger.info("  SCAN COMPLETE")
    logger.info("=" * 70)
    logger.info(f"  Runtime:                {stats['runtime_sec']:.1f}s")
    logger.info(f"  Markets monitored:      {stats['markets_monitored']}")
    logger.info(f"  Scan cycles:            {stats['cycle_count']}")
    logger.info(f"  Total price checks:     {stats['total_checks']}")
    logger.info(f"  API errors:             {stats['api_errors']}")
    logger.info(f"  Arb opportunities:      {stats['opportunities_found']}")
    logger.info(f"  Profitable after fee:   {stats['profitable_after_fee']}")
    logger.info(f"  Unique arb markets:     {stats['unique_arb_markets']}")

    if stats["best_opportunity"]:
        b = stats["best_opportunity"]
        logger.info(
            f"  Best:  Sum={b.sum_asks:.4f} gap={b.gap:.4f} "
            f"after_fee={b.gap_after_fee:.4f} @ {b.market_slug[:40]}"
        )

    logger.info(f"  CSV:                    {args.output}")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
