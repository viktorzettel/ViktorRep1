#!/usr/bin/env python3
"""
Standalone live source probe for Polymarket-aligned crypto prices.

Shows, for each asset:
  - live price per source
  - strike candidate per source using the same bucket-boundary logic as the web dashboard
  - tick age
  - current bucket close

Default sources:
  - Polymarket RTDS Chainlink
  - Polymarket RTDS Binance
  - Direct Binance spot trades
  - Coinbase USD spot polling

Run:
    python3 price_source_probe.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Optional

from kou_dual_compact_monitor import BinanceTradeStream
from kou_dual_compact_web import (
    DEFAULT_CLOSE_OFFSET_S,
    POLYMARKET_CHAINLINK_WS,
    POLYMARKET_WS_HEADERS,
)
import websockets


COINBASE_PRODUCTS = {
    "ethusdt": "ETH-USD",
    "xrpusdt": "XRP-USD",
    "btcusdt": "BTC-USD",
    "solusdt": "SOL-USD",
}


def _fmt_clock(ts: Optional[float]) -> str:
    if ts is None:
        return "--:--:--"
    return time.strftime("%H:%M:%S", time.gmtime(ts))


def _fmt_age(age: Optional[float]) -> str:
    if age is None:
        return "-"
    return f"{age:.1f}s"


def _fmt_price(value: Optional[float]) -> str:
    if value is None:
        return "-"
    abs_value = abs(value)
    if abs_value >= 1000:
        return f"{value:,.2f}"
    if abs_value >= 100:
        return f"{value:,.3f}"
    if abs_value >= 1:
        return f"{value:,.4f}"
    if abs_value >= 0.1:
        return f"{value:,.5f}"
    if abs_value >= 0.01:
        return f"{value:,.6f}"
    return f"{value:,.8f}"


class PolymarketChainlinkStream(BinanceTradeStream):
    def __init__(self, symbol: str, history_seconds: int = 1200) -> None:
        super().__init__(symbol=symbol, history_seconds=history_seconds)
        base = symbol.lower().replace("usdt", "")
        self.chainlink_symbol = f"{base}/usd"
        self.url = POLYMARKET_CHAINLINK_WS

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    additional_headers=POLYMARKET_WS_HEADERS,
                    ping_interval=20.0,
                    ping_timeout=20.0,
                    close_timeout=5.0,
                    max_size=2_000_000,
                ) as ws:
                    sub = {
                        "action": "subscribe",
                        "subscriptions": [
                            {
                                "topic": "crypto_prices_chainlink",
                                "type": "*",
                                "filters": json.dumps({"symbol": self.chainlink_symbol}),
                            }
                        ],
                    }
                    await ws.send(json.dumps(sub))
                    last_ping = time.time()
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        now_ts = time.time()
                        if now_ts - last_ping >= 5.0:
                            try:
                                await ws.send("PING")
                            except Exception:
                                pass
                            last_ping = now_ts
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        if msg.get("topic") != "crypto_prices_chainlink":
                            continue
                        payload = msg.get("payload") or {}
                        symbol = (payload.get("symbol") or payload.get("asset") or "").lower()
                        if symbol != self.chainlink_symbol:
                            continue
                        try:
                            price = float(payload.get("value"))
                        except Exception:
                            continue
                        ts_ms = payload.get("timestamp") or payload.get("updatedAt")
                        ts = float(ts_ms) / 1000.0 if ts_ms is not None else now_ts
                        self._update(ts, price)
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 10.0)


class PolymarketBinanceStream(BinanceTradeStream):
    def __init__(self, symbol: str, history_seconds: int = 1200) -> None:
        super().__init__(symbol=symbol, history_seconds=history_seconds)
        self.binance_symbol = symbol.lower()
        self.url = POLYMARKET_CHAINLINK_WS

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    additional_headers=POLYMARKET_WS_HEADERS,
                    ping_interval=20.0,
                    ping_timeout=20.0,
                    close_timeout=5.0,
                    max_size=2_000_000,
                ) as ws:
                    sub = {
                        "action": "subscribe",
                        "subscriptions": [
                            {
                                "topic": "crypto_prices",
                                "type": "update",
                                "filters": json.dumps({"symbol": self.binance_symbol}),
                            }
                        ],
                    }
                    await ws.send(json.dumps(sub))
                    last_ping = time.time()
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        now_ts = time.time()
                        if now_ts - last_ping >= 5.0:
                            try:
                                await ws.send("PING")
                            except Exception:
                                pass
                            last_ping = now_ts
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        if msg.get("topic") != "crypto_prices":
                            continue
                        payload = msg.get("payload") or {}
                        symbol = (payload.get("symbol") or payload.get("asset") or "").lower()
                        if symbol != self.binance_symbol:
                            continue
                        try:
                            price = float(payload.get("value"))
                        except Exception:
                            continue
                        ts_ms = payload.get("timestamp") or payload.get("updatedAt")
                        ts = float(ts_ms) / 1000.0 if ts_ms is not None else now_ts
                        self._update(ts, price)
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 10.0)


class CoinbasePollingStream(BinanceTradeStream):
    def __init__(self, symbol: str, history_seconds: int = 1200, poll_seconds: float = 1.0) -> None:
        super().__init__(symbol=symbol, history_seconds=history_seconds)
        self.product = COINBASE_PRODUCTS[symbol.lower()]
        self.poll_seconds = max(0.5, poll_seconds)

    def _fetch_price(self) -> Optional[float]:
        url = f"https://api.coinbase.com/v2/prices/{self.product}/spot"
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        amount = data.get("data", {}).get("amount")
        return float(amount) if amount is not None else None

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                price = await asyncio.to_thread(self._fetch_price)
                if price is not None and price > 0.0:
                    self._update(time.time(), price)
            except Exception:
                pass
            await asyncio.sleep(self.poll_seconds)


@dataclass
class SourceView:
    name: str
    live: Optional[float]
    strike: Optional[float]
    age_s: Optional[float]


class AssetProbe:
    def __init__(self, symbol: str, bucket_seconds: int, close_offset_s: float) -> None:
        self.symbol = symbol.lower()
        self.bucket_seconds = bucket_seconds
        self.close_offset_s = close_offset_s
        self.streams: list[tuple[str, BinanceTradeStream]] = [
            ("poly-chainlink", PolymarketChainlinkStream(self.symbol)),
            ("poly-binance", PolymarketBinanceStream(self.symbol)),
            ("binance-direct", BinanceTradeStream(self.symbol)),
            ("coinbase-usd", CoinbasePollingStream(self.symbol)),
        ]

    def _bucket_start(self, now_ts: float) -> int:
        return (int(now_ts + self.close_offset_s) // self.bucket_seconds) * self.bucket_seconds

    def _boundary_ts(self, now_ts: float) -> float:
        return self._bucket_start(now_ts) - self.close_offset_s

    def _strike_for(self, stream: BinanceTradeStream, boundary_ts: float) -> Optional[float]:
        price = stream.last_price_at_or_before(boundary_ts, max_age_s=2.0)
        if price is None:
            price = stream.first_price_at_or_after(boundary_ts, max_delay_s=1.0)
        return price

    def snapshot(self, now_ts: float) -> tuple[str, float, float, list[SourceView]]:
        boundary_ts = self._boundary_ts(now_ts)
        bucket_end = self._bucket_start(now_ts) + self.bucket_seconds - self.close_offset_s
        rows: list[SourceView] = []
        for name, stream in self.streams:
            live, ts = stream.latest()
            age_s = None if ts is None else max(0.0, now_ts - ts)
            rows.append(
                SourceView(
                    name=name,
                    live=live,
                    strike=self._strike_for(stream, boundary_ts),
                    age_s=age_s,
                )
            )
        return self.symbol.replace("usdt", "").upper(), boundary_ts, bucket_end, rows


def _render(probes: list[AssetProbe], now_ts: float) -> str:
    width = max(88, shutil.get_terminal_size((120, 30)).columns)
    lines = [
        f"Price Source Probe  {_fmt_clock(now_ts)} UTC  compare strikes/live against Polymarket",
        "-" * min(width, 120),
    ]
    for probe in probes:
        asset, boundary_ts, bucket_end, rows = probe.snapshot(now_ts)
        lines.append(
            f"{asset}  boundary {_fmt_clock(boundary_ts)}  close {_fmt_clock(bucket_end)}  t_left {max(0.0, bucket_end - now_ts):.1f}s"
        )
        lines.append(f"{'source':<15} {'live':>14} {'strike':>14} {'age':>8}")
        for row in rows:
            lines.append(
                f"{row.name:<15} {_fmt_price(row.live):>14} {_fmt_price(row.strike):>14} {_fmt_age(row.age_s):>8}"
            )
        lines.append("")
    return "\n".join(lines)


async def _display_loop(probes: list[AssetProbe], refresh_seconds: float) -> None:
    sys.stdout.write("\x1b[?25l")
    sys.stdout.flush()
    try:
        while True:
            now_ts = time.time()
            screen = _render(probes, now_ts)
            sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.write(screen)
            sys.stdout.write("\n")
            sys.stdout.flush()
            await asyncio.sleep(refresh_seconds)
    finally:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe multiple live price sources for strike/live alignment")
    parser.add_argument("--symbols", default="ethusdt,xrpusdt", help="Comma-separated symbols (default: ethusdt,xrpusdt)")
    parser.add_argument("--bucket-seconds", type=int, default=300, help="Bucket size in seconds (default: 300)")
    parser.add_argument(
        "--close-offset-seconds",
        type=float,
        default=DEFAULT_CLOSE_OFFSET_S,
        help="Shift bucket close earlier by this many seconds (default: 1.0)",
    )
    parser.add_argument("--refresh-seconds", type=float, default=0.5, help="Screen refresh interval (default: 0.5)")
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    symbols = [piece.strip().lower() for piece in args.symbols.split(",") if piece.strip()]
    if not symbols:
        symbols = ["ethusdt", "xrpusdt"]
    probes = [AssetProbe(symbol, max(30, int(args.bucket_seconds)), max(0.0, float(args.close_offset_seconds))) for symbol in symbols]

    tasks = []
    for probe in probes:
        for name, stream in probe.streams:
            tasks.append(asyncio.create_task(stream.run(), name=f"{probe.symbol}:{name}"))

    display_task = asyncio.create_task(_display_loop(probes, max(0.2, float(args.refresh_seconds))), name="display")
    try:
        await display_task
    finally:
        for probe in probes:
            for _, stream in probe.streams:
                stream.stop()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
