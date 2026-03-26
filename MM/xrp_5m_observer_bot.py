#!/usr/bin/env python3
"""
Read-only Polymarket observer bot for 5-minute crypto markets.

What it does:
- Authenticates to Polymarket CLOB with credentials from `.env`.
- Tracks the active 5-minute market for one asset (default: XRP).
- Polls YES/NO top-of-book every 0.5 seconds.
- Detects market end/closure and rotates to the next market automatically.

What it does NOT do:
- No order creation, cancellation, buying, selling, or market-making.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import ssl
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.error import URLError

import py_clob_client.http_helpers.helpers as _helpers
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

from config import settings


GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Origin": "https://polymarket.com",
}

ASSET_ALIASES = {
    "xrp": ("xrp", "ripple"),
    "eth": ("eth", "ethereum"),
    "sol": ("sol", "solana"),
}


def _patch_clob_headers() -> None:
    """Make CLOB requests look browser-like to reduce proxy/edge rejections."""

    def _patched(method: str, headers: Optional[dict[str, str]]) -> dict[str, str]:
        out = dict(headers or {})
        out.setdefault("User-Agent", BROWSER_HEADERS["User-Agent"])
        out.setdefault("Accept", "*/*")
        out.setdefault("Content-Type", "application/json")
        out.setdefault("Origin", "https://polymarket.com")
        return out

    _helpers.overloadHeaders = _patched


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes"}
    if isinstance(v, (int, float)):
        return bool(v)
    return False


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


def _parse_iso_ts(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)

    s = str(v).strip()
    if not s:
        return None

    if s.isdigit() and len(s) >= 10:
        return float(int(s[:10]))

    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _slug_epoch_ts(slug: str) -> Optional[float]:
    m = re.search(r"-(\d{10})(?:$|[^0-9])", slug)
    if not m:
        return None
    try:
        return float(int(m.group(1)))
    except ValueError:
        return None


def _slug_prefix(slug: str) -> Optional[str]:
    m = re.search(r"^(.*)-(\d{10})(?:$|[^0-9])", slug.strip())
    if not m:
        return None
    prefix = m.group(1).strip("-")
    return prefix or None


def _fmt_ts(ts: Optional[float]) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _fmt_num(v: Optional[float], digits: int = 4) -> str:
    if v is None:
        return "-"
    return f"{v:.{digits}f}"


def _http_get_json(url: str, params: Optional[dict[str, Any]] = None, timeout: float = 8.0) -> Any:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    req = urllib.request.Request(f"{url}{query}", headers=BROWSER_HEADERS)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _detect_interval_minutes(market: dict[str, Any]) -> Optional[int]:
    rec = str(market.get("recurrence", "")).strip().lower()
    if rec in {"5m", "5min", "5-minute", "5 minute"}:
        return 5

    events = market.get("events")
    if isinstance(events, list) and events and isinstance(events[0], dict):
        series = events[0].get("series")
        if isinstance(series, list) and series and isinstance(series[0], dict):
            rec = str(series[0].get("recurrence", "")).strip().lower()
            if rec in {"5m", "5min", "5-minute", "5 minute"}:
                return 5

    blob = " ".join(
        [
            str(market.get("slug", "")),
            str(market.get("question", "")),
            str(market.get("title", "")),
        ]
    ).lower()
    if re.search(r"(^|[^0-9])5\s*[- ]?\s*(m|min|minute)([^a-z0-9]|$)", blob):
        return 5
    return None


def _detect_asset(market: dict[str, Any]) -> Optional[str]:
    blob = " ".join(
        [
            str(market.get("slug", "")),
            str(market.get("question", "")),
            str(market.get("title", "")),
            str(market.get("resolutionSource", "")),
        ]
    ).lower()
    for asset, aliases in ASSET_ALIASES.items():
        if any(alias in blob for alias in aliases):
            return asset
    return None


def _extract_yes_no_tokens(market: dict[str, Any]) -> tuple[str, str, str, str]:
    token_ids = _parse_json_field(market.get("clobTokenIds", []))
    if len(token_ids) < 2:
        return "", "", "YES", "NO"

    outcomes_raw = _parse_json_field(market.get("outcomes", []))
    outcomes = [str(x).strip().lower() for x in outcomes_raw]

    yes_idx = next((i for i, o in enumerate(outcomes) if o in {"yes", "up"}), None)
    no_idx = next((i for i, o in enumerate(outcomes) if o in {"no", "down"}), None)
    if yes_idx is not None and no_idx is not None:
        return (
            str(token_ids[yes_idx]),
            str(token_ids[no_idx]),
            str(outcomes_raw[yes_idx]).upper(),
            str(outcomes_raw[no_idx]).upper(),
        )

    return str(token_ids[0]), str(token_ids[1]), "YES", "NO"


def _extract_start_end_ts(market: dict[str, Any], interval_minutes: int) -> tuple[Optional[float], Optional[float]]:
    def _first_ts(keys: list[str], payload: dict[str, Any]) -> Optional[float]:
        for key in keys:
            if key in payload:
                ts = _parse_iso_ts(payload.get(key))
                if ts is not None:
                    return ts
        return None

    start_keys = ["eventStartTime", "startTime", "startDate", "acceptingOrdersTimestamp"]
    end_keys = ["endDate", "endTime"]

    start_ts = _first_ts(start_keys, market)
    end_ts = _first_ts(end_keys, market)

    events = market.get("events")
    if isinstance(events, list) and events and isinstance(events[0], dict):
        event0 = events[0]
        if start_ts is None:
            start_ts = _first_ts(start_keys, event0)
        if end_ts is None:
            end_ts = _first_ts(end_keys, event0)

    slug_ts = _slug_epoch_ts(str(market.get("slug", "")))
    if start_ts is None and slug_ts is not None:
        start_ts = slug_ts

    if start_ts is None and end_ts is not None:
        start_ts = end_ts - (interval_minutes * 60)
    if end_ts is None and start_ts is not None:
        end_ts = start_ts + (interval_minutes * 60)

    return start_ts, end_ts


@dataclass
class MarketCandidate:
    slug: str
    question: str
    asset: str
    interval_minutes: int
    token_yes: str
    token_no: str
    yes_label: str
    no_label: str
    start_ts: float
    end_ts: float
    accepting_orders: bool
    active: bool
    closed: bool
    liquidity: float


@dataclass
class BookTop:
    bid: float
    ask: float
    bid_size: float
    ask_size: float


def _market_to_candidate(market: dict[str, Any], asset: str) -> Optional[MarketCandidate]:
    if not _to_bool(market.get("enableOrderBook", False)):
        return None

    if _detect_interval_minutes(market) != 5:
        return None

    if _detect_asset(market) != asset:
        return None

    slug = str(market.get("slug", "")).strip()
    if not slug:
        return None

    token_yes, token_no, yes_label, no_label = _extract_yes_no_tokens(market)
    if not token_yes or not token_no:
        return None

    start_ts, end_ts = _extract_start_end_ts(market, interval_minutes=5)
    if start_ts is None or end_ts is None:
        return None

    return MarketCandidate(
        slug=slug,
        question=str(market.get("question", "")),
        asset=asset,
        interval_minutes=5,
        token_yes=token_yes,
        token_no=token_no,
        yes_label=yes_label,
        no_label=no_label,
        start_ts=start_ts,
        end_ts=end_ts,
        accepting_orders=_to_bool(market.get("acceptingOrders", False)),
        active=_to_bool(market.get("active", False)),
        closed=_to_bool(market.get("closed", False)),
        liquidity=_to_float(market.get("liquidityNum", market.get("liquidity", 0.0))),
    )


def discover_5m_markets(asset: str, limit: int) -> list[MarketCandidate]:
    payload = _http_get_json(
        GAMMA_MARKETS_URL,
        {"closed": "false", "limit": limit},
        timeout=10.0,
    )
    markets = payload if isinstance(payload, list) else []

    out: list[MarketCandidate] = []
    for market in markets:
        candidate = _market_to_candidate(market, asset)
        if candidate is not None:
            out.append(candidate)

    out.sort(key=lambda m: (m.start_ts, m.end_ts))
    return out


def probe_5m_markets_by_slug(
    asset: str,
    now_ts: float,
    *,
    lookback_slots: int = 2,
    lookahead_slots: int = 4,
    slug_prefixes: Optional[list[str]] = None,
) -> list[MarketCandidate]:
    """
    Fallback discovery for short-cycle markets that may be omitted from list pages.
    """
    step_seconds = 5 * 60
    slot_base = int(now_ts // step_seconds) * step_seconds
    slots = [slot_base + (i * step_seconds) for i in range(-lookback_slots, lookahead_slots + 1)]

    if slug_prefixes:
        patterns = [f"{p.strip('-')}-{{ts}}" for p in slug_prefixes if p]
    else:
        # Known slug variants for crypto up/down short-cycle markets.
        patterns = [
            f"{asset}-updown-5m-{{ts}}",
            f"{asset}-up-or-down-5m-{{ts}}",
            f"{asset}-updown-{{ts}}",
            f"{asset}-up-or-down-{{ts}}",
        ]

    out: list[MarketCandidate] = []
    seen: set[str] = set()
    for ts in slots:
        for pattern in patterns:
            slug = pattern.format(ts=ts)
            if slug in seen:
                continue
            seen.add(slug)

            payload = _http_get_json(GAMMA_MARKETS_URL, {"slug": slug}, timeout=5.0)
            rows = payload if isinstance(payload, list) else []
            for market in rows:
                candidate = _market_to_candidate(market, asset)
                if candidate is not None:
                    out.append(candidate)

    unique_by_slug: dict[str, MarketCandidate] = {m.slug: m for m in out}
    results = list(unique_by_slug.values())
    results.sort(key=lambda m: (m.start_ts, m.end_ts))
    return results


def select_current_and_next(markets: list[MarketCandidate], now_ts: float) -> tuple[Optional[MarketCandidate], Optional[MarketCandidate]]:
    live = [m for m in markets if m.start_ts <= now_ts < m.end_ts and not m.closed]
    if live:
        live.sort(key=lambda m: (m.end_ts, -m.start_ts))
        current = live[0]
    else:
        upcoming = [m for m in markets if now_ts < m.start_ts and not m.closed]
        upcoming.sort(key=lambda m: m.start_ts)
        current = upcoming[0] if upcoming else None

    if current is None:
        return None, None

    next_candidates = [m for m in markets if m.start_ts > current.start_ts and not m.closed]
    next_candidates.sort(key=lambda m: m.start_ts)
    return current, (next_candidates[0] if next_candidates else None)


def login_clob_client() -> tuple[ClobClient, str]:
    """
    Initialize authenticated CLOB client with credentials from config/.env.
    """
    _patch_clob_headers()

    signature_type = 2 if settings.poly_proxy_address else None
    client = ClobClient(
        host=settings.poly_host,
        key=settings.poly_private_key,
        chain_id=settings.poly_chain_id,
        funder=settings.poly_proxy_address,
        signature_type=signature_type,
    )

    if settings.has_saved_credentials():
        creds = ApiCreds(
            api_key=settings.poly_api_key or "",
            api_secret=settings.poly_api_secret or "",
            api_passphrase=settings.poly_api_passphrase or "",
        )
    else:
        creds = client.create_or_derive_api_creds()
        if creds is None:
            raise RuntimeError("Failed to derive Polymarket API credentials")

    client.set_api_creds(creds)

    # Auth verification call (L2). Raises if credentials are invalid.
    client.get_api_keys()

    address = client.get_address() or "-"
    return client, address


def fetch_book_top(client: ClobClient, token_id: str) -> Optional[BookTop]:
    book = client.get_order_book(token_id)
    bids = getattr(book, "bids", None) or []
    asks = getattr(book, "asks", None) or []
    if not bids or not asks:
        return None

    b0 = bids[0]
    a0 = asks[0]
    bid = _to_float(getattr(b0, "price", None))
    ask = _to_float(getattr(a0, "price", None))
    if bid <= 0.0 or ask <= 0.0:
        return None

    return BookTop(
        bid=bid,
        ask=ask,
        bid_size=_to_float(getattr(b0, "size", None)),
        ask_size=_to_float(getattr(a0, "size", None)),
    )


class ObserverBot:
    def __init__(
        self,
        *,
        asset: str,
        poll_seconds: float,
        discover_seconds: float,
        slug_probe_seconds: float,
        market_limit: int,
        seed_slug: Optional[str] = None,
    ) -> None:
        self.asset = asset
        self.poll_seconds = max(0.1, poll_seconds)
        self.discover_seconds = max(0.5, discover_seconds)
        self.slug_probe_seconds = max(2.0, slug_probe_seconds)
        self.market_limit = max(50, market_limit)
        self.seed_slug = (seed_slug.strip() if seed_slug else None)
        self.seed_slug_prefix = _slug_prefix(self.seed_slug) if self.seed_slug else None

        self.client: Optional[ClobClient] = None
        self.current_market: Optional[MarketCandidate] = None
        self.next_market: Optional[MarketCandidate] = None
        self._state_lock = asyncio.Lock()
        self._wake_discovery = asyncio.Event()
        self._last_slug_probe_ts = 0.0

    async def _discover_once(self) -> None:
        now_ts = time.time()
        markets = await asyncio.to_thread(discover_5m_markets, self.asset, self.market_limit)
        if (not markets) and ((now_ts - self._last_slug_probe_ts) >= self.slug_probe_seconds):
            slug_prefixes = [self.seed_slug_prefix] if self.seed_slug_prefix else None
            probed = await asyncio.to_thread(
                probe_5m_markets_by_slug,
                self.asset,
                now_ts,
                slug_prefixes=slug_prefixes,
            )
            self._last_slug_probe_ts = now_ts
            if probed:
                markets = probed
                logging.info(
                    "Fallback slug probe found %d %s 5m market(s).",
                    len(probed),
                    self.asset.upper(),
                )

        current, next_market = select_current_and_next(markets, now_ts)

        async with self._state_lock:
            old_slug = self.current_market.slug if self.current_market else None
            new_slug = current.slug if current else None
            self.current_market = current
            self.next_market = next_market

        if old_slug != new_slug:
            if current is None:
                logging.info("No active/upcoming %s 5m market found.", self.asset.upper())
                return
            logging.info(
                (
                    "Switch -> slug=%s start=%s end=%s active=%s accepting_orders=%s liquidity=%s "
                    "url=%s"
                ),
                current.slug,
                _fmt_ts(current.start_ts),
                _fmt_ts(current.end_ts),
                current.active,
                current.accepting_orders,
                _fmt_num(current.liquidity, 2),
                f"https://polymarket.com/event/{current.slug}",
            )

    async def _discovery_loop(self) -> None:
        while True:
            try:
                await self._discover_once()
            except (URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
                logging.warning("Discovery error: %s", exc)
            except Exception as exc:
                logging.warning("Unexpected discovery error: %s", exc)

            try:
                await asyncio.wait_for(self._wake_discovery.wait(), timeout=self.discover_seconds)
            except TimeoutError:
                pass
            finally:
                self._wake_discovery.clear()

    async def _observe_loop(self) -> None:
        while True:
            cycle_start = time.time()

            async with self._state_lock:
                current = self.current_market
                next_market = self.next_market

            if current is None:
                if next_market is None:
                    logging.info("state=WAITING asset=%s note=no_market_discovered", self.asset.upper())
                else:
                    until_next = max(0.0, next_market.start_ts - cycle_start)
                    logging.info(
                        "state=WAITING asset=%s next_slug=%s next_starts_in_s=%.1f",
                        self.asset.upper(),
                        next_market.slug,
                        until_next,
                    )
                await asyncio.sleep(self.poll_seconds)
                continue

            if cycle_start >= current.end_ts or current.closed:
                self._wake_discovery.set()
                status = "ENDED"
            elif cycle_start < current.start_ts:
                status = "UPCOMING"
            else:
                status = "LIVE"

            yes_top: Optional[BookTop] = None
            no_top: Optional[BookTop] = None
            if self.client is not None and status == "LIVE":
                try:
                    yes_task = asyncio.to_thread(fetch_book_top, self.client, current.token_yes)
                    no_task = asyncio.to_thread(fetch_book_top, self.client, current.token_no)
                    yes_top, no_top = await asyncio.gather(yes_task, no_task)
                except Exception as exc:
                    logging.warning("Book fetch error for %s: %s", current.slug, exc)

            yes_bid = yes_top.bid if yes_top else None
            yes_ask = yes_top.ask if yes_top else None
            no_bid = no_top.bid if no_top else None
            no_ask = no_top.ask if no_top else None

            left_s = current.end_ts - cycle_start
            next_slug = next_market.slug if next_market else "-"

            # yes_mid + no_mid should be ~1.0 in an efficient market
            if None not in (yes_bid, yes_ask, no_bid, no_ask):
                mid_sum = (yes_bid + yes_ask) / 2.0 + (no_bid + no_ask) / 2.0
            else:
                mid_sum = None

            logging.info(
                (
                    "state=%s slug=%s t_left_s=%.1f %s_bid=%s %s_ask=%s %s_bid=%s %s_ask=%s "
                    "mid_sum=%s next=%s"
                ),
                status,
                current.slug,
                left_s,
                current.yes_label,
                _fmt_num(yes_bid),
                current.yes_label,
                _fmt_num(yes_ask),
                current.no_label,
                _fmt_num(no_bid),
                current.no_label,
                _fmt_num(no_ask),
                _fmt_num(mid_sum),
                next_slug,
            )

            elapsed = time.time() - cycle_start
            await asyncio.sleep(max(0.0, self.poll_seconds - elapsed))

    async def run(self) -> None:
        logging.info("Logging in to Polymarket...")
        self.client, address = await asyncio.to_thread(login_clob_client)
        logging.info("Login successful. wallet=%s asset=%s timeframe=5m", address, self.asset.upper())
        if self.seed_slug_prefix:
            logging.info("Using seed slug prefix for discovery: %s-<epoch>", self.seed_slug_prefix)

        discovery_task = asyncio.create_task(self._discovery_loop(), name="discovery_loop")
        observe_task = asyncio.create_task(self._observe_loop(), name="observe_loop")
        try:
            await asyncio.gather(discovery_task, observe_task)
        finally:
            discovery_task.cancel()
            observe_task.cancel()
            await asyncio.gather(discovery_task, observe_task, return_exceptions=True)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Observe Polymarket 5-minute crypto markets (read-only)")
    p.add_argument("--asset", choices=sorted(ASSET_ALIASES.keys()), default="xrp", help="Asset to observe")
    p.add_argument(
        "--seed-slug",
        default=None,
        help="Known slug to lock discovery pattern (e.g. xrp-updown-5m-1772990100)",
    )
    p.add_argument("--poll-seconds", type=float, default=0.5, help="Observation loop interval")
    p.add_argument("--discover-seconds", type=float, default=1.0, help="Market discovery/rotation interval")
    p.add_argument(
        "--slug-probe-seconds",
        type=float,
        default=20.0,
        help="Fallback slug-probe cadence when list discovery is empty",
    )
    p.add_argument("--market-limit", type=int, default=500, help="Gamma fetch limit")
    p.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return p


async def _main_async(args: argparse.Namespace) -> int:
    bot = ObserverBot(
        asset=args.asset.lower(),
        poll_seconds=args.poll_seconds,
        discover_seconds=args.discover_seconds,
        slug_probe_seconds=args.slug_probe_seconds,
        market_limit=args.market_limit,
        seed_slug=args.seed_slug,
    )
    await bot.run()
    return 0


def main() -> int:
    args = build_parser().parse_args()
    _setup_logging(args.verbose)
    try:
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        logging.info("Stopped by user.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
