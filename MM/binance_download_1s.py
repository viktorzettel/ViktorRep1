#!/usr/bin/env python3
"""
Download 1-second Binance spot klines for one or more symbols.

Examples:
  python3 binance_download_1s.py --symbols ETHUSDT,XRPUSDT --days 30 --out-dir data/binance_1s
  python3 binance_download_1s.py --symbols ETHUSDT --start 2026-03-01 --end 2026-04-01 --out-dir data/binance_1s
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    import certifi

    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()


BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
KLINE_LIMIT = 1000
INTERVAL = "1s"


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def request_json(url: str, params: dict[str, object], retries: int = 6, timeout: float = 20.0) -> list:
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{url}?{query}",
        headers={"User-Agent": "MM/binance-download-1s"},
    )
    delay = 1.0
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:
            last_error = exc
            time.sleep(delay)
            delay = min(delay * 2.0, 15.0)
    raise RuntimeError(f"Binance request failed after {retries} retries: {last_error}") from last_error


def build_output_path(out_dir: str, symbol: str, start_dt: datetime, end_dt: datetime) -> str:
    start_tag = start_dt.strftime("%Y%m%d")
    end_tag = end_dt.strftime("%Y%m%d")
    filename = f"{symbol.lower()}_1s_binance_{start_tag}_{end_tag}.csv.gz"
    return os.path.join(out_dir, filename)


def open_csv_writer(path: str) -> tuple[object, csv.writer]:
    handle = gzip.open(path, "wt", newline="", encoding="utf-8")
    writer = csv.writer(handle)
    writer.writerow(
        [
            "open_time",
            "open_time_iso",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "trades",
        ]
    )
    return handle, writer


def download_symbol(
    symbol: str,
    *,
    start_dt: datetime,
    end_dt: datetime,
    out_dir: str,
    sleep_s: float,
    overwrite: bool,
) -> tuple[str, int]:
    os.makedirs(out_dir, exist_ok=True)
    out_path = build_output_path(out_dir, symbol, start_dt, end_dt)
    if os.path.exists(out_path) and not overwrite:
        raise FileExistsError(f"Output already exists: {out_path}")

    handle, writer = open_csv_writer(out_path)
    cursor_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    total_rows = 0
    batch_count = 0

    try:
        while cursor_ms < end_ms:
            params = {
                "symbol": symbol.upper(),
                "interval": INTERVAL,
                "startTime": cursor_ms,
                "endTime": end_ms,
                "limit": KLINE_LIMIT,
            }
            batch = request_json(BINANCE_KLINES_URL, params)
            if not batch:
                break

            for row in batch:
                if len(row) < 9:
                    continue
                ts_ms = int(row[0])
                if ts_ms >= end_ms:
                    continue
                dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
                writer.writerow(
                    [
                        ts_ms,
                        dt.isoformat(),
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        row[5],
                        row[8],
                    ]
                )
                total_rows += 1

            next_cursor_ms = int(batch[-1][6]) + 1
            if next_cursor_ms <= cursor_ms:
                break
            cursor_ms = next_cursor_ms
            batch_count += 1
            if batch_count % 100 == 0:
                pct = min(100.0, 100.0 * (cursor_ms - int(start_dt.timestamp() * 1000)) / max(end_ms - int(start_dt.timestamp() * 1000), 1))
                print(f"  {symbol}: {total_rows:,} rows fetched ({pct:.1f}%)", flush=True)
            time.sleep(max(0.0, sleep_s))
    finally:
        handle.close()

    return out_path, total_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Download Binance 1-second spot klines")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols, e.g. ETHUSDT,XRPUSDT")
    parser.add_argument("--days", type=int, default=30, help="Lookback days if --start omitted")
    parser.add_argument("--start", default="", help="UTC start datetime, e.g. 2026-03-01 or 2026-03-01T00:00:00")
    parser.add_argument("--end", default="", help="UTC end datetime, default now")
    parser.add_argument("--out-dir", default="data/binance_1s", help="Directory for .csv.gz outputs")
    parser.add_argument("--sleep", type=float, default=0.03, help="Sleep between Binance requests")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output files")
    args = parser.parse_args()

    end_dt = parse_dt(args.end) if args.end else datetime.now(timezone.utc).replace(microsecond=0)
    start_dt = parse_dt(args.start) if args.start else end_dt - timedelta(days=max(1, int(args.days)))
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    if not symbols:
        raise ValueError("No symbols provided")

    print(f"Downloading Binance 1s klines from {start_dt.isoformat()} to {end_dt.isoformat()}")
    for symbol in symbols:
        print(f"Starting {symbol}...")
        out_path, rows = download_symbol(
            symbol,
            start_dt=start_dt,
            end_dt=end_dt,
            out_dir=args.out_dir,
            sleep_s=float(args.sleep),
            overwrite=bool(args.overwrite),
        )
        print(f"Saved {rows:,} rows to {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
