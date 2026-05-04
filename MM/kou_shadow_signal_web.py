#!/usr/bin/env python3
"""
Tiny web view for the current Kou / shadow-candidate signal stack.

Run the normal engine first:
    python kou_dual_compact_web.py

For the exact candidate-approved paper buys used in validation captures, also
run the Polymarket sidecar with --shadow-candidate. This page reads the live
Kou snapshot and tails the latest shadow order ledger; it never places orders.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional


DEFAULT_SNAPSHOT_URL = "http://127.0.0.1:8071/api/snapshot"
DEFAULT_CAPTURE_ROOT = "data/live_capture"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Small localhost view for Kou safety-gated and shadow buy signals")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8072)
    parser.add_argument("--snapshot-url", default=DEFAULT_SNAPSHOT_URL)
    parser.add_argument("--capture-root", default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--session-id", default=None, help="Optional capture session id to tail")
    return parser.parse_args()


def utc_iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(ts)))


def safe_float(value: Any, digits: int = 6) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return float(f"{number:.{digits}f}")


def fetch_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
    with urllib.request.urlopen(req, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def read_last_jsonl(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    last: Optional[dict[str, Any]] = None
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    last = row
    except Exception:
        return None
    return last


def newest_session_dir(capture_root: Path, session_id: Optional[str]) -> Optional[Path]:
    if session_id:
        path = capture_root / session_id
        return path if path.exists() else None
    if not capture_root.exists():
        return None
    candidates = [path for path in capture_root.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def summarize_asset(asset: dict[str, Any]) -> dict[str, Any]:
    signal = asset.get("signal")
    safety_label = asset.get("trade_score_label")
    safety_score = safe_float(asset.get("trade_score"), 1)
    gated = signal in {"BUY_YES", "BUY_NO"} and safety_label == "GOOD"
    side = "yes" if signal == "BUY_YES" else "no" if signal == "BUY_NO" else None
    return {
        "symbol": asset.get("symbol"),
        "name": asset.get("name"),
        "state": asset.get("state"),
        "price": asset.get("price"),
        "strike": asset.get("strike"),
        "time_left_s": asset.get("time_left_s"),
        "kou_yes": asset.get("kou_yes"),
        "bs_yes": asset.get("bs_yes"),
        "signal": signal,
        "signal_side": side,
        "signal_hold_s": asset.get("signal_hold_s"),
        "safety_label": safety_label,
        "safety_score": safety_score,
        "safety_reason": asset.get("trade_score_reason"),
        "policy_level": asset.get("late_policy_level"),
        "policy_margin_z": asset.get("late_policy_margin_z"),
        "policy_override": bool(asset.get("policy_override")),
        "kou_phase": asset.get("kou_phase"),
        "sample_count": asset.get("sample_count"),
        "safety_gated_buy": gated,
    }


def summarize_shadow_order(order: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not order:
        return None
    source = order.get("source_grid_event") or {}
    payload = order.get("order") or {}
    candidate = order.get("candidate") or {}
    now_ts = time.time()
    ts = safe_float(order.get("ts"), 3)
    bucket_end = safe_float(source.get("bucket_end"), 3)
    live_now = bool(ts is not None and now_ts - ts <= 180.0 and (bucket_end is None or now_ts <= bucket_end + 10.0))
    return {
        "live_now": live_now,
        "age_s": None if ts is None else safe_float(now_ts - ts, 1),
        "iso_utc": order.get("iso_utc"),
        "candidate_name": (candidate.get("name") or ""),
        "side": payload.get("side"),
        "entry_price": payload.get("entry_price"),
        "fill_status": payload.get("fill_status"),
        "fillable_size": payload.get("hypothetical_fill_size"),
        "asset": source.get("asset"),
        "symbol": source.get("symbol"),
        "time_left_s": source.get("time_left_s"),
        "threshold": source.get("threshold"),
        "hold_seconds": source.get("hold_seconds"),
        "side_probability": source.get("side_probability"),
    }


class StatusSource:
    def __init__(self, snapshot_url: str, capture_root: Path, session_id: Optional[str]) -> None:
        self.snapshot_url = snapshot_url
        self.capture_root = capture_root
        self.session_id = session_id

    def status(self) -> dict[str, Any]:
        now_ts = time.time()
        errors: list[str] = []
        try:
            snapshot = fetch_json(self.snapshot_url)
        except Exception as exc:
            snapshot = {}
            errors.append(f"snapshot: {exc}")

        assets = [summarize_asset(asset) for asset in snapshot.get("assets", []) if isinstance(asset, dict)]
        xrp = next((asset for asset in assets if str(asset.get("symbol") or "").lower() == "xrpusdt"), None)
        safety_gated = [asset for asset in assets if asset.get("safety_gated_buy")]

        session_dir = newest_session_dir(self.capture_root, self.session_id)
        meta = read_json(session_dir / "session_meta.json") if session_dir else {}
        shadow = summarize_shadow_order(read_last_jsonl(session_dir / "shadow_orders.jsonl") if session_dir else None)

        action = "WAIT"
        action_reason = "no safety-gated signal"
        if shadow and shadow.get("live_now"):
            side = str(shadow.get("side") or "").upper()
            action = f"SHADOW BUY {side}"
            action_reason = "candidate-approved paper order emitted by Polymarket sidecar"
        elif safety_gated:
            side = str(safety_gated[0].get("signal_side") or "").upper()
            action = f"KOU SAFETY BUY {side}"
            action_reason = "Kou signal is armed and dashboard safety is GOOD"

        return {
            "now_ts": safe_float(now_ts, 3),
            "now_iso": utc_iso(now_ts),
            "snapshot_url": self.snapshot_url,
            "session_id": None if session_dir is None else session_dir.name,
            "capture_profile": meta.get("capture_profile"),
            "shadow_candidate": (meta.get("shadow_execution") or {}).get("candidate_name"),
            "action": action,
            "action_reason": action_reason,
            "xrp": xrp,
            "assets": assets,
            "shadow_order": shadow,
            "errors": errors,
        }


HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Kou Shadow Signal</title>
  <style>
    :root { color-scheme: dark; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; background: #101318; color: #eef2f7; }
    main { max-width: 780px; margin: 0 auto; padding: 24px; }
    .hero { border: 1px solid #29313d; background: #171c24; border-radius: 8px; padding: 20px; }
    .action { font-size: 42px; line-height: 1; font-weight: 800; margin: 10px 0; }
    .wait { color: #a9b4c2; }
    .buy { color: #43f2a0; }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 10px; margin-top: 14px; }
    .metric { border: 1px solid #29313d; border-radius: 8px; padding: 12px; background: #121720; min-height: 46px; }
    .label { color: #8996a8; font-size: 12px; text-transform: uppercase; }
    .value { font-size: 18px; font-weight: 700; margin-top: 4px; overflow-wrap: anywhere; }
    .small { color: #a9b4c2; font-size: 13px; margin-top: 8px; }
    .bad { color: #ff7676; }
    @media (max-width: 620px) { .grid { grid-template-columns: 1fr; } .action { font-size: 34px; } }
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <div class="label">Current actionable state</div>
      <div id="action" class="action wait">Loading</div>
      <div id="reason" class="small">Connecting...</div>
      <div class="grid">
        <div class="metric"><div class="label">XRP signal</div><div id="signal" class="value">-</div></div>
        <div class="metric"><div class="label">Safety</div><div id="safety" class="value">-</div></div>
        <div class="metric"><div class="label">Kou yes</div><div id="kou" class="value">-</div></div>
        <div class="metric"><div class="label">Time left</div><div id="timeleft" class="value">-</div></div>
        <div class="metric"><div class="label">Late policy</div><div id="policy" class="value">-</div></div>
        <div class="metric"><div class="label">Latest shadow</div><div id="shadow" class="value">-</div></div>
      </div>
      <div id="meta" class="small"></div>
      <div id="errors" class="small bad"></div>
    </section>
  </main>
  <script>
    function fmt(v, d=2) {
      return v === null || v === undefined ? '-' : Number(v).toFixed(d);
    }
    async function refresh() {
      let data;
      try {
        const res = await fetch('/api/status', {cache: 'no-store'});
        data = await res.json();
      } catch (err) {
        document.getElementById('errors').textContent = String(err);
        return;
      }
      const action = document.getElementById('action');
      action.textContent = data.action || 'WAIT';
      action.className = 'action ' + ((data.action || '').includes('BUY') ? 'buy' : 'wait');
      document.getElementById('reason').textContent = data.action_reason || '';
      const x = data.xrp || {};
      document.getElementById('signal').textContent = (x.signal || 'WAIT') + (x.signal_hold_s == null ? '' : ' · ' + fmt(x.signal_hold_s, 1) + 's');
      document.getElementById('safety').textContent = (x.safety_label || '-') + (x.safety_score == null ? '' : ' · ' + fmt(x.safety_score, 0)) + (x.safety_reason ? ' · ' + x.safety_reason : '');
      document.getElementById('kou').textContent = x.kou_yes == null ? '-' : fmt(100 * x.kou_yes, 2) + '%';
      document.getElementById('timeleft').textContent = x.time_left_s == null ? '-' : fmt(x.time_left_s, 1) + 's';
      document.getElementById('policy').textContent = (x.policy_level || '-') + (x.policy_margin_z == null ? '' : ' · z ' + fmt(x.policy_margin_z, 2));
      const s = data.shadow_order || {};
      document.getElementById('shadow').textContent = s.side ? (s.live_now ? 'LIVE ' : '') + String(s.side).toUpperCase() + ' @ ' + fmt(s.entry_price, 2) + ' · ' + (s.candidate_name || 'candidate') : '-';
      document.getElementById('meta').textContent = 'session ' + (data.session_id || '-') + ' · shadow candidate ' + (data.shadow_candidate || '-') + ' · updated ' + (data.now_iso || '-');
      document.getElementById('errors').textContent = (data.errors || []).join(' | ');
    }
    refresh();
    setInterval(refresh, 1000);
  </script>
</body>
</html>
"""


def make_handler(source: StatusSource) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/status":
                body = json.dumps(source.status(), sort_keys=True).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path in {"/", "/index.html"}:
                body = HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            self.send_error(HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def main() -> int:
    args = parse_args()
    source = StatusSource(
        snapshot_url=str(args.snapshot_url),
        capture_root=Path(args.capture_root),
        session_id=args.session_id,
    )
    server = ThreadingHTTPServer((args.host, int(args.port)), make_handler(source))
    print(f"Kou shadow signal web view: http://{args.host}:{args.port}")
    print("This is read-only. It shows live snapshot state and tails shadow_orders.jsonl if present.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
