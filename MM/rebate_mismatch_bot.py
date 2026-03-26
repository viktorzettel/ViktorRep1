#!/usr/bin/env python3
"""
Polymarket low-liquidity mismatch bot for 5m/15m crypto markets.

Strategy:
1) Scan short-dated crypto markets (SOL/XRP/ETH by default).
2) Detect book mismatches where YES_bid + NO_bid is high.
3) Enter paired inventory (buy YES + buy NO) only under entry controls.
4) Exit only when YES_bid + NO_bid exceeds configured threshold.

Safety:
- Dry-run by default.
- Live mode requires --live and valid API credentials in .env.
- Uses FOK orders to reduce partial fill risk.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.error import URLError

from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL


GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
CLOB_BOOK_URL = "https://clob.polymarket.com/book"
CLOB_FEE_RATE_URL = "https://clob.polymarket.com/fee-rate"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Origin": "https://polymarket.com",
}

DEFAULT_ASSETS = ("sol", "xrp", "eth")
DEFAULT_INTERVALS = (5, 15)


logger = logging.getLogger("rebate_mismatch_bot")


@dataclass
class MarketCandidate:
    slug: str
    question: str
    token_yes: str
    token_no: str
    interval_minutes: int
    liquidity: float
    volume_24h: float
    fee_rate_bps: int


@dataclass
class BookTop:
    bid: float
    bid_size: float
    ask: float
    ask_size: float


@dataclass
class Opportunity:
    market: MarketCandidate
    yes: BookTop
    no: BookTop
    bid_sum: float
    ask_sum: float
    gross_edge: float
    est_taker_fee_per_pair: float
    net_edge_after_fee: float


@dataclass
class Position:
    market: MarketCandidate
    shares: float
    entry_yes: float
    entry_no: float
    opened_ts: float


def _http_get_json(url: str, params: Optional[dict[str, Any]] = None, timeout: float = 8.0) -> Any:
    q = f"?{urllib.parse.urlencode(params)}" if params else ""
    req = urllib.request.Request(f"{url}{q}", headers=BROWSER_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw)
    except URLError as exc:
        raise RuntimeError(f"network request failed for {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from {url}: {exc}") from exc


def _parse_json_field(v: Any) -> list[Any]:
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _extract_yes_no_tokens(market: dict[str, Any]) -> tuple[str, str]:
    token_ids = _parse_json_field(market.get("clobTokenIds", []))
    if len(token_ids) < 2:
        return "", ""

    outcomes = [str(x).strip().lower() for x in _parse_json_field(market.get("outcomes", []))]
    if len(outcomes) >= 2:
        yes_idx = next((i for i, o in enumerate(outcomes) if o == "yes"), None)
        no_idx = next((i for i, o in enumerate(outcomes) if o == "no"), None)
        if yes_idx is not None and no_idx is not None:
            return str(token_ids[yes_idx]), str(token_ids[no_idx])

    return str(token_ids[0]), str(token_ids[1])


def _detect_interval_minutes(text: str) -> Optional[int]:
    t = text.lower()
    if re.search(r"(^|[^0-9])15\s*[- ]?\s*(min|minute|m)([^a-z0-9]|$)", t):
        return 15
    if re.search(r"(^|[^0-9])5\s*[- ]?\s*(min|minute|m)([^a-z0-9]|$)", t):
        return 5
    return None


def _is_target_asset(text: str, assets: tuple[str, ...]) -> bool:
    t = text.lower()
    return any(a in t for a in assets)


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def fetch_fee_rate_bps(token_id: str) -> int:
    data = _http_get_json(CLOB_FEE_RATE_URL, {"token_id": token_id})
    raw = data.get("fee_rate_bps", data.get("base_fee", 0))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def fetch_book_top(token_id: str) -> Optional[BookTop]:
    data = _http_get_json(CLOB_BOOK_URL, {"token_id": token_id})
    bids = data.get("bids", [])
    asks = data.get("asks", [])
    if not bids or not asks:
        return None

    bid0 = bids[0]
    ask0 = asks[0]
    bid = _to_float(bid0.get("price"))
    ask = _to_float(ask0.get("price"))
    bid_size = _to_float(bid0.get("size"))
    ask_size = _to_float(ask0.get("size"))

    if bid <= 0.0 or ask <= 0.0:
        return None
    return BookTop(bid=bid, bid_size=bid_size, ask=ask, ask_size=ask_size)


def discover_markets(
    assets: tuple[str, ...],
    intervals: tuple[int, ...],
    max_liquidity: float,
    limit: int,
    fee_enabled_only: bool,
) -> list[MarketCandidate]:
    try:
        payload = _http_get_json(
            GAMMA_MARKETS_URL,
            {"active": "true", "closed": "false", "limit": limit},
            timeout=12.0,
        )
    except Exception as exc:
        logger.warning("Market discovery failed: %s", exc)
        return []
    markets = payload if isinstance(payload, list) else []

    out: list[MarketCandidate] = []
    for m in markets:
        if not m.get("enableOrderBook", False):
            continue

        slug = str(m.get("slug", ""))
        question = str(m.get("question", ""))
        combined = f"{slug} {question}"

        interval = _detect_interval_minutes(combined)
        if interval is None or interval not in intervals:
            continue
        if not _is_target_asset(combined, assets):
            continue

        liquidity = _to_float(m.get("liquidityNum", m.get("liquidity", 0)))
        if liquidity > max_liquidity:
            continue

        token_yes, token_no = _extract_yes_no_tokens(m)
        if not token_yes or not token_no:
            continue

        try:
            fee_bps = fetch_fee_rate_bps(token_yes)
        except Exception:
            continue
        if fee_enabled_only and fee_bps <= 0:
            continue

        out.append(
            MarketCandidate(
                slug=slug,
                question=question,
                token_yes=token_yes,
                token_no=token_no,
                interval_minutes=interval,
                liquidity=liquidity,
                volume_24h=_to_float(m.get("volume24hr", 0)),
                fee_rate_bps=fee_bps,
            )
        )

    return out


def est_taker_fee_usdc(shares: float, price: float, fee_rate_bps: int) -> float:
    """
    Approximate fee from the docs fee table for 5m/15m crypto.

    At fee_rate_bps=1000, effective rate peaks at ~1.56% around p=0.5.
    We scale linearly by fee_rate_bps to stay conservative if the schedule changes.
    """
    if shares <= 0 or price <= 0 or fee_rate_bps <= 0:
        return 0.0
    p = min(max(price, 0.001), 0.999)
    effective_rate = 0.0624 * p * (1.0 - p) * (fee_rate_bps / 1000.0)
    return shares * p * effective_rate


def scan_opportunities(
    markets: list[MarketCandidate],
    min_bid_sum: float,
    min_net_edge: float,
) -> list[Opportunity]:
    results: list[Opportunity] = []
    for m in markets:
        try:
            yes = fetch_book_top(m.token_yes)
            no = fetch_book_top(m.token_no)
        except Exception:
            continue

        if not yes or not no:
            continue

        bid_sum = yes.bid + no.bid
        ask_sum = yes.ask + no.ask
        gross_edge = bid_sum - 1.0
        est_fee = est_taker_fee_usdc(1.0, yes.bid, m.fee_rate_bps) + est_taker_fee_usdc(
            1.0, no.bid, m.fee_rate_bps
        )
        net_edge = gross_edge - est_fee

        if bid_sum < min_bid_sum or net_edge < min_net_edge:
            continue

        results.append(
            Opportunity(
                market=m,
                yes=yes,
                no=no,
                bid_sum=bid_sum,
                ask_sum=ask_sum,
                gross_edge=gross_edge,
                est_taker_fee_per_pair=est_fee,
                net_edge_after_fee=net_edge,
            )
        )

    results.sort(key=lambda o: o.net_edge_after_fee, reverse=True)
    return results


def _fmt_pct(x: float) -> str:
    return f"{x*100:.2f}%"


def _print_opportunities(opps: list[Opportunity], max_rows: int = 15) -> None:
    if not opps:
        print("No qualifying opportunities.")
        return

    print(
        "rank | bid_sum | ask_sum | gross | net_after_fee | interval | liq | fee_bps | market"
    )
    for i, o in enumerate(opps[:max_rows], start=1):
        print(
            f"{i:>4} | "
            f"{o.bid_sum:>7.4f} | "
            f"{o.ask_sum:>7.4f} | "
            f"{_fmt_pct(o.gross_edge):>7} | "
            f"{_fmt_pct(o.net_edge_after_fee):>13} | "
            f"{o.market.interval_minutes:>8} | "
            f"${o.market.liquidity:>7,.0f} | "
            f"{o.market.fee_rate_bps:>7} | "
            f"{o.market.slug}"
        )


class PairMismatchBot:
    def __init__(
        self,
        assets: tuple[str, ...],
        intervals: tuple[int, ...],
        max_liquidity: float,
        market_limit: int,
        refresh_markets_every: int,
        poll_seconds: float,
        entry_sum_max: float,
        exit_sum_min: float,
        min_net_edge: float,
        usd_per_leg: float,
        min_shares: float,
        fee_enabled_only: bool,
        live: bool,
    ) -> None:
        self.assets = assets
        self.intervals = intervals
        self.max_liquidity = max_liquidity
        self.market_limit = market_limit
        self.refresh_markets_every = max(1, refresh_markets_every)
        self.poll_seconds = max(0.2, poll_seconds)
        self.entry_sum_max = entry_sum_max
        self.exit_sum_min = exit_sum_min
        self.min_net_edge = min_net_edge
        self.usd_per_leg = usd_per_leg
        self.min_shares = min_shares
        self.fee_enabled_only = fee_enabled_only
        self.live = live
        self.position: Optional[Position] = None
        self.client = None

        if self.live:
            # Import lazily so scan/dry-run can run without local credential config.
            from client_wrapper import PolymarketClient

            poly = PolymarketClient()
            self.client = poly.get_client()

    def _shares_for_entry(self, opp: Opportunity) -> float:
        shares_by_budget_yes = self.usd_per_leg / opp.yes.ask
        shares_by_budget_no = self.usd_per_leg / opp.no.ask
        shares = min(shares_by_budget_yes, shares_by_budget_no, opp.yes.ask_size, opp.no.ask_size)
        return math.floor(shares * 100.0) / 100.0

    def _submit_fok(self, token_id: str, side: str, price: float, size: float) -> bool:
        order = OrderArgs(token_id=token_id, side=side, price=price, size=size)
        signed = self.client.create_order(order)
        resp = self.client.post_order(signed, OrderType.FOK)
        if not isinstance(resp, dict):
            return False
        if resp.get("errorMsg"):
            return False
        return bool(resp.get("orderID") or resp.get("id") or resp.get("success"))

    def _enter(self, opp: Opportunity) -> bool:
        shares = self._shares_for_entry(opp)
        if shares < self.min_shares:
            logger.info("Skip %s: shares %.2f below min_shares %.2f", opp.market.slug, shares, self.min_shares)
            return False

        logger.info(
            "ENTRY signal %s | YES ask %.4f NO ask %.4f sum %.4f shares %.2f",
            opp.market.slug,
            opp.yes.ask,
            opp.no.ask,
            opp.ask_sum,
            shares,
        )

        if not self.live:
            self.position = Position(
                market=opp.market,
                shares=shares,
                entry_yes=opp.yes.ask,
                entry_no=opp.no.ask,
                opened_ts=time.time(),
            )
            return True

        yes_ok = self._submit_fok(opp.market.token_yes, BUY, opp.yes.ask, shares)
        if not yes_ok:
            logger.warning("YES leg failed for %s", opp.market.slug)
            return False

        no_ok = self._submit_fok(opp.market.token_no, BUY, opp.no.ask, shares)
        if not no_ok:
            logger.warning("NO leg failed for %s, attempting immediate unwind of YES leg", opp.market.slug)
            unwind = self._submit_fok(opp.market.token_yes, SELL, opp.yes.bid, shares)
            logger.warning("YES unwind %s", "succeeded" if unwind else "failed")
            return False

        self.position = Position(
            market=opp.market,
            shares=shares,
            entry_yes=opp.yes.ask,
            entry_no=opp.no.ask,
            opened_ts=time.time(),
        )
        return True

    def _exit(self, opp: Opportunity) -> bool:
        if not self.position:
            return False
        shares = self.position.shares
        pnl_per_share = opp.bid_sum - (self.position.entry_yes + self.position.entry_no)
        logger.info(
            "EXIT signal %s | YES bid %.4f NO bid %.4f sum %.4f est_pnl/share %.4f",
            opp.market.slug,
            opp.yes.bid,
            opp.no.bid,
            opp.bid_sum,
            pnl_per_share,
        )

        if not self.live:
            self.position = None
            return True

        yes_ok = self._submit_fok(opp.market.token_yes, SELL, opp.yes.bid, shares)
        no_ok = self._submit_fok(opp.market.token_no, SELL, opp.no.bid, shares)
        if yes_ok and no_ok:
            self.position = None
            return True

        logger.warning("Exit partially failed (YES=%s NO=%s) - manual check recommended", yes_ok, no_ok)
        return False

    def run(self) -> None:
        logger.info(
            "Starting mismatch bot | live=%s assets=%s intervals=%s entry<=%.4f exit>=%.4f",
            self.live,
            ",".join(self.assets),
            ",".join(str(i) for i in self.intervals),
            self.entry_sum_max,
            self.exit_sum_min,
        )

        markets: list[MarketCandidate] = []
        cycle = 0
        while True:
            cycle += 1
            try:
                if not markets or cycle % self.refresh_markets_every == 1:
                    markets = discover_markets(
                        assets=self.assets,
                        intervals=self.intervals,
                        max_liquidity=self.max_liquidity,
                        limit=self.market_limit,
                        fee_enabled_only=self.fee_enabled_only,
                    )
                    logger.info("Discovered %d candidate markets", len(markets))

                opps = scan_opportunities(
                    markets=markets,
                    min_bid_sum=max(1.0, self.exit_sum_min - 0.05),
                    min_net_edge=self.min_net_edge,
                )
            except Exception as exc:
                logger.warning("Cycle error: %s", exc)
                time.sleep(self.poll_seconds)
                continue
            if not opps:
                logger.info("No opportunities this cycle")
                time.sleep(self.poll_seconds)
                continue

            if self.position is None:
                entry = next((o for o in opps if o.ask_sum <= self.entry_sum_max), None)
                if entry:
                    self._enter(entry)
                else:
                    top = opps[0]
                    logger.info(
                        "Best scan %s | bid_sum %.4f ask_sum %.4f net_edge %.2f%%",
                        top.market.slug,
                        top.bid_sum,
                        top.ask_sum,
                        top.net_edge_after_fee * 100.0,
                    )
            else:
                pos_slug = self.position.market.slug
                pos_opp = next((o for o in opps if o.market.slug == pos_slug), None)
                if not pos_opp:
                    logger.info("Position market %s not in active opportunity set", pos_slug)
                elif pos_opp.bid_sum >= self.exit_sum_min:
                    self._exit(pos_opp)
                else:
                    logger.info(
                        "Holding %s | current bid_sum %.4f target %.4f",
                        pos_slug,
                        pos_opp.bid_sum,
                        self.exit_sum_min,
                    )

            time.sleep(self.poll_seconds)


def _parse_csv_assets(v: str) -> tuple[str, ...]:
    out = tuple(x.strip().lower() for x in v.split(",") if x.strip())
    return out or DEFAULT_ASSETS


def _parse_csv_intervals(v: str) -> tuple[int, ...]:
    vals: list[int] = []
    for p in v.split(","):
        p = p.strip()
        if not p:
            continue
        try:
            vals.append(int(p))
        except ValueError:
            continue
    out = tuple(i for i in vals if i in (5, 15))
    return out or DEFAULT_INTERVALS


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_scan(args: argparse.Namespace) -> int:
    markets = discover_markets(
        assets=_parse_csv_assets(args.assets),
        intervals=_parse_csv_intervals(args.intervals),
        max_liquidity=args.max_liquidity,
        limit=args.limit,
        fee_enabled_only=not args.include_fee_free,
    )
    print(f"Found {len(markets)} candidate markets at {datetime.now(timezone.utc).isoformat()}")
    try:
        opps = scan_opportunities(
            markets=markets,
            min_bid_sum=args.min_bid_sum,
            min_net_edge=args.min_net_edge,
        )
    except Exception as exc:
        logger.error("Opportunity scan failed: %s", exc)
        return 1
    _print_opportunities(opps, max_rows=args.rows)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    bot = PairMismatchBot(
        assets=_parse_csv_assets(args.assets),
        intervals=_parse_csv_intervals(args.intervals),
        max_liquidity=args.max_liquidity,
        market_limit=args.limit,
        refresh_markets_every=args.refresh_markets_every,
        poll_seconds=args.poll_seconds,
        entry_sum_max=args.entry_sum_max,
        exit_sum_min=args.exit_sum_min,
        min_net_edge=args.min_net_edge,
        usd_per_leg=args.usd_per_leg,
        min_shares=args.min_shares,
        fee_enabled_only=not args.include_fee_free,
        live=args.live,
    )
    bot.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Polymarket 5m/15m mismatch bot")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs")

    sub = parser.add_subparsers(dest="cmd", required=True)

    scan = sub.add_parser("scan", help="Scan markets and print opportunities")
    scan.add_argument("--assets", default="sol,xrp,eth", help="CSV asset filter (default: sol,xrp,eth)")
    scan.add_argument("--intervals", default="5,15", help="CSV interval minutes (default: 5,15)")
    scan.add_argument("--limit", type=int, default=500, help="Gamma market fetch limit")
    scan.add_argument("--max-liquidity", type=float, default=150000.0, help="Max market liquidity")
    scan.add_argument("--min-bid-sum", type=float, default=1.01, help="Require YES_bid + NO_bid >= this")
    scan.add_argument("--min-net-edge", type=float, default=0.0, help="Require net edge after fee >= this")
    scan.add_argument("--rows", type=int, default=15, help="Rows to print")
    scan.add_argument("--include-fee-free", action="store_true", help="Include fee-free markets")
    scan.set_defaults(func=cmd_scan)

    run = sub.add_parser("run", help="Run entry/exit loop (dry-run unless --live)")
    run.add_argument("--assets", default="sol,xrp,eth", help="CSV asset filter")
    run.add_argument("--intervals", default="5,15", help="CSV interval minutes")
    run.add_argument("--limit", type=int, default=500, help="Gamma market fetch limit")
    run.add_argument("--max-liquidity", type=float, default=150000.0, help="Max market liquidity")
    run.add_argument("--refresh-markets-every", type=int, default=20, help="Rescan markets every N cycles")
    run.add_argument("--poll-seconds", type=float, default=1.5, help="Cycle sleep")
    run.add_argument("--entry-sum-max", type=float, default=1.00, help="Enter only if YES_ask + NO_ask <= this")
    run.add_argument("--exit-sum-min", type=float, default=1.03, help="Exit only if YES_bid + NO_bid >= this")
    run.add_argument("--min-net-edge", type=float, default=0.0, help="Min net edge for candidates")
    run.add_argument("--usd-per-leg", type=float, default=25.0, help="Notional per leg")
    run.add_argument("--min-shares", type=float, default=2.0, help="Minimum shares per leg")
    run.add_argument("--include-fee-free", action="store_true", help="Include fee-free markets")
    run.add_argument(
        "--live",
        action="store_true",
        help="Enable live orders (default is dry-run simulation)",
    )
    run.set_defaults(func=cmd_run)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    _setup_logging(args.verbose)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
