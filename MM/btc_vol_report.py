#!/usr/bin/env python3
"""
BTC Volatility Report (One-Time)
================================
Pulls recent exchange OHLC data and prints a volatility summary with 90/95% CI bands.
Optionally outputs a plot of rolling 30m/60m volatility over the last N hours.

Default:
- Source: binance (1m candles)
- 6h window
- Short windows: 30m and 60m
"""

import argparse
import csv
import json
import math
import time
import os
from datetime import datetime, timezone
from dataclasses import dataclass
from statistics import stdev, NormalDist, mean, median
from threading import Thread
from typing import Optional
from zoneinfo import ZoneInfo

import httpx


BINANCE_REST = "https://api.binance.com/api/v3"
KRAKEN_REST = "https://api.kraken.com/0/public"
BITSTAMP_REST = "https://www.bitstamp.net/api/v2"

ASSET_MAP = {
    "BTC": {"binance": "BTCUSDT", "chainlink": "btc/usd"},
    "ETH": {"binance": "ETHUSDT", "chainlink": "eth/usd"},
    "SOL": {"binance": "SOLUSDT", "chainlink": "sol/usd"},
    "XRP": {"binance": "XRPUSDT", "chainlink": "xrp/usd"},
}


@dataclass
class Kline:
    ts: float
    open: float
    high: float
    low: float
    close: float




def chi2_ppf(p: float, df: int) -> float:
    try:
        from scipy.stats import chi2
        return float(chi2.ppf(p, df))
    except Exception:
        if df <= 0:
            return float("nan")
        z = NormalDist().inv_cdf(p)
        return df * (1 - 2 / (9 * df) + z * math.sqrt(2 / (9 * df))) ** 3


def sigma_ci(sigma: float, n: int, alpha: float):
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


def fmt_bps(x: float) -> str:
    return f"{x * 10000:.2f} bp"


def fmt_pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def fmt_usd(x: float) -> str:
    return f"${x:,.0f}"


def health_score(short_sigma: float, long_sigma: float) -> tuple[float, str]:
    if short_sigma <= 0 or long_sigma <= 0:
        return 0.0, "UNKNOWN"
    ratio = short_sigma / long_sigma
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


def fetch_1m_klines_binance(symbol: str, hours: float) -> list[Kline]:
    limit = max(2, min(1000, int(hours * 60)))
    params = {"symbol": symbol, "interval": "1m", "limit": limit}
    resp = httpx.get(f"{BINANCE_REST}/klines", params=params, timeout=10.0)
    resp.raise_for_status()
    rows = resp.json()
    klines: list[Kline] = []
    for r in rows:
        ts = r[0] / 1000.0
        open_ = float(r[1])
        high = float(r[2])
        low = float(r[3])
        close = float(r[4])
        klines.append(Kline(ts=ts, open=open_, high=high, low=low, close=close))
    return klines


def fetch_1m_klines_kraken(hours: float) -> list[Kline]:
    # Kraken OHLC endpoint returns up to 720 points for 1m (12h).
    # We simply request and take the most recent required points.
    params = {"pair": "XXBTZUSD", "interval": 1}
    resp = httpx.get(f"{KRAKEN_REST}/OHLC", params=params, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    result = data.get("result", {})
    pair_key = next((k for k in result.keys() if k != "last"), None)
    if not pair_key:
        return []
    rows = result.get(pair_key, [])
    # rows: [time, open, high, low, close, vwap, volume, count]
    klines: list[Kline] = []
    for r in rows:
        ts = float(r[0])
        open_ = float(r[1])
        high = float(r[2])
        low = float(r[3])
        close = float(r[4])
        klines.append(Kline(ts=ts, open=open_, high=high, low=low, close=close))
    # keep most recent N points
    need = int(hours * 60)
    if need > 0 and len(klines) > need:
        klines = klines[-need:]
    return klines


def fetch_1m_klines_bitstamp(hours: float) -> list[Kline]:
    limit = max(2, min(1000, int(hours * 60)))
    params = {"step": 60, "limit": limit}
    resp = httpx.get(f"{BITSTAMP_REST}/ohlc/btcusd/", params=params, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    ohlc = data.get("data", {}).get("ohlc", [])
    klines: list[Kline] = []
    for r in ohlc:
        ts = float(r["timestamp"])
        open_ = float(r["open"])
        high = float(r["high"])
        low = float(r["low"])
        close = float(r["close"])
        klines.append(Kline(ts=ts, open=open_, high=high, low=low, close=close))
    return klines


def log_returns(klines: list[Kline]) -> list[float]:
    rets = []
    for i in range(1, len(klines)):
        r = math.log(klines[i].close / klines[i - 1].close)
        rets.append(r)
    return rets


def rolling_sigma(returns: list[float], window: int) -> list[Optional[float]]:
    out: list[Optional[float]] = []
    for i in range(len(returns)):
        if i + 1 < window:
            out.append(None)
        else:
            seg = returns[i + 1 - window : i + 1]
            out.append(stdev(seg) if len(seg) > 1 else None)
    return out


def ascii_bar(value: float, max_value: float, width: int = 30) -> str:
    if max_value <= 0:
        return ""
    n = int(round((value / max_value) * width))
    return "#" * n + "-" * (width - n)


def empirical_quantile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    vals = sorted(values)
    n = len(vals)
    if n == 1:
        return vals[0]
    pos = p * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    w = pos - lo
    return vals[lo] * (1 - w) + vals[hi] * w


def aggregated_returns(returns: list[float], t: int) -> list[float]:
    if t <= 1:
        return returns[:]
    out = []
    for i in range(t - 1, len(returns)):
        out.append(sum(returns[i - t + 1 : i + 1]))
    return out


def ewma(series: list[float], half_life: float) -> float:
    """Exponentially weighted moving average (by half-life, in samples)."""
    if not series:
        return float("nan")
    if half_life <= 0:
        return mean(series)
    lam = math.exp(math.log(0.5) / half_life)
    value = series[0]
    for x in series[1:]:
        value = lam * value + (1 - lam) * x
    return value


def rolling_mean_abs(returns: list[float], window: int) -> list[Optional[float]]:
    out: list[Optional[float]] = []
    for i in range(len(returns)):
        if i + 1 < window:
            out.append(None)
        else:
            seg = returns[i + 1 - window : i + 1]
            out.append(mean([abs(x) for x in seg]) if seg else None)
    return out


def trend_badge(recent: float, prior: float) -> tuple[str, str]:
    if prior <= 0:
        return "N/A", "#94a3b8"
    delta = (recent - prior) / prior
    if delta > 0.05:
        return f"UP {delta*100:.1f}%", "#ff9f1c"
    if delta < -0.05:
        return f"DOWN {abs(delta)*100:.1f}%", "#63f3a3"
    return f"FLAT {delta*100:.1f}%", "#35f3ff"


def state_color(state: str) -> str:
    state = (state or "").upper()
    if state.startswith("TREND_UP"):
        return "#63f3a3"
    if state.startswith("TREND_DOWN"):
        return "#ff5c8a"
    if state == "CHOP":
        return "#ffd166"
    if state == "NEUTRAL":
        return "#35f3ff"
    return "#94a3b8"


def tier_label(value: float, cutoffs: list[float]) -> str:
    if value <= cutoffs[0]:
        return "Very Low"
    if value <= cutoffs[1]:
        return "Low"
    if value <= cutoffs[2]:
        return "Medium"
    if value <= cutoffs[3]:
        return "High"
    return "Very High"


def sparkline_svg(
    values: list[Optional[float]],
    width: int = 380,
    height: int = 100,
    stroke: str = "#35f3ff",
    y_label: str = "bp",
    x_label: str = "time",
) -> str:
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return "<div class='muted'>N/A</div>"
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    vmin, vmax = min(ys), max(ys)
    if vmax - vmin < 1e-12:
        vmax = vmin + 1e-12
    left, right, top, bottom = 28, 8, 8, 18
    plot_w = width - left - right
    plot_h = height - top - bottom
    scale_x = plot_w / (xs[-1] - xs[0] if xs[-1] != xs[0] else 1)
    scale_y = plot_h / (vmax - vmin)
    path = []
    for x, y in pts:
        px = left + (x - xs[0]) * scale_x
        py = top + (vmax - y) * scale_y
        path.append(f"{px:.1f},{py:.1f}")
    d = "M " + " L ".join(path)
    axis = (
        f"<line x1='{left}' y1='{top}' x2='{left}' y2='{height-bottom}' stroke='#1f2937' stroke-width='1'/>"
        f"<line x1='{left}' y1='{height-bottom}' x2='{width-right}' y2='{height-bottom}' stroke='#1f2937' stroke-width='1'/>"
    )
    labels = (
        f"<text x='4' y='{top+10}' fill='#94a3b8' font-size='10' font-family='JetBrains Mono'>{y_label}</text>"
        f"<text x='{width-48}' y='{height-4}' fill='#94a3b8' font-size='10' font-family='JetBrains Mono'>{x_label} →</text>"
    )
    return (
        f"<svg viewBox='0 0 {width} {height}' class='spark'>"
        f"{axis}{labels}"
        f"<path d='{d}' fill='none' stroke='{stroke}' stroke-width='2'/>"
        f"</svg>"
    )


def sigma_scatter_svg(
    times: list[float],
    values: list[Optional[float]],
    hours: float,
    width: int = 520,
    height: int = 170,
    stroke: str = "#35f3ff",
) -> str:
    if not times or not values:
        return "<div class='muted'>N/A</div>"
    cutoff = times[-1] - hours * 3600
    pts = [(t, v) for t, v in zip(times, values) if v is not None and t >= cutoff]
    if len(pts) < 2:
        return "<div class='muted'>N/A</div>"
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    vmin, vmax = min(ys), max(ys)
    if vmax - vmin < 1e-12:
        vmax = vmin + 1e-12

    left, right, top, bottom = 36, 10, 10, 26
    plot_w = width - left - right
    plot_h = height - top - bottom
    span = max(1.0, hours * 3600)

    def x_pos(t: float) -> float:
        return left + ((t - (times[-1] - span)) / span) * plot_w

    def y_pos(v: float) -> float:
        return top + (vmax - v) / (vmax - vmin) * plot_h

    axis = (
        f"<line x1='{left}' y1='{top}' x2='{left}' y2='{height-bottom}' stroke='#1f2937' stroke-width='1'/>"
        f"<line x1='{left}' y1='{height-bottom}' x2='{width-right}' y2='{height-bottom}' stroke='#1f2937' stroke-width='1'/>"
    )

    # y ticks
    y_mid = (vmin + vmax) / 2
    y_ticks = [
        (vmin, f"{vmin:.2f}"),
        (y_mid, f"{y_mid:.2f}"),
        (vmax, f"{vmax:.2f}"),
    ]
    y_labels = "".join(
        f"<text x='2' y='{y_pos(val)+4:.1f}' fill='#94a3b8' font-size='10' font-family='JetBrains Mono'>{lab}</text>"
        for val, lab in y_ticks
    )

    # x ticks (minutes ago)
    minutes = int(round(hours * 60))
    x_ticks = [minutes, minutes // 2, 0]
    x_labels = []
    for m in x_ticks:
        t = times[-1] - m * 60
        x = x_pos(t)
        label = f"{m}m"
        x_labels.append(
            f"<text x='{x-8:.1f}' y='{height-6}' fill='#94a3b8' font-size='10' font-family='JetBrains Mono'>{label}</text>"
        )
    x_labels = "".join(x_labels)

    points = "".join(
        f"<circle cx='{x_pos(t):.1f}' cy='{y_pos(v):.1f}' r='2' fill='{stroke}'/>" for t, v in pts
    )

    labels = (
        f"<text x='4' y='{top+10}' fill='#94a3b8' font-size='10' font-family='JetBrains Mono'>bp</text>"
        f"<text x='{width-90}' y='{height-6}' fill='#94a3b8' font-size='10' font-family='JetBrains Mono'>minutes ago →</text>"
    )

    return (
        f"<svg viewBox='0 0 {width} {height}' class='spark'>"
        f"{axis}{y_labels}{x_labels}{labels}{points}"
        f"</svg>"
    )


def forward_max_moves_ohlc(
    klines: list[Kline],
    horizons: list[int],
    interval_min: int = 15,
) -> tuple[dict[int, list[float]], dict[int, list[float]]]:
    """Forward max move from 15m boundary using OHLC (open as anchor).
    Returns (bps_lists, usd_lists) per horizon.
    """
    out_bps = {h: [] for h in horizons}
    out_usd = {h: [] for h in horizons}
    if not klines:
        return out_bps, out_usd
    for i, k in enumerate(klines):
        dt = datetime.fromtimestamp(k.ts, tz=timezone.utc)
        if dt.minute % interval_min != 0 or dt.second != 0:
            continue
        anchor = k.open
        if anchor <= 0:
            continue
        for h in horizons:
            if i + h >= len(klines):
                continue
            seg = klines[i + 1 : i + h + 1]
            max_high = max(x.high for x in seg)
            min_low = min(x.low for x in seg)
            max_move = max(max_high - anchor, anchor - min_low)
            out_usd[h].append(max_move)
            out_bps[h].append(max_move / anchor)
    return out_bps, out_usd


def label_color(label: str) -> str:
    label = (label or "").upper()
    if label == "CALM":
        return "#63f3a3"
    if label == "NORMAL":
        return "#35f3ff"
    if label == "ELEVATED":
        return "#ffd166"
    if label == "HOT":
        return "#ff9f1c"
    if label == "EXTREME":
        return "#ff5c8a"
    return "#94a3b8"


def render_html(data: dict, out_path: str) -> None:
    # Lightweight, professional UI with subtle neon accents
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>BTC Volatility Report</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&family=JetBrains+Mono:wght@400;600&display=swap');
    :root {{
      --bg: #0a0f1c;
      --panel: #111827;
      --panel-2: #0f172a;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --accent: #35f3ff;
      --accent-2: #63f3a3;
      --border: #1f2937;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: 'Space Grotesk', sans-serif;
      color: var(--text);
      background:
        radial-gradient(800px 500px at 10% 10%, rgba(53,243,255,0.08), transparent),
        radial-gradient(600px 400px at 90% 0%, rgba(99,243,163,0.08), transparent),
        linear-gradient(180deg, #0a0f1c 0%, #0b1224 100%);
    }}
    .wrap {{
      max-width: 1100px;
      margin: 32px auto 48px;
      padding: 0 20px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 16px;
    }}
    h1 {{
      font-size: 28px;
      margin: 0;
      letter-spacing: 0.4px;
    }}
    .meta {{
      color: var(--muted);
      font-size: 14px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 14px;
      margin-top: 16px;
    }}
    .row {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px 16px;
      box-shadow: 0 0 0 1px rgba(255,255,255,0.02);
    }}
    .card h3 {{
      margin: 0 0 8px 0;
      font-size: 13px;
      color: var(--muted);
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.8px;
    }}
    .input {{
      width: 100%;
      padding: 8px 10px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: #0b1224;
      color: var(--text);
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      outline: none;
    }}
    .input:focus {{
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(53,243,255,0.15);
    }}
    .toggle {{
      display: flex;
      gap: 8px;
      margin-top: 6px;
    }}
    .toggle button {{
      flex: 1;
      padding: 8px 10px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: #0b1224;
      color: var(--muted);
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      cursor: pointer;
    }}
    .toggle button.active {{
      color: var(--text);
      border-color: var(--accent);
      background: rgba(53,243,255,0.12);
    }}
    .big {{
      font-size: 22px;
      font-weight: 700;
      font-family: 'JetBrains Mono', monospace;
    }}
    .sub {{
      color: var(--muted);
      font-size: 13px;
    }}
    .badge {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      border: 1px solid rgba(255,255,255,0.08);
      background: rgba(255,255,255,0.03);
    }}
    .section {{
      margin-top: 22px;
    }}
    .section h2 {{
      font-size: 16px;
      margin: 0 0 10px 0;
      color: var(--muted);
      letter-spacing: 0.4px;
      text-transform: uppercase;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
    }}
    th, td {{
      padding: 8px 10px;
      border-bottom: 1px solid var(--border);
      text-align: left;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
    }}
    .bars {{
      display: grid;
      grid-template-columns: 80px 1fr 80px;
      gap: 8px;
      align-items: center;
      margin: 6px 0;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
    }}
    .bar {{
      height: 8px;
      background: #0b1224;
      border: 1px solid var(--border);
      border-radius: 999px;
      overflow: hidden;
    }}
    .bar > span {{
      display: block;
      height: 100%;
      background: linear-gradient(90deg, var(--accent), var(--accent-2));
    }}
    .img {{
      margin-top: 12px;
      border: 1px solid var(--border);
      border-radius: 14px;
      overflow: hidden;
      background: var(--panel-2);
    }}
    .img img {{
      width: 100%;
      display: block;
    }}
    .muted {{
      color: var(--muted);
    }}
    .spark {{
      width: 100%;
      height: 170px;
      margin-top: 6px;
    }}
    .countdown {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: var(--accent);
    }}
    .pill {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid rgba(53,243,255,0.35);
      background: rgba(53,243,255,0.12);
      font-weight: 700;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>BTC Volatility Report</h1>
      <div>
        <div class="meta">Source: {data["source"]} · Lookback: {data["hours"]:.1f}h · {data["timestamp"]}</div>
        <div class="countdown"><span class="pill">Refresh in <span id="refresh-t">{data["refresh_s"]}</span>s</span></div>
      </div>
    </header>

    <div class="grid">
      <div class="card">
        <h3>Last Price</h3>
        <div class="big">{data["last_price"]}</div>
        <div class="sub">Spot close</div>
      </div>
      <div class="card">
        <h3>Health (Current)</h3>
        <div class="big">{data["current_score"]}/100</div>
        <div class="sub"><span class="badge" style="color:{data["current_color"]}">{data["current_label"]}</span></div>
      </div>
      <div class="card">
        <h3>Health (Historical)</h3>
        <div class="big">{data["combined_score"]}/100</div>
        <div class="sub"><span class="badge" style="color:{data["combined_color"]}">{data["combined_label"]}</span></div>
      </div>
      <div class="card">
        <h3>Shock (Last 5m)</h3>
        <div class="big">{data["jump_count"]}</div>
        <div class="sub">jumps ≥ 2σ · 60m rate {data["jump_rate_60m"]} · max {data["max_jump_usd"]} · {data["jump_thresh"]}</div>
        <div class="sub">Last 15m: max {data["jump15_max"]} · median {data["jump15_med"]}</div>
      </div>
    </div>

    <div class="section">
      <h2>Volatility Snapshot</h2>
      <div class="row">
        <div class="card">
          <h3>Volatility Metrics</h3>
          <table>
            <tr><th>Metric</th><th>Value</th></tr>
            <tr><td>Long σ (6h)</td><td>{data["long_sigma_bps"]} (~{data["long_sigma_usd"]})</td></tr>
            <tr><td>Short 30m σ</td><td>{data["short30"]}</td></tr>
            <tr><td>Short 60m σ</td><td>{data["short60"]}</td></tr>
            <tr><td>Regime (30m/long)</td><td>{data["regime"]}</td></tr>
          </table>
        </div>
        <div class="card">
          <h3>Movement (1m)</h3>
          <table>
            <tr><th>Metric</th><th>Value</th></tr>
            <tr><td>Avg 1m move (abs, close‑to‑close)</td><td>{data["avg_move"]}</td></tr>
            <tr><td>Trend (avg move, last 5m vs prior 5m)</td><td><span class="badge" style="color:{data["trend_color"]}">{data["trend_label"]}</span></td></tr>
          </table>
        </div>
      </div>
    </div>

    <div class="section">
      <h2>Strike Buffer Guide</h2>
      <table>
        <tr><th>Minutes Left</th><th>90% Hybrid</th><th>95% Hybrid</th><th>90% M/E/H (bp)</th><th>95% M/E/H (bp)</th></tr>
        {data["buffer_rows"]}
      </table>
    </div>

    <div class="section">
      <h2>Market State (last 15m)</h2>
      <div class="card">
        <table>
          <tr><th>Metric</th><th>Value</th></tr>
          <tr><td>State</td><td><span class="badge" style="color:{data["state_color"]}">{data["state_label"]}</span></td></tr>
          <tr><td>Efficiency Ratio</td><td>{data["state_er"]}</td></tr>
          <tr><td>Persistence</td><td>{data["state_persist"]}</td></tr>
          <tr><td>Run Length</td><td>{data["state_run"]}</td></tr>
        </table>
      </div>
    </div>

    <div class="section">
      <h2>Historical Context (NY Half‑Hour)</h2>
      <div class="card">
        <table>
          <tr><th>Metric</th><th>Value</th></tr>
          <tr><td>Current Block</td><td>{data["hist_time"]}</td></tr>
          <tr><td>Baseline Vol</td><td><span class="badge">{data["hist_vol_label"]}</span> ({data["hist_vol"]})</td></tr>
          <tr><td>Baseline Jump</td><td><span class="badge">{data["hist_jump_label"]}</span> ({data["hist_jump"]})</td></tr>
          <tr><td>Current Vol (60m)</td><td>{data["hist_current_vol"]}</td></tr>
          <tr><td>Current Jump (60m)</td><td>{data["hist_current_jump"]}</td></tr>
        </table>
      </div>
    </div>

    <div class="section">
      <h2>Binary Probability Calculator (Black‑Scholes)</h2>
      <div class="row">
        <div class="card">
          <h3>Inputs</h3>
          <label class="sub">Strike price</label>
          <input class="input" id="calc-strike" placeholder="e.g. 67000.00" />
          <div class="sub" style="margin-top:10px;">Market length</div>
          <div class="toggle" id="calc-toggle">
            <button data-tf="15">15m</button>
            <button data-tf="5">5m</button>
          </div>
          <div class="sub" style="margin-top:10px;">Vol input: {data["calc_sigma_label"]}</div>
          <div class="sub">Spot (Chainlink): <span id="calc-spot">{data["calc_spot"]}</span></div>
          <div class="sub">Time left: <span id="calc-time">—</span></div>
        </div>
        <div class="card">
          <h3>Output (updates every 10s)</h3>
          <div class="big" id="calc-prob">—</div>
          <div class="sub" id="calc-details">—</div>
        </div>
      </div>
    </div>

    <div class="section">
      <h2>Micro (Chainlink 1s)</h2>
      <div class="card">
        <table>
          <tr><th>Metric</th><th>Value</th></tr>
          <tr><td>Window</td><td id="micro-window">{data["micro_window"]}m</td></tr>
          <tr><td>σ (1s)</td><td id="micro-sigma">—</td></tr>
          <tr><td>σ (15m, 1m‑eq)</td><td id="micro-sigma15">—</td></tr>
          <tr><td>2σ Threshold</td><td id="micro-2sigma">—</td></tr>
          <tr><td>Jumps ≥2σ</td><td id="micro-jumps">—</td></tr>
          <tr><td>Max 1s jump</td><td id="micro-max">—</td></tr>
          <tr><td>Samples</td><td id="micro-samples">—</td></tr>
        </table>
      </div>
    </div>

    <div class="section">
      <h2>Jump Activity (last 60m, 5m buckets, ≥2σ)</h2>
      {data["jump_bars"]}
    </div>

  </div>
  <script>
    window.__REPORT__ = {{
      spot: {data["calc_spot_num"]},
      sigma1m: {data["calc_sigma1m"]},
      sigmaLabel: "{data["calc_sigma_label"]}"
    }};

    function erf(x) {{
      // Abramowitz & Stegun approximation
      const sign = x >= 0 ? 1 : -1;
      x = Math.abs(x);
      const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741;
      const a4 = -1.453152027, a5 = 1.061405429, p = 0.3275911;
      const t = 1 / (1 + p * x);
      const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);
      return sign * y;
    }}
    function normCdf(x) {{
      return 0.5 * (1 + erf(x / Math.SQRT2));
    }}
    function timeLeft(tfMin) {{
      const tfMs = tfMin * 60 * 1000;
      const now = Date.now();
      const rem = now % tfMs;
      const leftMs = rem === 0 ? tfMs : (tfMs - rem);
      const leftSec = Math.max(0, Math.round(leftMs / 1000));
      const m = Math.floor(leftSec / 60);
      const s = leftSec % 60;
      return {{ minutes: leftSec / 60, mm: m, ss: s }};
    }}
    function calcProb(S, K, tMin, sigma1m) {{
      if (!S || !K || sigma1m <= 0) {{
        return null;
      }}
      const t = Math.max(tMin, 1e-6);
      const sigT = sigma1m * Math.sqrt(t);
      if (sigT <= 0) {{
        return S > K ? 1 : 0;
      }}
      const d2 = (Math.log(S / K) - 0.5 * sigT * sigT) / sigT;
      return normCdf(d2);
    }}

    const strikeInput = document.getElementById("calc-strike");
    const toggle = document.getElementById("calc-toggle");
    const probEl = document.getElementById("calc-prob");
    const detailEl = document.getElementById("calc-details");
    const spotEl = document.getElementById("calc-spot");
    const microSigmaEl = document.getElementById("micro-sigma");
    const microSigma15El = document.getElementById("micro-sigma15");
    const micro2sEl = document.getElementById("micro-2sigma");
    const microJumpsEl = document.getElementById("micro-jumps");
    const microMaxEl = document.getElementById("micro-max");
    const microSamplesEl = document.getElementById("micro-samples");

    let tf = parseInt(localStorage.getItem("calc_tf") || "15", 10);
    let strike = localStorage.getItem("calc_strike") || "";
    strikeInput.value = strike;

    function setActive() {{
      toggle.querySelectorAll("button").forEach(btn => {{
        btn.classList.toggle("active", parseInt(btn.dataset.tf, 10) === tf);
      }});
    }}
    setActive();

    toggle.addEventListener("click", (e) => {{
      if (!e.target.dataset.tf) return;
      tf = parseInt(e.target.dataset.tf, 10);
      localStorage.setItem("calc_tf", tf.toString());
      setActive();
      updateCalc();
    }});
    strikeInput.addEventListener("input", (e) => {{
      strike = e.target.value.replace(/,/g, "");
      localStorage.setItem("calc_strike", strike);
      updateCalc();
    }});

    function updateCalc() {{
      const S = window.__REPORT__.spot;
      const sigma1m = window.__REPORT__.sigma1m;
      const K = parseFloat(strike);
      if (!S || !sigma1m || !K) {{
        probEl.textContent = "—";
        detailEl.textContent = "Enter strike to compute.";
        return;
      }}
      const t = timeLeft(tf);
      const tMin = t.minutes;
      const timeEl = document.getElementById("calc-time");
      if (timeEl) {{
        const mm = t.mm.toString();
        const ss = t.ss.toString().padStart(2, "0");
        timeEl.textContent = mm + ":" + ss;
      }}
      const pYes = calcProb(S, K, tMin, sigma1m);
      if (pYes === null) {{
        probEl.textContent = "—";
        detailEl.textContent = "Insufficient data.";
        return;
      }}
      const pNo = 1 - pYes;
      probEl.textContent = `YES ${{(pYes*100).toFixed(1)}}% / NO ${{(pNo*100).toFixed(1)}}%`;
      detailEl.textContent = `Spot ${{S.toFixed(2)}} · K ${{K.toFixed(2)}} · t ${{tMin.toFixed(2)}}m · σ1m ${{ (sigma1m*10000).toFixed(2) }} bp`;
    }}

    function stdev(arr) {{
      const n = arr.length;
      if (n < 2) return 0;
      const mean = arr.reduce((a, b) => a + b, 0) / n;
      let v = 0;
      for (const x of arr) v += (x - mean) * (x - mean);
      return Math.sqrt(v / (n - 1));
    }}

    const micro = {{
      windowSec: {data["micro_window"]} * 60,
      secs: [],
      prices: []
    }};

    function appendMicro(sec, price) {{
      if (!micro.secs.length) {{
        micro.secs.push(sec);
        micro.prices.push(price);
        return;
      }}
      const lastSec = micro.secs[micro.secs.length - 1];
      if (sec <= lastSec) {{
        micro.prices[micro.prices.length - 1] = price;
        return;
      }}
      const lastPrice = micro.prices[micro.prices.length - 1];
      if (sec - lastSec > 1) {{
        for (let s = lastSec + 1; s < sec; s++) {{
          micro.secs.push(s);
          micro.prices.push(lastPrice);
        }}
      }}
      micro.secs.push(sec);
      micro.prices.push(price);
      while (micro.secs.length > micro.windowSec) {{
        micro.secs.shift();
        micro.prices.shift();
      }}
    }}

    function updateMicro() {{
      if (micro.prices.length < 5) return;
      const returns = [];
      for (let i = 1; i < micro.prices.length; i++) {{
        const p0 = micro.prices[i - 1];
        const p1 = micro.prices[i];
        if (p0 > 0 && p1 > 0) {{
          returns.push(Math.log(p1 / p0));
        }}
      }}
      if (returns.length < 5) return;
      const sigma = stdev(returns);
      const spot = window.__REPORT__.spot || micro.prices[micro.prices.length - 1];
      const sigmaBp = sigma * 10000;
      const sigmaUsd = sigma * spot;
      const sigma1mEq = sigma * Math.sqrt(60);
      const sigma15mEq = sigma1mEq * Math.sqrt(15);
      const sigma15mBp = sigma15mEq * 10000;
      const sigma15mUsd = sigma15mEq * spot;
      const twoSigmaUsd = 2 * sigma * spot;
      let jumps = 0;
      let maxJump = 0;
      for (const r of returns) {{
        const a = Math.abs(r);
        if (a >= 2 * sigma) jumps += 1;
        if (a > maxJump) maxJump = a;
      }}
      const maxJumpUsd = maxJump * spot;
      if (microSigmaEl) microSigmaEl.textContent = `${{sigmaBp.toFixed(2)}} bp (~${{sigmaUsd.toFixed(0)}})`;
      if (microSigma15El) microSigma15El.textContent = `${{sigma15mBp.toFixed(2)}} bp (~${{sigma15mUsd.toFixed(0)}})`;
      if (micro2sEl) micro2sEl.textContent = `~${{twoSigmaUsd.toFixed(0)}}`;
      if (microJumpsEl) microJumpsEl.textContent = `${{jumps}} / ${{returns.length}}`;
      if (microMaxEl) microMaxEl.textContent = `~${{maxJumpUsd.toFixed(0)}}`;
      if (microSamplesEl) microSamplesEl.textContent = `${{returns.length}}`;
    }}
    function connectChainlink() {{
      const ws = new WebSocket("wss://ws-live-data.polymarket.com");
      let pingTimer = null;
      ws.addEventListener("open", () => {{
        const msg = {{
          action: "subscribe",
          subscriptions: [
            {{
              topic: "crypto_prices_chainlink",
              type: "*",
              filters: "{{\\"symbol\\":\\"btc/usd\\"}}"
            }}
          ]
        }};
        ws.send(JSON.stringify(msg));
        pingTimer = setInterval(() => {{
          try {{ ws.send("PING"); }} catch (_) {{}}
        }}, 5000);
      }});
      ws.addEventListener("message", (event) => {{
        try {{
          const data = JSON.parse(event.data);
          if (data.topic === "crypto_prices_chainlink" && data.payload && data.payload.value) {{
            const price = parseFloat(data.payload.value);
            if (!Number.isNaN(price)) {{
              window.__REPORT__.spot = price;
              if (spotEl) {{
                spotEl.textContent = price.toFixed(2);
              }}
              const sec = Math.floor(Date.now() / 1000);
              appendMicro(sec, price);
              updateMicro();
              updateCalc();
            }}
          }}
        }} catch (_) {{}}
      }});
      ws.addEventListener("close", () => {{
        if (pingTimer) clearInterval(pingTimer);
        setTimeout(connectChainlink, 2000);
      }});
      ws.addEventListener("error", () => {{
        try {{ ws.close(); }} catch (_) {{}}
      }});
    }}

    updateCalc();
    setInterval(updateCalc, 1000);
    setInterval(updateMicro, 2000);
    connectChainlink();

    let t = {data["refresh_s"]};
    const el = document.getElementById("refresh-t");
    setInterval(() => {{
      t -= 1;
      if (t <= 0) {{
        location.reload();
      }} else {{
        el.textContent = t;
      }}
    }}, 1000);
  </script>
</body>
</html>
"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)




def maybe_plot(times, sig30, sig60, long_sigma, lo90, hi90, lo95, hi95, out_path: str):
    try:
        import matplotlib.pyplot as plt
        from datetime import datetime

        x = [datetime.fromtimestamp(t) for t in times]
        y30 = [s * 10000 if s is not None else None for s in sig30]
        y60 = [s * 10000 if s is not None else None for s in sig60]

        plt.figure(figsize=(12, 5))
        plt.plot(x, y30, label="Rolling 30m sigma (bp)", color="#1f77b4")
        plt.plot(x, y60, label="Rolling 60m sigma (bp)", color="#ff7f0e")

        if long_sigma > 0:
            plt.axhline(long_sigma * 10000, color="black", linestyle="--", linewidth=1, label="Long sigma")
        if lo90 > 0 and hi90 > 0:
            plt.axhline(lo90 * 10000, color="green", linestyle=":", linewidth=1, label="Long sigma 90% CI")
            plt.axhline(hi90 * 10000, color="green", linestyle=":", linewidth=1)
        if lo95 > 0 and hi95 > 0:
            plt.axhline(lo95 * 10000, color="red", linestyle=":", linewidth=1, label="Long sigma 95% CI")
            plt.axhline(hi95 * 10000, color="red", linestyle=":", linewidth=1)

        plt.title("BTC Volatility Report (Rolling Sigma)")
        plt.xlabel("Time")
        plt.ylabel("Sigma (bp per 1m)")
        plt.grid(True, alpha=0.3)
        plt.legend()
        plt.tight_layout()
        plt.savefig(out_path, dpi=160)
        plt.close()
        return True
    except Exception:
        return False


def run_once(args, refresh_s: int) -> None:
    if args.source == "kraken":
        klines = fetch_1m_klines_kraken(args.hours)
    elif args.source == "bitstamp":
        klines = fetch_1m_klines_bitstamp(args.hours)
    else:
        klines = fetch_1m_klines_binance("BTCUSDT", args.hours)
    if len(klines) < 3:
        print("Not enough data.")
        return

    returns = log_returns(klines)
    closes = [k.close for k in klines]
    times = [k.ts for k in klines]
    long_sigma = stdev(returns)
    lo90, hi90 = sigma_ci(long_sigma, len(returns), 0.10)
    lo95, hi95 = sigma_ci(long_sigma, len(returns), 0.05)

    short_n = max(2, args.short_min)
    short2_n = max(2, args.short2_min)
    short_sigma = stdev(returns[-short_n:]) if len(returns) >= short_n else long_sigma
    short2_sigma = stdev(returns[-short2_n:]) if len(returns) >= short2_n else long_sigma
    recent_returns = returns[-short2_n:] if len(returns) >= short2_n else returns

    # Mean absolute 1m movement (simple & EWMA)
    abs_returns = [abs(r) for r in returns]
    mean_abs_30m = mean(abs_returns[-short_n:]) if len(abs_returns) >= short_n else mean(abs_returns)
    mean_abs_60m = mean(abs_returns[-short2_n:]) if len(abs_returns) >= short2_n else mean(abs_returns)
    mean_abs_ewma_30 = ewma(abs_returns, half_life=30.0)

    # Trend badge (last 5m vs prior 5m average abs move)
    trend_label = "N/A"
    trend_color = "#94a3b8"
    if len(abs_returns) >= 10:
        recent = mean(abs_returns[-5:])
        prior = mean(abs_returns[-10:-5])
        trend_label, trend_color = trend_badge(recent, prior)

    # Market state (momentum vs chop) on last 15m
    window = min(15, len(returns))
    seg = returns[-window:] if window > 0 else []
    net = sum(seg) if seg else 0.0
    abs_sum = sum(abs(r) for r in seg) if seg else 0.0
    er = abs(net) / abs_sum if abs_sum > 0 else 0.0
    direction = "UP" if net > 0 else ("DOWN" if net < 0 else "FLAT")
    if er >= 0.6:
        state_label = f"TREND_{direction}"
    elif er <= 0.2:
        state_label = "CHOP"
    else:
        state_label = "NEUTRAL"
    if net == 0:
        persist = 0.0
    else:
        sign = 1 if net > 0 else -1
        persist = sum(1 for r in seg if (r > 0) == (sign > 0)) / max(1, len(seg))
    run_len = 0
    if net != 0:
        for r in reversed(seg):
            if (r > 0) == (net > 0):
                run_len += 1
            else:
                break

    regime_score, regime_label = health_score(short_sigma, long_sigma)
    regime_ratio = (short_sigma / long_sigma) if long_sigma > 0 else None

    # Jump stats (last 5m/15m) + $ magnitude from 1m closes
    last5 = returns[-5:] if len(returns) >= 5 else returns
    last5_d = [abs(closes[-i] - closes[-i - 1]) for i in range(1, min(6, len(closes)))]
    jump_thresh = 2 * long_sigma
    jumps = [r for r in last5 if abs(r) >= jump_thresh]
    max_jump = max((abs(r) for r in last5), default=0.0)
    max_jump_usd = max(last5_d) if last5_d else 0.0
    last15 = returns[-15:] if len(returns) >= 15 else returns
    last15_abs = [abs(r) for r in last15] if last15 else []
    last15_max = max(last15_abs) if last15_abs else 0.0
    last15_med = median(last15_abs) if last15_abs else 0.0
    # Recent 60m jump rate
    recent_jump_rate = None
    if recent_returns and jump_thresh > 0:
        recent_jump_rate = sum(1 for r in recent_returns if abs(r) >= jump_thresh) / len(recent_returns)

    # Historical half-hour context (NY), if available
    hist_block = {
        "time": "N/A",
        "vol": "N/A",
        "jump": "N/A",
        "vol_label": "N/A",
        "jump_label": "N/A",
        "health_score": None,
        "health_label": "N/A",
        "current_vol": "N/A",
        "current_jump": "N/A",
        "vol_ratio": None,
        "jump_ratio": None,
    }
    stats_path = os.path.join("analysis_time_patterns", "halfhour_stats_ny.csv")
    stats_dow_path = os.path.join("analysis_time_patterns", "halfhour_stats_ny_dow.csv")
    summary_path = os.path.join("analysis_time_patterns", "summary.txt")
    global_sigma_bp = None
    if os.path.exists(summary_path):
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                for line in f:
                    if "Global sigma (1m returns):" in line:
                        parts = line.strip().split(":")
                        if len(parts) >= 2 and "bp" in parts[1]:
                            val = parts[1].strip().replace("bp", "").strip()
                            global_sigma_bp = float(val)
                        break
        except Exception:
            global_sigma_bp = None
    if os.path.exists(stats_path) or os.path.exists(stats_dow_path):
        rows = []
        now_ny = datetime.now(ZoneInfo("America/New_York"))
        hh_now = now_ny.hour * 2 + (1 if now_ny.minute >= 30 else 0)
        wday_now = now_ny.strftime("%A")

        if os.path.exists(stats_dow_path):
            with open(stats_dow_path, "r", encoding="utf-8") as f:
                r = csv.DictReader(f)
                for row in r:
                    try:
                        wday = row.get("ny_wday", "")
                        hh = int(row.get("ny_halfhour") or 0)
                        mean_abs = float(row.get("mean_abs_ret", "nan"))
                        jump2 = float(row.get("jump2_rate", "nan"))
                        rows.append((wday, hh, mean_abs, jump2))
                    except Exception:
                        continue
        elif os.path.exists(stats_path):
            with open(stats_path, "r", encoding="utf-8") as f:
                r = csv.DictReader(f)
                for row in r:
                    try:
                        hh = int(row.get("ny_halfhour") or row.get("Unnamed: 0") or 0)
                        mean_abs = float(row.get("mean_abs_ret", "nan"))
                        jump2 = float(row.get("jump2_rate", "nan"))
                        rows.append(("", hh, mean_abs, jump2))
                    except Exception:
                        continue

        day_rows = [x for x in rows if x[0] == wday_now] if rows and rows[0][0] else []
        use_rows = day_rows if day_rows else [(None, hh, m, j) for (_, hh, m, j) in rows]

        if use_rows:
            mean_list = [x[2] for x in use_rows if not math.isnan(x[2])]
            jump_list = [x[3] for x in use_rows if not math.isnan(x[3])]
            if mean_list and jump_list:
                mean_cuts = [
                    empirical_quantile(mean_list, 0.2),
                    empirical_quantile(mean_list, 0.4),
                    empirical_quantile(mean_list, 0.6),
                    empirical_quantile(mean_list, 0.8),
                ]
                jump_cuts = [
                    empirical_quantile(jump_list, 0.2),
                    empirical_quantile(jump_list, 0.4),
                    empirical_quantile(jump_list, 0.6),
                    empirical_quantile(jump_list, 0.8),
                ]
                match = next((x for x in use_rows if x[1] == hh_now), None)
                if match:
                    mean_abs = match[2]
                    jump2 = match[3]
                    current_mean = mean_abs_60m if not math.isnan(mean_abs_60m) else mean_abs
                    # Current jump rate over last 60m using global sigma (from summary) if available
                    if global_sigma_bp is not None:
                        global_sigma = global_sigma_bp / 10000.0
                    else:
                        global_sigma = long_sigma
                    recent_jump_rate = None
                    if recent_returns and global_sigma > 0:
                        recent_jump_rate = sum(1 for r in recent_returns if abs(r) >= 2 * global_sigma) / len(recent_returns)
                    # Ratios vs historical baseline for this block
                    vol_ratio = current_mean / mean_abs if mean_abs > 0 else None
                    jump_ratio = (recent_jump_rate / jump2) if (recent_jump_rate is not None and jump2 > 0) else None
                    # Combined score using ratios (vol 60%, jump 40%)
                    if vol_ratio is not None and jump_ratio is not None:
                        combo_ratio = 0.6 * vol_ratio + 0.4 * jump_ratio
                    elif vol_ratio is not None:
                        combo_ratio = vol_ratio
                    else:
                        combo_ratio = 1.0
                    health_score_val = max(0.0, min(100.0, 50.0 * (math.log(combo_ratio, 2) + 1.0))) if combo_ratio > 0 else 0.0
                    health_label = tier_label(combo_ratio, [0.8, 0.95, 1.05, 1.25])
                    hist_block = {
                        "time": f"{now_ny.hour:02d}:{'30' if now_ny.minute>=30 else '00'} NY",
                        "vol": fmt_bps(mean_abs),
                        "jump": fmt_pct(jump2),
                        "vol_label": tier_label(mean_abs, mean_cuts),
                        "jump_label": tier_label(jump2, jump_cuts),
                        "health_score": health_score_val,
                        "health_label": health_label,
                        "current_vol": fmt_bps(current_mean),
                        "current_jump": fmt_pct(recent_jump_rate) if recent_jump_rate is not None else "N/A",
                        "vol_ratio": vol_ratio,
                        "jump_ratio": jump_ratio,
                    }

    # Sigma bucket counts removed from HTML to reduce clutter.

    # Expected move per 1m (long + short window)
    exp90_long = 1.645 * long_sigma
    exp95_long = 1.96 * long_sigma
    exp90_short2 = 1.645 * short2_sigma
    exp95_short2 = 1.96 * short2_sigma

    # Empirical buffers by horizon (1-6m) using recent 60m window
    empirical_buffers: dict[int, dict[str, float]] = {}
    coverage_90 = None
    coverage_95 = None
    for t in range(1, 7):
        t_rets = aggregated_returns(recent_returns, t)
        abs_vals = [abs(r) for r in t_rets]
        emp90 = empirical_quantile(abs_vals, 0.90)
        emp95 = empirical_quantile(abs_vals, 0.95)
        model90 = 1.645 * short2_sigma * math.sqrt(t)
        model95 = 1.96 * short2_sigma * math.sqrt(t)
        empirical_buffers[t] = {
            "model90": model90,
            "model95": model95,
            "emp90": emp90,
            "emp95": emp95,
            "hybrid90": max(model90, emp90),
            "hybrid95": max(model95, emp95),
        }
        if t == 1 and abs_vals:
            coverage_90 = sum(1 for r in abs_vals if r <= model90) / len(abs_vals)
            coverage_95 = sum(1 for r in abs_vals if r <= model95) / len(abs_vals)

    # Short-window (30m) empirical/model for apples-to-apples
    short_abs = abs_returns[-short_n:] if len(abs_returns) >= short_n else abs_returns
    short_emp90 = empirical_quantile(short_abs, 0.90)
    short_emp95 = empirical_quantile(short_abs, 0.95)
    short_model90 = 1.645 * short_sigma
    short_model95 = 1.96 * short_sigma

    print("=" * 80)
    print("BTC Volatility Report (1m returns)")
    print(f"Source: {args.source} | Lookback: {args.hours:.1f}h | Samples: {len(returns)}")
    last_px = closes[-1] if closes else None
    if last_px:
        long_sigma_usd = long_sigma * last_px
        short_sigma_usd = short_sigma * last_px
        short2_sigma_usd = short2_sigma * last_px
        exp90_usd = exp90_long * last_px
        exp95_usd = exp95_long * last_px
        exp90_60_usd = exp90_short2 * last_px
        exp95_60_usd = exp95_short2 * last_px
        mean_abs_30m_usd = mean_abs_30m * last_px
        mean_abs_60m_usd = mean_abs_60m * last_px
        mean_abs_ewma_usd = mean_abs_ewma_30 * last_px
    else:
        long_sigma_usd = short_sigma_usd = short2_sigma_usd = exp90_usd = exp95_usd = 0.0
        mean_abs_30m_usd = mean_abs_60m_usd = mean_abs_ewma_usd = 0.0

    print(f"Last price: {last_px:.2f}" if last_px else "Last price: N/A")
    print(f"Long sigma: {fmt_bps(long_sigma)} (~{fmt_usd(long_sigma_usd)} per 1m)")
    print(f"90% CI: [{fmt_bps(lo90)}, {fmt_bps(hi90)}] | 95% CI: [{fmt_bps(lo95)}, {fmt_bps(hi95)}]")
    print(f"Short {args.short_min}m sigma: {fmt_bps(short_sigma)} (~{fmt_usd(short_sigma_usd)} per 1m)")
    print(f"Short {args.short2_min}m sigma: {fmt_bps(short2_sigma)} (~{fmt_usd(short2_sigma_usd)} per 1m)")
    print(
        f"Avg 1m move (abs, close-to-close): 30m={fmt_bps(mean_abs_30m)} (~{fmt_usd(mean_abs_30m_usd)}), "
        f"60m={fmt_bps(mean_abs_60m)} (~{fmt_usd(mean_abs_60m_usd)}), "
        f"EWMA(hl=30m)={fmt_bps(mean_abs_ewma_30)} (~{fmt_usd(mean_abs_ewma_usd)})"
    )
    print(f"Market state (last {window}m): {state_label} | ER={er:.2f} | persist={persist*100:.0f}% | run={run_len}m")
    if long_sigma > 0:
        ratio_short = regime_ratio if regime_ratio is not None else (short_sigma / long_sigma)
        ratio_short2 = short2_sigma / long_sigma
        print(f"Ratios vs long σ: 30m={ratio_short:.2f}x | 60m={ratio_short2:.2f}x")
        print(f"Regime: {regime_label} (30m/long σ={ratio_short:.2f}x)")
    current_score, current_label = health_score(short_sigma, short2_sigma)
    print(f"Health score (current 30m vs 60m): {current_score:.0f}/100 ({current_label})")
    if hist_block["health_score"] is not None:
        print(
            f"Health score (historical block): {hist_block['health_score']:.0f}/100 ({hist_block['health_label']}) | "
            f"vol {hist_block['current_vol']} vs {hist_block['vol']} | "
            f"jump {hist_block['current_jump']} vs {hist_block['jump']}"
        )
    else:
        print("Health score (historical block): N/A")
    if last_px:
        print(
            f"Expected 1m move (60m σ): 90% ±{fmt_bps(exp90_short2)} (~{fmt_usd(exp90_60_usd)}) | "
            f"95% ±{fmt_bps(exp95_short2)} (~{fmt_usd(exp95_60_usd)})"
        )
    else:
        print(f"Expected 1m move: 90% ±{fmt_bps(exp90)} | 95% ±{fmt_bps(exp95)}")
    if last_px and 1 in empirical_buffers:
        emp90_1m = empirical_buffers[1]["emp90"]
        emp95_1m = empirical_buffers[1]["emp95"]
        if not math.isnan(emp90_1m) and not math.isnan(emp95_1m):
            print(
                f"Empirical 1m move: 90% ±{fmt_bps(emp90_1m)} (~{fmt_usd(emp90_1m * last_px)}) | "
                f"95% ±{fmt_bps(emp95_1m)} (~{fmt_usd(emp95_1m * last_px)})"
            )
            gap90 = (emp90_1m / exp90_short2 - 1.0) if exp90_short2 > 0 else 0.0
            gap95 = (emp95_1m / exp95_short2 - 1.0) if exp95_short2 > 0 else 0.0
            print(f"Tail gap (empirical vs model): 90% {gap90:+.0%} | 95% {gap95:+.0%}")
    if last_px and not math.isnan(short_emp90) and not math.isnan(short_emp95):
        print(
            f"Short-window 30m move: model 90% ±{fmt_bps(short_model90)} (~{fmt_usd(short_model90*last_px)}) | "
            f"95% ±{fmt_bps(short_model95)} (~{fmt_usd(short_model95*last_px)}) "
            f"|| empirical 90% ±{fmt_bps(short_emp90)} (~{fmt_usd(short_emp90*last_px)}) | "
            f"95% ±{fmt_bps(short_emp95)} (~{fmt_usd(short_emp95*last_px)})"
        )
    if coverage_90 is not None and coverage_95 is not None:
        print(f"Coverage check (1m, model): 90% -> {coverage_90*100:.1f}% | 95% -> {coverage_95*100:.1f}%")
    if hist_block["time"] != "N/A":
        print(
            f"Historical block (NY half-hour): {hist_block['time']} | "
            f"Vol {hist_block['vol_label']} ({hist_block['vol']}) | "
            f"Jump {hist_block['jump_label']} ({hist_block['jump']})"
        )
    two_sigma_usd = (2 * long_sigma * last_px) if (last_px and long_sigma > 0) else 0.0
    three_sigma_usd = (3 * long_sigma * last_px) if (last_px and long_sigma > 0) else 0.0
    print(
        f"Jump stats (last 5m): jumps>=2σ={len(jumps)} | max jump={fmt_bps(max_jump)} "
        f"(~${max_jump_usd:.0f}) | 2σ≈{fmt_usd(two_sigma_usd)} 3σ≈{fmt_usd(three_sigma_usd)}"
    )
    if last_px and long_sigma > 0:
        print("Strike buffer guide (hybrid recommended):")
        print("  t | 90% hybrid | 95% hybrid | 90% M/E/H (bp) | 95% M/E/H (bp)")
        for t in range(1, 7):
            buf = empirical_buffers.get(t)
            if not buf:
                continue
            m90, e90, h90 = buf["model90"], buf["emp90"], buf["hybrid90"]
            m95, e95, h95 = buf["model95"], buf["emp95"], buf["hybrid95"]
            h90_usd = fmt_usd(h90 * last_px)
            h95_usd = fmt_usd(h95 * last_px)
            print(
                f" {t:>2} | {h90_usd:>9} | {h95_usd:>9} | "
                f"{fmt_bps(m90)}/{fmt_bps(e90)}/{fmt_bps(h90)} | "
                f"{fmt_bps(m95)}/{fmt_bps(e95)}/{fmt_bps(h95)}"
            )
    recent_klines = klines[-(short2_n + 1) :] if len(klines) >= (short2_n + 1) else klines
    boundary_bps, boundary_usd = forward_max_moves_ohlc(recent_klines, list(range(1, 6)), interval_min=15)
    if last_px:
        print("15m boundary forward max move (open to high/low, 90% / 95%):")
        print("  t | 90% max | 95% max | samples")
        for t in range(1, 6):
            vals_bps = boundary_bps.get(t, [])
            vals_usd = boundary_usd.get(t, [])
            if not vals_bps or not vals_usd:
                continue
            p90 = empirical_quantile(vals_bps, 0.90)
            p95 = empirical_quantile(vals_bps, 0.95)
            p90_usd = empirical_quantile(vals_usd, 0.90)
            p95_usd = empirical_quantile(vals_usd, 0.95)
            print(
                f" {t:>2} | {fmt_bps(p90)} (~{fmt_usd(p90_usd)}) | "
                f"{fmt_bps(p95)} (~{fmt_usd(p95_usd)}) | {len(vals_bps)}"
            )
    # Sigma bucket counts removed from console for a leaner report.

    if args.plot:
        times = [k.ts for k in klines[1:]]
        sig30 = rolling_sigma(returns, max(2, args.short_min))
        sig60 = rolling_sigma(returns, max(2, args.short2_min))
        out_path = "vol_report.png"
        ok = maybe_plot(times, sig30, sig60, long_sigma, lo90, hi90, lo95, hi95, out_path)
        if ok:
            print(f"Plot saved to {out_path}")
        else:
            print("Plot requested but matplotlib not available.")

    # Optional HTML report
    if args.html or args.serve:
        buffer_rows = []
        if last_px and long_sigma > 0:
            for t in range(1, 7):
                buf = empirical_buffers.get(t)
                if not buf:
                    continue
                m90, e90, h90 = buf["model90"], buf["emp90"], buf["hybrid90"]
                m95, e95, h95 = buf["model95"], buf["emp95"], buf["hybrid95"]
                h90_usd = fmt_usd(h90 * last_px)
                h95_usd = fmt_usd(h95 * last_px)
                buffer_rows.append(
                    f"<tr><td>{t}m</td><td>{h90_usd}</td><td>{h95_usd}</td>"
                    f"<td>{fmt_bps(m90)}/{fmt_bps(e90)}/{fmt_bps(h90)}</td>"
                    f"<td>{fmt_bps(m95)}/{fmt_bps(e95)}/{fmt_bps(h95)}</td></tr>"
                )
        ratio_short = short_sigma / long_sigma if long_sigma > 0 else None
        regime_ratio = ratio_short

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        # Jump bars (last 60m, 5m buckets) using 2σ threshold
        jump_bars = []
        if long_sigma > 0 and returns:
            window = min(60, len(returns))
            sub = returns[-window:]
            bucket = 5
            buckets = [sub[i : i + bucket] for i in range(0, len(sub), bucket)]
            counts = [sum(1 for r in b if abs(r) >= jump_thresh) for b in buckets]
            max_count = max(counts) if counts else 0
            total_min = len(sub)
            for i, b in enumerate(buckets):
                start = max(0, total_min - (i + 1) * bucket)
                end = max(0, total_min - i * bucket)
                label = f"{start:02d}-{end:02d}m"
                count = counts[i]
                max_abs = max((abs(r) for r in b), default=0.0)
                max_usd = max_abs * last_px if last_px else 0.0
                width = 0 if max_count == 0 else int(round((count / max_count) * 100))
                jump_bars.append(
                    f"<div class='bars'><div>{label}</div><div class='bar'><span style='width:{width}%'></span></div>"
                    f"<div>{count} | {fmt_usd(max_usd)}</div></div>"
                )
        if not jump_bars:
            jump_bars.append("<div class='muted'>N/A</div>")

        # 15m boundary forward max moves (close-to-close)
        boundary_rows = []
        boundary_bps, boundary_usd = forward_max_moves_ohlc(klines, list(range(1, 6)), interval_min=15)
        for h in range(1, 6):
            vals_bps = boundary_bps.get(h, [])
            vals_usd = boundary_usd.get(h, [])
            if not vals_bps or not vals_usd:
                continue
            p90 = empirical_quantile(vals_bps, 0.90)
            p95 = empirical_quantile(vals_bps, 0.95)
            p90_usd = fmt_usd(empirical_quantile(vals_usd, 0.90))
            p95_usd = fmt_usd(empirical_quantile(vals_usd, 0.95))
            boundary_rows.append(
                f"<tr><td>{h}m</td><td>{fmt_bps(p90)} ({p90_usd})</td><td>{fmt_bps(p95)} ({p95_usd})</td><td>{len(vals_bps)}</td></tr>"
            )

        data = {
            "source": args.source,
            "hours": args.hours,
            "timestamp": ts,
            "last_price": f"{last_px:,.2f}" if last_px else "N/A",
            "long_sigma_bps": fmt_bps(long_sigma),
            "long_sigma_usd": fmt_usd(long_sigma_usd),
            "combined_score": f"{hist_block['health_score']:.0f}" if hist_block["health_score"] is not None else "N/A",
            "combined_label": hist_block["health_label"],
            "combined_color": label_color(hist_block["health_label"]),
            "current_score": f"{current_score:.0f}",
            "current_label": current_label,
            "current_color": label_color(current_label),
            "max_jump_usd": fmt_usd(max_jump_usd),
            "jump_count": str(len(jumps)),
            "jump_thresh": f"2σ≈{fmt_usd(two_sigma_usd)} · 3σ≈{fmt_usd(three_sigma_usd)}",
            "jump_rate_60m": fmt_pct(recent_jump_rate) if recent_jump_rate is not None else "N/A",
            "jump15_max": fmt_usd(last15_max * last_px) if last_px else "N/A",
            "jump15_med": fmt_usd(last15_med * last_px) if last_px else "N/A",
            "short30": f"{fmt_bps(short_sigma)} (~{fmt_usd(short_sigma_usd)})",
            "short60": f"{fmt_bps(short2_sigma)} (~{fmt_usd(short2_sigma_usd)})",
            "regime": f"{regime_label} ({regime_ratio:.2f}x)" if regime_ratio is not None else "N/A",
            "calc_spot": f"{last_px:,.2f}" if last_px else "N/A",
            "calc_spot_num": f"{last_px:.8f}" if last_px else "0",
            "calc_sigma1m": f"{short_sigma:.8f}",
            "calc_sigma_label": "30m sigma (regime-aware)",
            "hist_time": hist_block["time"],
            "hist_vol": hist_block["vol"],
            "hist_jump": hist_block["jump"],
            "hist_vol_label": hist_block["vol_label"],
            "hist_jump_label": hist_block["jump_label"],
            "hist_current_vol": hist_block["current_vol"],
            "hist_current_jump": hist_block["current_jump"],
            "avg_move": f"30m={fmt_bps(mean_abs_30m)} (~{fmt_usd(mean_abs_30m_usd)}), "
                        f"60m={fmt_bps(mean_abs_60m)} (~{fmt_usd(mean_abs_60m_usd)}), "
                        f"EWMA(hl=30m)={fmt_bps(mean_abs_ewma_30)} (~{fmt_usd(mean_abs_ewma_usd)})",
            "micro_window": f"{args.micro_min}",
            "buffer_rows": "\n".join(buffer_rows) if buffer_rows else "<tr><td colspan='5'>N/A</td></tr>",
            "jump_bars": "\n".join(jump_bars),
            "trend_label": trend_label,
            "trend_color": trend_color,
            "refresh_s": max(1, int(refresh_s)),
            "state_label": state_label,
            "state_color": state_color(state_label),
            "state_er": f"{er:.2f}",
            "state_persist": f"{persist*100:.0f}%",
            "state_run": f"{run_len}m",
        }
        render_html(data, "vol_report.html")
        print("HTML report saved to vol_report.html")


def start_server(port: int):
    from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

    host = "127.0.0.1"
    httpd = ThreadingHTTPServer((host, port), SimpleHTTPRequestHandler)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, host


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        choices=["binance", "kraken", "bitstamp"],
        default="binance",
        help="Data source for 1m candles.",
    )
    parser.add_argument("--hours", type=float, default=6.0, help="Lookback window in hours (1m candles).")
    parser.add_argument("--short-min", type=int, default=30, help="Short window in minutes (meso).")
    parser.add_argument("--short2-min", type=int, default=60, help="Second short window in minutes (meso).")
    parser.add_argument("--micro-min", type=int, default=15, help="Chainlink micro window in minutes (default 15m).")
    parser.add_argument("--sigma-hours", type=float, default=3.0, help="Hours to show in rolling sigma chart.")
    parser.add_argument("--plot", action="store_true", help="Save a plot to vol_report.png if matplotlib is available.")
    parser.add_argument("--html", action="store_true", help="Write an HTML report to vol_report.html.")
    parser.add_argument("--serve", action="store_true", help="Serve the HTML report on localhost.")
    parser.add_argument("--port", type=int, default=8008, help="Port for --serve (default 8008).")
    parser.add_argument("--loop", action="store_true", help="Re-run the report every --interval seconds.")
    parser.add_argument("--interval", type=int, default=60, help="Refresh interval in seconds (default 60).")
    args = parser.parse_args()

    if args.serve:
        args.html = True

    if args.loop:
        httpd = None
        if args.serve:
            httpd, host = start_server(args.port)
            print(f"Serving http://{host}:{args.port}/vol_report.html (Ctrl+C to stop)")
        try:
            next_ts = time.time()
            while True:
                run_once(args, args.interval)
                next_ts += max(1, args.interval)
                sleep_s = max(1.0, next_ts - time.time())
                time.sleep(sleep_s)
        except KeyboardInterrupt:
            print("Stopped.")
        finally:
            if httpd:
                httpd.shutdown()
    else:
        run_once(args, args.interval)
        if args.serve:
            from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

            host = "127.0.0.1"
            print(f"Serving http://{host}:{args.port}/vol_report.html (Ctrl+C to stop)")
            httpd = ThreadingHTTPServer((host, args.port), SimpleHTTPRequestHandler)
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("Server stopped.")


if __name__ == "__main__":
    main()
