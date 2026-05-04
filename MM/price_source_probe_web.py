#!/usr/bin/env python3
"""
Local web page for comparing live crypto price sources against Polymarket.

This probe intentionally keeps the live Polymarket feeds in the browser, where
the repo already has a working integration pattern for RTDS sockets.

Run:
    python3 price_source_probe_web.py
Open:
    http://127.0.0.1:8072
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Price Source Probe</title>
  <style>
    :root {
      --bg: #f4f0e8;
      --ink: #18202a;
      --muted: #64707d;
      --panel: rgba(255, 255, 255, 0.78);
      --border: rgba(24, 32, 42, 0.10);
      --good: #0f766e;
      --warn: #b45309;
      --bad: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(15,118,110,0.08), transparent 28%),
        linear-gradient(180deg, #f6f3ec 0%, #efe9df 100%);
    }
    .wrap {
      max-width: 1180px;
      margin: 0 auto;
      padding: 18px 16px 28px;
    }
    .top {
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 16px;
      margin-bottom: 16px;
    }
    h1 {
      margin: 0;
      font-size: 34px;
      line-height: 1;
      letter-spacing: -0.04em;
    }
    .meta {
      color: var(--muted);
      font-size: 13px;
      margin-top: 6px;
    }
    .pill {
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(255,255,255,0.58);
      border: 1px solid var(--border);
      font-size: 12px;
      color: var(--muted);
      white-space: nowrap;
    }
    .grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 14px;
    }
    .card {
      padding: 16px;
      border-radius: 24px;
      background: var(--panel);
      border: 1px solid var(--border);
      box-shadow: 0 14px 34px rgba(24, 32, 42, 0.08);
    }
    .card-head {
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 12px;
      margin-bottom: 14px;
    }
    .asset {
      font-size: 28px;
      font-weight: 800;
      letter-spacing: -0.04em;
      line-height: 1;
    }
    .sub {
      color: var(--muted);
      font-size: 13px;
      margin-top: 4px;
    }
    .boundary {
      text-align: right;
      color: var(--muted);
      font-size: 12px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      padding: 11px 10px;
      border-bottom: 1px solid rgba(24, 32, 42, 0.08);
      text-align: left;
      font-size: 14px;
    }
    th {
      color: var(--muted);
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    tr:last-child td { border-bottom: none; }
    .src {
      font-weight: 700;
      letter-spacing: -0.01em;
    }
    .active {
      color: var(--good);
    }
    .empty {
      color: var(--muted);
    }
    .age-good { color: var(--good); }
    .age-warn { color: var(--warn); }
    .age-bad { color: var(--bad); }
    .basis {
      display: inline-block;
      padding: 4px 8px;
      border-radius: 10px;
      background: rgba(24, 32, 42, 0.05);
      font-weight: 700;
    }
    .basis.pos { color: var(--warn); }
    .basis.neg { color: var(--good); }
    .foot {
      margin-top: 12px;
      color: var(--muted);
      font-size: 12px;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <h1>Price Source Probe</h1>
        <div class="meta">Compare strike and live candidates per source before locking the dashboard feed.</div>
      </div>
      <div class="pill" id="topMeta">connecting...</div>
    </div>
    <div class="grid" id="grid"></div>
  </div>
  <script>
    const ASSETS = {
      ETH: { polyChainlink: 'eth/usd', polyBinance: 'ethusdt', binance: 'ethusdt', coinbase: 'ETH-USD' },
      XRP: { polyChainlink: 'xrp/usd', polyBinance: 'xrpusdt', binance: 'xrpusdt', coinbase: 'XRP-USD' },
    };
    const SOURCE_ORDER = ['poly-chainlink', 'poly-binance', 'binance-direct', 'coinbase-usd'];
    const SOURCE_LABELS = {
      'poly-chainlink': 'Polymarket Chainlink',
      'poly-binance': 'Polymarket Binance',
      'binance-direct': 'Binance Direct',
      'coinbase-usd': 'Coinbase USD',
    };
    const CLOSE_OFFSET_S = 1.0;
    const BUCKET_SECONDS = 300;
    const MAX_TICK_AGE_S = 900;
    const state = {};
    const topMeta = document.getElementById('topMeta');
    const grid = document.getElementById('grid');

    for (const asset of Object.keys(ASSETS)) {
      state[asset] = {};
      for (const source of SOURCE_ORDER) {
        state[asset][source] = { price: null, ts: null, ticks: [] };
      }
    }

    function fmtClock(ts) {
      if (ts == null) return '--:--:--';
      return new Date(ts * 1000).toISOString().slice(11, 19);
    }

    function fmtPrice(value) {
      if (value == null) return '-';
      const abs = Math.abs(value);
      if (abs >= 1000) return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      if (abs >= 100) return value.toLocaleString(undefined, { minimumFractionDigits: 3, maximumFractionDigits: 3 });
      if (abs >= 1) return value.toLocaleString(undefined, { minimumFractionDigits: 4, maximumFractionDigits: 4 });
      if (abs >= 0.1) return value.toLocaleString(undefined, { minimumFractionDigits: 5, maximumFractionDigits: 5 });
      if (abs >= 0.01) return value.toLocaleString(undefined, { minimumFractionDigits: 6, maximumFractionDigits: 6 });
      return value.toLocaleString(undefined, { minimumFractionDigits: 8, maximumFractionDigits: 8 });
    }

    function fmtAge(age) {
      if (age == null) return '-';
      return `${age.toFixed(1)}s`;
    }

    function ageClass(age) {
      if (age == null) return 'empty';
      if (age <= 1.5) return 'age-good';
      if (age <= 5) return 'age-warn';
      return 'age-bad';
    }

    function bucketStart(nowTs) {
      return Math.floor((nowTs + CLOSE_OFFSET_S) / BUCKET_SECONDS) * BUCKET_SECONDS;
    }

    function boundaryTs(nowTs) {
      return bucketStart(nowTs) - CLOSE_OFFSET_S;
    }

    function bucketEnd(nowTs) {
      return bucketStart(nowTs) + BUCKET_SECONDS - CLOSE_OFFSET_S;
    }

    function appendTick(asset, source, price, ts) {
      const rec = state[asset][source];
      rec.price = price;
      rec.ts = ts;
      rec.ticks.push([ts, price]);
      const minTs = ts - MAX_TICK_AGE_S;
      while (rec.ticks.length && rec.ticks[0][0] < minTs) rec.ticks.shift();
    }

    function parseAssetFromSymbol(symRaw) {
      const sym = String(symRaw || '').toUpperCase();
      if (!sym) return null;
      if (sym.includes('BTC')) return 'BTC';
      if (sym.includes('ETH')) return 'ETH';
      if (sym.includes('SOL')) return 'SOL';
      if (sym.includes('XRP')) return 'XRP';
      return null;
    }

    function strikeFor(asset, source, nowTs) {
      const boundary = boundaryTs(nowTs);
      const ticks = state[asset][source].ticks;
      for (let i = ticks.length - 1; i >= 0; i -= 1) {
        const [ts, price] = ticks[i];
        if (ts <= boundary) {
          if ((boundary - ts) <= 2.0) return price;
          break;
        }
      }
      for (let i = 0; i < ticks.length; i += 1) {
        const [ts, price] = ticks[i];
        if (ts >= boundary) {
          if ((ts - boundary) <= 1.0) return price;
          break;
        }
      }
      return null;
    }

    function basisBps(a, b) {
      if (a == null || b == null || a <= 0) return null;
      return ((b - a) / a) * 10000;
    }

    function render() {
      const nowTs = Date.now() / 1000;
      topMeta.textContent = `boundary ${fmtClock(boundaryTs(nowTs))} UTC · close ${fmtClock(bucketEnd(nowTs))} UTC · t_left ${Math.max(0, bucketEnd(nowTs) - nowTs).toFixed(1)}s`;
      grid.innerHTML = Object.keys(ASSETS).map(asset => {
        const cl = state[asset]['poly-chainlink'];
        const pb = state[asset]['poly-binance'];
        const bd = state[asset]['binance-direct'];
        const cb = state[asset]['coinbase-usd'];
        const rows = SOURCE_ORDER.map(source => {
          const rec = state[asset][source];
          const age = rec.ts == null ? null : nowTs - rec.ts;
          const strike = strikeFor(asset, source, nowTs);
          return `
            <tr>
              <td class="src ${age != null && age < 2.0 ? 'active' : ''}">${SOURCE_LABELS[source]}</td>
              <td class="${rec.price == null ? 'empty' : ''}">${fmtPrice(rec.price)}</td>
              <td class="${strike == null ? 'empty' : ''}">${fmtPrice(strike)}</td>
              <td class="${ageClass(age)}">${fmtAge(age)}</td>
            </tr>
          `;
        }).join('');
        const clPbBasis = basisBps(cl.price, pb.price);
        const clBdBasis = basisBps(cl.price, bd.price);
        return `
          <section class="card">
            <div class="card-head">
              <div>
                <div class="asset">${asset}</div>
                <div class="sub">Compare live and strike candidates across four sources.</div>
              </div>
              <div class="boundary">
                <div>boundary ${fmtClock(boundaryTs(nowTs))} UTC</div>
                <div>close ${fmtClock(bucketEnd(nowTs))} UTC</div>
              </div>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Live</th>
                  <th>Strike</th>
                  <th>Age</th>
                </tr>
              </thead>
              <tbody>${rows}</tbody>
            </table>
            <div class="foot">
              <span class="basis ${clPbBasis != null && clPbBasis >= 0 ? 'pos' : 'neg'}">poly-binance vs chainlink ${clPbBasis == null ? '-' : (clPbBasis >= 0 ? '+' : '') + clPbBasis.toFixed(1) + 'bps'}</span>
              <span class="basis ${clBdBasis != null && clBdBasis >= 0 ? 'pos' : 'neg'}" style="margin-left:8px;">binance-direct vs chainlink ${clBdBasis == null ? '-' : (clBdBasis >= 0 ? '+' : '') + clBdBasis.toFixed(1) + 'bps'}</span>
            </div>
          </section>
        `;
      }).join('');
    }

    function connectPolymarketChainlink() {
      const ws = new WebSocket('wss://ws-live-data.polymarket.com');
      let pingTimer = null;
      ws.addEventListener('open', () => {
        const subscriptions = Object.entries(ASSETS).flatMap(([asset, cfg]) => ([
          {
            topic: 'crypto_prices_chainlink',
            type: '*',
            filters: `{\\"symbol\\":\\"${cfg.polyChainlink}\\"}`
          },
          {
            topic: 'crypto_prices_chainlink',
            type: '*',
            filters: `{\\"symbol\\":\\"${cfg.polyChainlink.toUpperCase()}\\"}`
          }
        ]));
        ws.send(JSON.stringify({ action: 'subscribe', subscriptions }));
        pingTimer = setInterval(() => { try { ws.send('PING'); } catch (_) {} }, 5000);
      });
      ws.addEventListener('message', event => {
        try {
          const data = JSON.parse(event.data);
          if (data.topic !== 'crypto_prices_chainlink' || !data.payload || !data.payload.value) return;
          const sym = String(data.payload.symbol || data.payload.asset || '');
          const asset = parseAssetFromSymbol(sym);
          const price = parseFloat(data.payload.value);
          if (!asset || Number.isNaN(price)) return;
          appendTick(asset, 'poly-chainlink', price, Date.now() / 1000);
        } catch (_) {}
      });
      ws.addEventListener('close', () => {
        if (pingTimer) clearInterval(pingTimer);
        setTimeout(connectPolymarketChainlink, 2000);
      });
      ws.addEventListener('error', () => { try { ws.close(); } catch (_) {} });
    }

    function connectPolymarketBinance() {
      const ws = new WebSocket('wss://ws-live-data.polymarket.com');
      let pingTimer = null;
      ws.addEventListener('open', () => {
        const subscriptions = Object.entries(ASSETS).flatMap(([asset, cfg]) => ([
          {
            topic: 'crypto_prices',
            type: '*',
            filters: `{\\"symbol\\":\\"${cfg.polyBinance}\\"}`
          },
          {
            topic: 'crypto_prices',
            type: 'update',
            filters: `{\\"symbol\\":\\"${cfg.polyBinance}\\"}`
          },
          {
            topic: 'crypto_prices',
            type: '*',
            filters: `{\\"symbol\\":\\"${cfg.polyBinance.toUpperCase()}\\"}`
          }
        ]));
        ws.send(JSON.stringify({ action: 'subscribe', subscriptions }));
        pingTimer = setInterval(() => { try { ws.send('PING'); } catch (_) {} }, 5000);
      });
      ws.addEventListener('message', event => {
        try {
          const data = JSON.parse(event.data);
          if (data.topic !== 'crypto_prices' || !data.payload || !data.payload.value) return;
          const sym = String(data.payload.symbol || data.payload.asset || '');
          const asset = parseAssetFromSymbol(sym);
          const price = parseFloat(data.payload.value);
          if (!asset || Number.isNaN(price)) return;
          appendTick(asset, 'poly-binance', price, Date.now() / 1000);
        } catch (_) {}
      });
      ws.addEventListener('close', () => {
        if (pingTimer) clearInterval(pingTimer);
        setTimeout(connectPolymarketBinance, 2000);
      });
      ws.addEventListener('error', () => { try { ws.close(); } catch (_) {} });
    }

    function connectBinanceDirect() {
      const streams = Object.values(ASSETS).map(cfg => `${cfg.binance}@trade`);
      const ws = new WebSocket(`wss://stream.binance.com:9443/stream?streams=${streams.join('/')}`);
      ws.addEventListener('message', event => {
        try {
          const msg = JSON.parse(event.data);
          const payload = msg && msg.data ? msg.data : msg;
          const sym = String(payload.s || '').toLowerCase();
          const asset = Object.keys(ASSETS).find(key => ASSETS[key].binance === sym);
          const price = parseFloat(payload.p ?? payload.c);
          const ts = payload.E ? Number(payload.E) / 1000 : Date.now() / 1000;
          if (!asset || Number.isNaN(price)) return;
          appendTick(asset, 'binance-direct', price, ts);
        } catch (_) {}
      });
      ws.addEventListener('close', () => setTimeout(connectBinanceDirect, 2000));
      ws.addEventListener('error', () => { try { ws.close(); } catch (_) {} });
    }

    async function pollCoinbase() {
      try {
        const res = await fetch('/api/coinbase_spot', { cache: 'no-store' });
        const data = await res.json();
        const nowTs = Date.now() / 1000;
        for (const [asset, price] of Object.entries(data)) {
          if (typeof price === 'number' && !Number.isNaN(price)) {
            appendTick(asset, 'coinbase-usd', price, nowTs);
          }
        }
      } catch (_) {}
      setTimeout(pollCoinbase, 1000);
    }

    connectPolymarketChainlink();
    connectPolymarketBinance();
    connectBinanceDirect();
    pollCoinbase();
    render();
    setInterval(render, 250);
  </script>
</body>
</html>
"""


COINBASE_PRODUCTS = {
    "ETH": "ETH-USD",
    "XRP": "XRP-USD",
}


def fetch_coinbase_spot() -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for asset, product in COINBASE_PRODUCTS.items():
        try:
            url = f"https://api.coinbase.com/v2/prices/{urllib.parse.quote(product)}/spot"
            with urllib.request.urlopen(url, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
            out[asset] = float(data["data"]["amount"])
        except Exception:
            out[asset] = None
    return out


class ProbeHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def make_handler() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html"):
                body = HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path == "/api/coinbase_spot":
                payload = json.dumps(fetch_coinbase_spot()).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)
                return

            self.send_error(HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args) -> None:
            return

    return Handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Browser-based price source probe for Polymarket alignment")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8072, help="Bind port (default: 8072)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    server = ProbeHTTPServer((args.host, int(args.port)), make_handler())
    try:
        print(f"Price source probe running at http://{args.host}:{int(args.port)}")
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
