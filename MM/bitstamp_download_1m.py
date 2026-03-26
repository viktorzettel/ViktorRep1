#!/usr/bin/env python3
"""
Download 1m OHLC data from Bitstamp in batches.

Usage:
  python bitstamp_download_1m.py --months 6 --out btcusd_1m_6m.csv
  python bitstamp_download_1m.py --start 2025-08-01 --end 2026-02-01 --out btcusd_1m.csv
"""

import argparse
import csv
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx


BITSTAMP_REST = "https://www.bitstamp.net/api/v2"


def parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def subtract_months(dt: datetime, months: int) -> datetime:
    # Manual month subtraction to avoid external deps.
    year = dt.year
    month = dt.month - months
    while month <= 0:
        month += 12
        year -= 1
    # Clamp day to last day of target month.
    day = min(dt.day, [31, 29 if year % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return dt.replace(year=year, month=month, day=day)


def fetch_ohlc(start_ts: int, end_ts: int, limit: int = 1000):
    params = {
        "step": 60,
        "limit": limit,
        "start": start_ts,
        "end": end_ts,
    }
    resp = httpx.get(f"{BITSTAMP_REST}/ohlc/btcusd/", params=params, timeout=20.0)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", {}).get("ohlc", [])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=6, help="Lookback months (approx).")
    parser.add_argument("--start", type=str, default="", help="Start date (YYYY-MM-DD) in UTC.")
    parser.add_argument("--end", type=str, default="", help="End date (YYYY-MM-DD) in UTC.")
    parser.add_argument("--out", type=str, default="btcusd_1m_6m.csv", help="Output CSV path.")
    parser.add_argument("--sleep", type=float, default=0.2, help="Sleep seconds between requests.")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    if args.end:
        end_dt = parse_date(args.end)
    else:
        end_dt = now
    if args.start:
        start_dt = parse_date(args.start)
    else:
        start_dt = subtract_months(end_dt, args.months)

    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    print(f"Downloading Bitstamp 1m OHLC from {start_dt} to {end_dt}")
    limit = 1000
    step = 60
    chunk = limit * step

    rows_by_ts = {}
    cursor_end = end_ts
    while cursor_end > start_ts:
        cursor_start = max(start_ts, cursor_end - chunk)
        ohlc = fetch_ohlc(cursor_start, cursor_end, limit=limit)
        for r in ohlc:
            ts = int(r["timestamp"])
            rows_by_ts[ts] = {
                "timestamp": ts,
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"],
            }
        cursor_end = cursor_start - 1
        time.sleep(args.sleep)

    # Sort and write
    rows = [rows_by_ts[k] for k in sorted(rows_by_ts.keys())]
    print(f"Fetched {len(rows)} candles.")

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["open_time", "open_time_iso", "open", "high", "low", "close", "volume"])
        for r in rows:
            ts = r["timestamp"]
            iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            w.writerow([ts, iso, r["open"], r["high"], r["low"], r["close"], r["volume"]])

    print(f"Saved to {args.out}")


if __name__ == "__main__":
    main()
