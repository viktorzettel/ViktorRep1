#!/usr/bin/env python3
"""
BTC Volatility Tracker (Real-time)
==================================
Tracks short- and long-window realized volatility from live Binance trades,
with 90/95% confidence bands for the long-window sigma and a "health score".

Defaults:
- Bucket: 5s (you can set 1s/10s via --bucket)
- Long window: 6h
- Short window: 15m
"""

import argparse
import asyncio
import json
import math
import time
from collections import deque
from dataclasses import dataclass
from statistics import mean, stdev, NormalDist

import websockets


BINANCE_WS = "wss://stream.binance.com:9443/ws/btcusdt@trade"


@dataclass
class ReturnPoint:
    ts: float
    r: float


def chi2_ppf(p: float, df: int) -> float:
    """
    Chi-square quantile.
    Uses scipy if available, otherwise Wilson-Hilferty approximation.
    """
    try:
        from scipy.stats import chi2
        return float(chi2.ppf(p, df))
    except Exception:
        # Wilson-Hilferty approximation
        if df <= 0:
            return float("nan")
        z = NormalDist().inv_cdf(p)
        return df * (1 - 2 / (9 * df) + z * math.sqrt(2 / (9 * df))) ** 3


def sigma_ci(sigma: float, n: int, alpha: float):
    """CI for sigma based on chi-square distribution."""
    if n < 2 or sigma <= 0:
        return (float("nan"), float("nan"))
    s2 = sigma * sigma
    df = n - 1
    chi2_low = chi2_ppf(alpha / 2, df)
    chi2_high = chi2_ppf(1 - alpha / 2, df)
    if chi2_low <= 0 or chi2_high <= 0:
        return (float("nan"), float("nan"))
    var_low = df * s2 / chi2_high
    var_high = df * s2 / chi2_low
    return (math.sqrt(var_low), math.sqrt(var_high))


def health_score(short_sigma: float, long_sigma: float) -> tuple[float, str]:
    if short_sigma <= 0 or long_sigma <= 0:
        return 0.0, "UNKNOWN"
    ratio = short_sigma / long_sigma
    # Score: 50 at ratio=1, 0 at 0.5, 100 at 2.0
    score = 50.0 * (math.log(ratio, 2) + 1.0)
    score = max(0.0, min(100.0, score))
    if ratio < 0.75:
        label = "CALM"
    elif ratio < 1.25:
        label = "NORMAL"
    elif ratio < 1.75:
        label = "ELEVATED"
    elif ratio < 2.5:
        label = "HOT"
    else:
        label = "EXTREME"
    return score, label


def fmt_bps(x: float) -> str:
    return f"{x * 10000:.2f} bp"


def fmt_pct(x: float) -> str:
    return f"{x * 100:.2f}%"


async def run_tracker(bucket_s: int, window_h: float, short_min: float, print_every: float):
    bucket = None
    bucket_close = None
    last_close = None
    returns: deque[ReturnPoint] = deque()
    closes: deque[tuple[float, float]] = deque()
    last_print = 0.0
    last_trade_price = None

    window_sec = window_h * 3600
    short_sec = short_min * 60

    async with websockets.connect(BINANCE_WS) as ws:
        while True:
            msg = await ws.recv()
            data = json.loads(msg)
            price = float(data.get("p"))
            ts = data.get("T", int(time.time() * 1000)) / 1000.0
            last_trade_price = price

            bucket_id = int(ts // bucket_s)
            if bucket is None:
                bucket = bucket_id
                bucket_close = price
                last_close = price
                continue

            if bucket_id == bucket:
                bucket_close = price
            else:
                # finalize previous bucket
                if last_close is not None and bucket_close is not None:
                    r = math.log(bucket_close / last_close)
                    returns.append(ReturnPoint(ts, r))
                    closes.append((ts, bucket_close))
                last_close = bucket_close
                bucket = bucket_id
                bucket_close = price

                # prune old data
                while returns and (ts - returns[0].ts > window_sec):
                    returns.popleft()
                while closes and (ts - closes[0][0] > window_sec):
                    closes.popleft()

            if time.time() - last_print < print_every:
                continue
            last_print = time.time()

            if len(returns) < 10:
                print("Collecting data... (need more samples)")
                continue

            # compute long and short vol
            long_returns = [rp.r for rp in returns]
            long_sigma = stdev(long_returns)
            short_returns = [rp.r for rp in returns if (ts - rp.ts) <= short_sec]
            short_sigma = stdev(short_returns) if len(short_returns) >= 5 else long_sigma

            # CI for long sigma
            lo90, hi90 = sigma_ci(long_sigma, len(long_returns), 0.10)
            lo95, hi95 = sigma_ci(long_sigma, len(long_returns), 0.05)

            # expected move bands (next bucket)
            exp90 = 1.645 * long_sigma
            exp95 = 1.96 * long_sigma

            # jump detection
            recent = [rp.r for rp in returns if (ts - rp.ts) <= 300]
            jump_thresh = 3 * long_sigma
            jumps = [r for r in recent if abs(r) >= jump_thresh]
            max_jump = max((abs(r) for r in recent), default=0.0)

            score, label = health_score(short_sigma, long_sigma)
            ratio = short_sigma / long_sigma if long_sigma > 0 else float("nan")

            print("=" * 80)
            print(
                f"BTC Volatility Tracker | bucket={bucket_s}s | window={window_h:.1f}h | short={short_min:.1f}m"
            )
            print(f"Last price: {last_trade_price:.2f}")
            print(
                f"Long sigma: {fmt_bps(long_sigma)} | 90% CI: [{fmt_bps(lo90)}, {fmt_bps(hi90)}] "
                f"| 95% CI: [{fmt_bps(lo95)}, {fmt_bps(hi95)}] | n={len(long_returns)}"
            )
            print(
                f"Short sigma: {fmt_bps(short_sigma)} | ratio={ratio:.2f} | health={score:.0f}/100 ({label})"
            )
            print(
                f"Expected next move (1 bucket): 90% ±{fmt_bps(exp90)} | 95% ±{fmt_bps(exp95)}"
            )
            print(
                f"Jump stats (last 5m): jumps>=3σ={len(jumps)} | max jump={fmt_bps(max_jump)}"
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", type=int, default=5, help="Bucket size in seconds (1,5,10).")
    parser.add_argument("--window-hours", type=float, default=6.0, help="Long window in hours.")
    parser.add_argument("--short-min", type=float, default=15.0, help="Short window in minutes.")
    parser.add_argument("--print-every", type=float, default=5.0, help="Print interval in seconds.")
    args = parser.parse_args()

    try:
        asyncio.run(run_tracker(args.bucket, args.window_hours, args.short_min, args.print_every))
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
