#!/usr/bin/env python3
"""
Read-only Polymarket short-cycle market observer.

Features:
- Scans live Polymarket crypto up/down markets (5m/15m).
- Tracks the current market and rotates automatically as markets close/open.
- Uses Binance websocket as the underlying live price stream.
- Sets "price to beat" from Binance at market start timestamp.
- Logs YES/NO top-of-book prices every second (no trading).
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
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.error import URLError

import websockets


GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
CLOB_BOOK_URL = "https://clob.polymarket.com/book"
BINANCE_WS_BASE = "wss://stream.binance.com:9443/ws"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Origin": "https://polymarket.com",
}

ASSET_ALIASES = {
    "xrp": ("xrp", "ripple"),
    "sol": ("sol", "solana"),
    "eth": ("eth", "ethereum"),
    "btc": ("btc", "bitcoin"),
}


def _parse_json_field(v: Any) -> list[Any]:
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            data = json.loads(v)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []
    return []


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


def _fmt_ts(ts: Optional[float]) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _fmt_num(v: Optional[float], d: int = 4) -> str:
    if v is None:
        return "-"
    return f"{v:.{d}f}"


def _http_get_json(url: str, params: Optional[dict[str, Any]] = None, timeout: float = 6.0) -> Any:
    q = f"?{urllib.parse.urlencode(params)}" if params else ""
    req = urllib.request.Request(f"{url}{q}", headers=BROWSER_HEADERS)
    ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


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
    volume_24h: float
    resolution_source: str


@dataclass
class BookTop:
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    ts: float


def _extract_recurrence(m: dict[str, Any]) -> str:
    rec = str(m.get("recurrence", "")).strip().lower()
    if rec:
        return rec

    events = m.get("events")
    if isinstance(events, list) and events:
        ev = events[0] if isinstance(events[0], dict) else {}
        series = ev.get("series")
        if isinstance(series, list) and series:
            s0 = series[0] if isinstance(series[0], dict) else {}
            rec = str(s0.get("recurrence", "")).strip().lower()
            if rec:
                return rec
    return ""


def _detect_interval_minutes(m: dict[str, Any]) -> Optional[int]:
    rec = _extract_recurrence(m)
    if rec in {"5m", "5min", "5-minute", "5 minute"}:
        return 5
    if rec in {"15m", "15min", "15-minute", "15 minute"}:
        return 15

    blob = " ".join(
        [
            str(m.get("slug", "")),
            str(m.get("question", "")),
            str(m.get("title", "")),
        ]
    ).lower()
    if re.search(r"(^|[^0-9])15\s*[- ]?\s*(m|min|minute)([^a-z0-9]|$)", blob):
        return 15
    if re.search(r"(^|[^0-9])5\s*[- ]?\s*(m|min|minute)([^a-z0-9]|$)", blob):
        return 5
    return None


def _detect_asset(m: dict[str, Any]) -> Optional[str]:
    blob = " ".join(
        [
            str(m.get("slug", "")),
            str(m.get("question", "")),
            str(m.get("title", "")),
            str(m.get("resolutionSource", "")),
        ]
    ).lower()
    for asset, aliases in ASSET_ALIASES.items():
        if any(a in blob for a in aliases):
            return asset
    return None


def _extract_yes_no_tokens(m: dict[str, Any]) -> tuple[str, str, str, str]:
    token_ids = _parse_json_field(m.get("clobTokenIds", []))
    if len(token_ids) < 2:
        return "", "", "YES", "NO"

    outcomes_raw = _parse_json_field(m.get("outcomes", []))
    outcomes = [str(x).strip().lower() for x in outcomes_raw]
    yes_idx = next((i for i, o in enumerate(outcomes) if o in {"yes", "up"}), None)
    no_idx = next((i for i, o in enumerate(outcomes) if o in {"no", "down"}), None)

    if yes_idx is not None and no_idx is not None:
        yes_label = str(outcomes_raw[yes_idx]).upper()
        no_label = str(outcomes_raw[no_idx]).upper()
        return str(token_ids[yes_idx]), str(token_ids[no_idx]), yes_label, no_label

    return str(token_ids[0]), str(token_ids[1]), "YES", "NO"


def _extract_start_end_ts(m: dict[str, Any], interval_minutes: int) -> tuple[Optional[float], Optional[float]]:
    def first_ts(keys: list[str], obj: dict[str, Any]) -> Optional[float]:
        for k in keys:
            if k in obj:
                ts = _parse_iso_ts(obj.get(k))
                if ts is not None:
                    return ts
        return None

    start_keys = ["eventStartTime", "startTime", "startDate", "acceptingOrdersTimestamp"]
    end_keys = ["endDate", "endTime"]

    start_ts = first_ts(start_keys, m)
    end_ts = first_ts(end_keys, m)

    events = m.get("events")
    if isinstance(events, list) and events and isinstance(events[0], dict):
        ev0 = events[0]
        if start_ts is None:
            start_ts = first_ts(start_keys, ev0)
        if end_ts is None:
            end_ts = first_ts(end_keys, ev0)

    slug = str(m.get("slug", ""))
    slug_ts = _slug_epoch_ts(slug)
    if start_ts is None and slug_ts is not None:
        start_ts = slug_ts

    if start_ts is None and end_ts is not None:
        start_ts = end_ts - (interval_minutes * 60)
    if end_ts is None and start_ts is not None:
        end_ts = start_ts + (interval_minutes * 60)

    return start_ts, end_ts


def discover_markets(
    *,
    asset: str,
    intervals: tuple[int, ...],
    limit: int,
    slug_prefix: Optional[str] = None,
) -> list[MarketCandidate]:
    payload = _http_get_json(
        GAMMA_MARKETS_URL,
        {"closed": "false", "limit": limit},
        timeout=10.0,
    )
    markets = payload if isinstance(payload, list) else []

    out: list[MarketCandidate] = []
    for m in markets:
        if not _to_bool(m.get("enableOrderBook", False)):
            continue

        interval = _detect_interval_minutes(m)
        if interval is None or interval not in intervals:
            continue

        market_asset = _detect_asset(m)
        if market_asset != asset:
            continue

        slug = str(m.get("slug", ""))
        if not slug:
            continue
        if slug_prefix and not slug.startswith(slug_prefix):
            continue

        token_yes, token_no, yes_label, no_label = _extract_yes_no_tokens(m)
        if not token_yes or not token_no:
            continue

        start_ts, end_ts = _extract_start_end_ts(m, interval)
        if start_ts is None or end_ts is None:
            continue

        out.append(
            MarketCandidate(
                slug=slug,
                question=str(m.get("question", "")),
                asset=market_asset,
                interval_minutes=interval,
                token_yes=token_yes,
                token_no=token_no,
                yes_label=yes_label,
                no_label=no_label,
                start_ts=start_ts,
                end_ts=end_ts,
                accepting_orders=_to_bool(m.get("acceptingOrders", False)),
                active=_to_bool(m.get("active", False)),
                closed=_to_bool(m.get("closed", False)),
                liquidity=_to_float(m.get("liquidityNum", m.get("liquidity", 0.0))),
                volume_24h=_to_float(m.get("volume24hr", 0.0)),
                resolution_source=str(m.get("resolutionSource", "")),
            )
        )

    out.sort(key=lambda x: (x.start_ts, x.end_ts))
    return out


def select_current_market(
    markets: list[MarketCandidate], now_ts: float
) -> tuple[Optional[MarketCandidate], list[MarketCandidate]]:
    live = [m for m in markets if (m.start_ts - 1.0) <= now_ts < m.end_ts and not m.closed]
    if live:
        live.sort(key=lambda m: (m.end_ts, -m.start_ts))
        current = live[0]
    else:
        upcoming = [m for m in markets if now_ts < m.start_ts and not m.closed]
        upcoming.sort(key=lambda m: m.start_ts)
        current = upcoming[0] if upcoming else None

    if current is None:
        return None, []

    upcoming_from_current = [m for m in markets if m.start_ts > current.start_ts and not m.closed]
    upcoming_from_current.sort(key=lambda m: m.start_ts)
    return current, upcoming_from_current[:2]


def fetch_book_top(token_id: str, timeout: float = 1.0) -> Optional[BookTop]:
    data = _http_get_json(CLOB_BOOK_URL, {"token_id": token_id}, timeout=timeout)
    bids = data.get("bids", [])
    asks = data.get("asks", [])
    if not bids or not asks:
        return None

    b0 = bids[0]
    a0 = asks[0]
    bid = _to_float(b0.get("price"))
    ask = _to_float(a0.get("price"))
    if bid <= 0.0 or ask <= 0.0:
        return None
    return BookTop(
        bid=bid,
        ask=ask,
        bid_size=_to_float(b0.get("size")),
        ask_size=_to_float(a0.get("size")),
        ts=time.time(),
    )


class BinancePriceStream:
    def __init__(self, symbol: str, history_seconds: int = 3600) -> None:
        self.symbol = symbol.upper()
        self.url = f"{BINANCE_WS_BASE}/{self.symbol.lower()}@trade"
        self.history_seconds = max(600, history_seconds)
        self.history: deque[tuple[float, float]] = deque()
        self.last_price: Optional[float] = None
        self.last_ts: Optional[float] = None
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def latest(self) -> tuple[Optional[float], Optional[float]]:
        return self.last_price, self.last_ts

    def _update(self, ts: float, price: float) -> None:
        self.last_price = price
        self.last_ts = ts
        self.history.append((ts, price))

        min_ts = ts - self.history_seconds
        while self.history and self.history[0][0] < min_ts:
            self.history.popleft()

    def price_at_or_near(self, target_ts: float, max_diff_seconds: float = 120.0) -> Optional[float]:
        if not self.history:
            return None
        best_price: Optional[float] = None
        best_diff = float("inf")
        for ts, p in self.history:
            diff = abs(ts - target_ts)
            if diff < best_diff:
                best_diff = diff
                best_price = p
        if best_diff <= max_diff_seconds:
            return best_price
        return None

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=20.0,
                    ping_timeout=20.0,
                    close_timeout=5.0,
                    max_size=2_000_000,
                ) as ws:
                    logging.info("Binance stream connected: %s", self.symbol)
                    backoff = 1.0
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        try:
                            msg = json.loads(raw)
                            p = _to_float(msg.get("p"))
                            if p <= 0:
                                continue
                            evt = msg.get("E") or msg.get("T")
                            ts = _to_float(evt) / 1000.0 if evt is not None else time.time()
                            if ts <= 0:
                                ts = time.time()
                            self._update(ts, p)
                        except Exception:
                            continue
            except Exception as exc:
                logging.warning("Binance stream reconnecting after error: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 10.0)


class MarketObserverBot:
    def __init__(
        self,
        *,
        asset: str,
        intervals: tuple[int, ...],
        refresh_seconds: float,
        discover_seconds: float,
        market_limit: int,
        slug_prefix: Optional[str],
        start_price_tolerance: float,
    ) -> None:
        self.asset = asset
        self.intervals = intervals
        self.refresh_seconds = max(0.2, refresh_seconds)
        self.discover_seconds = max(0.5, discover_seconds)
        self.market_limit = market_limit
        self.slug_prefix = slug_prefix
        self.start_price_tolerance = max(1.0, start_price_tolerance)

        self.stream = BinancePriceStream(symbol=f"{asset.upper()}USDT")
        self.current_market: Optional[MarketCandidate] = None
        self.next_markets: list[MarketCandidate] = []
        self.price_to_beat: Optional[float] = None
        self.price_to_beat_source: str = "-"
        self.last_discovery_ts: float = 0.0

    def _switch_market(self, market: MarketCandidate) -> None:
        self.current_market = market
        self.price_to_beat = None
        self.price_to_beat_source = "-"
        logging.info(
            "Switched market -> %s | interval=%sm | start=%s | end=%s | url=%s",
            market.slug,
            market.interval_minutes,
            _fmt_ts(market.start_ts),
            _fmt_ts(market.end_ts),
            f"https://polymarket.com/event/{market.slug}",
        )

    def _ensure_price_to_beat(self, now_ts: float) -> None:
        if self.current_market is None:
            return
        if self.price_to_beat is not None:
            return
        if now_ts < self.current_market.start_ts:
            return

        from_start = self.stream.price_at_or_near(
            self.current_market.start_ts,
            max_diff_seconds=self.start_price_tolerance,
        )
        if from_start is not None:
            self.price_to_beat = from_start
            self.price_to_beat_source = "binance@market_start"
            return

        last_price, _ = self.stream.latest()
        if last_price is not None:
            self.price_to_beat = last_price
            self.price_to_beat_source = "binance@first_seen"

    def _market_status(self, now_ts: float) -> str:
        if self.current_market is None:
            return "NO_MARKET"
        if now_ts < self.current_market.start_ts:
            return "UPCOMING"
        if now_ts >= self.current_market.end_ts:
            return "ENDED"
        return "LIVE"

    async def _log_snapshot(self) -> None:
        now_ts = time.time()
        spot, spot_ts = self.stream.latest()
        lag = (now_ts - spot_ts) if spot_ts else None

        if self.current_market is None:
            logging.info(
                "state=WAITING asset=%s spot=%s spot_lag_s=%s",
                self.asset.upper(),
                _fmt_num(spot, 6 if self.asset == "xrp" else 4),
                _fmt_num(lag, 1),
            )
            return

        self._ensure_price_to_beat(now_ts)
        yes = None
        no = None
        try:
            yes_task = asyncio.to_thread(fetch_book_top, self.current_market.token_yes, 1.0)
            no_task = asyncio.to_thread(fetch_book_top, self.current_market.token_no, 1.0)
            yes, no = await asyncio.gather(yes_task, no_task)
        except Exception as exc:
            logging.warning("Book fetch error for %s: %s", self.current_market.slug, exc)

        if yes and no:
            mid_yes = (yes.bid + yes.ask) / 2.0
            mid_no = (no.bid + no.ask) / 2.0
            total_mid = mid_yes + mid_no
            yes_bid = yes.bid
            no_bid = no.bid
            yes_ask = yes.ask
            no_ask = no.ask
        else:
            mid_yes = None
            mid_no = None
            total_mid = None
            yes_bid = no_bid = yes_ask = no_ask = None

        delta = None
        if self.price_to_beat is not None and spot is not None:
            delta = spot - self.price_to_beat

        left_s = self.current_market.end_ts - now_ts
        next1 = self.next_markets[0].slug if len(self.next_markets) >= 1 else "-"
        next2 = self.next_markets[1].slug if len(self.next_markets) >= 2 else "-"

        logging.info(
            (
                "state=%s slug=%s tf=%sm t_left_s=%.0f "
                "spot=%s spot_lag_s=%s beat=%s beat_src=%s delta=%s "
                "%s_bid=%s %s_ask=%s %s_bid=%s %s_ask=%s yes_no_mid_sum=%s "
                "next1=%s next2=%s"
            ),
            self._market_status(now_ts),
            self.current_market.slug,
            self.current_market.interval_minutes,
            left_s,
            _fmt_num(spot, 6 if self.asset == "xrp" else 4),
            _fmt_num(lag, 1),
            _fmt_num(self.price_to_beat, 6 if self.asset == "xrp" else 4),
            self.price_to_beat_source,
            _fmt_num(delta, 6 if self.asset == "xrp" else 4),
            self.current_market.yes_label,
            _fmt_num(yes_bid),
            self.current_market.yes_label,
            _fmt_num(yes_ask),
            self.current_market.no_label,
            _fmt_num(no_bid),
            self.current_market.no_label,
            _fmt_num(no_ask),
            _fmt_num(total_mid, 4),
            next1,
            next2,
        )

    def _discover_and_rotate(self) -> None:
        now_ts = time.time()
        markets = discover_markets(
            asset=self.asset,
            intervals=self.intervals,
            limit=self.market_limit,
            slug_prefix=self.slug_prefix,
        )
        current, next_markets = select_current_market(markets, now_ts)
        self.next_markets = next_markets

        if current is None:
            if self.current_market is not None:
                logging.info("No active/upcoming market found. Clearing current market context.")
            self.current_market = None
            self.price_to_beat = None
            self.price_to_beat_source = "-"
            return

        if self.current_market is None or self.current_market.slug != current.slug:
            self._switch_market(current)

    async def run(self) -> None:
        stream_task = asyncio.create_task(self.stream.run(), name="binance_stream")
        try:
            while True:
                cycle_start = time.time()
                if (
                    self.last_discovery_ts <= 0
                    or (cycle_start - self.last_discovery_ts) >= self.discover_seconds
                    or (self.current_market is not None and cycle_start >= self.current_market.end_ts)
                ):
                    try:
                        await asyncio.to_thread(self._discover_and_rotate)
                    except (URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
                        logging.warning("Discovery error: %s", exc)
                    except Exception as exc:
                        logging.warning("Unexpected discovery error: %s", exc)
                    self.last_discovery_ts = cycle_start

                await self._log_snapshot()

                elapsed = time.time() - cycle_start
                await asyncio.sleep(max(0.0, self.refresh_seconds - elapsed))
        finally:
            self.stream.stop()
            stream_task.cancel()
            await asyncio.gather(stream_task, return_exceptions=True)


def _parse_intervals(v: str) -> tuple[int, ...]:
    out: list[int] = []
    for p in v.split(","):
        p = p.strip()
        if not p:
            continue
        try:
            i = int(p)
            if i in {5, 15}:
                out.append(i)
        except ValueError:
            continue
    return tuple(sorted(set(out))) or (5, 15)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Observe Polymarket 5m/15m up/down markets (no trading)")
    p.add_argument("--asset", default="xrp", choices=sorted(ASSET_ALIASES.keys()), help="Underlying asset")
    p.add_argument("--intervals", default="5,15", help="CSV intervals to track (5,15)")
    p.add_argument("--refresh-seconds", type=float, default=1.0, help="Main refresh cycle in seconds")
    p.add_argument(
        "--discover-seconds",
        type=float,
        default=1.0,
        help="How often to refresh market list from Gamma API",
    )
    p.add_argument("--market-limit", type=int, default=500, help="Gamma fetch limit")
    p.add_argument("--slug-prefix", default=None, help="Optional slug prefix filter (e.g. xrp-updown-5m)")
    p.add_argument(
        "--start-price-tolerance",
        type=float,
        default=120.0,
        help="Max seconds from market start to accept Binance start price",
    )
    p.add_argument("--verbose", action="store_true", help="Enable debug logs")
    return p


async def _main_async(args: argparse.Namespace) -> int:
    bot = MarketObserverBot(
        asset=args.asset.lower(),
        intervals=_parse_intervals(args.intervals),
        refresh_seconds=args.refresh_seconds,
        discover_seconds=args.discover_seconds,
        market_limit=args.market_limit,
        slug_prefix=(args.slug_prefix.strip() if args.slug_prefix else None),
        start_price_tolerance=args.start_price_tolerance,
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
