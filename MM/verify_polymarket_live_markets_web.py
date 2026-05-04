#!/usr/bin/env python3
"""
Local browser dashboard for verifying live Polymarket 5-minute markets.

This server is read-only. It does not write capture files and does not place or
cancel orders. Open the page in a browser to compare the current ETH/XRP market
slugs, timing, token ids, and top-of-book quotes against Polymarket.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from kou_polymarket_live_capture import (
    ASSET_ALIASES,
    BROWSER_HEADERS,
    BookTop,
    DiscoveryResult,
    asset_from_symbol,
    build_book_payload,
    build_market_payload,
    discover_current_and_next_5m_markets,
    discover_slug_first_current_and_next_5m_markets,
    market_status,
    parse_assets,
    safe_float,
    selected_snapshot_assets,
    utc_iso,
)

CLOB_HOST = "https://clob.polymarket.com"


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Polymarket Live Market Verifier</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #17201c;
      --muted: #5b665f;
      --line: #ccd7d0;
      --paper: #f7faf8;
      --panel: #ffffff;
      --ok: #14784d;
      --warn: #b65d12;
      --bad: #b42222;
      --accent: #1f7a8c;
      --soft: #e8f4ef;
      --yellow: #fff8df;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      color: var(--ink);
      background: var(--paper);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }

    header {
      border-bottom: 1px solid var(--line);
      background: #ffffff;
    }

    .topbar {
      max-width: 1180px;
      margin: 0 auto;
      padding: 18px 18px 14px;
      display: flex;
      gap: 16px;
      align-items: flex-start;
      justify-content: space-between;
      flex-wrap: wrap;
    }

    h1 {
      margin: 0 0 6px;
      font-size: 24px;
      line-height: 1.15;
    }

    p {
      margin: 0;
      color: var(--muted);
      line-height: 1.45;
    }

    .status-strip {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
      min-height: 30px;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 5px 9px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--soft);
      color: var(--ink);
      font-size: 13px;
      white-space: nowrap;
    }

    .pill.ok { color: var(--ok); border-color: #9bd5bc; background: #ecf8f2; }
    .pill.warn { color: var(--warn); border-color: #e4bb92; background: #fff4e8; }
    .pill.bad { color: var(--bad); border-color: #e2a0a0; background: #fff0f0; }

    main {
      max-width: 1180px;
      margin: 0 auto;
      padding: 18px;
    }

    .asset-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      align-items: stretch;
    }

    .asset-panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      min-width: 0;
      overflow: hidden;
    }

    .asset-head {
      padding: 14px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }

    .asset-title {
      display: flex;
      flex-direction: column;
      gap: 3px;
      min-width: 0;
    }

    .asset-title strong {
      font-size: 20px;
      line-height: 1.1;
    }

    .asset-title span {
      color: var(--muted);
      font-size: 13px;
      overflow-wrap: anywhere;
    }

    .metric-table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }

    .metric-table th,
    .metric-table td {
      padding: 9px 14px;
      border-bottom: 1px solid #e7eee9;
      vertical-align: top;
      text-align: left;
      font-size: 14px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }

    .metric-table th {
      width: 32%;
      color: var(--muted);
      font-weight: 600;
    }

    .capture-band {
      margin-bottom: 14px;
      background: #ffffff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px 14px;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }

    .capture-item {
      min-width: 0;
    }

    .capture-item span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
      margin-bottom: 3px;
    }

    .capture-item strong {
      display: block;
      font-size: 14px;
      line-height: 1.3;
      overflow-wrap: anywhere;
    }

    .price-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }

    .price-box {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px;
      background: #ffffff;
      min-height: 104px;
    }

    .price-box strong {
      display: block;
      font-size: 13px;
      margin-bottom: 5px;
    }

    .price-box div {
      font-size: 13px;
      line-height: 1.35;
    }

    .match-ok { color: var(--ok); font-weight: 700; }
    .match-warn { color: var(--warn); font-weight: 700; }
    .match-bad { color: var(--bad); font-weight: 700; }

    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }

    a {
      color: var(--accent);
      text-decoration-thickness: 1px;
      text-underline-offset: 3px;
    }

    .foot {
      margin-top: 14px;
      color: var(--muted);
      font-size: 13px;
    }

    .error {
      margin-bottom: 14px;
      border: 1px solid #e2a0a0;
      background: #fff0f0;
      color: var(--bad);
      border-radius: 8px;
      padding: 10px 12px;
      display: none;
    }

    @media (max-width: 760px) {
      .asset-grid { grid-template-columns: 1fr; }
      .topbar { align-items: stretch; }
      .status-strip { justify-content: flex-start; }
      h1 { font-size: 21px; }
      .metric-table th { width: 38%; }
      .capture-band { grid-template-columns: 1fr; }
      .price-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div class="topbar">
      <div>
        <h1>Polymarket Live Market Verifier</h1>
        <p>Current 5-minute ETH/XRP market, Kou alignment, next slug, and YES/NO order book quotes.</p>
      </div>
      <div class="status-strip">
        <span id="server-pill" class="pill warn">starting</span>
        <span id="kou-pill" class="pill warn">Kou unknown</span>
        <span id="refresh-pill" class="pill">refreshing</span>
      </div>
    </div>
  </header>
  <main>
    <div id="error" class="error"></div>
    <section id="capture" class="capture-band"></section>
    <section id="assets" class="asset-grid"></section>
    <p class="foot">This page is read-only. It refreshes every second, compares live Polymarket discovery with the capture sidecar output, and shows YES/NO token prices.</p>
  </main>
  <script>
    const captureEl = document.getElementById('capture');
    const assetsEl = document.getElementById('assets');
    const errorEl = document.getElementById('error');
    const serverPill = document.getElementById('server-pill');
    const kouPill = document.getElementById('kou-pill');
    const refreshPill = document.getElementById('refresh-pill');

    const text = (value) => value === null || value === undefined || value === '' ? '-' : String(value);
    const fixed = (value, digits = 1) => value === null || value === undefined ? '-' : Number(value).toFixed(digits);
    const pillClass = (kind) => `pill ${kind || ''}`.trim();

    function escapeHtml(value) {
      return text(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
    }

    function metric(label, value, cls = '') {
      return `<tr><th>${escapeHtml(label)}</th><td class="${cls}">${value}</td></tr>`;
    }

    function bookLine(book) {
      if (!book) return '-';
      const yesExtra = book.yes_extra || {};
      const noExtra = book.no_extra || {};
      return priceGrid(
        {
          label: 'LIVE YES',
          buy: yesExtra.buy_price,
          ask: book.yes && book.yes.ask,
          askSize: book.yes && book.yes.ask_size,
        },
        {
          label: 'LIVE NO',
          buy: noExtra.buy_price,
          ask: book.no && book.no.ask,
          askSize: book.no && book.no.ask_size,
        }
      );
    }

    function priceGrid(yes, no, footer = '') {
      const box = (side) => `
        <div class="price-box">
          <strong>${escapeHtml(side.label)}</strong>
          <div>buy ${text(side.buy)}</div>
          <div>best ask ${text(side.ask)}</div>
          <div>ask size ${text(side.askSize)}</div>
        </div>
      `;
      return `<div class="price-grid">${box(yes)}${box(no)}</div>${footer ? `<div style="margin-top:6px">${escapeHtml(footer)}</div>` : ''}`;
    }

    function captureItem(label, value) {
      return `<div class="capture-item"><span>${escapeHtml(label)}</span><strong>${value}</strong></div>`;
    }

    function renderCapture(capture) {
      if (!capture) {
        captureEl.innerHTML = captureItem('Capture sidecar', '<span class="match-warn">not checked</span>');
        return;
      }
      const age = capture.latest_age_s === null || capture.latest_age_s === undefined ? '-' : `${Number(capture.latest_age_s).toFixed(1)}s`;
      const statusClass = capture.ok ? 'match-ok' : 'match-warn';
      captureEl.innerHTML = [
        captureItem('Capture sidecar', `<span class="${statusClass}">${capture.status}</span>`),
        captureItem('Session', escapeHtml(capture.session_id || '-')),
        captureItem('Latest quote age', escapeHtml(age)),
        captureItem('Stored rows', `${text(capture.quote_rows)} quotes · ${text(capture.grid_rows)} grid`),
      ].join('');
    }

    let latestData = null;
    let refreshInFlight = false;

    function renderAsset(item, nowTs) {
      const market = item.current_market || {};
      const kou = item.kou || {};
      const discovery = item.discovery || {};
      const next = item.next_market || {};
      const cap = item.capture || {};
      const statusKind = market.status === 'LIVE' ? 'ok' : ['NO_MARKET', 'ERROR'].includes(market.status) ? 'bad' : 'warn';
      const alignKind = market.alignment_status === 'aligned' ? 'ok' : market.alignment_status === 'warning' ? 'warn' : '';
      const tLeft = market.end_ts ? Math.max(0, Number(market.end_ts) - nowTs) : null;
      const url = market.url ? `<a href="${escapeHtml(market.url)}" target="_blank" rel="noreferrer">${escapeHtml(market.slug)}</a>` : '-';
      const nextUrl = next.url ? `<a href="${escapeHtml(next.url)}" target="_blank" rel="noreferrer">${escapeHtml(next.slug)}</a>` : escapeHtml(next.slug);
      const switchIn = tLeft === null ? '-' : `${fixed(tLeft, 1)}s`;
      const captureMatchClass = cap.slug_matches_current === true ? 'match-ok' : cap.slug_matches_current === false ? 'match-bad' : 'match-warn';
      const captureMatch = cap.slug_matches_current === true ? 'MATCH' : cap.slug_matches_current === false ? 'MISMATCH' : 'WAITING';
      const capturedPrices = cap.latest_quote ? priceGrid(
        {
          label: 'CAPTURED YES',
          buy: cap.latest_quote.token_prices && cap.latest_quote.token_prices.yes && cap.latest_quote.token_prices.yes.buy_price,
          ask: cap.latest_quote.book && cap.latest_quote.book.yes && cap.latest_quote.book.yes.ask,
          askSize: cap.latest_quote.book && cap.latest_quote.book.yes && cap.latest_quote.book.yes.ask_size,
        },
        {
          label: 'CAPTURED NO',
          buy: cap.latest_quote.token_prices && cap.latest_quote.token_prices.no && cap.latest_quote.token_prices.no.buy_price,
          ask: cap.latest_quote.book && cap.latest_quote.book.no && cap.latest_quote.book.no.ask,
          askSize: cap.latest_quote.book && cap.latest_quote.book.no && cap.latest_quote.book.no.ask_size,
        },
        `captured ${escapeHtml(cap.latest_quote.session && cap.latest_quote.session.captured_at_iso)}`
      ) : escapeHtml(cap.error || 'no captured quote yet');

      return `
        <article class="asset-panel">
          <div class="asset-head">
            <div class="asset-title">
              <strong>${escapeHtml(item.asset).toUpperCase()}</strong>
              <span>${escapeHtml(market.question || 'Current Polymarket 5-minute market')}</span>
            </div>
            <span class="${pillClass(statusKind)}">${escapeHtml(market.status)}</span>
          </div>
          <table class="metric-table">
            <tbody>
              ${metric('Current slug', url, 'mono')}
              ${metric('Window', `${escapeHtml(market.start_iso)}<br>${escapeHtml(market.end_iso)}`)}
              ${metric('Switch countdown', switchIn)}
              ${metric('Flags', `active=${text(market.active)} accepting=${text(market.accepting_orders)} closed=${text(market.closed)}`)}
              ${metric('Token ids', `YES ${escapeHtml(market.token_yes)}<br>NO ${escapeHtml(market.token_no)}`, 'mono')}
              ${metric('Discovery', `list=${text(discovery.list_count)} probe=${text(discovery.probe_count)} slug_probe=${text(discovery.used_slug_probe)}`)}
              ${metric('Kou bucket', `${escapeHtml(kou.symbol)}<br>${escapeHtml(kou.bucket_end_iso)}<br>time left ${text(kou.time_left_s)} · signal ${escapeHtml(kou.signal)}`)}
              ${metric('Alignment', `<span class="${pillClass(alignKind)}">${escapeHtml(market.alignment_status)}</span> ${text(market.kou_market_end_delta_s)}s`)}
              ${metric('Live YES / NO prices', item.book_error ? escapeHtml(item.book_error) : bookLine(item.book))}
              ${metric('Capture match', `<span class="${captureMatchClass}">${captureMatch}</span><br>${escapeHtml(cap.current_slug || '-')}`, 'mono')}
              ${metric('Captured YES / NO prices', capturedPrices)}
              ${metric('Next slug', next.slug ? `${nextUrl}<br>starts ${escapeHtml(next.start_iso)}` : '-')}
            </tbody>
          </table>
        </article>
      `;
    }

    function paint() {
      if (!latestData) return;
      const nowTs = Date.now() / 1000;
      errorEl.style.display = 'none';
      serverPill.className = 'pill ok';
      serverPill.textContent = `live ${new Date().toLocaleTimeString()}`;
      kouPill.className = latestData.kou_snapshot_seen ? 'pill ok' : 'pill warn';
      kouPill.textContent = latestData.kou_snapshot_seen ? 'Kou available' : 'Kou unavailable';
      refreshPill.textContent = `updated ${new Date().toLocaleTimeString()}`;
      renderCapture(latestData.capture);
      assetsEl.innerHTML = latestData.assets.map((item) => renderAsset(item, nowTs)).join('');
    }

    async function refresh() {
      if (refreshInFlight) {
        paint();
        return;
      }
      refreshInFlight = true;
      try {
        const res = await fetch('/api/status', { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        latestData = await res.json();
        if (latestData.error) {
          errorEl.textContent = latestData.error;
          errorEl.style.display = 'block';
        }
        paint();
      } catch (err) {
        serverPill.className = 'pill bad';
        serverPill.textContent = 'error';
        errorEl.textContent = err.message || String(err);
        errorEl.style.display = 'block';
      } finally {
        refreshInFlight = false;
      }
    }

    refresh();
    setInterval(paint, 1000);
    setInterval(refresh, 1000);
  </script>
</body>
</html>
"""


class VerifierState:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.requested_assets = parse_assets(args.assets)
        self.assets = sorted(self.requested_assets or set(ASSET_ALIASES))
        self._status_lock = threading.Lock()
        self._status_cache: Optional[dict[str, Any]] = None
        self._status_cache_ts = 0.0
        self._asset_report_cache: dict[str, dict[str, Any]] = {}

    def _resolve_capture_session(self) -> dict[str, Any]:
        root = Path(self.args.output_root)
        if self.args.session_id:
            session_dir = root / self.args.session_id
            return {
                "session_id": self.args.session_id,
                "session_dir": session_dir,
                "mode": "explicit",
                "error": None if session_dir.exists() else "session folder does not exist",
            }

        active: list[tuple[float, str, Path]] = []
        latest: list[tuple[float, str, Path]] = []
        if root.exists():
            for meta_path in root.glob("*/session_meta.json"):
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                try:
                    started_ts = float(meta.get("started_at_ts") or meta_path.stat().st_mtime)
                except (TypeError, ValueError):
                    started_ts = meta_path.stat().st_mtime
                item = (started_ts, meta_path.parent.name, meta_path.parent)
                latest.append(item)
                if meta.get("stopped_at_ts") is None and meta.get("stopped_at_iso") is None:
                    active.append(item)

        if len(active) == 1:
            _ts, session_id, session_dir = active[0]
            return {"session_id": session_id, "session_dir": session_dir, "mode": "active", "error": None}
        if len(active) > 1:
            active.sort()
            _ts, session_id, session_dir = active[-1]
            return {
                "session_id": session_id,
                "session_dir": session_dir,
                "mode": "newest_active",
                "error": "multiple active sessions; pass --session-id for exact monitoring",
            }
        if latest:
            latest.sort()
            _ts, session_id, session_dir = latest[-1]
            return {"session_id": session_id, "session_dir": session_dir, "mode": "latest_finished", "error": None}
        return {"session_id": None, "session_dir": None, "mode": "none", "error": "no live_capture sessions found"}

    def _read_tail_jsonl(self, path: Path, limit: int) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: deque[str] = deque(maxlen=max(1, limit))
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(line)
        out: list[dict[str, Any]] = []
        for line in rows:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                out.append(payload)
        return out

    def _count_jsonl_rows(self, path: Path) -> int:
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())

    def _capture_status(self, now_ts: float) -> dict[str, Any]:
        resolved = self._resolve_capture_session()
        session_dir = resolved.get("session_dir")
        status = {
            "session_id": resolved.get("session_id"),
            "session_mode": resolved.get("mode"),
            "status": "not found" if resolved.get("error") else "available",
            "ok": resolved.get("error") is None,
            "error": resolved.get("error"),
            "latest_age_s": None,
            "quote_rows": 0,
            "grid_rows": 0,
            "latest_by_asset": {},
        }
        if not isinstance(session_dir, Path):
            return status

        quotes_path = session_dir / "polymarket_quotes.jsonl"
        grid_path = session_dir / "polymarket_grid_signals.jsonl"
        quote_rows = self._read_tail_jsonl(quotes_path, self.args.capture_tail_lines)
        status["quote_rows"] = self._count_jsonl_rows(quotes_path)
        status["grid_rows"] = self._count_jsonl_rows(grid_path)
        latest_by_asset: dict[str, dict[str, Any]] = {}
        latest_ts: Optional[float] = None
        for row in quote_rows:
            market = row.get("polymarket_market") or {}
            asset = market.get("asset")
            if not asset:
                kou_ref = row.get("kou_ref") or {}
                asset = asset_from_symbol(kou_ref.get("symbol"))
            if not asset:
                continue
            captured_ts = safe_float((row.get("session") or {}).get("captured_at_ts"), 3)
            if captured_ts is not None and (latest_ts is None or captured_ts > latest_ts):
                latest_ts = captured_ts
            old = latest_by_asset.get(asset)
            old_ts = safe_float(((old or {}).get("session") or {}).get("captured_at_ts"), 3)
            if old is None or (captured_ts is not None and (old_ts is None or captured_ts >= old_ts)):
                latest_by_asset[str(asset)] = row
        status["latest_by_asset"] = latest_by_asset
        if latest_ts is not None:
            status["latest_age_s"] = safe_float(max(0.0, now_ts - latest_ts), 3)
            if status["latest_age_s"] is not None and status["latest_age_s"] <= 3.0:
                status["status"] = "capturing"
            elif status["latest_age_s"] is not None:
                status["status"] = "stale"
                status["ok"] = False
        elif session_dir.exists():
            status["status"] = "waiting for polymarket quotes"
            status["ok"] = False
        return status

    def _asset_snapshot_map(self, payload: Optional[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        if not payload:
            return {}
        return {asset: snapshot for asset, snapshot in selected_snapshot_assets(payload, None)}

    def _fetch_kou_snapshot(self) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        if self.args.skip_kou:
            return None, None
        try:
            payload = _http_get_json_url(
                self.args.url,
                headers={"Cache-Control": "no-store"},
                timeout=self.args.kou_timeout_seconds,
            )
            return payload if isinstance(payload, dict) else {}, None
        except urllib.error.URLError as exc:
            return None, str(exc)
        except Exception as exc:
            return None, str(exc)

    def _asset_report(
        self,
        *,
        asset: str,
        now_ts: float,
        kou_snapshot: Optional[dict[str, Any]],
        kou_by_asset: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            discovery = self._discover_asset_market(asset, now_ts)
        except Exception as exc:
            return self._asset_error_report(asset=asset, now_ts=now_ts, error=str(exc), kou_snapshot=kou_snapshot, kou_by_asset=kou_by_asset)

        kou_ref = kou_by_asset.get(asset, {})
        kou_bucket_end = kou_ref.get("bucket_end")
        if kou_bucket_end is None and kou_snapshot is not None:
            kou_bucket_end = kou_snapshot.get("bucket_end")

        market_payload = build_market_payload(
            asset=asset,
            market=discovery.current,
            next_market=discovery.next_market,
            kou_bucket_end=kou_bucket_end,
            now_ts=now_ts,
        )
        market_payload["url"] = _market_url(market_payload.get("slug"))

        book_payload = None
        book_error = None
        if not self.args.no_books:
            if discovery.current is not None and market_status(discovery.current, now_ts) == "LIVE":
                try:
                    book_payload = _public_fetch_market_quotes(
                        discovery.current.token_yes,
                        discovery.current.token_no,
                        timeout=self.args.clob_timeout_seconds,
                    )
                except Exception as exc:
                    book_error = str(exc)
        capture_latest_by_asset = self._capture_status_cache.get("latest_by_asset", {}) if hasattr(self, "_capture_status_cache") else {}
        captured_quote = capture_latest_by_asset.get(asset) if isinstance(capture_latest_by_asset, dict) else None
        captured_slug = None
        if captured_quote:
            captured_slug = ((captured_quote.get("polymarket_market") or {}).get("slug"))

        return {
            "asset": asset,
            "discovery": {
                "list_count": discovery.list_count,
                "probe_count": discovery.probe_count,
                "used_slug_probe": discovery.used_slug_probe,
                "deep_probe": self.args.deep_probe,
            },
            "kou": {
                "symbol": kou_ref.get("symbol"),
                "bucket_end": safe_float(kou_bucket_end, 3),
                "bucket_end_iso": None if kou_bucket_end is None else utc_iso(float(kou_bucket_end)),
                "time_left_s": kou_ref.get("time_left_s", None if kou_snapshot is None else kou_snapshot.get("time_left_s")),
                "price": kou_ref.get("price"),
                "strike": kou_ref.get("strike"),
                "signal": kou_ref.get("signal"),
            },
            "current_market": market_payload,
            "next_market": None
            if discovery.next_market is None
            else {
                "slug": discovery.next_market.slug,
                "url": _market_url(discovery.next_market.slug),
                "start_ts": safe_float(discovery.next_market.start_ts, 3),
                "start_iso": utc_iso(discovery.next_market.start_ts),
                "end_ts": safe_float(discovery.next_market.end_ts, 3),
                "end_iso": utc_iso(discovery.next_market.end_ts),
            },
            "book": book_payload,
            "book_error": book_error,
            "capture": {
                "current_slug": captured_slug,
                "slug_matches_current": None
                if not captured_slug or not market_payload.get("slug")
                else captured_slug == market_payload.get("slug"),
                "latest_quote": captured_quote,
                "error": None if captured_quote else "no captured quote for this asset",
            },
        }

    def _discover_asset_market(self, asset: str, now_ts: float) -> DiscoveryResult:
        if self.args.slug_first:
            return discover_slug_first_current_and_next_5m_markets(
                asset,
                now_ts,
                market_limit=self.args.market_limit,
                gamma_timeout=self.args.gamma_timeout_seconds,
                slug_timeout=self.args.slug_probe_timeout_seconds,
                fallback_to_list=True,
                broad_slug_probe=self.args.broad_slug_probe,
            )

        return discover_current_and_next_5m_markets(
            asset,
            now_ts,
            market_limit=self.args.market_limit,
            allow_slug_probe=True,
            force_slug_probe=self.args.deep_probe,
            gamma_timeout=self.args.gamma_timeout_seconds,
            slug_timeout=self.args.slug_probe_timeout_seconds,
            broad_slug_probe=self.args.broad_slug_probe,
        )

    def _asset_error_report(
        self,
        *,
        asset: str,
        now_ts: float,
        error: str,
        kou_snapshot: Optional[dict[str, Any]],
        kou_by_asset: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        kou_ref = kou_by_asset.get(asset, {})
        kou_bucket_end = kou_ref.get("bucket_end")
        if kou_bucket_end is None and kou_snapshot is not None:
            kou_bucket_end = kou_snapshot.get("bucket_end")
        capture_latest_by_asset = self._capture_status_cache.get("latest_by_asset", {}) if hasattr(self, "_capture_status_cache") else {}
        captured_quote = capture_latest_by_asset.get(asset) if isinstance(capture_latest_by_asset, dict) else None
        captured_slug = None
        if captured_quote:
            captured_slug = ((captured_quote.get("polymarket_market") or {}).get("slug"))
        return {
            "asset": asset,
            "discovery": {
                "list_count": None,
                "probe_count": None,
                "used_slug_probe": None,
                "deep_probe": self.args.deep_probe,
                "error": error,
            },
            "kou": {
                "symbol": kou_ref.get("symbol"),
                "bucket_end": safe_float(kou_bucket_end, 3),
                "bucket_end_iso": None if kou_bucket_end is None else utc_iso(float(kou_bucket_end)),
                "time_left_s": kou_ref.get("time_left_s", None if kou_snapshot is None else kou_snapshot.get("time_left_s")),
                "price": kou_ref.get("price"),
                "strike": kou_ref.get("strike"),
                "signal": kou_ref.get("signal"),
            },
            "current_market": {
                "asset": asset,
                "status": "ERROR",
                "slug": None,
                "question": error,
                "start_ts": None,
                "start_iso": None,
                "end_ts": None,
                "end_iso": None,
                "token_yes": None,
                "token_no": None,
                "yes_label": None,
                "no_label": None,
                "accepting_orders": False,
                "active": False,
                "closed": False,
                "liquidity": None,
                "next_slug": None,
                "kou_market_end_delta_s": None,
                "alignment_status": "unknown",
                "url": None,
            },
            "next_market": None,
            "book": None,
            "book_error": error,
            "capture": {
                "current_slug": captured_slug,
                "slug_matches_current": None,
                "latest_quote": captured_quote,
                "error": None if captured_quote else error,
            },
        }

    def status_payload(self) -> dict[str, Any]:
        now_ts = time.time()
        with self._status_lock:
            cache_age = now_ts - self._status_cache_ts
            if self._status_cache is not None and cache_age < max(0.5, float(self.args.data_refresh_seconds)):
                return self._status_cache

        kou_snapshot, kou_error = self._fetch_kou_snapshot()
        kou_by_asset = self._asset_snapshot_map(kou_snapshot)
        capture_status = self._capture_status(now_ts)
        self._capture_status_cache = capture_status
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(self.assets)))
        try:
            futures = {
                asset: pool.submit(
                    self._asset_report,
                    asset=asset,
                    now_ts=now_ts,
                    kou_snapshot=kou_snapshot,
                    kou_by_asset=kou_by_asset,
                )
                for asset in self.assets
            }
            assets = []
            deadline = time.monotonic() + max(0.2, float(self.args.asset_timeout_seconds))
            for asset, future in futures.items():
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    report = future.result(timeout=remaining)
                    assets.append(self._cache_or_reuse_asset_report(asset, report, None, now_ts))
                except concurrent.futures.TimeoutError:
                    future.cancel()
                    error = f"dashboard lookup timed out after {self.args.asset_timeout_seconds:.1f}s"
                    assets.append(
                        self._cache_or_reuse_asset_report(
                            asset,
                            self._asset_error_report(
                                asset=asset,
                                now_ts=now_ts,
                                error=error,
                                kou_snapshot=kou_snapshot,
                                kou_by_asset=kou_by_asset,
                            ),
                            error,
                            now_ts,
                        )
                    )
                except Exception as exc:
                    assets.append(
                        self._cache_or_reuse_asset_report(
                            asset,
                            self._asset_error_report(
                                asset=asset,
                                now_ts=now_ts,
                                error=str(exc),
                                kou_snapshot=kou_snapshot,
                                kou_by_asset=kou_by_asset,
                            ),
                            str(exc),
                            now_ts,
                        )
                    )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        payload = {
            "now_ts": safe_float(now_ts, 3),
            "now_iso": utc_iso(now_ts),
            "kou_snapshot_seen": kou_snapshot is not None,
            "kou_error": kou_error,
            "with_books": not self.args.no_books,
            "data_refresh_seconds": self.args.data_refresh_seconds,
            "capture": {
                key: value
                for key, value in capture_status.items()
                if key != "latest_by_asset"
            },
            "assets": assets,
        }
        with self._status_lock:
            self._status_cache = payload
            self._status_cache_ts = now_ts
        return payload

    def _cache_or_reuse_asset_report(
        self,
        asset: str,
        report: dict[str, Any],
        error: Optional[str],
        now_ts: float,
    ) -> dict[str, Any]:
        market = report.get("current_market") or {}
        if market.get("status") != "ERROR" and market.get("slug"):
            self._asset_report_cache[asset] = report
            return report

        cached = self._asset_report_cache.get(asset)
        cached_market = (cached or {}).get("current_market") or {}
        cached_end_ts = safe_float(cached_market.get("end_ts"), 3)
        if cached is None or cached_end_ts is None or now_ts > float(cached_end_ts) + 10.0:
            return report

        reused = json.loads(json.dumps(cached))
        reused["book_error"] = f"using cached market during lookup error: {error or market.get('question') or 'unknown error'}"
        reused.setdefault("discovery", {})["stale_cache"] = True
        reused.setdefault("capture", {}).setdefault("error", "cached market; capture state may lag")
        return reused


def _market_url(slug: Optional[str]) -> Optional[str]:
    if not slug:
        return None
    return f"https://polymarket.com/event/{slug}"


def _extract_price_value(payload: Any) -> Optional[float]:
    if isinstance(payload, dict):
        for key in ("price", "mid", "midpoint", "last_trade_price", "last"):
            value = payload.get(key)
            parsed = safe_float(value, 6)
            if parsed is not None:
                return parsed
        for value in payload.values():
            parsed = safe_float(value, 6)
            if parsed is not None:
                return parsed
        return None
    return safe_float(payload, 6)


def _http_get_json_url(url: str, *, headers: Optional[dict[str, str]] = None, timeout: float) -> Any:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def _public_get_json(path: str, params: Optional[dict[str, Any]] = None, *, timeout: float) -> Any:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    req = urllib.request.Request(f"{CLOB_HOST}{path}{query}", headers=BROWSER_HEADERS)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def _public_price_call(
    path: str,
    params: dict[str, Any],
    *,
    timeout: float,
    attempts: int = 2,
    retry_delay_s: float = 0.05,
) -> Optional[float]:
    for attempt in range(max(1, int(attempts))):
        try:
            payload = _public_get_json(path, params, timeout=timeout)
            return _extract_price_value(payload)
        except Exception:
            if attempt + 1 >= max(1, int(attempts)):
                return None
            time.sleep(max(0.0, float(retry_delay_s)))
    return None


def _public_fetch_market_quotes(token_yes: str, token_no: str, *, timeout: float) -> dict[str, Any]:
    def _empty_prices() -> dict[str, Optional[float]]:
        return {"buy_price": None}

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=10)
    try:
        futures = {
            pool.submit(_public_fetch_book_top, token_yes, timeout=timeout): "yes_top",
            pool.submit(_public_fetch_book_top, token_no, timeout=timeout): "no_top",
            pool.submit(_public_price_call, "/price", {"token_id": token_yes, "side": "BUY"}, timeout=timeout): "yes_buy",
            pool.submit(_public_price_call, "/price", {"token_id": token_no, "side": "BUY"}, timeout=timeout): "no_buy",
        }
        yes_top = None
        no_top = None
        yes_prices = _empty_prices()
        no_prices = _empty_prices()
        done_iter = concurrent.futures.as_completed(futures, timeout=max(0.1, timeout))
        try:
            for future in done_iter:
                name = futures[future]
                try:
                    value = future.result()
                except Exception:
                    value = None
                if name == "yes_top":
                    yes_top = value
                elif name == "no_top":
                    no_top = value
                elif name == "yes_buy":
                    yes_prices["buy_price"] = value
                elif name == "no_buy":
                    no_prices["buy_price"] = value
        except concurrent.futures.TimeoutError:
            pass
        for future in futures:
            try:
                future.cancel()
            except Exception:
                pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    payload = build_book_payload(yes_top, no_top)
    payload["yes_extra"] = yes_prices
    payload["no_extra"] = no_prices
    return payload


def _order_price_size(order: Any) -> tuple[float, float]:
    if isinstance(order, dict):
        return (
            float(safe_float(order.get("price"), 12) or 0.0),
            float(safe_float(order.get("size"), 12) or 0.0),
        )
    return (
        float(safe_float(getattr(order, "price", None), 12) or 0.0),
        float(safe_float(getattr(order, "size", None), 12) or 0.0),
    )


def _public_fetch_book_top(
    token_id: str,
    *,
    timeout: float,
    attempts: int = 2,
    retry_delay_s: float = 0.05,
) -> Optional[BookTop]:
    for attempt in range(max(1, int(attempts))):
        try:
            payload = _public_get_json("/book", {"token_id": token_id}, timeout=timeout)
            break
        except Exception:
            if attempt + 1 >= max(1, int(attempts)):
                return None
            time.sleep(max(0.0, float(retry_delay_s)))

    bids = payload.get("bids") if isinstance(payload, dict) else None
    asks = payload.get("asks") if isinstance(payload, dict) else None
    if not isinstance(asks, list):
        return None
    valid_bids = []
    for order in (bids if isinstance(bids, list) else []):
        price, size = _order_price_size(order)
        if price > 0.0:
            valid_bids.append((price, size))
    valid_asks = []
    for order in asks:
        price, size = _order_price_size(order)
        if price > 0.0:
            valid_asks.append((price, size))
    if not valid_asks:
        return None
    bid, bid_size = max(valid_bids, key=lambda item: item[0]) if valid_bids else (0.0, 0.0)
    ask, ask_size = min(valid_asks, key=lambda item: item[0])
    if ask <= 0.0:
        return None
    return BookTop(bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size)


def make_handler(state: VerifierState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html"):
                self._send_html(DASHBOARD_HTML)
                return
            if self.path == "/api/status":
                try:
                    payload = state.status_payload()
                except Exception as exc:
                    self._send_json({"error": str(exc), "assets": []})
                    return
                self._send_json(payload)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def _send_html(self, body: str) -> None:
            raw = body.encode("utf-8")
            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                return

        def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            raw = json.dumps(payload, sort_keys=True).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                return

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    return Handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve a local Polymarket live market verifier dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host")
    parser.add_argument("--port", type=int, default=8091, help="HTTP port")
    parser.add_argument("--assets", default="eth,xrp", help="Comma-separated assets, default eth,xrp")
    parser.add_argument("--url", default="http://127.0.0.1:8071/api/snapshot", help="Kou snapshot API URL")
    parser.add_argument("--skip-kou", action="store_true", help="Do not fetch Kou snapshot alignment")
    parser.add_argument("--kou-timeout-seconds", type=float, default=0.6, help="Timeout for the local Kou snapshot API")
    parser.add_argument("--market-limit", type=int, default=500, help="Gamma markets list limit")
    parser.add_argument("--gamma-timeout-seconds", type=float, default=0.8, help="Timeout for Gamma market list requests")
    parser.add_argument("--slug-probe-timeout-seconds", type=float, default=6.0, help="Timeout for individual Gamma slug probes")
    parser.add_argument("--no-slug-first", action="store_false", dest="slug_first", help="Disable fast current/next slug lookup before Gamma list discovery")
    parser.add_argument("--deep-probe", action="store_true", help="Always combine Gamma list and slug-probed markets")
    parser.add_argument("--broad-slug-probe", action="store_true", help="Use slower fallback slug patterns when the fast current/next probe misses")
    parser.add_argument("--no-books", action="store_true", help="Disable public CLOB top-of-book quotes")
    parser.add_argument("--clob-timeout-seconds", type=float, default=2.5, help="Timeout for public CLOB quote endpoints")
    parser.add_argument("--asset-timeout-seconds", type=float, default=13.0, help="Hard dashboard budget per refresh for all asset lookups")
    parser.add_argument("--data-refresh-seconds", type=float, default=5.0, help="Backend discovery refresh cadence; browser countdown still repaints every second")
    parser.add_argument("--output-root", default="data/live_capture", help="Live capture root to monitor")
    parser.add_argument("--session-id", default=None, help="Optional exact live capture session id to monitor")
    parser.add_argument("--capture-tail-lines", type=int, default=500, help="Recent quote rows to scan for capture status")
    return parser


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def main() -> int:
    args = build_parser().parse_args()
    state = VerifierState(args)
    server = ReusableThreadingHTTPServer((args.host, args.port), make_handler(state))
    server.daemon_threads = True
    url = f"http://{args.host}:{args.port}/"
    print(f"Polymarket live market verifier: {url}", flush=True)
    print("Read-only. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
