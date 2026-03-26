#!/usr/bin/env python3
"""
Kou Jump-Diffusion Dashboard — real-time web interface.

Combines the 10s-candle Kou decision engine with an aiohttp web server
that pushes live data to the browser via WebSocket.

Run:
    python3 kou_dashboard.py --symbol ethusdt
    → open http://localhost:8060
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

import numpy as np
import websockets
from aiohttp import web

# ── SSL ───────────────────────────────────────────────────────────────────────

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

# ── Constants ─────────────────────────────────────────────────────────────────

BINANCE_WS_BASE = "wss://stream.binance.com:9443/ws"
MC_PATHS = 5_000
CALIB_WINDOW_S = 6 * 3600
CANDLE_INTERVAL_S = 10
JUMP_THRESHOLD_SIGMA = 2.0
MIN_CALIB_CANDLES = 30

def _round4(v: Optional[float]) -> Optional[float]:
    if v is None: return None
    return float(f"{v:.4f}")

def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def _fmt_ts(ts: float) -> str:
    return time.strftime("%H:%M:%S UTC", time.gmtime(ts))

# ── 10s OHLC candle ──────────────────────────────────────────────────────────

@dataclass
class Candle10s:
    bucket_ts: float
    open: float
    high: float
    low: float
    close: float
    n_ticks: int = 0

@dataclass
class PricePoint:
    ts: float
    price: float

class BinancePriceStream:
    def __init__(self, symbol: str, history_seconds: int = CALIB_WINDOW_S + 120) -> None:
        self.symbol = symbol.lower()
        self.url = f"{BINANCE_WS_BASE}/{self.symbol}@trade"
        self.history_seconds = max(120, history_seconds)
        self.last_price: Optional[float] = None
        self.last_ts: Optional[float] = None
        self.candles: deque[Candle10s] = deque()
        self._current_candle: Optional[Candle10s] = None
        self.tick_history: deque[PricePoint] = deque()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def latest(self) -> tuple[Optional[float], Optional[float]]:
        return self.last_price, self.last_ts

    def _candle_bucket(self, ts: float) -> float:
        return (int(ts) // CANDLE_INTERVAL_S) * CANDLE_INTERVAL_S

    def _update(self, ts: float, price: float) -> None:
        self.last_price = price
        self.last_ts = ts
        self.tick_history.append(PricePoint(ts=ts, price=price))
        while self.tick_history and self.tick_history[0].ts < ts - 180:
            self.tick_history.popleft()

        bucket = self._candle_bucket(ts)
        if self._current_candle is None or bucket != self._current_candle.bucket_ts:
            if self._current_candle is not None and self._current_candle.n_ticks > 0:
                self.candles.append(self._current_candle)
            self._current_candle = Candle10s(
                bucket_ts=bucket, open=price, high=price, low=price, close=price, n_ticks=1
            )
            min_ts = ts - self.history_seconds
            while self.candles and self.candles[0].bucket_ts < min_ts:
                self.candles.popleft()
        else:
            c = self._current_candle
            c.high = max(c.high, price)
            c.low = min(c.low, price)
            c.close = price
            c.n_ticks += 1

    def get_candles(self, window_s: int = CALIB_WINDOW_S) -> list[Candle10s]:
        if not self.candles: return []
        now_ts = self.candles[-1].bucket_ts
        cutoff = now_ts - window_s
        return [c for c in self.candles if c.bucket_ts >= cutoff]

    def recent_prices(self, seconds: int = 120) -> list[dict]:
        if not self.tick_history: return []
        now_ts = self.tick_history[-1].ts
        cutoff = now_ts - seconds
        out, last_t = [], 0.0
        for p in self.tick_history:
            if p.ts < cutoff: continue
            if p.ts - last_t >= 1.0:
                out.append({"t": round(p.ts, 3), "p": round(p.price, 5)})
                last_t = p.ts
        last = self.tick_history[-1]
        if not out or out[-1]["t"] != round(last.ts, 3):
            out.append({"t": round(last.ts, 3), "p": round(last.price, 5)})
        return out

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.url, ssl=_SSL_CTX,
                    ping_interval=20.0, ping_timeout=20.0,
                    close_timeout=5.0, max_size=2_000_000,
                ) as ws:
                    logging.info("Binance stream connected: %s", self.symbol.upper())
                    backoff = 1.0
                    async for raw in ws:
                        if self._stop.is_set(): break
                        try:
                            msg = json.loads(raw)
                            price = float(msg.get("p", 0.0))
                            if price <= 0.0: continue
                            evt = msg.get("E") or msg.get("T")
                            ts = float(evt) / 1000.0 if evt is not None else time.time()
                            if ts <= 0: ts = time.time()
                            self._update(ts, price)
                        except Exception: continue
            except Exception as exc:
                logging.warning("Binance reconnecting: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 10.0)

# ── Kou calibration ──────────────────────────────────────────────────────────

@dataclass
class KouParams:
    sigma: float; lam: float; p_up: float; eta1: float; eta2: float; sigma_park: float
    @property
    def xi(self) -> float:
        t1 = self.p_up * self.eta1 / (self.eta1 - 1.0) if self.eta1 > 1.0 else 0.0
        t2 = (1.0 - self.p_up) * self.eta2 / (self.eta2 + 1.0)
        return t1 + t2 - 1.0
    @property
    def sigma_per_sqrt_s(self) -> float:
        return self.sigma / math.sqrt(CANDLE_INTERVAL_S)

def parkinson_sigma(candles: list[Candle10s]) -> float:
    valid = [(c.high, c.low) for c in candles if c.high > c.low]
    if len(valid) < 10: return 0.0
    log_hl_sq = np.array([math.log(h / l) ** 2 for h, l in valid])
    return float(np.sqrt(np.sum(log_hl_sq) / (4 * len(valid) * math.log(2))))

def calibrate_kou(candles: list[Candle10s]) -> Optional[KouParams]:
    if len(candles) < MIN_CALIB_CANDLES + 1: return None
    closes = np.array([c.close for c in candles])
    log_ret = np.diff(np.log(closes))
    if len(log_ret) < MIN_CALIB_CANDLES: return None
    sigma_rough = float(np.std(log_ret))
    if sigma_rough <= 1e-12: return None
    jump_mask = np.abs(log_ret) > JUMP_THRESHOLD_SIGMA * sigma_rough
    non_jump = log_ret[~jump_mask]
    sigma = float(np.std(non_jump)) if len(non_jump) >= 20 else sigma_rough
    if sigma <= 1e-12: sigma = sigma_rough
    jump_mask = np.abs(log_ret) > JUMP_THRESHOLD_SIGMA * sigma
    n_jumps = int(jump_mask.sum())
    sigma_pk = parkinson_sigma(candles)
    if n_jumps < 3:
        return KouParams(sigma=sigma, lam=1e-6, p_up=0.5, eta1=10.0, eta2=10.0, sigma_park=sigma_pk)
    lam = n_jumps / len(log_ret)
    jr = log_ret[jump_mask]
    uj, dj = jr[jr > 0], jr[jr < 0]
    p_up = max(0.05, min(0.95, len(uj) / len(jr)))
    eta1 = max(1.01, 1.0 / float(np.mean(uj))) if len(uj) >= 2 else 10.0
    eta2 = max(0.1, 1.0 / float(np.mean(np.abs(dj)))) if len(dj) >= 2 else 10.0
    return KouParams(sigma=sigma, lam=lam, p_up=p_up, eta1=eta1, eta2=eta2, sigma_park=sigma_pk)

# ── Monte Carlo ───────────────────────────────────────────────────────────────

class KouMC:
    def __init__(self, n_paths: int = MC_PATHS):
        self.n_paths = n_paths
        self.rng = np.random.default_rng()

    def prob_yes(self, current: float, strike: float, t_left_s: float, params: KouParams) -> float:
        if t_left_s <= 0: return 1.0 if current >= strike else 0.0
        if current <= 0 or strike <= 0: return 0.5
        n_p = t_left_s / CANDLE_INTERVAL_S
        sT = params.sigma * math.sqrt(n_p)
        lT = params.lam * n_p
        drift = -0.5 * sT * sT - lT * params.xi
        Z = self.rng.standard_normal(self.n_paths)
        nj = self.rng.poisson(lT, size=self.n_paths)
        tj = np.zeros(self.n_paths)
        mx = int(nj.max()) if nj.max() > 0 else 0
        for j in range(mx):
            act = nj > j
            up = self.rng.random(self.n_paths) < params.p_up
            us = self.rng.exponential(1.0 / params.eta1, self.n_paths)
            ds = -self.rng.exponential(1.0 / params.eta2, self.n_paths)
            tj += np.where(act, np.where(up, us, ds), 0.0)
        lr = drift + sT * Z + tj
        thr = math.log(strike / current)
        return float(np.clip(np.mean(lr > thr), 0.0, 1.0))

def bs_prob_yes(current: float, strike: float, t_left_s: float, sigma_ps: float) -> float:
    if t_left_s <= 0: return 1.0 if current >= strike else 0.0
    if sigma_ps <= 1e-12: return 1.0 if current >= strike else 0.0
    sT = sigma_ps * math.sqrt(t_left_s)
    d = (math.log(current / strike) - 0.5 * sT * sT) / sT
    return float(np.clip(_normal_cdf(d), 0.0, 1.0))

# ── Decision engine (dashboard version) ──────────────────────────────────────

class DecisionEngine:
    def __init__(self, *, symbol: str, bucket_seconds: int, sigma_fallback: float) -> None:
        self.symbol = symbol
        self.bucket_seconds = bucket_seconds
        self.sigma_fallback = sigma_fallback
        self.stream = BinancePriceStream(symbol=symbol)
        self.mc = KouMC()
        self.strike_price: Optional[float] = None
        self.bucket_start: Optional[float] = None
        self.kou_params: Optional[KouParams] = None

    def _current_bucket_start(self, now_ts: float) -> float:
        return (int(now_ts) // self.bucket_seconds) * self.bucket_seconds

    def _try_calibrate(self) -> None:
        """Attempt Kou calibration from available 10s candles."""
        candles = self.stream.get_candles()
        if len(candles) >= MIN_CALIB_CANDLES:
            params = calibrate_kou(candles)
            if params:
                self.kou_params = params
                logging.info("Kou calibrated: σ=%.6f λ=%.4f p=%.3f η₁=%.1f η₂=%.1f (%d candles)",
                    params.sigma, params.lam, params.p_up,
                    params.eta1, params.eta2, len(candles))

    def _roll_bucket(self, now_ts: float) -> None:
        bs = self._current_bucket_start(now_ts)
        if self.bucket_start is None or bs != self.bucket_start:
            self.bucket_start = bs
            self.strike_price = None
            self._try_calibrate()
        elif self.kou_params is None:
            # Haven't calibrated yet — try as soon as enough candles exist
            self._try_calibrate()

    def _ensure_strike(self, price: Optional[float]) -> None:
        if self.strike_price is None and price is not None:
            self.strike_price = _round4(price)

    def snapshot(self) -> dict:
        now = time.time()
        self._roll_bucket(now)
        price, _ = self.stream.latest()
        self._ensure_strike(price)
        if self.bucket_start is None:
            return {"state": "INIT"}

        expiry = self.bucket_start + self.bucket_seconds
        t_left = max(0.0, expiry - now)
        p4 = _round4(price)
        s4 = _round4(self.strike_price)

        model = "KOU" if self.kou_params else "BS-FALLBACK"

        if p4 is not None and s4 is not None:
            sigma_bs = self.kou_params.sigma_per_sqrt_s if self.kou_params else self.sigma_fallback
            bs_y = bs_prob_yes(p4, s4, t_left, sigma_bs)
            if self.kou_params:
                kou_y = self.mc.prob_yes(p4, s4, t_left, self.kou_params)
            else:
                kou_y = bs_y
            diff_bps = round((p4 - s4) / s4 * 10000, 1) if s4 else 0.0
            state = "LIVE"
        else:
            kou_y = bs_y = None
            diff_bps = 0.0
            state = "WAITING"

        kp = self.kou_params
        return {
            "state": state, "model": model,
            "price": p4, "strike": s4,
            "time_left": round(t_left, 2),
            "bucket_end": expiry,
            "kou_yes": round(kou_y, 4) if kou_y is not None else None,
            "kou_no": round(1 - kou_y, 4) if kou_y is not None else None,
            "bs_yes": round(bs_y, 4) if bs_y is not None else None,
            "bs_no": round(1 - bs_y, 4) if bs_y is not None else None,
            "diff_bps": diff_bps,
            "sigma_cc": round(kp.sigma, 6) if kp else None,
            "sigma_park": round(kp.sigma_park, 6) if kp else None,
            "lam": round(kp.lam, 4) if kp else None,
            "p_up": round(kp.p_up, 3) if kp else None,
            "eta1": round(kp.eta1, 1) if kp else None,
            "eta2": round(kp.eta2, 1) if kp else None,
            "n_candles": len(self.stream.candles),
            "chart": self.stream.recent_prices(120),
        }

# ── Web handlers ──────────────────────────────────────────────────────────────

async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    engine: DecisionEngine = request.app["engine"]
    try:
        while not ws.closed:
            snap = engine.snapshot()
            await ws.send_json(snap)
            await asyncio.sleep(0.5)
    except Exception:
        pass
    return ws

async def index_handler(request: web.Request) -> web.Response:
    symbol = request.app["engine"].symbol.upper()
    asset = symbol.replace("USDT", "")
    return web.Response(text=DASHBOARD_HTML.replace("{{SYMBOL}}", symbol).replace("{{ASSET}}", asset),
                        content_type="text/html")

# ── HTML/CSS/JS ───────────────────────────────────────────────────────────────

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ASSET}} Kou Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0e17;color:#e0e6f0;font-family:'JetBrains Mono',monospace;overflow:hidden;height:100vh}
.top-bar{display:flex;align-items:center;justify-content:space-between;padding:12px 24px;background:linear-gradient(135deg,#0d1321,#151c2e);border-bottom:1px solid #1e2a42}
.top-bar .logo{font-size:18px;font-weight:700;background:linear-gradient(135deg,#6366f1,#a855f7);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.top-bar .model-badge{padding:4px 12px;border-radius:4px;font-size:11px;font-weight:600;letter-spacing:1px}
.model-kou{background:linear-gradient(135deg,#6366f1,#a855f7);color:#fff}
.model-bs{background:#374151;color:#9ca3af}
.main{display:grid;grid-template-columns:1fr 300px;grid-template-rows:auto 1fr auto;gap:0;height:calc(100vh - 50px)}
.price-row{grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;padding:16px 24px;background:#0d1321;border-bottom:1px solid #1e2a42}
.price-block{text-align:center}
.price-label{font-size:10px;text-transform:uppercase;letter-spacing:1.5px;color:#64748b;margin-bottom:4px}
.price-value{font-size:28px;font-weight:700;color:#f1f5f9}
.price-up{color:#22c55e!important}
.price-down{color:#ef4444!important}
.strike-value{font-size:22px;font-weight:500;color:#94a3b8}
.diff-badge{font-size:14px;padding:4px 10px;border-radius:6px;font-weight:600}
.diff-pos{background:rgba(34,197,94,.15);color:#22c55e;border:1px solid rgba(34,197,94,.3)}
.diff-neg{background:rgba(239,68,68,.15);color:#ef4444;border:1px solid rgba(239,68,68,.3)}

.center-area{display:flex;flex-direction:column;padding:16px 24px;gap:12px;overflow:hidden}
.prob-section{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.prob-card{background:linear-gradient(135deg,#111827,#1a2235);border:1px solid #1e2a42;border-radius:12px;padding:16px;text-align:center}
.prob-card .label{font-size:10px;text-transform:uppercase;letter-spacing:1.5px;color:#64748b;margin-bottom:6px}
.prob-card .model-name{font-size:9px;text-transform:uppercase;letter-spacing:1px;color:#6366f1;margin-bottom:4px}
.prob-row{display:flex;justify-content:center;gap:24px;margin-top:4px}
.prob-num{font-size:32px;font-weight:700;line-height:1}
.prob-yes{color:#22c55e}.prob-no{color:#ef4444}
.prob-sub{font-size:10px;color:#64748b;margin-top:2px}
.prob-bar{height:6px;border-radius:3px;background:#1e2a42;margin-top:10px;overflow:hidden;position:relative}
.prob-bar-fill{height:100%;border-radius:3px;transition:width .3s ease}
.fill-kou{background:linear-gradient(90deg,#ef4444,#22c55e)}
.fill-bs{background:linear-gradient(90deg,#ef4444 0%,#94a3b8 50%,#22c55e 100%)}
.chart-area{flex:1;min-height:120px;background:#111827;border:1px solid #1e2a42;border-radius:12px;padding:12px;position:relative}
.chart-area canvas{width:100%!important;height:100%!important}

.sidebar{background:#0d1321;border-left:1px solid #1e2a42;padding:16px;display:flex;flex-direction:column;gap:12px;overflow-y:auto}
.sidebar-title{font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:#6366f1;font-weight:600;margin-bottom:2px}
.metric{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid #1a2035}
.metric .mk{font-size:11px;color:#64748b}.metric .mv{font-size:12px;font-weight:600;color:#e0e6f0}
.countdown-ring{width:100px;height:100px;margin:8px auto}
.countdown-ring svg{width:100%;height:100%}
.countdown-ring .ring-bg{fill:none;stroke:#1e2a42;stroke-width:4}
.countdown-ring .ring-fg{fill:none;stroke:url(#ringGrad);stroke-width:4;stroke-linecap:round;transition:stroke-dashoffset .5s ease}
.countdown-text{font-size:20px;font-weight:700;fill:#f1f5f9}
.countdown-label{font-size:8px;fill:#64748b;text-transform:uppercase;letter-spacing:1px}

.bottom-bar{grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;padding:8px 24px;background:#0a0e17;border-top:1px solid #1e2a42;font-size:10px;color:#475569}
</style>
</head>
<body>
<div class="top-bar">
  <div class="logo">{{ASSET}}/USDT — Kou Dashboard</div>
  <div class="model-badge model-bs" id="modelBadge">BS-FALLBACK</div>
</div>
<div class="main">
  <div class="price-row">
    <div class="price-block"><div class="price-label">Live Price</div><div class="price-value" id="price">—</div></div>
    <div class="price-block"><div class="price-label">Strike</div><div class="strike-value" id="strike">—</div></div>
    <div class="price-block"><div class="price-label">Δ from Strike</div><div class="diff-badge diff-pos" id="diff">—</div></div>
  </div>
  <div class="center-area">
    <div class="prob-section">
      <div class="prob-card">
        <div class="model-name">Kou Jump-Diffusion</div>
        <div class="prob-row">
          <div><div class="prob-num prob-yes" id="kouYes">—</div><div class="prob-sub">P(YES)</div></div>
          <div><div class="prob-num prob-no" id="kouNo">—</div><div class="prob-sub">P(NO)</div></div>
        </div>
        <div class="prob-bar"><div class="prob-bar-fill fill-kou" id="kouBar" style="width:50%"></div></div>
      </div>
      <div class="prob-card">
        <div class="model-name">Black-Scholes</div>
        <div class="prob-row">
          <div><div class="prob-num prob-yes" id="bsYes">—</div><div class="prob-sub">P(YES)</div></div>
          <div><div class="prob-num prob-no" id="bsNo">—</div><div class="prob-sub">P(NO)</div></div>
        </div>
        <div class="prob-bar"><div class="prob-bar-fill fill-bs" id="bsBar" style="width:50%"></div></div>
      </div>
    </div>
    <div class="chart-area"><canvas id="chart"></canvas></div>
  </div>
  <div class="sidebar">
    <div class="sidebar-title">Time to Expiry</div>
    <div class="countdown-ring">
      <svg viewBox="0 0 100 100">
        <defs><linearGradient id="ringGrad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#6366f1"/><stop offset="100%" stop-color="#a855f7"/>
        </linearGradient></defs>
        <circle class="ring-bg" cx="50" cy="50" r="42"/>
        <circle class="ring-fg" id="ring" cx="50" cy="50" r="42"
          stroke-dasharray="264" stroke-dashoffset="0"
          transform="rotate(-90 50 50)"/>
        <text class="countdown-text" x="50" y="48" text-anchor="middle" id="countdown">—</text>
        <text class="countdown-label" x="50" y="62" text-anchor="middle">REMAINING</text>
      </svg>
    </div>
    <div class="sidebar-title">Kou Parameters</div>
    <div class="metric"><span class="mk">σ (close-close)</span><span class="mv" id="mSigCC">—</span></div>
    <div class="metric"><span class="mk">σ (Parkinson)</span><span class="mv" id="mSigPK">—</span></div>
    <div class="metric"><span class="mk">λ (jump rate)</span><span class="mv" id="mLam">—</span></div>
    <div class="metric"><span class="mk">p(up jump)</span><span class="mv" id="mPup">—</span></div>
    <div class="metric"><span class="mk">η₁ (up decay)</span><span class="mv" id="mEta1">—</span></div>
    <div class="metric"><span class="mk">η₂ (down decay)</span><span class="mv" id="mEta2">—</span></div>
    <div class="sidebar-title" style="margin-top:8px">Data</div>
    <div class="metric"><span class="mk">10s candles</span><span class="mv" id="mCandles">—</span></div>
    <div class="metric"><span class="mk">Model</span><span class="mv" id="mModel">—</span></div>
  </div>
  <div class="bottom-bar">
    <span>Kou Jump-Diffusion · 10s candle calibration · 5K MC paths</span>
    <span id="statusDot">● Connected</span>
  </div>
</div>
<script>
const $ = id => document.getElementById(id);
const ws = new WebSocket(`ws://${location.host}/ws`);
let lastPrice = null;
const canvas = $('chart');
const ctx = canvas.getContext('2d');

function resizeCanvas(){
  const r = canvas.parentElement.getBoundingClientRect();
  canvas.width = r.width - 24; canvas.height = r.height - 24;
}
window.addEventListener('resize', resizeCanvas);
resizeCanvas();

function drawChart(pts, strike){
  if(!pts||pts.length<2)return;
  const W=canvas.width, H=canvas.height;
  ctx.clearRect(0,0,W,H);
  const prices = pts.map(p=>p.p);
  const mn = Math.min(...prices, strike||Infinity), mx = Math.max(...prices, strike||-Infinity);
  const pad = (mx-mn)*0.1||0.001;
  const yMin=mn-pad, yRange=mx-mn+2*pad;
  const tMin=pts[0].t, tRange=pts[pts.length-1].t-tMin||1;
  const toX=t=>(t-tMin)/tRange*W, toY=p=>H-(p-yMin)/yRange*H;

  // Strike line
  if(strike){
    const sy=toY(strike);
    ctx.strokeStyle='rgba(148,163,184,0.3)';ctx.lineWidth=1;
    ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(0,sy);ctx.lineTo(W,sy);ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle='#64748b';ctx.font='10px JetBrains Mono';ctx.fillText('strike',4,sy-4);
  }
  // Price line
  ctx.beginPath();
  pts.forEach((p,i)=>{i===0?ctx.moveTo(toX(p.t),toY(p.p)):ctx.lineTo(toX(p.t),toY(p.p))});
  const last=pts[pts.length-1];
  const col = strike&&last.p>=strike?'#22c55e':'#ef4444';
  ctx.strokeStyle=col;ctx.lineWidth=2;ctx.stroke();
  // Glow
  const grad=ctx.createLinearGradient(0,toY(mx),0,H);
  grad.addColorStop(0,col+'40');grad.addColorStop(1,col+'00');
  ctx.lineTo(toX(last.t),H);ctx.lineTo(toX(pts[0].t),H);ctx.closePath();
  ctx.fillStyle=grad;ctx.fill();
  // Dot
  ctx.beginPath();ctx.arc(toX(last.t),toY(last.p),4,0,Math.PI*2);ctx.fillStyle=col;ctx.fill();
}

ws.onmessage = e => {
  const d = JSON.parse(e.data);
  if(d.state==='INIT')return;

  // Price
  const p = d.price;
  if(p!=null){
    const el=$('price');el.textContent=p.toFixed(4);
    el.classList.remove('price-up','price-down');
    if(lastPrice!=null)el.classList.add(p>=lastPrice?'price-up':'price-down');
    lastPrice=p;
  }
  $('strike').textContent=d.strike!=null?d.strike.toFixed(4):'—';

  // Diff
  const dEl=$('diff');
  dEl.textContent=d.diff_bps!=null?(d.diff_bps>=0?'+':'')+d.diff_bps.toFixed(1)+' bps':'—';
  dEl.className='diff-badge '+(d.diff_bps>=0?'diff-pos':'diff-neg');

  // Kou probs
  $('kouYes').textContent=d.kou_yes!=null?(d.kou_yes*100).toFixed(1)+'%':'—';
  $('kouNo').textContent=d.kou_no!=null?(d.kou_no*100).toFixed(1)+'%':'—';
  if(d.kou_yes!=null)$('kouBar').style.width=(d.kou_yes*100)+'%';

  // BS probs
  $('bsYes').textContent=d.bs_yes!=null?(d.bs_yes*100).toFixed(1)+'%':'—';
  $('bsNo').textContent=d.bs_no!=null?(d.bs_no*100).toFixed(1)+'%':'—';
  if(d.bs_yes!=null)$('bsBar').style.width=(d.bs_yes*100)+'%';

  // Model badge
  const mb=$('modelBadge');
  mb.textContent=d.model||'—';
  mb.className='model-badge '+(d.model==='KOU'?'model-kou':'model-bs');

  // Countdown
  const tl=d.time_left||0;
  const mm=Math.floor(tl/60),ss=Math.floor(tl%60);
  $('countdown').textContent=mm+':'+(ss<10?'0':'')+ss;
  const frac=1-tl/300; // 5m bucket
  $('ring').setAttribute('stroke-dashoffset',String(264*frac));

  // Sidebar
  $('mSigCC').textContent=d.sigma_cc!=null?d.sigma_cc.toFixed(6):'—';
  $('mSigPK').textContent=d.sigma_park!=null?d.sigma_park.toFixed(6):'—';
  $('mLam').textContent=d.lam!=null?d.lam.toFixed(4)+'/candle':'—';
  $('mPup').textContent=d.p_up!=null?(d.p_up*100).toFixed(1)+'%':'—';
  $('mEta1').textContent=d.eta1!=null?d.eta1.toFixed(1):'—';
  $('mEta2').textContent=d.eta2!=null?d.eta2.toFixed(1):'—';
  $('mCandles').textContent=d.n_candles!=null?d.n_candles.toLocaleString():'—';
  $('mModel').textContent=d.model||'—';

  // Chart
  drawChart(d.chart, d.strike);
};
ws.onclose=()=>{$('statusDot').textContent='● Disconnected';$('statusDot').style.color='#ef4444'};
</script>
</body></html>
"""

# ── App setup ─────────────────────────────────────────────────────────────────

async def on_startup(app: web.Application) -> None:
    engine: DecisionEngine = app["engine"]
    app["stream_task"] = asyncio.create_task(engine.stream.run())

async def on_cleanup(app: web.Application) -> None:
    engine: DecisionEngine = app["engine"]
    engine.stream.stop()
    app["stream_task"].cancel()
    await asyncio.gather(app["stream_task"], return_exceptions=True)

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Kou dashboard")
    p.add_argument("--symbol", default="ethusdt")
    p.add_argument("--bucket-seconds", type=int, default=300)
    p.add_argument("--sigma-fallback", type=float, default=0.0003)
    p.add_argument("--port", type=int, default=8060)
    return p

def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S")
    engine = DecisionEngine(symbol=args.symbol, bucket_seconds=args.bucket_seconds, sigma_fallback=args.sigma_fallback)
    app = web.Application()
    app["engine"] = engine
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/", index_handler)
    app.router.add_get("/ws", ws_handler)
    asset = args.symbol.upper().replace("USDT", "")
    print(f"\n  🚀 {asset} Kou Dashboard → http://localhost:{args.port}\n")
    web.run_app(app, host="0.0.0.0", port=args.port, print=None)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
