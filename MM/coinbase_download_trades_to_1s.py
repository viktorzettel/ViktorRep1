#!/usr/bin/env python3
"""
Download Coinbase Exchange public trades and aggregate them into 1-second OHLC.

Examples:
  python3 coinbase_download_trades_to_1s.py --product XRP-USD --days 21 --out xrpusd_1s_coinbase_21d.csv.gz
  python3 coinbase_download_trades_to_1s.py --product XRP-USD --start 2026-03-15 --end 2026-04-04 --out xrpusd_1s_coinbase.csv.gz
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import ssl
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

try:
    import certifi

    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()


COINBASE_TRADES_URL = "https://api.exchange.coinbase.com/products/{product}/trades"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


@dataclass
class Bucket1s:
    open_ts: float
    open: float
    high: float
    low: float
    close_ts: float
    close: float
    volume: float
    trades: int


def fmt_duration(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "--:--:--"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fetch_trade_page(product: str, after_cursor: str | None, limit: int, timeout: float = 20.0) -> tuple[list[dict], str | None]:
    params = {"limit": str(limit)}
    if after_cursor:
        params["after"] = after_cursor
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        COINBASE_TRADES_URL.format(product=product) + f"?{query}",
        headers={"User-Agent": "MM/coinbase-trades-1s"},
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        payload = json.loads(resp.read().decode())
        next_cursor = resp.headers.get("CB-AFTER")
        return payload, next_cursor


def main() -> int:
    parser = argparse.ArgumentParser(description="Download Coinbase public trades and aggregate to 1-second OHLC")
    parser.add_argument("--product", required=True, help="Coinbase product id, e.g. XRP-USD")
    parser.add_argument("--days", type=int, default=21, help="Lookback days if --start omitted")
    parser.add_argument("--start", default="", help="UTC start date/time, e.g. 2026-03-15 or 2026-03-15T00:00:00")
    parser.add_argument("--end", default="", help="UTC end date/time; default now")
    parser.add_argument("--out", required=True, help="Output CSV.gz path")
    parser.add_argument("--limit", type=int, default=1000, help="Trades per request (default: 1000)")
    parser.add_argument("--sleep", type=float, default=0.05, help="Sleep between requests")
    parser.add_argument(
        "--progress-every-pages",
        type=int,
        default=25,
        help="Print progress every N pages (default: 25)",
    )
    args = parser.parse_args()

    end_dt = parse_dt(args.end) if args.end else datetime.now(timezone.utc).replace(microsecond=0)
    start_dt = parse_dt(args.start) if args.start else end_dt - timedelta(days=max(1, int(args.days)))
    start_ts = start_dt.timestamp()
    end_ts = end_dt.timestamp()

    print(f"Downloading {args.product} trades from {start_dt.isoformat()} to {end_dt.isoformat()}")

    buckets: dict[int, Bucket1s] = {}
    after_cursor: str | None = None
    pages = 0
    total_trades = 0
    oldest_seen: datetime | None = None
    wall_start = time.time()
    total_span_s = max(end_ts - start_ts, 1.0)

    while True:
        payload, next_cursor = fetch_trade_page(args.product, after_cursor, max(1, int(args.limit)))
        if not payload:
            break
        pages += 1

        oldest_page_dt: datetime | None = None
        for trade in payload:
            try:
                trade_dt = datetime.strptime(trade["time"], TIME_FORMAT).replace(tzinfo=timezone.utc)
                trade_ts = trade_dt.timestamp()
                if trade_ts > end_ts:
                    continue
                if trade_ts < start_ts:
                    oldest_page_dt = trade_dt
                    continue
                price = float(trade["price"])
                size = float(trade["size"])
            except Exception:
                continue

            bucket_ts = int(trade_ts)
            record = buckets.get(bucket_ts)
            if record is None:
                buckets[bucket_ts] = Bucket1s(
                    open_ts=trade_ts,
                    open=price,
                    high=price,
                    low=price,
                    close_ts=trade_ts,
                    close=price,
                    volume=size,
                    trades=1,
                )
            else:
                if trade_ts < record.open_ts:
                    record.open_ts = trade_ts
                    record.open = price
                if trade_ts > record.close_ts:
                    record.close_ts = trade_ts
                    record.close = price
                record.high = max(record.high, price)
                record.low = min(record.low, price)
                record.volume += size
                record.trades += 1
            total_trades += 1
            oldest_page_dt = trade_dt if oldest_page_dt is None else min(oldest_page_dt, trade_dt)

        if oldest_page_dt is not None:
            oldest_seen = oldest_page_dt if oldest_seen is None else min(oldest_seen, oldest_page_dt)

        progress_every = max(1, int(args.progress_every_pages))
        if pages % progress_every == 0 and oldest_seen is not None:
            covered_s = max(0.0, min(total_span_s, end_ts - oldest_seen.timestamp()))
            progress = covered_s / total_span_s
            elapsed_s = max(0.0, time.time() - wall_start)
            eta_s = None
            if progress > 0.0:
                eta_s = elapsed_s * (1.0 - progress) / progress
            print(
                "  "
                f"progress={progress * 100:6.2f}% "
                f"pages={pages:5d} "
                f"trades={total_trades:10,d} "
                f"buckets={len(buckets):9,d} "
                f"oldest={oldest_seen.isoformat()} "
                f"elapsed={fmt_duration(elapsed_s)} "
                f"eta={fmt_duration(eta_s)}",
                flush=True,
            )

        if oldest_seen is not None and oldest_seen.timestamp() <= start_ts:
            break
        if not next_cursor or next_cursor == after_cursor:
            break

        after_cursor = next_cursor
        time.sleep(max(0.0, float(args.sleep)))

    rows = []
    for bucket_ts in sorted(buckets):
        dt = datetime.fromtimestamp(bucket_ts, tz=timezone.utc)
        record = buckets[bucket_ts]
        rows.append(
            [
                bucket_ts,
                dt.isoformat(),
                record.open,
                record.high,
                record.low,
                record.close,
                record.volume,
                record.trades,
            ]
        )

    with gzip.open(args.out, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["open_time", "open_time_iso", "open", "high", "low", "close", "volume", "trades"])
        writer.writerows(rows)

    print(f"Saved {len(rows):,} 1s buckets to {args.out}")
    print(f"Pages fetched: {pages:,} | trades aggregated: {total_trades:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
