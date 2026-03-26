#!/usr/bin/env python3
"""
XRP 5-minute decision bot (read-only, independent of Polymarket).

Features:
- Connects to Binance trade websocket for real-time XRPUSDT price.
- Captures a 5-minute "strike" at bucket start (rounded to 4 decimals).
- Tracks time to expiry and current price (rounded to 4 decimals).
- Computes a simple probability of finishing YES/NO every second.
- Rolls to the next 5-minute bucket without lag.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

import websockets


BINANCE_WS_BASE = "wss://stream.binance.com:9443/ws"


def _round4(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    return float(f"{v:.4f}")


def _fmt4(v: Optional[float]) -> str:
    if v is None:
        return "-"
    return f"{v:.4f}"


def _fmt2(v: Optional[float]) -> str:
    if v is None:
        return "-"
    return f"{v:.2f}"


def _fmt_ts(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts))


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class PricePoint:
    ts: float
    price: float


class BinancePriceStream:
    def __init__(self, symbol: str, history_seconds: int = 900) -> None:
        self.symbol = symbol.lower()
        self.url = f"{BINANCE_WS_BASE}/{self.symbol}@trade"
        self.history_seconds = max(120, history_seconds)
        self.history: deque[PricePoint] = deque()
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
        self.history.append(PricePoint(ts=ts, price=price))

        min_ts = ts - self.history_seconds
        while self.history and self.history[0].ts < min_ts:
            self.history.popleft()

    def estimate_sigma(self, window_seconds: int = 300, subsample_seconds: float = 1.0, min_points: int = 10) -> Optional[float]:
        if len(self.history) < 2:
            return None

        now_ts = self.history[-1].ts
        window_min = now_ts - window_seconds
        points = [p for p in self.history if p.ts >= window_min]
        if len(points) < 2:
            return None

        # Subsample to reduce microstructure noise (bid-ask bounce, clustered ticks)
        sampled: list[PricePoint] = [points[0]]
        for p in points[1:]:
            if p.ts - sampled[-1].ts >= subsample_seconds:
                sampled.append(p)
        if len(sampled) < min_points:
            return None

        var_per_sec_sum = 0.0
        count = 0
        prev = sampled[0]
        for p in sampled[1:]:
            dt = p.ts - prev.ts
            if dt <= 0:
                prev = p
                continue
            r = math.log(p.price / prev.price)
            var_per_sec_sum += (r * r) / dt
            count += 1
            prev = p

        if count == 0:
            return None
        return math.sqrt(var_per_sec_sum / count)

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
                    logging.info("Binance stream connected: %s", self.symbol.upper())
                    backoff = 1.0
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        try:
                            msg = json.loads(raw)
                            price = float(msg.get("p", 0.0))
                            if price <= 0.0:
                                continue
                            evt = msg.get("E") or msg.get("T")
                            ts = float(evt) / 1000.0 if evt is not None else time.time()
                            if ts <= 0:
                                ts = time.time()
                            self._update(ts, price)
                        except Exception:
                            continue
            except Exception as exc:
                logging.warning("Binance stream reconnecting after error: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 10.0)


class DecisionBot:
    def __init__(
        self,
        *,
        symbol: str,
        bucket_seconds: int,
        poll_seconds: float,
        sigma_fallback: float,
    ) -> None:
        self.symbol = symbol
        self.bucket_seconds = bucket_seconds
        self.poll_seconds = max(0.2, poll_seconds)
        self.sigma_fallback = sigma_fallback

        self.stream = BinancePriceStream(symbol=symbol)
        self.strike_price: Optional[float] = None
        self.bucket_start: Optional[float] = None

    def _current_bucket_start(self, now_ts: float) -> float:
        return (int(now_ts) // self.bucket_seconds) * self.bucket_seconds

    def _roll_bucket_if_needed(self, now_ts: float) -> None:
        bucket_start = self._current_bucket_start(now_ts)
        if self.bucket_start is None or bucket_start != self.bucket_start:
            self.bucket_start = bucket_start
            self.strike_price = None
            logging.info("New bucket: start=%s end=%s", _fmt_ts(bucket_start), _fmt_ts(bucket_start + self.bucket_seconds))

    def _ensure_strike(self, current_price: Optional[float]) -> None:
        if self.strike_price is not None:
            return
        if current_price is None:
            return
        self.strike_price = _round4(current_price)
        logging.info("Strike set: %s", _fmt4(self.strike_price))

    def _probability_yes(self, current: float, strike: float, time_left: float) -> float:
        if time_left <= 0:
            return 1.0 if current >= strike else 0.0

        sigma = self.stream.estimate_sigma() or self.sigma_fallback
        if sigma <= 1e-12:
            return 1.0 if current >= strike else 0.0

        d = (math.log(current / strike) - 0.5 * sigma * sigma * time_left) / (sigma * math.sqrt(time_left))
        return max(0.0, min(1.0, _normal_cdf(d)))

    async def run(self) -> None:
        stream_task = asyncio.create_task(self.stream.run(), name="binance_stream")
        try:
            while True:
                cycle_start = time.time()
                self._roll_bucket_if_needed(cycle_start)

                current_price, _ = self.stream.latest()
                self._ensure_strike(current_price)

                if self.bucket_start is None:
                    await asyncio.sleep(self.poll_seconds)
                    continue

                expiry = self.bucket_start + self.bucket_seconds
                time_left = max(0.0, expiry - cycle_start)

                if current_price is None or self.strike_price is None:
                    logging.info(
                        "state=WAITING price=%s strike=%s t_left_s=%s",
                        _fmt4(_round4(current_price)),
                        _fmt4(self.strike_price),
                        _fmt2(time_left),
                    )
                else:
                    current_4 = _round4(current_price)
                    strike_4 = _round4(self.strike_price)
                    prob_yes = self._probability_yes(current_4, strike_4, time_left)
                    prob_no = 1.0 - prob_yes
                    logging.info(
                        (
                            "state=LIVE price=%s strike=%s t_left_s=%s "
                            "p_yes=%.4f p_no=%.4f"
                        ),
                        _fmt4(current_4),
                        _fmt4(strike_4),
                        _fmt2(time_left),
                        prob_yes,
                        prob_no,
                    )

                elapsed = time.time() - cycle_start
                await asyncio.sleep(max(0.0, self.poll_seconds - elapsed))
        finally:
            self.stream.stop()
            stream_task.cancel()
            await asyncio.gather(stream_task, return_exceptions=True)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="XRP 5-minute decision bot (read-only)")
    p.add_argument("--symbol", default="xrpusdt", help="Binance symbol (default: xrpusdt)")
    p.add_argument("--bucket-seconds", type=int, default=300, help="Bucket size in seconds (5m = 300)")
    p.add_argument("--poll-seconds", type=float, default=1.0, help="Decision loop interval")
    p.add_argument(
        "--sigma-fallback",
        type=float,
        default=0.0015,
        help="Fallback per-sqrt-second volatility if estimate is unavailable",
    )
    p.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return p


async def _main_async(args: argparse.Namespace) -> int:
    bot = DecisionBot(
        symbol=args.symbol,
        bucket_seconds=args.bucket_seconds,
        poll_seconds=args.poll_seconds,
        sigma_fallback=args.sigma_fallback,
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
