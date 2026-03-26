#!/usr/bin/env python3
"""
XRP 5-minute decision bot — real-time web dashboard.

Combines the Binance price stream + probability engine with an aiohttp
web server that pushes live snapshots to the browser via WebSocket.

Run:
    python3 xrp_5m_dashboard.py
    → open http://localhost:8050
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import ssl
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

import websockets
from aiohttp import web

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

# ── Binance stream ────────────────────────────────────────────────────────────

BINANCE_WS_BASE = "wss://stream.binance.com:9443/ws"


def _round4(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    return float(f"{v:.4f}")


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

    def recent_prices(self, seconds: int = 120, interval: float = 0.5) -> list[dict]:
        """Return subsampled prices for chart rendering (~1 point per `interval` secs)."""
        if not self.history:
            return []
        now_ts = self.history[-1].ts
        cutoff = now_ts - seconds

        out: list[dict] = []
        last_t = 0.0
        for p in self.history:
            if p.ts < cutoff:
                continue
            if p.ts - last_t >= interval:
                out.append({"t": round(p.ts, 3), "p": round(p.price, 5)})
                last_t = p.ts

        # Always include the very latest tick for real-time accuracy
        last = self.history[-1]
        if not out or out[-1]["t"] != round(last.ts, 3):
            out.append({"t": round(last.ts, 3), "p": round(last.price, 5)})

        return out

    def estimate_sigma(
        self, window_seconds: int = 300, subsample_seconds: float = 1.0, min_points: int = 10
    ) -> Optional[float]:
        if len(self.history) < 2:
            return None
        now_ts = self.history[-1].ts
        window_min = now_ts - window_seconds
        points = [p for p in self.history if p.ts >= window_min]
        if len(points) < 2:
            return None

        sampled: list[PricePoint] = [points[0]]
        for p in points[1:]:
            if p.ts - sampled[-1].ts >= subsample_seconds:
                sampled.append(p)
        if len(sampled) < min_points:
            return None

        var_sum = 0.0
        count = 0
        prev = sampled[0]
        for p in sampled[1:]:
            dt = p.ts - prev.ts
            if dt <= 0:
                prev = p
                continue
            r = math.log(p.price / prev.price)
            var_sum += (r * r) / dt
            count += 1
            prev = p

        if count == 0:
            return None
        return math.sqrt(var_sum / count)

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    ssl=_SSL_CTX,
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
                logging.warning("Binance reconnecting: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 10.0)


# ── Decision engine ──────────────────────────────────────────────────────────


class DecisionEngine:
    def __init__(self, *, symbol: str, bucket_seconds: int, sigma_fallback: float) -> None:
        self.symbol = symbol
        self.bucket_seconds = bucket_seconds
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
            logging.info("New bucket: %s → %s", _fmt_ts(bucket_start), _fmt_ts(bucket_start + self.bucket_seconds))

    def _ensure_strike(self, current_price: Optional[float]) -> None:
        if self.strike_price is not None:
            return
        if current_price is None:
            return
        self.strike_price = _round4(current_price)
        logging.info("Strike set: %.4f", self.strike_price)

    def _probability_yes(self, current: float, strike: float, time_left: float) -> float:
        if time_left <= 0:
            return 1.0 if current >= strike else 0.0
        sigma = self.stream.estimate_sigma() or self.sigma_fallback
        if sigma <= 1e-12:
            return 1.0 if current >= strike else 0.0
        d = (math.log(current / strike) - 0.5 * sigma * sigma * time_left) / (
            sigma * math.sqrt(time_left)
        )
        return max(0.0, min(1.0, _normal_cdf(d)))

    def snapshot(self) -> dict:
        now = time.time()
        self._roll_bucket_if_needed(now)
        current_price, _ = self.stream.latest()
        self._ensure_strike(current_price)

        if self.bucket_start is None:
            return {"state": "INIT"}

        expiry = self.bucket_start + self.bucket_seconds
        time_left = max(0.0, expiry - now)

        price4 = _round4(current_price)
        strike4 = _round4(self.strike_price)
        sigma = self.stream.estimate_sigma()

        if price4 is not None and strike4 is not None:
            p_yes = self._probability_yes(price4, strike4, time_left)
            p_no = 1.0 - p_yes
            diff_pips = round((price4 - strike4) * 10000, 1)
            diff_pct = round((price4 - strike4) / strike4 * 100, 4) if strike4 else 0.0
            state = "LIVE"
        else:
            p_yes = p_no = None
            diff_pips = diff_pct = 0.0
            state = "WAITING"

        return {
            "state": state,
            "price": price4,
            "strike": strike4,
            "time_left": round(time_left, 2),
            "bucket_start": self.bucket_start,
            "bucket_end": expiry,
            "p_yes": round(p_yes, 4) if p_yes is not None else None,
            "p_no": round(p_no, 4) if p_no is not None else None,
            "sigma": round(sigma, 6) if sigma is not None else None,
            "diff_pips": diff_pips,
            "diff_pct": diff_pct,
            "prices": self.stream.recent_prices(120),
        }


def _fmt_ts(ts: float) -> str:
    return time.strftime("%H:%M:%S UTC", time.gmtime(ts))


# ── Web server ───────────────────────────────────────────────────────────────

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>XRP 5m Decision Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet"/>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0a0e17;--card:#111827;--card-hover:#161f33;
  --border:#1e293b;--border-hl:#334155;
  --text:#e2e8f0;--text-dim:#94a3b8;--text-muted:#64748b;
  --green:#10b981;--green-glow:#10b98140;--green-dim:#065f46;
  --red:#ef4444;--red-glow:#ef444440;--red-dim:#7f1d1d;
  --cyan:#06b6d4;--amber:#f59e0b;
  --radius:12px;
}
body{background:var(--bg);color:var(--text);font-family:'Inter',sans-serif;min-height:100vh;overflow-x:hidden}

/* ── Top bar ── */
.topbar{
  display:flex;align-items:center;justify-content:space-between;
  padding:16px 28px;
  background:linear-gradient(135deg,#0f172a 0%,#1e1b4b 100%);
  border-bottom:1px solid var(--border);
  position:sticky;top:0;z-index:10;
}
.topbar-left{display:flex;align-items:center;gap:16px}
.logo{font-size:22px;font-weight:800;letter-spacing:-0.5px;background:linear-gradient(135deg,var(--cyan),var(--green));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.pair-badge{
  background:var(--card);border:1px solid var(--border);border-radius:8px;
  padding:6px 14px;font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:600;color:var(--cyan);
}
.live-dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 8px var(--green-glow);animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.topbar-price{
  font-family:'JetBrains Mono',monospace;font-size:28px;font-weight:700;
  transition:color .2s;
}
.topbar-price.up{color:var(--green)}
.topbar-price.down{color:var(--red)}
.topbar-price.flat{color:var(--text)}
.topbar-right{display:flex;align-items:center;gap:12px}
.state-badge{
  padding:5px 14px;border-radius:20px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;
}
.state-badge.live{background:var(--green-dim);color:var(--green);border:1px solid var(--green)}
.state-badge.waiting{background:#422006;color:var(--amber);border:1px solid var(--amber)}
.state-badge.init{background:#1e293b;color:var(--text-dim);border:1px solid var(--border)}
.ws-status{font-size:11px;color:var(--text-muted);font-family:'JetBrains Mono',monospace}

/* ── Grid ── */
.grid{
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
  gap:20px;padding:24px 28px;max-width:1400px;margin:0 auto;
}

/* ── Cards ── */
.card{
  background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
  padding:24px;position:relative;overflow:hidden;
  transition:border-color .25s,transform .15s;
}
.card:hover{border-color:var(--border-hl);transform:translateY(-2px)}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;border-radius:var(--radius) var(--radius) 0 0;opacity:.8}
.card.strike::before{background:linear-gradient(90deg,var(--cyan),#818cf8)}
.card.expiry::before{background:linear-gradient(90deg,var(--amber),#f97316)}
.card.prob::before{background:linear-gradient(90deg,var(--green),var(--red))}
.card.sigma::before{background:linear-gradient(90deg,#818cf8,#a78bfa)}
.card.diff::before{background:linear-gradient(90deg,var(--cyan),var(--green))}
.card-label{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:1.2px;color:var(--text-muted);margin-bottom:12px}
.card-value{font-family:'JetBrains Mono',monospace;font-size:32px;font-weight:700;line-height:1.1}
.card-sub{font-size:13px;color:var(--text-dim);margin-top:8px;font-family:'JetBrains Mono',monospace}

/* ── Probability bar ── */
.prob-container{margin-top:16px}
.prob-bar{display:flex;height:32px;border-radius:8px;overflow:hidden;background:#1e293b;position:relative}
.prob-bar .yes{background:linear-gradient(90deg,var(--green-dim),var(--green));transition:width .4s ease;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:#fff;min-width:40px}
.prob-bar .no{background:linear-gradient(90deg,var(--red),var(--red-dim));transition:width .4s ease;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:#fff;min-width:40px}
.prob-labels{display:flex;justify-content:space-between;margin-top:8px;font-size:11px;color:var(--text-muted);font-weight:600;text-transform:uppercase;letter-spacing:.5px}

/* ── Countdown ring ── */
.ring-wrap{display:flex;align-items:center;gap:20px}
.countdown-ring{position:relative;width:80px;height:80px;flex-shrink:0}
.countdown-ring svg{transform:rotate(-90deg)}
.countdown-ring circle{fill:none;stroke-width:5}
.countdown-ring .bg{stroke:var(--border)}
.countdown-ring .fg{stroke:var(--amber);stroke-linecap:round;transition:stroke-dashoffset .5s linear,stroke .3s}
.countdown-ring .fg.urgent{stroke:var(--red)}
.countdown-text{
  position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:700;
}

/* ── Chart ── */
.chart-card{grid-column:1/-1}
.chart-wrap{position:relative;height:200px;margin-top:12px}
#priceChart{width:100%;height:100%;display:block}

/* ── Responsive ── */
@media(max-width:768px){
  .topbar{flex-direction:column;gap:12px;padding:14px 16px}
  .topbar-price{font-size:24px}
  .grid{padding:16px;gap:14px;grid-template-columns:1fr 1fr}
  .card-value{font-size:24px}
  .chart-card{grid-column:1/-1}
}
@media(max-width:480px){
  .grid{grid-template-columns:1fr}
}
</style>
</head>
<body>

<!-- ── Top bar ── -->
<header class="topbar">
  <div class="topbar-left">
    <span class="logo">5m Decision</span>
    <span class="pair-badge">XRP / USDT</span>
    <span class="live-dot" id="liveDot"></span>
    <span class="topbar-price flat" id="livePrice">—</span>
  </div>
  <div class="topbar-right">
    <span class="state-badge init" id="stateBadge">INIT</span>
    <span class="ws-status" id="wsStatus">connecting…</span>
  </div>
</header>

<!-- ── Cards ── -->
<main class="grid">

  <!-- Strike -->
  <div class="card strike">
    <div class="card-label">Strike Price</div>
    <div class="card-value" id="strikeVal">—</div>
    <div class="card-sub" id="strikeSub">Captured at bucket start</div>
  </div>

  <!-- Time to Expiry -->
  <div class="card expiry">
    <div class="card-label">Time to Expiry</div>
    <div class="ring-wrap">
      <div class="countdown-ring">
        <svg viewBox="0 0 90 90">
          <circle class="bg" cx="45" cy="45" r="40"/>
          <circle class="fg" id="ringFg" cx="45" cy="45" r="40" stroke-dasharray="251.33" stroke-dashoffset="0"/>
        </svg>
        <div class="countdown-text" id="countdownText">—</div>
      </div>
      <div>
        <div class="card-value" id="expiryVal" style="font-size:26px">—</div>
        <div class="card-sub" id="expirySub">—</div>
      </div>
    </div>
  </div>

  <!-- Probability -->
  <div class="card prob" style="grid-column:span 2">
    <div class="card-label">Probability</div>
    <div style="display:flex;gap:32px;align-items:baseline">
      <div>
        <span style="color:var(--green);font-size:14px;font-weight:600">P(YES)</span>
        <span class="card-value" id="pYesVal" style="margin-left:8px;color:var(--green)">—</span>
      </div>
      <div>
        <span style="color:var(--red);font-size:14px;font-weight:600">P(NO)</span>
        <span class="card-value" id="pNoVal" style="margin-left:8px;color:var(--red)">—</span>
      </div>
    </div>
    <div class="prob-container">
      <div class="prob-bar">
        <div class="yes" id="probYesBar" style="width:50%">50%</div>
        <div class="no" id="probNoBar" style="width:50%">50%</div>
      </div>
      <div class="prob-labels"><span>▲ YES (UP)</span><span>▼ NO (DOWN)</span></div>
    </div>
  </div>

  <!-- Price vs Strike diff -->
  <div class="card diff">
    <div class="card-label">Price vs Strike</div>
    <div class="card-value" id="diffPips" style="font-size:28px">—</div>
    <div class="card-sub" id="diffPct">—</div>
  </div>

  <!-- Realized sigma -->
  <div class="card sigma">
    <div class="card-label">Realized σ (5m)</div>
    <div class="card-value" id="sigmaVal" style="font-size:28px">—</div>
    <div class="card-sub">per √second, 1s subsampled</div>
  </div>

  <!-- Price chart -->
  <div class="card chart-card">
    <div class="card-label">Price — Last 2 Minutes</div>
    <div class="chart-wrap">
      <canvas id="priceChart"></canvas>
    </div>
  </div>

</main>

<script>
// ── State ──
let prevPrice = null;
const BUCKET = 300;
const CIRC = 2 * Math.PI * 40; // ring circumference

// ── DOM refs ──
const $price = document.getElementById('livePrice');
const $state = document.getElementById('stateBadge');
const $wsStatus = document.getElementById('wsStatus');
const $strike = document.getElementById('strikeVal');
const $strikeSub = document.getElementById('strikeSub');
const $expiryVal = document.getElementById('expiryVal');
const $expirySub = document.getElementById('expirySub');
const $countdown = document.getElementById('countdownText');
const $ringFg = document.getElementById('ringFg');
const $pYes = document.getElementById('pYesVal');
const $pNo = document.getElementById('pNoVal');
const $probYes = document.getElementById('probYesBar');
const $probNo = document.getElementById('probNoBar');
const $diffPips = document.getElementById('diffPips');
const $diffPct = document.getElementById('diffPct');
const $sigma = document.getElementById('sigmaVal');
const canvas = document.getElementById('priceChart');
const ctx = canvas.getContext('2d');

function fmtTime(s) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, '0')}`;
}

function fmtUTC(ts) {
  const d = new Date(ts * 1000);
  return d.toISOString().slice(11, 19) + ' UTC';
}

// ── Chart ──
function drawChart(prices, strike) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  canvas.style.width = rect.width + 'px';
  canvas.style.height = rect.height + 'px';
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const W = rect.width, H = rect.height;
  ctx.clearRect(0, 0, W, H);

  if (!prices || prices.length < 2) {
    ctx.fillStyle = '#64748b';
    ctx.font = '14px Inter';
    ctx.textAlign = 'center';
    ctx.fillText('Waiting for price data…', W / 2, H / 2);
    return;
  }

  const ps = prices.map(p => p.p);
  const ts = prices.map(p => p.t);
  const minP = Math.min(...ps) - 0.0002;
  const maxP = Math.max(...ps) + 0.0002;
  const minT = ts[0], maxT = ts[ts.length - 1];
  const rangeP = maxP - minP || 1;
  const rangeT = maxT - minT || 1;

  const px = (t) => ((t - minT) / rangeT) * (W - 40) + 20;
  const py = (p) => H - 20 - ((p - minP) / rangeP) * (H - 40);

  // Grid lines
  ctx.strokeStyle = '#1e293b';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = 20 + (i / 4) * (H - 40);
    ctx.beginPath(); ctx.moveTo(20, y); ctx.lineTo(W - 20, y); ctx.stroke();
  }

  // Strike line
  if (strike != null && strike >= minP && strike <= maxP) {
    const sy = py(strike);
    ctx.strokeStyle = '#818cf850';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([6, 4]);
    ctx.beginPath(); ctx.moveTo(20, sy); ctx.lineTo(W - 20, sy); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#818cf8';
    ctx.font = '11px JetBrains Mono';
    ctx.textAlign = 'right';
    ctx.fillText('STRIKE ' + strike.toFixed(4), W - 24, sy - 6);
  }

  // Price line gradient
  const grad = ctx.createLinearGradient(0, 0, W, 0);
  grad.addColorStop(0, '#06b6d450');
  grad.addColorStop(1, '#10b981');
  ctx.strokeStyle = grad;
  ctx.lineWidth = 2;
  ctx.lineJoin = 'round';
  ctx.beginPath();
  for (let i = 0; i < prices.length; i++) {
    const x = px(ts[i]), y = py(ps[i]);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.stroke();

  // Area under curve
  const areaGrad = ctx.createLinearGradient(0, 0, 0, H);
  areaGrad.addColorStop(0, '#10b98118');
  areaGrad.addColorStop(1, '#10b98100');
  ctx.fillStyle = areaGrad;
  ctx.beginPath();
  ctx.moveTo(px(ts[0]), py(ps[0]));
  for (let i = 1; i < prices.length; i++) ctx.lineTo(px(ts[i]), py(ps[i]));
  ctx.lineTo(px(ts[ts.length - 1]), H - 20);
  ctx.lineTo(px(ts[0]), H - 20);
  ctx.closePath();
  ctx.fill();

  // Current price dot
  const lastX = px(ts[ts.length - 1]);
  const lastY = py(ps[ps.length - 1]);
  ctx.beginPath();
  ctx.arc(lastX, lastY, 4, 0, Math.PI * 2);
  ctx.fillStyle = '#10b981';
  ctx.fill();
  ctx.beginPath();
  ctx.arc(lastX, lastY, 8, 0, Math.PI * 2);
  ctx.strokeStyle = '#10b98160';
  ctx.lineWidth = 2;
  ctx.stroke();

  // Y-axis labels
  ctx.fillStyle = '#64748b';
  ctx.font = '10px JetBrains Mono';
  ctx.textAlign = 'left';
  for (let i = 0; i <= 4; i++) {
    const val = maxP - (i / 4) * rangeP;
    const y = 20 + (i / 4) * (H - 40);
    ctx.fillText(val.toFixed(4), 2, y - 4);
  }
}

// ── Update UI ──
function update(d) {
  // State badge
  const st = d.state || 'INIT';
  $state.textContent = st;
  $state.className = 'state-badge ' + st.toLowerCase();

  // Live price
  if (d.price != null) {
    const p = d.price.toFixed(4);
    $price.textContent = '$' + p;
    if (prevPrice != null) {
      $price.className = 'topbar-price ' + (d.price > prevPrice ? 'up' : d.price < prevPrice ? 'down' : 'flat');
    }
    prevPrice = d.price;
  } else {
    $price.textContent = '—';
    $price.className = 'topbar-price flat';
  }

  // Strike
  $strike.textContent = d.strike != null ? '$' + d.strike.toFixed(4) : '—';
  if (d.bucket_start) {
    $strikeSub.textContent = 'Set at ' + fmtUTC(d.bucket_start);
  }

  // Expiry
  if (d.time_left != null) {
    const tl = d.time_left;
    $expiryVal.textContent = fmtTime(tl);
    $countdown.textContent = fmtTime(tl);
    const frac = 1 - tl / BUCKET;
    $ringFg.style.strokeDashoffset = (CIRC * (1 - frac)).toString();
    $ringFg.classList.toggle('urgent', tl < 30);
    if (d.bucket_end) {
      $expirySub.textContent = 'Ends ' + fmtUTC(d.bucket_end);
    }
  }

  // Probability
  if (d.p_yes != null) {
    const yPct = (d.p_yes * 100).toFixed(1);
    const nPct = (d.p_no * 100).toFixed(1);
    $pYes.textContent = yPct + '%';
    $pNo.textContent = nPct + '%';
    $probYes.style.width = yPct + '%';
    $probYes.textContent = yPct + '%';
    $probNo.style.width = nPct + '%';
    $probNo.textContent = nPct + '%';
  } else {
    $pYes.textContent = $pNo.textContent = '—';
  }

  // Diff
  if (d.diff_pips != null && d.strike != null) {
    const sign = d.diff_pips >= 0 ? '+' : '';
    $diffPips.textContent = sign + d.diff_pips.toFixed(1) + ' pips';
    $diffPips.style.color = d.diff_pips >= 0 ? 'var(--green)' : 'var(--red)';
    $diffPct.textContent = (d.diff_pct >= 0 ? '+' : '') + d.diff_pct.toFixed(4) + '%';
  }

  // Sigma
  $sigma.textContent = d.sigma != null ? d.sigma.toFixed(6) : '—';

  // Chart
  drawChart(d.prices, d.strike);
}

// ── WebSocket ──
function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => { $wsStatus.textContent = 'connected'; };
  ws.onclose = () => {
    $wsStatus.textContent = 'reconnecting…';
    setTimeout(connect, 1500);
  };
  ws.onerror = () => { ws.close(); };
  ws.onmessage = (e) => {
    try { update(JSON.parse(e.data)); } catch (_) {}
  };
}

connect();
</script>
</body>
</html>"""


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    engine: DecisionEngine = request.app["engine"]

    try:
        while not ws.closed:
            snap = engine.snapshot()
            await ws.send_json(snap)
            await asyncio.sleep(0.5)
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    return ws


async def index_handler(_request: web.Request) -> web.Response:
    return web.Response(text=DASHBOARD_HTML, content_type="text/html")


async def start_engine(app: web.Application) -> None:
    engine: DecisionEngine = app["engine"]
    app["stream_task"] = asyncio.create_task(engine.stream.run(), name="binance_stream")
    logging.info("Dashboard ready at http://localhost:%d", app["port"])


async def stop_engine(app: web.Application) -> None:
    engine: DecisionEngine = app["engine"]
    engine.stream.stop()
    task = app.get("stream_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def build_app(symbol: str, bucket_seconds: int, sigma_fallback: float, port: int) -> web.Application:
    engine = DecisionEngine(symbol=symbol, bucket_seconds=bucket_seconds, sigma_fallback=sigma_fallback)
    app = web.Application()
    app["engine"] = engine
    app["port"] = port
    app.router.add_get("/", index_handler)
    app.router.add_get("/ws", ws_handler)
    app.on_startup.append(start_engine)
    app.on_cleanup.append(stop_engine)
    return app


def main() -> None:
    p = argparse.ArgumentParser(description="XRP 5m decision dashboard")
    p.add_argument("--symbol", default="xrpusdt", help="Binance symbol")
    p.add_argument("--bucket-seconds", type=int, default=300, help="Bucket size")
    p.add_argument("--sigma-fallback", type=float, default=0.0015, help="Fallback sigma")
    p.add_argument("--port", type=int, default=8050, help="HTTP port")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S")

    app = build_app(args.symbol, args.bucket_seconds, args.sigma_fallback, args.port)
    web.run_app(app, host="0.0.0.0", port=args.port, print=None)


if __name__ == "__main__":
    main()
