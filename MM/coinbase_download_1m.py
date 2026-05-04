#!/usr/bin/env python3
"""
Download 1-minute Coinbase Exchange candles in batches.

Usage:
  python3 coinbase_download_1m.py --product ETH-USD --months 6 --out ethusd_1m_coinbase_6m.csv
  python3 coinbase_download_1m.py --product XRP-USD --start 2025-10-01 --end 2026-04-01 --out xrpusd_1m_coinbase.csv
"""

from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime, timedelta, timezone

import httpx


COINBASE_REST = "https://api.exchange.coinbase.com"
GRANULARITY_S = 60
MAX_CANDLES = 300


def parse_date(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def subtract_months(dt: datetime, months: int) -> datetime:
    year = dt.year
    month = dt.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(dt.day, [31, 29 if year % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return dt.replace(year=year, month=month, day=day)


def fetch_candles(product: str, start_dt: datetime, end_dt: datetime) -> list[list[float]]:
    params = {
        "granularity": str(GRANULARITY_S),
        "start": start_dt.isoformat().replace("+00:00", "Z"),
        "end": end_dt.isoformat().replace("+00:00", "Z"),
    }
    headers = {"User-Agent": "MM/coinbase-download-1m"}
    response = httpx.get(
        f"{COINBASE_REST}/products/{product}/candles",
        params=params,
        headers=headers,
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected response payload: {payload!r}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Download Coinbase Exchange 1-minute candles")
    parser.add_argument("--product", required=True, help="Coinbase product id, e.g. ETH-USD")
    parser.add_argument("--months", type=int, default=6, help="Approximate lookback months if --start omitted")
    parser.add_argument("--start", default="", help="UTC start date/time, e.g. 2025-10-01 or 2025-10-01T12:00:00")
    parser.add_argument("--end", default="", help="UTC end date/time; default now")
    parser.add_argument("--out", required=True, help="Output CSV path")
    parser.add_argument("--sleep", type=float, default=0.15, help="Sleep between requests")
    args = parser.parse_args()

    end_dt = parse_date(args.end) if args.end else datetime.now(timezone.utc)
    start_dt = parse_date(args.start) if args.start else subtract_months(end_dt, max(1, int(args.months)))

    chunk_seconds = MAX_CANDLES * GRANULARITY_S
    cursor_start = start_dt
    rows_by_ts: dict[int, dict[str, object]] = {}

    print(f"Downloading {args.product} 1m candles from {start_dt.isoformat()} to {end_dt.isoformat()}")

    while cursor_start < end_dt:
        cursor_end = min(end_dt, cursor_start + timedelta(seconds=chunk_seconds))
        candles = fetch_candles(args.product, cursor_start, cursor_end)
        for item in candles:
            if len(item) < 6:
                continue
            ts = int(item[0])
            rows_by_ts[ts] = {
                "open_time": ts,
                "open_time_iso": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                "low": float(item[1]),
                "high": float(item[2]),
                "open": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
            }
        cursor_start = cursor_end
        time.sleep(max(0.0, float(args.sleep)))

    rows = [rows_by_ts[key] for key in sorted(rows_by_ts)]
    print(f"Fetched {len(rows)} candles")

    with open(args.out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["open_time", "open_time_iso", "open", "high", "low", "close", "volume"])
        for row in rows:
            writer.writerow(
                [
                    row["open_time"],
                    row["open_time_iso"],
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["volume"],
                ]
            )

    print(f"Saved to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
