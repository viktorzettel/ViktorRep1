#!/usr/bin/env python3
"""
Multi-asset decision report for SOL/XRP (optionally BTC/ETH).
Uses Binance 1m OHLC for volatility + Chainlink RTDS for live spot.
"""

import argparse
import json
import math
import os
import time
from datetime import datetime, timezone
from statistics import median

from btc_vol_report import (
    fetch_1m_klines_binance,
    log_returns,
    ewma,
    trend_badge,
    health_score,
    aggregated_returns,
    empirical_quantile,
    state_color,
    fmt_bps,
    fmt_pct,
    label_color,
)

ASSET_MAP = {
    "BTC": {"binance": "BTCUSDT", "chainlink": "btc/usd"},
    "ETH": {"binance": "ETHUSDT", "chainlink": "eth/usd"},
    "SOL": {"binance": "SOLUSDT", "chainlink": "sol/usd"},
    "XRP": {"binance": "XRPUSDT", "chainlink": "xrp/usd"},
}

# Stable anchor for jump threshold (absolute 1m return in basis points).
# Used in parallel to adaptive sigma-based jumps to avoid threshold drift.
JUMP_ANCHOR_BP = {
    "BTC": 30.0,
    "ETH": 40.0,
    "SOL": 55.0,
    "XRP": 45.0,
}

# In-process fallback cache so transient REST outages do not kill the report.
LAST_ASSET_DATA: dict[str, dict] = {}

def fmt_usd_local(x: float) -> str:
    ax = abs(x)
    if ax < 1:
        return f"${x:,.4f}"
    if ax < 10:
        return f"${x:,.3f}"
    if ax < 100:
        return f"${x:,.2f}"
    if ax < 1000:
        return f"${x:,.1f}"
    return f"${x:,.0f}"


def fetch_klines_with_retry(symbol: str, hours: float, retries: int = 3) -> list:
    last_err: Exception | None = None
    for i in range(retries):
        try:
            return fetch_1m_klines_binance(symbol, hours)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if i < retries - 1:
                time.sleep(0.7 * (i + 1))
    if last_err:
        raise last_err
    raise RuntimeError(f"Failed to fetch klines for {symbol}")


def compute_asset_metrics(klines, asset_code: str, short_min: int, short2_min: int):
    returns = log_returns(klines)
    if len(returns) < 5:
        return None

    closes = [k.close for k in klines]
    long_sigma = float(math.fsum((r - (sum(returns)/len(returns)))**2 for r in returns) / max(1, len(returns)-1)) ** 0.5

    short_n = max(2, short_min)
    short2_n = max(2, short2_min)
    short_sigma = (sum((r - (sum(returns[-short_n:])/len(returns[-short_n:])))**2 for r in returns[-short_n:]) / max(1, len(returns[-short_n:]) - 1)) ** 0.5 if len(returns) >= short_n else long_sigma
    short2_sigma = (sum((r - (sum(returns[-short2_n:])/len(returns[-short2_n:])))**2 for r in returns[-short2_n:]) / max(1, len(returns[-short2_n:]) - 1)) ** 0.5 if len(returns) >= short2_n else long_sigma
    recent_returns = returns[-short2_n:] if len(returns) >= short2_n else returns

    abs_returns = [abs(r) for r in returns]
    mean_abs_30m = sum(abs_returns[-short_n:]) / max(1, len(abs_returns[-short_n:]))
    mean_abs_60m = sum(abs_returns[-short2_n:]) / max(1, len(abs_returns[-short2_n:]))
    mean_abs_ewma_30 = ewma(abs_returns, half_life=30.0)

    trend_label, trend_color = "N/A", "#94a3b8"
    if len(abs_returns) >= 10:
        recent = sum(abs_returns[-5:]) / 5
        prior = sum(abs_returns[-10:-5]) / 5
        trend_label, trend_color = trend_badge(recent, prior)

    # Market state (15m)
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
    current_score, current_label = health_score(short_sigma, short2_sigma)

    # Jump stats
    # 1) Adaptive threshold: 2 * short 60m sigma
    jump_sigma = short2_sigma if short2_sigma > 0 else long_sigma
    jump_thresh = 2 * jump_sigma
    # 2) Fixed anchor threshold: absolute bp by asset
    anchor_bp = float(JUMP_ANCHOR_BP.get(asset_code.upper(), 40.0))
    jump_thresh_anchor = anchor_bp / 10000.0

    last5 = returns[-5:] if len(returns) >= 5 else returns
    jumps = [r for r in last5 if abs(r) >= jump_thresh]
    jumps_anchor = [r for r in last5 if abs(r) >= jump_thresh_anchor]
    last15 = returns[-15:] if len(returns) >= 15 else returns
    last15_abs = [abs(r) for r in last15] if last15 else []
    last15_max = max(last15_abs) if last15_abs else 0.0
    last15_med = median(last15_abs) if last15_abs else 0.0
    last15_count = sum(1 for r in last15 if abs(r) >= jump_thresh) if last15 else 0
    last15_count_anchor = sum(1 for r in last15 if abs(r) >= jump_thresh_anchor) if last15 else 0
    recent_jump_rate = sum(1 for r in recent_returns if abs(r) >= jump_thresh) / len(recent_returns) if recent_returns else None
    recent_jump_rate_anchor = sum(1 for r in recent_returns if abs(r) >= jump_thresh_anchor) / len(recent_returns) if recent_returns else None

    # Buffers (1-6m) using recent 60m
    empirical_buffers = {}
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

    last_px = closes[-1] if closes else None
    long_sigma_usd = long_sigma * last_px if last_px else 0.0
    short_sigma_usd = short_sigma * last_px if last_px else 0.0
    short2_sigma_usd = short2_sigma * last_px if last_px else 0.0
    mean_abs_30m_usd = mean_abs_30m * last_px if last_px else 0.0
    mean_abs_60m_usd = mean_abs_60m * last_px if last_px else 0.0
    mean_abs_ewma_usd = mean_abs_ewma_30 * last_px if last_px else 0.0
    two_sigma_usd = (2 * jump_sigma * last_px) if last_px and jump_sigma > 0 else 0.0
    three_sigma_usd = (3 * jump_sigma * last_px) if last_px and jump_sigma > 0 else 0.0
    anchor_usd = (jump_thresh_anchor * last_px) if last_px and jump_thresh_anchor > 0 else 0.0

    buffer_rows = []
    for t in range(1, 7):
        buf = empirical_buffers.get(t)
        if not buf:
            continue
        h90 = buf["hybrid90"]
        h95 = buf["hybrid95"]
        buffer_rows.append(
            f"<tr><td>{t}m</td><td>{fmt_usd_local(h90 * last_px)}</td><td>{fmt_usd_local(h95 * last_px)}</td>"
            f"<td>{fmt_bps(buf['model90'])}/{fmt_bps(buf['emp90'])}/{fmt_bps(h90)}</td>"
            f"<td>{fmt_bps(buf['model95'])}/{fmt_bps(buf['emp95'])}/{fmt_bps(h95)}</td></tr>"
        )

    # Jump bars (last 60m, 5m buckets)
    jump_bars = []
    jump_bars_anchor = []
    if returns:
        window = min(60, len(returns))
        sub = returns[-window:]
        bucket = 5
        buckets = [sub[i:i+bucket] for i in range(0, len(sub), bucket)]
        counts = [sum(1 for r in b if abs(r) >= jump_thresh) for b in buckets]
        counts_anchor = [sum(1 for r in b if abs(r) >= jump_thresh_anchor) for b in buckets]
        max_count = max(counts) if counts else 0
        max_count_anchor = max(counts_anchor) if counts_anchor else 0
        total_min = len(sub)
        for i, b in enumerate(buckets):
            start = max(0, total_min - (i + 1) * bucket)
            end = max(0, total_min - i * bucket)
            label = f"{start:02d}-{end:02d}m"
            count = counts[i]
            count_anchor = counts_anchor[i]
            max_abs = max((abs(r) for r in b), default=0.0)
            max_usd = max_abs * last_px if last_px else 0.0
            width = 0 if max_count == 0 else int(round((count / max_count) * 100))
            width_anchor = 0 if max_count_anchor == 0 else int(round((count_anchor / max_count_anchor) * 100))
            jump_bars.append(
                f"<div class='bars'><div>{label}</div><div class='bar'><span style='width:{width}%'></span></div>"
                f"<div>{count} | {fmt_usd_local(max_usd)}</div></div>"
            )
            jump_bars_anchor.append(
                f"<div class='bars'><div>{label}</div><div class='bar'><span style='width:{width_anchor}%'></span></div>"
                f"<div>{count_anchor} | {fmt_usd_local(max_usd)}</div></div>"
            )
    if not jump_bars:
        jump_bars.append("<div class='muted'>N/A</div>")
    if not jump_bars_anchor:
        jump_bars_anchor.append("<div class='muted'>N/A</div>")

    return {
        "asset": asset_code,
        "last_price": f"{last_px:,.2f}" if last_px else "N/A",
        "long_sigma_bps": fmt_bps(long_sigma),
        "long_sigma_usd": fmt_usd_local(long_sigma_usd),
        "current_score": f"{current_score:.0f}",
        "current_label": current_label,
        "current_color": label_color(current_label),
        "combined_score": "N/A",
        "combined_label": "N/A",
        "combined_color": label_color("UNKNOWN"),
        "max_jump_usd": fmt_usd_local(max(abs(r) for r in last5) * last_px) if last_px and last5 else "N/A",
        "jump_count": str(len(jumps)),
        "jump_count_anchor": str(len(jumps_anchor)),
        "jump_thresh": f"2σ(60m)≈{fmt_usd_local(two_sigma_usd)} · 3σ(60m)≈{fmt_usd_local(three_sigma_usd)}",
        "jump_thresh_anchor": f"{anchor_bp:.0f}bp≈{fmt_usd_local(anchor_usd)}",
        "jump_rate_60m": fmt_pct(recent_jump_rate) if recent_jump_rate is not None else "N/A",
        "jump_rate_60m_anchor": fmt_pct(recent_jump_rate_anchor) if recent_jump_rate_anchor is not None else "N/A",
        "jump15_max": fmt_usd_local(last15_max * last_px) if last_px else "N/A",
        "jump15_med": fmt_usd_local(last15_med * last_px) if last_px else "N/A",
        "jump15_count": str(last15_count),
        "jump15_count_anchor": str(last15_count_anchor),
        "jump15_max_bp": fmt_bps(last15_max),
        "jump15_med_bp": fmt_bps(last15_med),
        "short30": f"{fmt_bps(short_sigma)} (~{fmt_usd_local(short_sigma_usd)})",
        "short60": f"{fmt_bps(short2_sigma)} (~{fmt_usd_local(short2_sigma_usd)})",
        "regime": f"{regime_label} ({regime_ratio:.2f}x)" if regime_ratio is not None else "N/A",
        "calc_spot": f"{last_px:,.2f}" if last_px else "N/A",
        "calc_spot_num": float(f"{last_px:.8f}") if last_px else 0.0,
        "calc_sigma1m": float(short_sigma),
        "calc_sigma_label": "30m sigma (regime-aware)",
        "long_sigma_raw": float(long_sigma),
        "short_sigma_raw": float(short_sigma),
        "short2_sigma_raw": float(short2_sigma),
        "regime_ratio_raw": float(regime_ratio) if regime_ratio is not None else 0.0,
        "current_score_raw": float(current_score),
        "jump_rate_60m_raw": float(recent_jump_rate) if recent_jump_rate is not None else 0.0,
        "jump_rate_60m_anchor_raw": float(recent_jump_rate_anchor) if recent_jump_rate_anchor is not None else 0.0,
        "jump15_count_raw": int(last15_count),
        "jump15_count_anchor_raw": int(last15_count_anchor),
        "jump_anchor_bp_raw": anchor_bp,
        "avg_move": f"30m={fmt_bps(mean_abs_30m)} (~{fmt_usd_local(mean_abs_30m_usd)}), 60m={fmt_bps(mean_abs_60m)} (~{fmt_usd_local(mean_abs_60m_usd)}), EWMA(hl=30m)={fmt_bps(mean_abs_ewma_30)} (~{fmt_usd_local(mean_abs_ewma_usd)})",
        "buffer_rows": "\n".join(buffer_rows) if buffer_rows else "<tr><td colspan='5'>N/A</td></tr>",
        "jump_bars": "\n".join(jump_bars),
        "jump_bars_anchor": "\n".join(jump_bars_anchor),
        "trend_label": trend_label,
        "trend_color": trend_color,
        "state_label": state_label,
        "state_color": state_color(state_label),
        "state_er": f"{er:.2f}",
        "state_persist": f"{persist*100:.0f}%",
        "state_run": f"{run_len}m",
        "state_er_raw": float(er),
        "state_persist_raw": float(persist),
        "state_run_raw": int(run_len),
    }


def render_html(data: dict, out_path: str) -> None:
    assets = data["assets"]
    current = data["current_asset"]
    asset_data = data["asset_data"]
    current_data = asset_data[current]

    asset_buttons = "".join([f"<button data-asset='{a}'>{a}</button>" for a in assets])
    assets_json = json.dumps(assets)
    data_json = json.dumps(asset_data)
    chainlink_json = json.dumps(data["chainlink_symbols"])
    binance_json = json.dumps(data["binance_symbols"])
    live_source_json = json.dumps(data["live_source"])

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Asset Volatility Report</title>
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
    .wrap {{ max-width: 1100px; margin: 32px auto 48px; padding: 0 20px; }}
    header {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 16px; }}
    h1 {{ font-size: 28px; margin: 0; letter-spacing: 0.4px; }}
    .meta {{ color: var(--muted); font-size: 14px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; margin-top: 16px; }}
    .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    .card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 14px 16px; box-shadow: 0 0 0 1px rgba(255,255,255,0.02); }}
    .card h3 {{ margin: 0 0 8px 0; font-size: 13px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.8px; }}
    .input {{ width: 100%; padding: 8px 10px; border-radius: 10px; border: 1px solid var(--border); background: #0b1224; color: var(--text); font-family: 'JetBrains Mono', monospace; font-size: 13px; outline: none; }}
    .input:focus {{ border-color: var(--accent); box-shadow: 0 0 0 2px rgba(53,243,255,0.15); }}
    .toggle {{ display: flex; gap: 8px; margin-top: 6px; flex-wrap: wrap; }}
    .toggle button {{ padding: 8px 10px; border-radius: 10px; border: 1px solid var(--border); background: #0b1224; color: var(--muted); font-family: 'JetBrains Mono', monospace; font-size: 12px; cursor: pointer; }}
    .toggle button.active {{ color: var(--text); border-color: var(--accent); background: rgba(53,243,255,0.12); }}
    .big {{ font-size: 22px; font-weight: 700; font-family: 'JetBrains Mono', monospace; }}
    .sub {{ color: var(--muted); font-size: 13px; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; border: 1px solid rgba(255,255,255,0.08); background: rgba(255,255,255,0.03); }}
    .section {{ margin-top: 22px; }}
    .section h2 {{ font-size: 16px; margin: 0 0 10px 0; color: var(--muted); letter-spacing: 0.4px; text-transform: uppercase; }}
    table {{ width: 100%; border-collapse: collapse; font-family: 'JetBrains Mono', monospace; font-size: 13px; }}
    th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; }}
    th {{ color: var(--muted); font-weight: 600; }}
    .bars {{ display: grid; grid-template-columns: 80px 1fr 100px; gap: 8px; align-items: center; margin: 6px 0; font-family: 'JetBrains Mono', monospace; font-size: 12px; }}
    .bar {{ height: 8px; background: #0b1224; border: 1px solid var(--border); border-radius: 999px; overflow: hidden; }}
    .bar > span {{ display: block; height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent-2)); }}
    .countdown {{ font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--accent); }}
    .pill {{ display: inline-block; padding: 4px 10px; border-radius: 999px; border: 1px solid rgba(53,243,255,0.35); background: rgba(53,243,255,0.12); font-weight: 700; }}
    .decision {{
      border: 1px solid rgba(53,243,255,0.25);
      background: linear-gradient(180deg, rgba(53,243,255,0.06), rgba(99,243,163,0.04));
    }}
    .decision-row {{ display: flex; align-items: center; gap: 10px; }}
    .decision-icon {{
      width: 14px;
      height: 14px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.18);
      background: #64748b;
      box-shadow: 0 0 16px rgba(100,116,139,0.45);
    }}
    .decision-icon.up {{ background: #22c55e; box-shadow: 0 0 18px rgba(34,197,94,0.55); }}
    .decision-icon.down {{ background: #ef4444; box-shadow: 0 0 18px rgba(239,68,68,0.55); }}
    .decision-icon.wait {{ background: #f59e0b; box-shadow: 0 0 18px rgba(245,158,11,0.55); }}
    .decision-meter {{
      margin-top: 8px;
      width: 100%;
      height: 8px;
      background: #0b1224;
      border: 1px solid var(--border);
      border-radius: 999px;
      overflow: hidden;
    }}
    .decision-meter > span {{
      display: block;
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, #35f3ff, #63f3a3);
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>Asset Volatility Report</h1>
        <div class="meta">Source: Binance · Lookback: {data['hours']:.1f}h · {data['timestamp']}</div>
      </div>
      <div>
        <div class="countdown"><span class="pill">Refresh in <span id="refresh-t">{data['refresh_s']}</span>s</span></div>
      </div>
    </header>

    <div class="card">
      <h3>Asset</h3>
      <div style="display:flex;align-items:center;gap:16px;">
        <div class="toggle" id="asset-toggle">{asset_buttons}</div>
        <div style="font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:700;color:var(--accent);" id="live-spot">—</div>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <h3>Market Condition</h3>
        <div class="big" id="market-condition-label">BUILDING</div>
        <div class="sub" id="market-condition-score">Score: —</div>
        <div class="sub" id="market-condition-meta">Collecting live micro data...</div>
      </div>
      <div class="card">
        <h3>Health (Current)</h3>
        <div class="big" id="health-current-score">{current_data['current_score']}/100</div>
        <div class="sub"><span class="badge" id="health-current-label" style="color:{current_data['current_color']}">{current_data['current_label']}</span></div>
      </div>
      <div class="card">
        <h3>Shock (Last 5m)</h3>
        <div class="big" id="shock-count">{current_data['jump_count']}</div>
        <div class="sub" id="shock-sub">adaptive: 2σ(60m) rate {current_data['jump_rate_60m']} · last5 {current_data['jump_count']} · {current_data['jump_thresh']} · max {current_data['max_jump_usd']}</div>
        <div class="sub" id="shock-sub2">anchored: {current_data['jump_rate_60m_anchor']} · last5 {current_data['jump_count_anchor']} · {current_data['jump_thresh_anchor']} · last15 {current_data['jump15_count']} / {current_data['jump15_count_anchor']}</div>
      </div>
      <div class="card decision">
        <h3>Decision (5m/15m)</h3>
        <label class="sub">Decision strike price</label>
        <input class="input" id="decision-strike" placeholder="e.g. 245.50" />
        <div class="sub" style="margin-top:10px;">Decision timeframe</div>
        <div class="toggle" id="decision-toggle">
          <button data-dtf="15">15m</button>
          <button data-dtf="5">5m</button>
        </div>
        <div class="sub" style="margin-top:8px;">Decision time left: <span id="decision-time">—</span></div>
        <div class="decision-row">
          <span class="decision-icon wait" id="decision-icon"></span>
          <div class="big" id="decision-action">NO_TRADE</div>
        </div>
        <div class="sub"><span class="badge" id="decision-badge">WAIT</span></div>
        <div class="sub" id="decision-confidence">Confidence: --</div>
        <div class="decision-meter"><span id="decision-meter"></span></div>
        <div class="sub" style="margin-top:8px;" id="decision-reasons">Enter strike and wait for stream samples.</div>
        <div class="sub" id="decision-meta">Decision engine: inactive</div>
      </div>
    </div>

    <div class="section">
      <h2>Volatility Snapshot</h2>
      <div class="row">
        <div class="card">
          <h3>Volatility Metrics</h3>
          <table>
            <tr><th>Metric</th><th>Value</th></tr>
            <tr><td>Long σ (6h)</td><td id="long-sigma">{current_data['long_sigma_bps']} (~{current_data['long_sigma_usd']})</td></tr>
            <tr><td>Short 30m σ</td><td id="short30">{current_data['short30']}</td></tr>
            <tr><td>Short 60m σ</td><td id="short60">{current_data['short60']}</td></tr>
            <tr><td>Regime (30m/long)</td><td id="regime">{current_data['regime']}</td></tr>
          </table>
        </div>
        <div class="card">
          <h3>Movement (1m)</h3>
          <table>
            <tr><th>Metric</th><th>Value</th></tr>
            <tr><td>Avg 1m move (abs, close‑to‑close)</td><td id="avg-move">{current_data['avg_move']}</td></tr>
            <tr><td>Live Range (60s)</td><td id="live-range-60">—</td></tr>
            <tr><td>Avg Range (1m) — last 30m</td><td id="avg-range-1m">—</td></tr>
            <tr><td>90% Range (1m) — last 30m</td><td id="p90-range-1m">—</td></tr>
          </table>
        </div>
      </div>
    </div>

    <div class="section">
      <h2>Strike Buffer Guide</h2>
      <table>
        <tr><th>Minutes Left</th><th>90% Hybrid</th><th>95% Hybrid</th><th>90% M/E/H (bp)</th><th>95% M/E/H (bp)</th></tr>
        <tbody id="buffer-rows">{current_data['buffer_rows']}</tbody>
      </table>
    </div>

    <div class="section">
      <h2>Market State (last 15m)</h2>
      <div class="card">
        <table>
          <tr><th>Metric</th><th>Value</th></tr>
          <tr><td>State</td><td><span class="badge" id="state-label" style="color:{current_data['state_color']}">{current_data['state_label']}</span></td></tr>
          <tr><td>Efficiency Ratio</td><td id="state-er">{current_data['state_er']}</td></tr>
          <tr><td>Persistence</td><td id="state-persist">{current_data['state_persist']}</td></tr>
          <tr><td>Run Length</td><td id="state-run">{current_data['state_run']}</td></tr>
        </table>
      </div>
    </div>

    <div class="section">
      <h2>Micro (10s Aggregation, rolling window)</h2>
      <div class="card">
        <table>
          <tr><th>Metric</th><th>Value</th></tr>
          <tr><td>Window</td><td id="micro-window">{data['micro_window']}m</td></tr>
          <tr><td>Status</td><td id="micro-status">Waiting for live ticks...</td></tr>
          <tr><td>Last tick age</td><td id="micro-last-tick">—</td></tr>
          <tr><td>σ (10s)</td><td id="micro-sigma">—</td></tr>
          <tr><td>σ (1m-eq)</td><td id="micro-sigma1m">—</td></tr>
          <tr><td>σ (15m-eq)</td><td id="micro-sigma15">—</td></tr>
          <tr><td>2σ threshold (10s)</td><td id="micro-2sigma">—</td></tr>
          <tr><td>Jumps >=2σ (last 5m)</td><td id="micro-jumps5">—</td></tr>
          <tr><td>Jump rate (5m / 15m)</td><td id="micro-jumprate">—</td></tr>
          <tr><td>Max jump (last 5m)</td><td id="micro-max5">—</td></tr>
          <tr><td>Median jump (last 5m)</td><td id="micro-med5">—</td></tr>
          <tr><td>Samples (10s returns)</td><td id="micro-samples">—</td></tr>
        </table>
      </div>
    </div>

    <div class="section">
      <h2>Jump Activity (last 60m, 5m buckets)</h2>
      <div class="row">
        <div class="card">
          <h3>Adaptive (≥2σ 60m)</h3>
          <div id="jump-bars">{current_data['jump_bars']}</div>
        </div>
        <div class="card">
          <h3>Anchored (≥fixed bp)</h3>
          <div id="jump-bars-anchor">{current_data['jump_bars_anchor']}</div>
        </div>
      </div>
    </div>

  </div>

  <script>
    const ASSETS = {assets_json};
    const ASSET_DATA = {data_json};
    const CHAINLINK_SYMBOLS = {chainlink_json};
    const BINANCE_SYMBOLS = {binance_json};
    const LIVE_SOURCE_MODE = {live_source_json};
    let currentAsset = "{current}";

    const spotMap = {{}};
    const microState = {{}};
    const binanceShortState = {{}};
    const chainlinkState = {{
      connected: false,
      lastMsgMs: 0,
      lastTickByAsset: {{}},
    }};
    const binanceState = {{
      connected: false,
      lastMsgMs: 0,
      lastTickByAsset: {{}},
    }};
    const MICRO_STORAGE_KEY = "asset_vol_micro_state_v1";
    const DECISION_CFG_KEY = "asset_vol_decision_cfg_v1";
    const BINANCE_SHORT_KEY = "asset_vol_binance_short_v1";
    const decisionCfgByAsset = {{}};
    ASSETS.forEach(a => {{
      microState[a] = {{ windowSec: {data['micro_window']} * 60, secs: [], prices: [], lastStats: null }};
      binanceShortState[a] = {{ secs: [], prices: [], keepSec: 35 * 60 }};
      chainlinkState.lastTickByAsset[a] = 0;
      binanceState.lastTickByAsset[a] = 0;
      decisionCfgByAsset[a] = {{ tf: 15, strike: "" }};
    }});

    const el = (id) => document.getElementById(id);

    function clamp(x, lo, hi) {{ return Math.max(lo, Math.min(hi, x)); }}
    function sigmoid(x) {{ return 1 / (1 + Math.exp(-x)); }}
    function fmtSpot(x) {{
      if (!x || !Number.isFinite(x)) return "N/A";
      const ax = Math.abs(x);
      if (ax < 1) return x.toFixed(4);
      if (ax < 10) return x.toFixed(3);
      if (ax < 100) return x.toFixed(2);
      return x.toFixed(2);
    }}

    function fmtSigned(x) {{
      if (!Number.isFinite(x)) return "N/A";
      const sign = x > 0 ? "+" : "";
      const ax = Math.abs(x);
      const body = ax < 1 ? x.toFixed(4) : x.toFixed(3);
      return `${{sign}}${{body}}`;
    }}

    function setMicroStatus(msg) {{
      const statusEl = el("micro-status");
      if (statusEl) statusEl.textContent = msg;
    }}

    function updateStreamHealth(asset) {{
      const ageEl = el("micro-last-tick");
      if (!ageEl) return;
      const lastBinance = binanceState.lastTickByAsset[asset] || 0;
      const lastChainlink = chainlinkState.lastTickByAsset[asset] || 0;
      const lastMs = Math.max(lastBinance, lastChainlink);
      if (!lastMs) {{
        ageEl.textContent = "No ticks yet";
        return;
      }}
      const ageSec = Math.max(0, Math.round((Date.now() - lastMs) / 1000));
      const src = lastBinance >= lastChainlink ? "binance" : "chainlink";
      ageEl.textContent = `${{ageSec}}s (${{src}})`;
    }}

    function parseAssetFromSymbol(symRaw) {{
      const sym = String(symRaw || "").toUpperCase();
      if (!sym) return null;
      // Handles formats like "sol/usd", "SOLUSD", "crypto_prices:SOL/USD", etc.
      if (sym.includes("BTC")) return "BTC";
      if (sym.includes("ETH")) return "ETH";
      if (sym.includes("SOL")) return "SOL";
      if (sym.includes("XRP")) return "XRP";
      return null;
    }}

    function shouldAcceptTick(source, asset) {{
      if (LIVE_SOURCE_MODE === "binance") return source === "binance";
      if (LIVE_SOURCE_MODE === "chainlink") return source === "chainlink";
      // auto: prefer Binance for SOL/XRP if fresh (<15s), else allow Chainlink fallback.
      if (source === "binance") return true;
      const lastBinance = binanceState.lastTickByAsset[asset] || 0;
      if (!lastBinance) return true;
      const ageSec = (Date.now() - lastBinance) / 1000;
      return ageSec > 15;
    }}

    function pruneMicroState(nowSec = Math.floor(Date.now() / 1000)) {{
      ASSETS.forEach(asset => {{
        const m = microState[asset];
        if (!m) return;
        const minSec = nowSec - m.windowSec - 30;
        const secs = [];
        const prices = [];
        const n = Math.min(m.secs.length, m.prices.length);
        for (let i = 0; i < n; i++) {{
          const sec = m.secs[i];
          const price = m.prices[i];
          if (!Number.isFinite(sec) || !Number.isFinite(price)) continue;
          if (sec >= minSec) {{
            secs.push(sec);
            prices.push(price);
          }}
        }}
        m.secs = secs;
        m.prices = prices;
      }});
    }}

    function saveMicroState() {{
      try {{
        pruneMicroState();
        const payload = {{ v: 1, ts: Date.now(), assets: {{}} }};
        ASSETS.forEach(asset => {{
          const m = microState[asset];
          payload.assets[asset] = {{
            secs: m?.secs || [],
            prices: m?.prices || [],
          }};
        }});
        localStorage.setItem(MICRO_STORAGE_KEY, JSON.stringify(payload));
      }} catch (_) {{}}
    }}

    function restoreMicroState() {{
      try {{
        const raw = localStorage.getItem(MICRO_STORAGE_KEY);
        if (!raw) return;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object" || !parsed.assets) return;
        ASSETS.forEach(asset => {{
          const src = parsed.assets[asset];
          const m = microState[asset];
          if (!m || !src) return;
          if (Array.isArray(src.secs) && Array.isArray(src.prices)) {{
            const n = Math.min(src.secs.length, src.prices.length);
            const secs = [];
            const prices = [];
            for (let i = 0; i < n; i++) {{
              const sec = src.secs[i];
              const price = src.prices[i];
              if (!Number.isFinite(sec) || !Number.isFinite(price)) continue;
              secs.push(sec);
              prices.push(price);
            }}
            m.secs = secs;
            m.prices = prices;
          }}
        }});
        pruneMicroState();
      }} catch (_) {{}}
    }}

    function restoreDecisionCfg() {{
      try {{
        const raw = localStorage.getItem(DECISION_CFG_KEY);
        if (!raw) return;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object") return;
        ASSETS.forEach(asset => {{
          const src = parsed[asset];
          if (!src || typeof src !== "object") return;
          const tf = parseInt(src.tf, 10);
          const strike = String(src.strike || "");
          decisionCfgByAsset[asset] = {{
            tf: tf === 5 ? 5 : 15,
            strike,
          }};
        }});
      }} catch (_) {{}}
    }}

    function saveDecisionCfg() {{
      try {{
        localStorage.setItem(DECISION_CFG_KEY, JSON.stringify(decisionCfgByAsset));
      }} catch (_) {{}}
    }}

    function saveBinanceShortState() {{
      try {{
        const nowSec = Math.floor(Date.now() / 1000);
        const payload = {{ v: 1, ts: Date.now(), assets: {{}} }};
        ASSETS.forEach(asset => {{
          const s = binanceShortState[asset];
          if (!s) return;
          const minSec = nowSec - s.keepSec;
          const secs = [];
          const prices = [];
          const n = Math.min(s.secs.length, s.prices.length);
          for (let i = 0; i < n; i++) {{
            if (s.secs[i] >= minSec) {{
              secs.push(s.secs[i]);
              prices.push(s.prices[i]);
            }}
          }}
          payload.assets[asset] = {{ secs, prices }};
        }});
        localStorage.setItem(BINANCE_SHORT_KEY, JSON.stringify(payload));
      }} catch (_) {{}}
    }}

    function restoreBinanceShortState() {{
      try {{
        const raw = localStorage.getItem(BINANCE_SHORT_KEY);
        if (!raw) return;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object" || !parsed.assets) return;
        const nowSec = Math.floor(Date.now() / 1000);
        ASSETS.forEach(asset => {{
          const src = parsed.assets[asset];
          const s = binanceShortState[asset];
          if (!s || !src) return;
          if (Array.isArray(src.secs) && Array.isArray(src.prices)) {{
            const minSec = nowSec - s.keepSec;
            const n = Math.min(src.secs.length, src.prices.length);
            const secs = [];
            const prices = [];
            for (let i = 0; i < n; i++) {{
              const sec = src.secs[i];
              const price = src.prices[i];
              if (!Number.isFinite(sec) || !Number.isFinite(price)) continue;
              if (sec >= minSec) {{
                secs.push(sec);
                prices.push(price);
              }}
            }}
            s.secs = secs;
            s.prices = prices;
          }}
        }});
      }} catch (_) {{}}
    }}

    function setActiveAsset(asset) {{
      currentAsset = asset;
      loadDecisionForAsset(asset);
      const d = ASSET_DATA[asset];
      if (!d) return;
      el("health-current-score").textContent = `${{d.current_score}}/100`;
      const hcl = el("health-current-label");
      hcl.textContent = d.current_label;
      hcl.style.color = d.current_color;
      el("shock-count").textContent = d.jump_count;
      el("shock-sub").textContent = `adaptive: 2σ(60m) rate ${{d.jump_rate_60m || "N/A"}} · last5 ${{d.jump_count || "0"}} · ${{d.jump_thresh || "N/A"}} · max ${{d.max_jump_usd || "N/A"}}`;
      el("shock-sub2").textContent = `anchored: ${{d.jump_rate_60m_anchor || "N/A"}} · last5 ${{d.jump_count_anchor || "0"}} · ${{d.jump_thresh_anchor || "N/A"}} · last15 ${{d.jump15_count || "0"}} / ${{d.jump15_count_anchor || "0"}}`;
      el("long-sigma").textContent = `${{d.long_sigma_bps}} (~${{d.long_sigma_usd}})`;
      el("short30").textContent = d.short30;
      el("short60").textContent = d.short60;
      el("regime").textContent = d.regime;
      el("avg-move").textContent = d.avg_move;
      const sl = el("state-label");
      sl.textContent = d.state_label;
      sl.style.color = d.state_color;
      el("state-er").textContent = d.state_er;
      el("state-persist").textContent = d.state_persist;
      el("state-run").textContent = d.state_run;
      el("buffer-rows").innerHTML = d.buffer_rows;
      el("jump-bars").innerHTML = d.jump_bars;
      el("jump-bars-anchor").innerHTML = d.jump_bars_anchor || "<div class='muted'>N/A</div>";

      const spot = spotMap[asset] ?? d.calc_spot_num;
      el("live-spot").textContent = spot ? fmtSpot(spot) : "—";

      updateMicro();
      updateRangeMetrics();
      updateMarketCondition();
      updateDecision();
      setToggleState();
    }}

    const assetToggle = document.getElementById("asset-toggle");
    if (assetToggle) {{
      assetToggle.addEventListener("click", (e) => {{
        if (!e.target.dataset.asset) return;
        setActiveAsset(e.target.dataset.asset);
      }});
    }}

    function setToggleState() {{
      assetToggle.querySelectorAll("button").forEach(btn => {{
        btn.classList.toggle("active", btn.dataset.asset === currentAsset);
      }});
    }}

    function erf(x) {{
      const sign = x >= 0 ? 1 : -1;
      x = Math.abs(x);
      const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741;
      const a4 = -1.453152027, a5 = 1.061405429, p = 0.3275911;
      const t = 1 / (1 + p * x);
      const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);
      return sign * y;
    }}
    function normCdf(x) {{ return 0.5 * (1 + erf(x / Math.SQRT2)); }}
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
      if (!S || !K || sigma1m <= 0) return null;
      const t = Math.max(tMin, 1e-6);
      const sigT = sigma1m * Math.sqrt(t);
      if (sigT <= 0) return S > K ? 1 : 0;
      const d2 = (Math.log(S / K) - 0.5 * sigT * sigT) / sigT;
      return normCdf(d2);
    }}

    const decisionStrikeInput = document.getElementById("decision-strike");
    const decisionToggle = document.getElementById("decision-toggle");

    let decisionTf = 15;
    let decisionStrike = "";
    if (decisionStrikeInput) decisionStrikeInput.value = "";

    function loadDecisionForAsset(asset) {{
      const cfg = decisionCfgByAsset[asset] || {{}};
      decisionTf = cfg.tf === 5 ? 5 : 15;
      decisionStrike = String(cfg.strike || "");
      if (decisionStrikeInput) decisionStrikeInput.value = decisionStrike;
      setActiveDecisionTf();
    }}

    function saveDecisionForAsset(asset) {{
      decisionCfgByAsset[asset] = {{
        tf: decisionTf === 5 ? 5 : 15,
        strike: String(decisionStrike || ""),
      }};
      saveDecisionCfg();
    }}

    function setActiveDecisionTf() {{
      if (!decisionToggle) return;
      decisionToggle.querySelectorAll("button").forEach(btn => {{
        btn.classList.toggle("active", parseInt(btn.dataset.dtf, 10) === decisionTf);
      }});
    }}

    setActiveDecisionTf();

    if (decisionToggle) {{
      decisionToggle.addEventListener("click", (e) => {{
        if (!e.target.dataset.dtf) return;
        decisionTf = parseInt(e.target.dataset.dtf, 10);
        saveDecisionForAsset(currentAsset);
        setActiveDecisionTf();
        updateMicro();
        updateDecision();
      }});
    }}

    if (decisionStrikeInput) {{
      decisionStrikeInput.addEventListener("input", (e) => {{
        decisionStrike = e.target.value.replace(/,/g, "");
        saveDecisionForAsset(currentAsset);
        updateDecision();
      }});
    }}

    function stdev(arr) {{
      const n = arr.length;
      if (n < 2) return 0;
      const mean = arr.reduce((a,b) => a+b, 0) / n;
      let v = 0;
      for (const x of arr) v += (x-mean)*(x-mean);
      return Math.sqrt(v / (n-1));
    }}

    function median(arr) {{
      if (!arr || !arr.length) return 0;
      const xs = [...arr].sort((a, b) => a - b);
      const n = xs.length;
      const mid = Math.floor(n / 2);
      if (n % 2 === 1) return xs[mid];
      return 0.5 * (xs[mid - 1] + xs[mid]);
    }}

    function fmtUsd(x) {{
      const ax = Math.abs(x);
      if (ax < 1) return '$' + x.toFixed(4);
      if (ax < 10) return '$' + x.toFixed(3);
      if (ax < 100) return '$' + x.toFixed(2);
      if (ax < 1000) return '$' + x.toFixed(1);
      return '$' + x.toFixed(0);
    }}

    function appendMicro(asset, sec, price) {{
      const m = microState[asset];
      if (!m) return;
      if (!m.secs.length) {{ m.secs.push(sec); m.prices.push(price); return; }}
      const lastSec = m.secs[m.secs.length - 1];
      if (sec <= lastSec) {{ m.prices[m.prices.length - 1] = price; return; }}
      const lastPrice = m.prices[m.prices.length - 1];
      if (sec - lastSec > 1) {{
        for (let s = lastSec + 1; s < sec; s++) {{ m.secs.push(s); m.prices.push(lastPrice); }}
      }}
      m.secs.push(sec); m.prices.push(price);
      while (m.secs.length > m.windowSec) {{ m.secs.shift(); m.prices.shift(); }}
    }}

    function appendBinanceShort(asset, sec, price) {{
      const s = binanceShortState[asset];
      if (!s) return;
      if (!s.secs.length) {{
        s.secs.push(sec);
        s.prices.push(price);
        return;
      }}
      const lastSec = s.secs[s.secs.length - 1];
      if (sec <= lastSec) {{
        s.prices[s.prices.length - 1] = price;
        return;
      }}
      const lastPrice = s.prices[s.prices.length - 1];
      if (sec - lastSec > 1) {{
        for (let t = lastSec + 1; t < sec; t++) {{
          s.secs.push(t);
          s.prices.push(lastPrice);
        }}
      }}
      s.secs.push(sec);
      s.prices.push(price);
      const minSec = sec - s.keepSec;
      while (s.secs.length && s.secs[0] < minSec) {{
        s.secs.shift();
        s.prices.shift();
      }}
    }}

    function priceAtOrAfter(state, targetSec) {{
      for (let i = 0; i < state.secs.length; i++) {{
        if (state.secs[i] >= targetSec) return state.prices[i];
      }}
      return null;
    }}

    function liveRange(asset, windowSec) {{
      const s = binanceShortState[asset];
      if (!s || s.prices.length < 2) return null;
      const lastSec = s.secs[s.secs.length - 1];
      const fromSec = lastSec - windowSec;
      let hi = -Infinity, lo = Infinity;
      for (let i = 0; i < s.secs.length; i++) {{
        if (s.secs[i] >= fromSec) {{
          if (s.prices[i] > hi) hi = s.prices[i];
          if (s.prices[i] < lo) lo = s.prices[i];
        }}
      }}
      if (!Number.isFinite(hi) || !Number.isFinite(lo)) return null;
      return {{ hi, lo, range: hi - lo }};
    }}

    function bucketedRangeStats(asset, bucketSec, lookbackSec) {{
      const s = binanceShortState[asset];
      if (!s || s.prices.length < 2) return null;
      const lastSec = s.secs[s.secs.length - 1];
      const fromSec = lastSec - lookbackSec;
      // Build buckets from the start of the lookback
      const ranges = [];
      for (let bStart = fromSec; bStart + bucketSec <= lastSec; bStart += bucketSec) {{
        const bEnd = bStart + bucketSec;
        let hi = -Infinity, lo = Infinity, cnt = 0;
        for (let i = 0; i < s.secs.length; i++) {{
          if (s.secs[i] >= bStart && s.secs[i] < bEnd) {{
            if (s.prices[i] > hi) hi = s.prices[i];
            if (s.prices[i] < lo) lo = s.prices[i];
            cnt++;
          }}
        }}
        if (cnt >= 2 && Number.isFinite(hi) && Number.isFinite(lo)) {{
          ranges.push(hi - lo);
        }}
      }}
      if (!ranges.length) return null;
      const sorted = [...ranges].sort((a, b) => a - b);
      const avg = ranges.reduce((a, b) => a + b, 0) / ranges.length;
      const idx90 = Math.min(Math.ceil(sorted.length * 0.9) - 1, sorted.length - 1);
      return {{ avg, p90: sorted[Math.max(0, idx90)], n: ranges.length }};
    }}

    function updateRangeMetrics() {{
      // Live Range 60s
      const r60 = liveRange(currentAsset, 60);
      const lr60 = el("live-range-60");
      if (lr60) lr60.textContent = r60
        ? `${{fmtUsd(r60.range)}} (${{fmtSpot(r60.lo)}} – ${{fmtSpot(r60.hi)}})`
        : "—";

      // Avg & 90% Range for 1m buckets over last 30m
      const stats1m = bucketedRangeStats(currentAsset, 60, 30 * 60);
      const avgEl = el("avg-range-1m");
      const p90El = el("p90-range-1m");
      if (avgEl) {{
        const a1 = stats1m ? fmtUsd(stats1m.avg) : "—";
        avgEl.textContent = `${{a1}}`;
      }}
      if (p90El) {{
        const p1 = stats1m ? fmtUsd(stats1m.p90) : "—";
        p90El.textContent = `${{p1}}`;
      }}
    }}

    function buildTenSecondSeries(asset, lookbackSec) {{
      const m = microState[asset];
      if (!m || m.prices.length < 3 || m.secs.length !== m.prices.length) return [];
      const latestSec = m.secs[m.secs.length - 1];
      const startSec = latestSec - Math.max(60, lookbackSec) + 1;
      const bucketClose = new Map();
      for (let i = 0; i < m.secs.length; i++) {{
        const sec = m.secs[i];
        if (sec < startSec) continue;
        const bucket = Math.floor(sec / 10) * 10;
        bucketClose.set(bucket, m.prices[i]);
      }}
      const buckets = [...bucketClose.keys()].sort((a, b) => a - b);
      return buckets.map(b => ({{ sec: b + 9, price: bucketClose.get(b) }}));
    }}

    function getMicroStats(asset, tfMin) {{
      const lookbackSec = Math.max(300, microState[asset]?.windowSec || 900);
      const series = buildTenSecondSeries(asset, lookbackSec);
      if (series.length < 6) return null;

      const returns = [];
      const retSec = [];
      for (let i = 1; i < series.length; i++) {{
        const p0 = series[i - 1].price;
        const p1 = series[i].price;
        if (p0 > 0 && p1 > 0) {{
          returns.push(Math.log(p1 / p0));
          retSec.push(series[i].sec);
        }}
      }}
      if (returns.length < 5) return null;

      const sigma = stdev(returns);
      const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
      const net = Math.log(series[series.length - 1].price / series[0].price);
      const zMove = sigma > 0 ? net / (sigma * Math.sqrt(returns.length)) : 0;
      const upFrac = returns.filter(r => r > 0).length / returns.length;
      let microRun = 0;
      let lastSign = 0;
      for (let i = returns.length - 1; i >= 0; i--) {{
        const s = Math.sign(returns[i]);
        if (s !== 0) {{
          lastSign = s;
          break;
        }}
      }}
      if (lastSign !== 0) {{
        for (let i = returns.length - 1; i >= 0; i--) {{
          const s = Math.sign(returns[i]);
          if (s === 0) continue;
          if (s === lastSign) microRun += 1;
          else break;
        }}
      }}

      const latest = retSec.length ? retSec[retSec.length - 1] : Math.floor(Date.now() / 1000);
      const from5 = latest - 300;
      const fromWin = latest - lookbackSec;
      const from1 = latest - 60;
      const abs5 = [];
      let n5 = 0;
      let nWin = 0;
      let jumpCount5 = 0;
      let jumpCountWin = 0;
      let burst1m = 0;
      for (let i = 0; i < returns.length; i++) {{
        const r = returns[i];
        const a = Math.abs(r);
        const sec = retSec[i];
        const isJump = sigma > 0 && a >= 2 * sigma;
        if (sec >= fromWin) {{
          nWin += 1;
          if (isJump) jumpCountWin += 1;
        }}
        if (sec >= from5) {{
          n5 += 1;
          abs5.push(a);
          if (isJump) jumpCount5 += 1;
        }}
        if (sec >= from1 && isJump) {{
          burst1m += 1;
        }}
      }}

      const jumpRate5 = n5 > 0 ? (jumpCount5 / n5) : 0;
      const jumpRateWin = nWin > 0 ? (jumpCountWin / nWin) : 0;
      const maxJump5 = abs5.length ? Math.max(...abs5) : 0;
      const medJump5 = abs5.length ? median(abs5) : 0;

      return {{
        n: returns.length,
        n5,
        nWin,
        sigma10s: sigma,
        sigma1mEq: sigma * Math.sqrt(6),
        mean,
        net,
        zMove,
        upFrac,
        microRun,
        jumpCount5,
        jumpRate5,
        jumpCountWin,
        jumpRateWin,
        burst1m,
        maxJump5,
        medJump5,
      }};
    }}

    function conditionBand(score) {{
      if (score >= 80) return {{ label: "VERY GOOD", color: "#22c55e" }};
      if (score >= 65) return {{ label: "GOOD", color: "#84cc16" }};
      if (score >= 45) return {{ label: "MEDIUM", color: "#f59e0b" }};
      if (score >= 25) return {{ label: "BAD", color: "#f97316" }};
      return {{ label: "VERY BAD", color: "#ef4444" }};
    }}

    function computeMarketCondition(asset) {{
      const d = ASSET_DATA[asset];
      if (!d) return null;
      const micro = getMicroStats(asset, decisionTf);
      if (!micro || micro.n < 10) {{
        return {{
          score: 0,
          label: "BUILDING",
          color: "#94a3b8",
          meta: `Collecting samples (${{micro ? micro.n : 0}})...`,
        }};
      }}

      const stateLabel = d.state_label || "NEUTRAL";
      const stateTrend = stateLabel.startsWith("TREND_")
        ? clamp(d.state_er_raw * (0.7 + 0.3 * d.state_persist_raw), 0, 1)
        : 0;
      const microTrend = clamp(
        (Math.abs(micro.zMove) / 2.4) * 0.6 +
        ((micro.upFrac > 0.65 || micro.upFrac < 0.35) ? 0.4 : 0),
        0,
        1
      );
      let dirMatch = 0;
      const microUp = micro.net > 0;
      if (stateLabel.startsWith("TREND_UP") && microUp) dirMatch = 0.20;
      else if (stateLabel.startsWith("TREND_DOWN") && !microUp) dirMatch = 0.20;
      const directionConsistency = clamp(Math.abs(micro.upFrac - 0.5) * 2, 0, 1);
      const runNorm = clamp(d.state_run_raw / (decisionTf === 5 ? 3 : 5), 0, 1);
      const persistNorm = clamp((d.state_persist_raw - 0.5) / 0.5, 0, 1);
      const health = clamp(d.current_score_raw / 100, 0, 1);
      const microPersistNorm = clamp((micro.microRun || 0) / (decisionTf === 5 ? 4 : 7), 0, 1);

      const jumpPenalty = clamp(
        (0.30 * micro.jumpRate5) +
        (0.25 * micro.jumpRateWin) +
        (0.20 * d.jump_rate_60m_raw) +
        (0.25 * d.jump_rate_60m_anchor_raw),
        0,
        1
      );
      const burstPenalty = clamp(micro.burst1m / (decisionTf === 5 ? 4 : 6), 0, 1);
      const regimePenalty = clamp((d.regime_ratio_raw - 1.0) / 1.8, 0, 1);
      const chopPenalty = stateLabel === "CHOP" ? 0.55 : (stateLabel === "NEUTRAL" ? 0.20 : 0.0);
      const noisePenalty = clamp(
        0.45 * jumpPenalty + 0.20 * burstPenalty + 0.25 * regimePenalty + 0.10 * chopPenalty,
        0,
        1
      );

      const signalQuality = clamp(
        0.38 * stateTrend +
        0.28 * microTrend +
        0.12 * directionConsistency +
        0.10 * runNorm +
        0.10 * microPersistNorm +
        dirMatch,
        0,
        1
      );
      const base = clamp(
        (0.55 * signalQuality) + (0.20 * persistNorm) + (0.25 * health),
        0,
        1
      );
      const dataQuality = clamp(micro.n / (decisionTf === 5 ? 24 : 38), 0.35, 1.0);
      const finalScore = 100 * clamp((base * (1 - noisePenalty)) * dataQuality, 0, 1);
      const band = conditionBand(finalScore);
      return {{
        score: finalScore,
        label: band.label,
        color: band.color,
        meta: `trend ${{(signalQuality*100).toFixed(0)}} · noise ${{(noisePenalty*100).toFixed(0)}} · dir ${{dirMatch.toFixed(2)}} · mRun ${{micro.microRun || 0}} · jumps A/F ${{(d.jump_rate_60m_raw*100).toFixed(1)}}%/${{(d.jump_rate_60m_anchor_raw*100).toFixed(1)}}% · regime ${{d.regime_ratio_raw.toFixed(2)}}x`,
      }};
    }}

    function updateMarketCondition() {{
      const out = computeMarketCondition(currentAsset);
      const labelEl = el("market-condition-label");
      const scoreEl = el("market-condition-score");
      const metaEl = el("market-condition-meta");
      if (!labelEl || !scoreEl || !metaEl || !out) return;
      labelEl.textContent = out.label;
      labelEl.style.color = out.color;
      if (out.label === "BUILDING") {{
        scoreEl.textContent = "Score: —";
      }} else {{
        scoreEl.textContent = `Score: ${{out.score.toFixed(1)}}/100`;
      }}
      metaEl.textContent = out.meta;
    }}

    function updateMicro() {{
      const m = microState[currentAsset];
      updateStreamHealth(currentAsset);
      if (!m || m.prices.length < 5) {{
        setMicroStatus("Collecting live ticks...");
        updateMarketCondition();
        return;
      }}
      const stats = getMicroStats(currentAsset, decisionTf);
      if (!stats) {{
        setMicroStatus("Building 10s buckets...");
        el("micro-samples").textContent = `${{Math.max(0, m.prices.length - 1)}} raw`;
        updateMarketCondition();
        return;
      }}
      const spot = spotMap[currentAsset] ?? m.prices[m.prices.length-1];
      const sigmaBp = stats.sigma10s * 10000;
      const sigmaUsd = stats.sigma10s * spot;
      const sigma1mEq = stats.sigma1mEq;
      const sigma15mEq = sigma1mEq * Math.sqrt(15);
      const sigma1mBp = sigma1mEq * 10000;
      const sigma1mUsd = sigma1mEq * spot;
      const sigma15mBp = sigma15mEq * 10000;
      const sigma15mUsd = sigma15mEq * spot;
      const twoSigmaUsd = 2 * stats.sigma10s * spot;
      const maxJumpUsd = stats.maxJump5 * spot;
      const medJumpUsd = stats.medJump5 * spot;
      setMicroStatus(`Active · 10s buckets · last ${{Math.round((microState[currentAsset]?.windowSec || 900) / 60)}}m`);
      el("micro-sigma").textContent = `${{sigmaBp.toFixed(2)}} bp (~${{fmtUsd(sigmaUsd)}})`;
      el("micro-sigma1m").textContent = `${{sigma1mBp.toFixed(2)}} bp (~${{fmtUsd(sigma1mUsd)}})`;
      el("micro-sigma15").textContent = `${{sigma15mBp.toFixed(2)}} bp (~${{fmtUsd(sigma15mUsd)}})`;
      el("micro-2sigma").textContent = `~${{fmtUsd(twoSigmaUsd)}}`;
      el("micro-jumps5").textContent = `${{stats.jumpCount5}} / ${{stats.n5}}`;
      el("micro-jumprate").textContent = `${{(stats.jumpRate5 * 100).toFixed(1)}}% / ${{(stats.jumpRateWin * 100).toFixed(1)}}%`;
      el("micro-max5").textContent = `~${{fmtUsd(maxJumpUsd)}}`;
      el("micro-med5").textContent = `~${{fmtUsd(medJumpUsd)}}`;
      el("micro-samples").textContent = `${{stats.n}}`;
      m.lastStats = {{
        sigma10s: stats.sigma10s,
        sigma1mEq: stats.sigma1mEq,
        sigma15mEq,
        jumpCount5: stats.jumpCount5,
        jumpRate5: stats.jumpRate5,
        jumpRateWin: stats.jumpRateWin,
        microRun: stats.microRun,
        maxJump5: stats.maxJump5,
        medJump5: stats.medJump5,
        n: stats.n,
      }};
      updateMarketCondition();
      updateDecision();
    }}

    function computeDecision(asset) {{
      const d = ASSET_DATA[asset];
      if (!d) return null;
      const S = spotMap[asset] ?? d.calc_spot_num;
      const decisionStrikeLive = decisionStrikeInput
        ? decisionStrikeInput.value.replace(/,/g, "").trim()
        : "";
      if (decisionStrikeLive !== decisionStrike) {{
        decisionStrike = decisionStrikeLive;
        saveDecisionForAsset(asset);
      }}
      const K = parseFloat(decisionStrikeLive.replace(/,/g, ""));
      const t = timeLeft(decisionTf);
      const tMin = t.minutes;
      const timeText = `${{t.mm}}:${{t.ss.toString().padStart(2,"0")}}`;

      const reasons = [];
      const riskFlags = [];
      if (!S || !Number.isFinite(S)) {{
        return {{
          action: "NO_TRADE",
          badge: "WAIT",
          confidence: 0,
          iconClass: "wait",
          timeText,
          reasons: ["No live spot tick for current asset."],
          meta: "Waiting for stream",
        }};
      }}
      if (!K || !Number.isFinite(K)) {{
        return {{
          action: "NO_TRADE",
          badge: "WAIT",
          confidence: 0,
          iconClass: "wait",
          timeText,
          reasons: ["Enter strike to activate decision model."],
          meta: `Spot ${{fmtSpot(S)}} · tf ${{decisionTf}}m`,
        }};
      }}

      const micro = getMicroStats(asset, decisionTf);
      if (!micro || micro.n < (decisionTf === 5 ? 18 : 28)) {{
        return {{
          action: "NO_TRADE",
          badge: "BUILDING_WINDOW",
          confidence: 0,
          iconClass: "wait",
          timeText,
          reasons: ["Collecting rolling window samples."],
          meta: `Samples ${{micro ? micro.n : 0}} · need ${{decisionTf === 5 ? 18 : 28}}`,
        }};
      }}

      const sigmaBlend = Math.max(1e-6, 0.55 * d.calc_sigma1m + 0.45 * micro.sigma1mEq);
      const pYes = calcProb(S, K, tMin, sigmaBlend);
      if (pYes === null) {{
        return {{
          action: "NO_TRADE",
          badge: "WAIT",
          confidence: 0,
          iconClass: "wait",
          timeText,
          reasons: ["Probability model unavailable."],
          meta: "Sigma unavailable",
        }};
      }}

      const edge = pYes - 0.5;
      const sigT = sigmaBlend * Math.sqrt(Math.max(tMin, 1e-6));
      const zDist = sigT > 0 ? Math.log(S / K) / sigT : 0;
      const strikeBp = ((S / K) - 1) * 10000;
      const stateLabel = d.state_label || "NEUTRAL";
      let fState = 0.0;
      if (stateLabel.startsWith("TREND_UP")) fState = 0.35 + 0.65 * d.state_er_raw;
      else if (stateLabel.startsWith("TREND_DOWN")) fState = -(0.35 + 0.65 * d.state_er_raw);
      else if (stateLabel === "CHOP") fState = -0.1;

      // Strike sensitivity: reduce saturation so changing strike moves outputs visibly.
      const fProb = clamp(edge / 0.30, -1, 1);
      const fDist = clamp(zDist / 3.50, -1, 1);
      const fMicro = clamp(micro.zMove / 2.2, -1, 1);
      const jumpPenalty = clamp(
        (0.45 * micro.jumpRate5) + (0.30 * micro.jumpRateWin) + (0.25 * d.jump_rate_60m_raw),
        0,
        1
      );
      const volPenalty = clamp((d.regime_ratio_raw - 1.0) / 1.4, 0, 1);

      const timePressure = clamp((decisionTf - tMin) / Math.max(decisionTf, 1e-6), 0, 1);
      const strikeInfluence = clamp(Math.abs(zDist) / 2.5, 0, 1);
      const directional =
        0.52 * fProb +
        0.24 * fDist +
        0.18 * fMicro +
        0.06 * fState;
      const score =
        directional -
        0.14 * jumpPenalty -
        0.10 * volPenalty +
        0.06 * Math.sign(directional || 0) * timePressure * strikeInfluence;

      let threshold = decisionTf === 5 ? 0.13 : 0.10;
      threshold += 0.06 * jumpPenalty + 0.04 * volPenalty;
      if (Math.abs(zDist) < 0.25) threshold += 0.04;
      else if (Math.abs(zDist) < 0.50) threshold += 0.02;
      if (tMin < 1.0) threshold += 0.06;
      else if (tMin < 2.0) threshold += 0.04;
      else if (tMin < 4.0) threshold += 0.02;

      let hardBlock = false;
      if (micro.burst1m >= (decisionTf === 5 ? 4 : 5)) {{
        hardBlock = true;
        riskFlags.push(`micro jump burst ${{micro.burst1m}}/1m`);
      }}
      if (d.jump15_count_raw >= (decisionTf === 5 ? 5 : 7)) {{
        hardBlock = true;
        riskFlags.push(`15m jump count ${{d.jump15_count_raw}}`);
      }}
      if (d.regime_ratio_raw > 3.2) {{
        hardBlock = true;
        riskFlags.push(`regime ratio ${{d.regime_ratio_raw.toFixed(2)}}x`);
      }}
      if (tMin < 0.10) {{
        hardBlock = true;
        riskFlags.push("time-left under 6s");
      }}

      let action = "NO_TRADE";
      let badge = "WAIT";
      let iconClass = "wait";
      if (!hardBlock) {{
        if (score >= threshold) {{
          action = "BUY_UP";
          badge = "UP_EDGE";
          iconClass = "up";
        }} else if (score <= -threshold) {{
          action = "BUY_DOWN";
          badge = "DOWN_EDGE";
          iconClass = "down";
        }}
      }} else {{
        badge = "RISK_LOCK";
      }}

      const contributions = [
        {{ k: "prob", v: 0.40 * fProb }},
        {{ k: "dist", v: 0.16 * fDist }},
        {{ k: "micro", v: 0.22 * fMicro }},
        {{ k: "state", v: 0.12 * fState }},
        {{ k: "jump", v: -0.14 * jumpPenalty }},
        {{ k: "vol", v: -0.10 * volPenalty }},
      ].sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
      reasons.push(
        `KΔ ${{strikeBp.toFixed(0)}}bp · pYes ${{(pYes * 100).toFixed(1)}}% · edge ${{(edge * 100).toFixed(1)}}% · zDist ${{zDist.toFixed(2)}}`
      );
      reasons.push(
        `micro zMove ${{micro.zMove.toFixed(2)}} · jump 5m/15m ${{(micro.jumpRate5 * 100).toFixed(1)}}%/${{(micro.jumpRateWin * 100).toFixed(1)}}% · max5m ${{(micro.maxJump5 * 10000).toFixed(1)}}bp`
      );
      const lastTickMs = Math.max(
        chainlinkState.lastTickByAsset[asset] || 0,
        binanceState.lastTickByAsset[asset] || 0
      );
      const tickAge = lastTickMs
        ? Math.max(0, Math.round((Date.now() - lastTickMs) / 1000))
        : null;
      if (tickAge !== null) reasons.push(`live tick age ${{tickAge}}s`);
      reasons.push(
        `dominant: ${{contributions[0].k}} ${{contributions[0].v >= 0 ? "+" : ""}}${{contributions[0].v.toFixed(3)}}`
      );
      if (riskFlags.length) reasons.push(`gates: ${{riskFlags.join(", ")}}`);

      const baseConf = sigmoid((Math.abs(score) - threshold) * 10);
      const qData = clamp(micro.n / (decisionTf === 5 ? 28 : 50), 0.25, 1.0);
      const qTime = clamp(tMin / (decisionTf === 5 ? 2.0 : 5.0), 0.35, 1.0);
      const qJump = clamp(1.0 - jumpPenalty, 0.3, 1.0);
      const qHealth = clamp(d.current_score_raw / 100.0, 0.4, 1.0);
      const qStrike = clamp(Math.abs(edge) * 2.2 + Math.abs(zDist) * 0.12, 0.10, 1.0);

      // Edge-aware quality blending: when probability edge is extreme,
      // quality factors matter far less — the signal overwhelms the noise.
      const edgeCertainty = clamp(Math.abs(edge) / 0.45, 0, 1);
      const qualityProduct = qData * qTime * qJump * qHealth * qStrike;
      const blendedQuality = edgeCertainty * 1.0 + (1 - edgeCertainty) * qualityProduct;
      let confidence = 100 * baseConf * blendedQuality;
      if (action === "NO_TRADE") {{
        // Keep confidence informative even in NO_TRADE mode (avoid flat 49% display).
        const penalty = hardBlock ? 0.40 : 0.65;
        confidence *= penalty;
      }}
      confidence = clamp(confidence, 0, 99);

      return {{
        action,
        badge,
        confidence,
        iconClass,
        timeText,
        reasons,
        meta: `tf ${{decisionTf}}m · K ${{fmtSpot(K)}} · pYes ${{(pYes*100).toFixed(1)}}% · left ${{tMin.toFixed(2)}}m · score ${{score.toFixed(3)}} · thr ${{threshold.toFixed(3)}} · jumpPen ${{jumpPenalty.toFixed(2)}}`,
      }};
    }}

    function renderDecision(decision) {{
      if (!decision) return;
      const icon = el("decision-icon");
      const actionEl = el("decision-action");
      const badgeEl = el("decision-badge");
      const confEl = el("decision-confidence");
      const meterEl = el("decision-meter");
      const whyEl = el("decision-reasons");
      const metaEl = el("decision-meta");
      const timeEl = el("decision-time");
      if (!icon || !actionEl || !badgeEl || !confEl || !meterEl || !whyEl || !metaEl) return;

      icon.classList.remove("up", "down", "wait");
      icon.classList.add(decision.iconClass || "wait");
      actionEl.textContent = decision.action;
      badgeEl.textContent = decision.badge;
      confEl.textContent = `Confidence: ${{decision.confidence.toFixed(1)}}%`;
      meterEl.style.width = `${{decision.confidence.toFixed(1)}}%`;
      if (timeEl && decision.timeText) timeEl.textContent = decision.timeText;
      whyEl.textContent = (decision.reasons && decision.reasons.length)
        ? decision.reasons.join(" | ")
        : "No decision details.";
      metaEl.textContent = decision.meta || "";
    }}

    function updateDecision() {{
      const decision = computeDecision(currentAsset);
      renderDecision(decision);
    }}

    function connectChainlink() {{
      const ws = new WebSocket("wss://ws-live-data.polymarket.com");
      let pingTimer = null;
      ws.addEventListener("open", () => {{
        chainlinkState.connected = true;
        const subs = CHAINLINK_SYMBOLS.map(s => ({{
          topic: "crypto_prices_chainlink",
          type: "*",
          filters: `{{\\"symbol\\":\\"${{s}}\\"}}`
        }}));
        ws.send(JSON.stringify({{ action: "subscribe", subscriptions: subs }}));
        pingTimer = setInterval(() => {{ try {{ ws.send("PING"); }} catch (_) {{}} }}, 5000);
      }});
      ws.addEventListener("message", (event) => {{
        try {{
          chainlinkState.lastMsgMs = Date.now();
          const data = JSON.parse(event.data);
          if (data.topic === "crypto_prices_chainlink" && data.payload && data.payload.value) {{
            const price = parseFloat(data.payload.value);
            const sym = data.payload.symbol || data.payload.asset || "";
            const asset = parseAssetFromSymbol(sym);
            if (asset && ASSET_DATA[asset] && !Number.isNaN(price) && shouldAcceptTick("chainlink", asset)) {{
              spotMap[asset] = price;
              chainlinkState.lastTickByAsset[asset] = Date.now();
              const sec = Math.floor(Date.now() / 1000);
              appendMicro(asset, sec, price);
              saveMicroState();
              if (asset === currentAsset) {{
                updateMicro();
                updateDecision();
              }}
            }}
          }}
        }} catch (_) {{}}
      }});
      ws.addEventListener("close", () => {{
        chainlinkState.connected = false;
        if (pingTimer) clearInterval(pingTimer);
        setTimeout(connectChainlink, 2000);
      }});
      ws.addEventListener("error", () => {{
        chainlinkState.connected = false;
        try {{ ws.close(); }} catch (_) {{}}
      }});
    }}

    function connectBinance() {{
      const streams = ASSETS
        .map(asset => (BINANCE_SYMBOLS[asset] || "").toLowerCase())
        .filter(Boolean)
        .map(sym => `${{sym}}@trade`);
      if (!streams.length) return;
      const ws = new WebSocket(`wss://stream.binance.com:9443/stream?streams=${{streams.join("/")}}`);
      ws.addEventListener("open", () => {{
        binanceState.connected = true;
      }});
      ws.addEventListener("message", (event) => {{
        try {{
          binanceState.lastMsgMs = Date.now();
          const msg = JSON.parse(event.data);
          const payload = msg && msg.data ? msg.data : msg;
          const sym = (payload && payload.s) ? String(payload.s).toUpperCase() : "";
          const priceRaw = payload ? (payload.p ?? payload.c) : null;
          const price = parseFloat(priceRaw);
          const asset = parseAssetFromSymbol(sym);
          if (asset && ASSET_DATA[asset] && !Number.isNaN(price)) {{
            const nowMs = Date.now();
            binanceState.lastTickByAsset[asset] = nowMs;
            const sec = Math.floor(nowMs / 1000);
            appendBinanceShort(asset, sec, price);
            if (asset === currentAsset) {{
              el("live-spot").textContent = fmtSpot(price);
              updateRangeMetrics();
            }}
            if (!shouldAcceptTick("binance", asset)) return;

            spotMap[asset] = price;
            appendMicro(asset, sec, price);
            saveMicroState();
            if (asset === currentAsset) {{
              updateMicro();
              updateDecision();
            }}
          }}
        }} catch (_) {{}}
      }});
      ws.addEventListener("close", () => {{
        binanceState.connected = false;
        setTimeout(connectBinance, 2000);
      }});
      ws.addEventListener("error", () => {{
        binanceState.connected = false;
        try {{ ws.close(); }} catch (_) {{}}
      }});
    }}

    restoreMicroState();
    restoreBinanceShortState();
    restoreDecisionCfg();
    setActiveAsset(currentAsset);
    setActiveDecisionTf();
    updateMicro();
    updateRangeMetrics();
    updateMarketCondition();
    updateDecision();
    setInterval(updateMicro, 1000);
    setInterval(updateRangeMetrics, 1000);
    setInterval(updateMarketCondition, 1000);
    setInterval(updateDecision, 1000);
    setInterval(() => updateStreamHealth(currentAsset), 1000);
    setInterval(() => {{ saveMicroState(); saveBinanceShortState(); }}, 5000);
    window.addEventListener("beforeunload", () => {{ saveMicroState(); saveBinanceShortState(); }});
    document.addEventListener("visibilitychange", () => {{
      if (document.visibilityState === "hidden") {{ saveMicroState(); saveBinanceShortState(); }}
    }});
    // Binance stream is always connected for short-window diagnostics.
    connectBinance();
    if (LIVE_SOURCE_MODE === "chainlink") {{
      connectChainlink();
    }} else if (LIVE_SOURCE_MODE === "auto") {{
      connectChainlink();
    }}

    let t = {data['refresh_s']};
    const rt = document.getElementById("refresh-t");
    setInterval(() => {{
      t -= 1;
      if (t <= 0) location.reload();
      else rt.textContent = t;
    }}, 1000);
  </script>
</body>
</html>
"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


def run_once(args, refresh_s: int):
    assets = [a.strip().upper() for a in args.assets.split(",") if a.strip()]
    assets = [a for a in assets if a in ASSET_MAP]
    if not assets:
        print("No valid assets selected.")
        return

    asset_data = {}
    for asset in assets:
        symbol = ASSET_MAP[asset]["binance"]
        try:
            klines = fetch_klines_with_retry(symbol, args.hours, retries=3)
            data = compute_asset_metrics(klines, asset, args.short_min, args.short2_min)
            if data:
                asset_data[asset] = data
                LAST_ASSET_DATA[asset] = dict(data)
        except Exception as exc:  # noqa: BLE001
            cached = LAST_ASSET_DATA.get(asset)
            if cached:
                fallback = dict(cached)
                fallback["data_note"] = "stale (REST timeout fallback)"
                asset_data[asset] = fallback
                print(f"[warn] {asset}: REST fetch failed, using cached metrics ({exc})")
            else:
                print(f"[warn] {asset}: REST fetch failed and no cache available ({exc})")

    if not asset_data:
        print("No asset data computed.")
        return

    current = assets[0]
    data = {
        "source": "binance",
        "hours": args.hours,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "assets": assets,
        "current_asset": current,
        "asset_data": asset_data,
        "refresh_s": max(1, int(refresh_s)),
        "micro_window": int(args.micro_min),
        "chainlink_symbols": [ASSET_MAP[a]["chainlink"] for a in assets],
        "binance_symbols": {a: ASSET_MAP[a]["binance"] for a in assets},
        "live_source": args.live_source,
    }
    render_html(data, "vol_report.html")
    print("HTML report saved to vol_report.html")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", default="SOL,XRP", help="Comma-separated assets")
    parser.add_argument("--hours", type=float, default=6.0, help="Lookback window in hours (1m candles)")
    parser.add_argument("--short-min", type=int, default=30, help="Short window in minutes (meso)")
    parser.add_argument("--short2-min", type=int, default=60, help="Second short window in minutes (meso)")
    parser.add_argument("--micro-min", type=int, default=15, help="Chainlink micro window in minutes")
    parser.add_argument(
        "--live-source",
        default="auto",
        choices=["auto", "binance", "chainlink"],
        help="Live tick source for micro/decision (default auto = Binance primary, Chainlink fallback)",
    )
    parser.add_argument("--interval", type=int, default=60, help="Refresh interval in seconds")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8008)
    args = parser.parse_args()

    if args.loop:
        httpd = None
        if args.serve:
            from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
            from threading import Thread
            host = "127.0.0.1"
            httpd = ThreadingHTTPServer((host, args.port), SimpleHTTPRequestHandler)
            httpd.daemon_threads = True
            thread = Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            print(f"Serving http://{host}:{args.port}/vol_report.html (Ctrl+C to stop)")
        try:
            while True:
                try:
                    run_once(args, args.interval)
                except Exception as exc:  # noqa: BLE001
                    print(f"[warn] run_once failed: {exc}")
                import time as _t
                _t.sleep(max(1, args.interval))
        except KeyboardInterrupt:
            pass
        finally:
            if httpd:
                httpd.shutdown()
    else:
        run_once(args, args.interval)


if __name__ == "__main__":
    main()
