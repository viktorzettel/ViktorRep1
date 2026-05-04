#!/usr/bin/env python3
"""
Lightweight XRP source-alignment logger.

Records Coinbase Advanced XRP-USD and Polymarket RTDS Chainlink xrp/usd side by
side so we can quantify source basis and bucket-open strike differences without
running the full capture stack.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import json
import math
import statistics
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

import websockets


POLYMARKET_WS = "wss://ws-live-data.polymarket.com"
COINBASE_ADVANCED_WS = "wss://advanced-trade-ws.coinbase.com"
POLY_SYMBOL = "xrp/usd"
COINBASE_PRODUCT = "XRP-USD"
BUCKET_SECONDS = 300
DEFAULT_CLOSE_OFFSET_S = 1.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture XRP Coinbase Advanced vs Polymarket Chainlink basis")
    parser.add_argument("--runtime-seconds", type=float, default=3600.0)
    parser.add_argument("--sample-seconds", type=float, default=1.0)
    parser.add_argument("--output-dir", default="data/source_alignment")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--close-offset-seconds", type=float, default=DEFAULT_CLOSE_OFFSET_S)
    parser.add_argument("--spike-threshold", type=float, default=0.0003)
    parser.add_argument(
        "--browser-poly-port",
        type=int,
        default=8074,
        help="Serve a browser helper on this port for Polymarket RTDS ticks; set 0 to disable",
    )
    return parser.parse_args()


def utc_iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def safe_float(value: Any, digits: int = 8) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return float(f"{number:.{digits}f}")


def percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = (len(ordered) - 1) * pct
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - idx) + ordered[hi] * (idx - lo)


class FeedState:
    def __init__(self) -> None:
        self.price: Optional[float] = None
        self.ts: Optional[float] = None
        self.ticks: deque[tuple[float, float]] = deque()
        self._lock = threading.Lock()

    def update(self, ts: float, price: float) -> None:
        if price <= 0.0:
            return
        with self._lock:
            if self.ts is not None and ts + 1e-6 < self.ts:
                return
            self.price = price
            self.ts = ts
            self.ticks.append((ts, price))
            cutoff = time.time() - 900.0
            while self.ticks and self.ticks[0][0] < cutoff:
                self.ticks.popleft()

    def latest(self) -> tuple[Optional[float], Optional[float]]:
        with self._lock:
            return self.price, self.ts

    def price_at_boundary(self, boundary_ts: float) -> Optional[float]:
        with self._lock:
            ticks = list(self.ticks)
            latest_price = self.price
        for tick_ts, price in reversed(ticks):
            if tick_ts <= boundary_ts and boundary_ts - tick_ts <= 2.0:
                return price
        for tick_ts, price in ticks:
            if tick_ts >= boundary_ts and tick_ts - boundary_ts <= 1.0:
                return price
        return latest_price


BROWSER_HELPER_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>XRP Polymarket RTDS Helper</title>
  <style>
    body { margin: 0; background: #101318; color: #eef2f7; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    main { max-width: 620px; margin: 0 auto; padding: 24px; }
    .panel { border: 1px solid #29313d; border-radius: 8px; padding: 18px; background: #171c24; }
    .price { font-size: 44px; font-weight: 800; margin: 10px 0; color: #43f2a0; }
    .muted { color: #98a4b5; }
    .bad { color: #ff7676; }
  </style>
</head>
<body>
<main>
  <div class="panel">
    <div class="muted">Browser Polymarket RTDS helper: xrp/usd</div>
    <div id="price" class="price">connecting...</div>
    <div id="status" class="muted">Open this page while the capture command runs.</div>
  </div>
</main>
<script>
  const priceEl = document.getElementById('price');
  const statusEl = document.getElementById('status');
  let ws = null;
  let reconnectTimer = null;
  async function pushTick(price) {
    try {
      await fetch('/api/poly_tick', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        cache: 'no-store',
        keepalive: true,
        body: JSON.stringify({price, ts: Date.now() / 1000})
      });
    } catch (_) {}
  }
  function connect() {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    ws = new WebSocket('wss://ws-live-data.polymarket.com');
    let pingTimer = null;
    ws.addEventListener('open', () => {
      const subscriptions = ['xrp/usd', 'XRP/USD'].map(symbol => ({
        topic: 'crypto_prices_chainlink',
        type: '*',
        filters: `{\\"symbol\\":\\"${symbol}\\"}`
      }));
      ws.send(JSON.stringify({action: 'subscribe', subscriptions}));
      pingTimer = setInterval(() => { try { ws.send('PING'); } catch (_) {} }, 5000);
      statusEl.textContent = 'connected; forwarding ticks to logger';
      statusEl.className = 'muted';
    });
    ws.addEventListener('message', event => {
      try {
        const data = JSON.parse(event.data);
        if (data.topic !== 'crypto_prices_chainlink' || !data.payload || !data.payload.value) return;
        const sym = String(data.payload.symbol || data.payload.asset || '').toLowerCase();
        if (sym !== 'xrp/usd') return;
        const price = parseFloat(data.payload.value);
        if (Number.isNaN(price)) return;
        priceEl.textContent = price.toFixed(5);
        pushTick(price);
      } catch (_) {}
    });
    ws.addEventListener('close', () => {
      if (pingTimer) clearInterval(pingTimer);
      statusEl.textContent = 'disconnected; reconnecting...';
      statusEl.className = 'bad';
      reconnectTimer = setTimeout(connect, 1000);
    });
    ws.addEventListener('error', () => { try { ws.close(); } catch (_) {} });
  }
  connect();
</script>
</body>
</html>
"""


def start_browser_poly_helper(state: FeedState, port: int) -> Optional[ThreadingHTTPServer]:
    if port <= 0:
        return None

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path in {"/", "/index.html"}:
                body = BROWSER_HELPER_HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/poly_tick":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(max(0, length)).decode("utf-8"))
                price = float(payload.get("price"))
                ts = float(payload.get("ts", time.time()))
            except Exception:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            state.update(ts, price)
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, name="poly-browser-helper", daemon=True)
    thread.start()
    return server


async def run_polymarket_feed(state: FeedState, stop: asyncio.Event) -> None:
    backoff = 1.0
    while not stop.is_set():
        try:
            async with websockets.connect(
                POLYMARKET_WS,
                additional_headers={"Origin": "https://polymarket.com", "User-Agent": "Mozilla/5.0"},
                ping_interval=20.0,
                ping_timeout=20.0,
                close_timeout=5.0,
                max_size=2_000_000,
            ) as ws:
                subscribe = {
                    "action": "subscribe",
                    "subscriptions": [
                        {
                            "topic": "crypto_prices_chainlink",
                            "type": "*",
                            "filters": json.dumps({"symbol": POLY_SYMBOL}),
                        },
                        {
                            "topic": "crypto_prices_chainlink",
                            "type": "*",
                            "filters": json.dumps({"symbol": POLY_SYMBOL.upper()}),
                        },
                    ],
                }
                await ws.send(json.dumps(subscribe))
                backoff = 1.0
                async for raw in ws:
                    if stop.is_set():
                        break
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if msg.get("topic") != "crypto_prices_chainlink":
                        continue
                    payload = msg.get("payload") or {}
                    symbol = str(payload.get("symbol") or payload.get("asset") or "").lower()
                    if symbol != POLY_SYMBOL:
                        continue
                    try:
                        price = float(payload.get("value"))
                    except Exception:
                        continue
                    ts_ms = payload.get("timestamp") or payload.get("updatedAt")
                    ts = float(ts_ms) / 1000.0 if ts_ms is not None else time.time()
                    state.update(ts, price)
        except Exception:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 10.0)


async def run_coinbase_feed(state: FeedState, stop: asyncio.Event) -> None:
    backoff = 1.0
    while not stop.is_set():
        try:
            async with websockets.connect(
                COINBASE_ADVANCED_WS,
                ping_interval=20.0,
                ping_timeout=20.0,
                close_timeout=5.0,
                max_size=2_000_000,
            ) as ws:
                await ws.send(json.dumps({"type": "subscribe", "product_ids": [COINBASE_PRODUCT], "channel": "ticker"}))
                await ws.send(
                    json.dumps({"type": "subscribe", "product_ids": [COINBASE_PRODUCT], "channel": "heartbeats"})
                )
                backoff = 1.0
                async for raw in ws:
                    if stop.is_set():
                        break
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if msg.get("channel") != "ticker":
                        continue
                    for event in msg.get("events") or []:
                        for ticker in event.get("tickers") or []:
                            if str(ticker.get("product_id") or "").upper() != COINBASE_PRODUCT:
                                continue
                            price_raw = ticker.get("price") or ticker.get("last_price")
                            if price_raw is None:
                                continue
                            try:
                                price = float(price_raw)
                            except Exception:
                                continue
                            ts = time.time()
                            ts_str = ticker.get("time") or msg.get("timestamp")
                            if isinstance(ts_str, str):
                                try:
                                    ts = dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                                except Exception:
                                    ts = time.time()
                            state.update(ts, price)
        except Exception:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 10.0)


def bucket_bounds(now_ts: float, close_offset_s: float) -> tuple[int, float, float]:
    shifted_now = now_ts + close_offset_s
    bucket_start = (int(shifted_now) // BUCKET_SECONDS) * BUCKET_SECONDS
    bucket_end = bucket_start + BUCKET_SECONDS - close_offset_s
    return bucket_start, bucket_end, max(0.0, bucket_end - now_ts)


def build_summary(rows: list[dict[str, Any]], spike_threshold: float, runtime_seconds: float) -> dict[str, Any]:
    basis_values = [abs(float(row["basis"])) for row in rows if row.get("basis") not in (None, "")]
    final30_basis = [
        abs(float(row["basis"]))
        for row in rows
        if row.get("basis") not in (None, "") and float(row.get("time_left_s") or 999.0) <= 30.0
    ]
    strike_basis = [
        abs(float(row["strike_basis"]))
        for row in rows
        if row.get("strike_basis") not in (None, "")
    ]
    spike_runs: list[float] = []
    active_start: Optional[float] = None
    last_ts: Optional[float] = None
    for row in rows:
        ts = float(row["captured_at_ts"])
        basis = row.get("basis")
        is_spike = basis not in (None, "") and abs(float(basis)) >= spike_threshold
        if is_spike and active_start is None:
            active_start = ts
        if not is_spike and active_start is not None:
            spike_runs.append(max(0.0, (last_ts or ts) - active_start))
            active_start = None
        last_ts = ts
    if active_start is not None and last_ts is not None:
        spike_runs.append(max(0.0, last_ts - active_start))

    return {
        "rows": len(rows),
        "runtime_seconds": safe_float(runtime_seconds, 3),
        "basis_abs": {
            "median": safe_float(statistics.median(basis_values), 8) if basis_values else None,
            "p90": safe_float(percentile(basis_values, 0.90), 8),
            "p95": safe_float(percentile(basis_values, 0.95), 8),
            "p99": safe_float(percentile(basis_values, 0.99), 8),
            "max": safe_float(max(basis_values), 8) if basis_values else None,
        },
        "final30_basis_abs": {
            "median": safe_float(statistics.median(final30_basis), 8) if final30_basis else None,
            "p95": safe_float(percentile(final30_basis, 0.95), 8),
            "max": safe_float(max(final30_basis), 8) if final30_basis else None,
            "samples": len(final30_basis),
        },
        "strike_basis_abs": {
            "median": safe_float(statistics.median(strike_basis), 8) if strike_basis else None,
            "max": safe_float(max(strike_basis), 8) if strike_basis else None,
        },
        "spikes": {
            "threshold": spike_threshold,
            "count": len(spike_runs),
            "median_duration_s": safe_float(statistics.median(spike_runs), 3) if spike_runs else 0.0,
            "max_duration_s": safe_float(max(spike_runs), 3) if spike_runs else 0.0,
        },
    }


async def main_async(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    session_id = args.session_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    csv_path = output_dir / f"xrp_alignment_{session_id}.csv"
    summary_path = output_dir / f"xrp_alignment_{session_id}_summary.json"

    poly = FeedState()
    coinbase = FeedState()
    stop = asyncio.Event()
    browser_helper = start_browser_poly_helper(poly, int(args.browser_poly_port))
    if browser_helper is not None:
        print(f"Open browser helper for Polymarket RTDS ticks: http://127.0.0.1:{int(args.browser_poly_port)}")
    tasks = [asyncio.create_task(run_coinbase_feed(coinbase, stop))]
    if browser_helper is None:
        tasks.append(asyncio.create_task(run_polymarket_feed(poly, stop)))

    rows: list[dict[str, Any]] = []
    started = time.time()
    fieldnames = [
        "captured_at_ts",
        "iso_utc",
        "poly_chainlink_price",
        "poly_age_s",
        "coinbase_advanced_price",
        "coinbase_age_s",
        "basis",
        "abs_basis",
        "basis_bps_poly",
        "bucket_start",
        "bucket_end",
        "time_left_s",
        "poly_strike",
        "coinbase_strike",
        "strike_basis",
        "poly_dist_to_strike",
        "coinbase_dist_to_strike",
        "final_30s",
        "final_10s",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        while time.time() - started < float(args.runtime_seconds):
            now_ts = time.time()
            bucket_start, bucket_end, time_left_s = bucket_bounds(now_ts, float(args.close_offset_seconds))
            poly_strike = poly.price_at_boundary(float(bucket_start) - float(args.close_offset_seconds))
            coinbase_strike = coinbase.price_at_boundary(float(bucket_start) - float(args.close_offset_seconds))
            poly_price, poly_ts = poly.latest()
            coinbase_price, coinbase_ts = coinbase.latest()
            basis = None
            basis_bps = None
            if poly_price is not None and coinbase_price is not None:
                basis = coinbase_price - poly_price
                if abs(poly_price) > 1e-12:
                    basis_bps = basis / poly_price * 10000.0
            row = {
                "captured_at_ts": safe_float(now_ts, 3),
                "iso_utc": utc_iso(now_ts),
                "poly_chainlink_price": safe_float(poly_price, 8),
                "poly_age_s": None if poly_ts is None else safe_float(now_ts - poly_ts, 3),
                "coinbase_advanced_price": safe_float(coinbase_price, 8),
                "coinbase_age_s": None if coinbase_ts is None else safe_float(now_ts - coinbase_ts, 3),
                "basis": safe_float(basis, 8),
                "abs_basis": None if basis is None else safe_float(abs(basis), 8),
                "basis_bps_poly": safe_float(basis_bps, 4),
                "bucket_start": bucket_start,
                "bucket_end": safe_float(bucket_end, 3),
                "time_left_s": safe_float(time_left_s, 3),
                "poly_strike": safe_float(poly_strike, 8),
                "coinbase_strike": safe_float(coinbase_strike, 8),
                "strike_basis": None
                if poly_strike is None or coinbase_strike is None
                else safe_float(coinbase_strike - poly_strike, 8),
                "poly_dist_to_strike": None
                if poly_price is None or poly_strike is None
                else safe_float(poly_price - poly_strike, 8),
                "coinbase_dist_to_strike": None
                if coinbase_price is None or coinbase_strike is None
                else safe_float(coinbase_price - coinbase_strike, 8),
                "final_30s": time_left_s <= 30.0,
                "final_10s": time_left_s <= 10.0,
            }
            writer.writerow(row)
            handle.flush()
            rows.append(row)
            await asyncio.sleep(max(0.2, float(args.sample_seconds)))

    stop.set()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if browser_helper is not None:
        browser_helper.shutdown()
        browser_helper.server_close()

    runtime = time.time() - started
    summary = build_summary(rows, float(args.spike_threshold), runtime)
    summary["session_id"] = session_id
    summary["csv_path"] = str(csv_path)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"Wrote {csv_path}")
    print(f"Wrote {summary_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
