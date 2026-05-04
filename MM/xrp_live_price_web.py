#!/usr/bin/env python3
"""
Tiny live XRP price audit page.

This is intentionally separate from the Kou engine. It shows only:
- Coinbase Advanced XRP-USD
- Polymarket/Chainlink XRP/USD from the browser RTDS stream
- live basis: Coinbase minus Polymarket

The browser page opens the Polymarket websocket and forwards ticks back to this
local server. Coinbase Advanced runs in Python.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import math
import signal
import threading
import time
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

import websockets


COINBASE_ADVANCED_WS = "wss://advanced-trade-ws.coinbase.com"
COINBASE_PRODUCT = "XRP-USD"
COINBASE_SPOT_URL = f"https://api.coinbase.com/v2/prices/{COINBASE_PRODUCT}/spot"
COINBASE_POLL_SECONDS = 1.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve a tiny XRP Coinbase vs Polymarket price page")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8074)
    return parser.parse_args()


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


def iso_from_ts(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


class PriceState:
    def __init__(self) -> None:
        self.price: Optional[float] = None
        self.ts: Optional[float] = None
        self.status = "waiting"
        self.error: Optional[str] = None
        self._lock = threading.Lock()

    def update(self, price: float, ts: Optional[float] = None, status: str = "live") -> None:
        if price <= 0.0:
            return
        now_ts = time.time()
        tick_ts = now_ts if ts is None else float(ts)
        with self._lock:
            if self.ts is not None and tick_ts + 1e-6 < self.ts:
                tick_ts = now_ts
            self.price = price
            self.ts = tick_ts
            self.status = status
            self.error = None

    def mark(self, status: str, error: Optional[str] = None) -> None:
        with self._lock:
            self.status = status
            self.error = error

    def snapshot(self) -> dict[str, Any]:
        now_ts = time.time()
        with self._lock:
            price = self.price
            ts = self.ts
            status = self.status
            error = self.error
        return {
            "price": safe_float(price, 8),
            "price_4dp": None if price is None else f"{price:.4f}",
            "price_5dp": None if price is None else f"{price:.5f}",
            "ts": ts,
            "iso": iso_from_ts(ts),
            "age_s": None if ts is None else safe_float(now_ts - ts, 3),
            "status": status,
            "error": error,
        }


class AppState:
    def __init__(self) -> None:
        self.coinbase = PriceState()
        self.poly = PriceState()

    def snapshot(self) -> dict[str, Any]:
        coinbase = self.coinbase.snapshot()
        poly = self.poly.snapshot()
        coinbase_price = coinbase["price"]
        poly_price = poly["price"]
        basis = None
        if coinbase_price is not None and poly_price is not None:
            basis = float(coinbase_price) - float(poly_price)
        return {
            "now": time.time(),
            "now_iso": iso_from_ts(time.time()),
            "coinbase_advanced": coinbase,
            "polymarket_chainlink": poly,
            "basis_coinbase_minus_poly": safe_float(basis, 8),
            "abs_basis": safe_float(abs(basis), 8) if basis is not None else None,
        }


APP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>XRP Live Price Audit</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0d1117;
      --panel: #161b22;
      --panel-2: #101820;
      --line: #30363d;
      --text: #f0f6fc;
      --muted: #8b949e;
      --green: #3fb950;
      --yellow: #d29922;
      --red: #f85149;
      --blue: #58a6ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      width: min(980px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0;
    }
    header {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 16px;
    }
    h1 {
      margin: 0;
      font-size: clamp(24px, 3vw, 38px);
      letter-spacing: 0;
      line-height: 1.05;
    }
    .clock {
      color: var(--muted);
      font-size: 13px;
      text-align: right;
      white-space: nowrap;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }
    .card, .basis {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 18px;
    }
    .label {
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }
    .price {
      font-variant-numeric: tabular-nums;
      font-size: clamp(40px, 8vw, 76px);
      line-height: 1;
      font-weight: 800;
      letter-spacing: 0;
      overflow-wrap: anywhere;
    }
    .price.live { color: var(--green); }
    .price.waiting { color: var(--yellow); }
    .meta {
      margin-top: 12px;
      display: grid;
      grid-template-columns: 80px 1fr;
      gap: 6px 10px;
      color: var(--muted);
      font-size: 13px;
      font-variant-numeric: tabular-nums;
    }
    .meta strong { color: var(--text); font-weight: 600; }
    .basis {
      margin-top: 14px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: center;
      background: var(--panel-2);
    }
    .basis-value {
      font-size: clamp(32px, 5vw, 58px);
      font-weight: 800;
      font-variant-numeric: tabular-nums;
      color: var(--blue);
      letter-spacing: 0;
    }
    .status-line {
      margin-top: 14px;
      color: var(--muted);
      font-size: 13px;
      min-height: 18px;
    }
    .bad { color: var(--red); }
    @media (max-width: 720px) {
      header { display: block; }
      .clock { text-align: left; margin-top: 8px; }
      .grid { grid-template-columns: 1fr; }
      .basis { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
<main>
  <header>
    <h1>XRP Live Price Audit</h1>
    <div class="clock" id="clock">loading...</div>
  </header>

  <section class="grid">
    <article class="card">
      <div class="label">Coinbase Advanced XRP-USD</div>
      <div id="coinbasePrice" class="price waiting">waiting</div>
      <div class="meta">
        <span>Status</span><strong id="coinbaseStatus">waiting</strong>
        <span>Age</span><strong id="coinbaseAge">-</strong>
        <span>Tick</span><strong id="coinbaseTick">-</strong>
      </div>
    </article>

    <article class="card">
      <div class="label">Polymarket Chainlink XRP/USD</div>
      <div id="polyPrice" class="price waiting">waiting</div>
      <div class="meta">
        <span>Status</span><strong id="polyStatus">waiting</strong>
        <span>Age</span><strong id="polyAge">-</strong>
        <span>Tick</span><strong id="polyTick">-</strong>
      </div>
    </article>
  </section>

  <section class="basis">
    <div>
      <div class="label">Basis, Coinbase minus Polymarket</div>
      <div class="status-line">Screen refresh and Coinbase fallback poll run once per second. Keep this page open for the Polymarket browser stream.</div>
    </div>
    <div id="basis" class="basis-value">-</div>
  </section>

  <div id="browserStatus" class="status-line">Polymarket browser websocket connecting...</div>
</main>

<script>
  const $ = id => document.getElementById(id);
  let polyWS = null;
  let reconnectTimer = null;

  function fmtAge(age) {
    if (age === null || age === undefined) return '-';
    return `${Number(age).toFixed(1)}s`;
  }

  function fmtTick(iso) {
    if (!iso) return '-';
    return iso.replace('T', ' ').replace('Z', ' UTC');
  }

  function setPrice(prefix, feed) {
    const priceEl = $(`${prefix}Price`);
    const statusEl = $(`${prefix}Status`);
    const ageEl = $(`${prefix}Age`);
    const tickEl = $(`${prefix}Tick`);
    const priceText = feed.price_4dp || 'waiting';
    priceEl.textContent = priceText;
    priceEl.className = `price ${feed.price === null ? 'waiting' : 'live'}`;
    statusEl.textContent = feed.error ? `${feed.status}: ${feed.error}` : feed.status;
    statusEl.className = feed.error ? 'bad' : '';
    ageEl.textContent = fmtAge(feed.age_s);
    tickEl.textContent = fmtTick(feed.iso);
  }

  async function pollPrices() {
    try {
      const response = await fetch('/api/prices', {cache: 'no-store'});
      const data = await response.json();
      $('clock').textContent = `Local refresh: ${new Date().toLocaleTimeString()} | Server UTC: ${fmtTick(data.now_iso)}`;
      setPrice('coinbase', data.coinbase_advanced);
      setPrice('poly', data.polymarket_chainlink);
      $('basis').textContent = data.basis_coinbase_minus_poly === null
        ? '-'
        : Number(data.basis_coinbase_minus_poly).toFixed(5);
    } catch (error) {
      $('clock').textContent = `server polling failed: ${error}`;
    }
  }

  async function pushPolyTick(price, ts) {
    try {
      await fetch('/api/poly_tick', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        cache: 'no-store',
        keepalive: true,
        body: JSON.stringify({price, ts})
      });
    } catch (_) {}
  }

  function connectPolymarket() {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    let pingTimer = null;
    polyWS = new WebSocket('wss://ws-live-data.polymarket.com');

    polyWS.addEventListener('open', () => {
      const subscriptions = ['xrp/usd', 'XRP/USD'].map(symbol => ({
        topic: 'crypto_prices_chainlink',
        type: '*',
        filters: `{\\"symbol\\":\\"${symbol}\\"}`
      }));
      polyWS.send(JSON.stringify({action: 'subscribe', subscriptions}));
      pingTimer = setInterval(() => {
        try { polyWS.send('PING'); } catch (_) {}
      }, 5000);
      $('browserStatus').textContent = 'Polymarket browser websocket connected.';
      $('browserStatus').className = 'status-line';
    });

    polyWS.addEventListener('message', event => {
      try {
        const data = JSON.parse(event.data);
        if (data.topic !== 'crypto_prices_chainlink' || !data.payload || !data.payload.value) return;
        const symbol = String(data.payload.symbol || data.payload.asset || '').toLowerCase();
        if (symbol !== 'xrp/usd') return;
        const price = Number(data.payload.value);
        if (!Number.isFinite(price) || price <= 0) return;
        pushPolyTick(price, Date.now() / 1000);
      } catch (_) {}
    });

    polyWS.addEventListener('close', () => {
      if (pingTimer) clearInterval(pingTimer);
      $('browserStatus').textContent = 'Polymarket browser websocket disconnected; reconnecting...';
      $('browserStatus').className = 'status-line bad';
      reconnectTimer = setTimeout(connectPolymarket, 1000);
    });

    polyWS.addEventListener('error', () => {
      try { polyWS.close(); } catch (_) {}
    });
  }

  connectPolymarket();
  pollPrices();
  setInterval(pollPrices, 1000);
</script>
</body>
</html>
"""


async def run_coinbase_feed(state: PriceState, stop: asyncio.Event) -> None:
    backoff = 1.0
    while not stop.is_set():
        try:
            state.mark("connecting")
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
                state.mark("connected")
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
                            state.update(price, ts=ts)
        except Exception as exc:
            state.mark("reconnecting", str(exc))
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2.0, 10.0)


def fetch_coinbase_spot_price() -> Optional[float]:
    request = urllib.request.Request(COINBASE_SPOT_URL, headers={"User-Agent": "xrp-live-price-web/1.0"})
    with urllib.request.urlopen(request, timeout=5) as response:
        data = json.loads(response.read().decode("utf-8"))
    amount = data.get("data", {}).get("amount")
    return float(amount) if amount is not None else None


async def run_coinbase_poll_feed(state: PriceState, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            price = await asyncio.to_thread(fetch_coinbase_spot_price)
            if price is not None and price > 0.0:
                state.update(price, ts=time.time(), status="live/poll")
        except Exception as exc:
            state.mark("poll retry", str(exc))
        try:
            await asyncio.wait_for(stop.wait(), timeout=COINBASE_POLL_SECONDS)
        except asyncio.TimeoutError:
            pass


def start_coinbase_thread(app: AppState) -> tuple[asyncio.AbstractEventLoop, asyncio.Event, threading.Thread]:
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    holder: dict[str, asyncio.Event] = {}

    def runner() -> None:
        asyncio.set_event_loop(loop)
        stop_event = asyncio.Event()
        holder["stop_event"] = stop_event
        ready.set()
        loop.create_task(run_coinbase_feed(app.coinbase, stop_event))
        loop.create_task(run_coinbase_poll_feed(app.coinbase, stop_event))
        loop.run_forever()

    thread = threading.Thread(target=runner, name="coinbase-advanced-xrp", daemon=True)
    thread.start()
    ready.wait(timeout=5.0)
    return loop, holder["stop_event"], thread


def make_handler(app: AppState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path in {"/", "/index.html"}:
                body = APP_HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/prices":
                body = json.dumps(app.snapshot(), separators=(",", ":")).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
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
            app.poly.update(price, ts=ts)
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def main() -> None:
    args = parse_args()
    app = AppState()
    loop, stop_event, _thread = start_coinbase_thread(app)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(app))

    def stop(*_args: Any) -> None:
        threading.Thread(target=server.shutdown, name="shutdown-http", daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    print(f"XRP live price page: http://{args.host}:{args.port}")
    print("Open the page and keep it open for the Polymarket/Chainlink browser stream.")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        loop.call_soon_threadsafe(stop_event.set)
        loop.call_soon_threadsafe(loop.stop)


if __name__ == "__main__":
    main()
