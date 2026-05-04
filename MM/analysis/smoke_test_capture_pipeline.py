#!/usr/bin/env python3
"""
Deterministic smoke test for the Kou + Polymarket capture pipeline.

The test starts a mock Kou snapshot HTTP server, runs both capture sidecars
against it, and uses `--mock-polymarket` so no live Polymarket network calls or
orders are needed. It verifies that signal data flows into both capture outputs,
that observed token prices are stored, and that grid-trigger rows are emitted.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def utc_iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class MockSnapshotState:
    def __init__(self) -> None:
        self.started_at = time.time()

    def payload(self) -> dict[str, Any]:
        now_ts = time.time()
        bucket_seconds = 300
        slot_start = int(now_ts // bucket_seconds) * bucket_seconds
        bucket_end = float(slot_start + bucket_seconds - 1)
        time_left = max(0.0, bucket_end - now_ts)
        assets = []
        for symbol, price, strike, kou_yes, signal in [
            ("ethusdt", 3100.0, 3000.0, 0.94, "BUY_YES"),
            ("xrpusdt", 0.53, 0.55, 0.05, "BUY_NO"),
        ]:
            assets.append(
                {
                    "symbol": symbol,
                    "name": symbol.upper(),
                    "state": "LIVE",
                    "display_source": "mock",
                    "model_source": "mock",
                    "age_s": 0.1,
                    "model_age_s": 0.1,
                    "model": "KOU",
                    "kou_phase": "full",
                    "sample_count": 999,
                    "price": price,
                    "model_price": price,
                    "strike": strike,
                    "delta_bps": round((price - strike) / strike * 10000.0, 1),
                    "time_left_s": round(time_left, 1),
                    "kou_yes": kou_yes,
                    "raw_kou_yes": kou_yes,
                    "bs_yes": 0.7 if signal == "BUY_YES" else 0.3,
                    "kou_weight": 1.0,
                    "edge_pp": 1.0,
                    "signal": signal,
                    "signal_hold_s": max(0.0, now_ts - self.started_at),
                    "trade_score": 88,
                    "trade_score_label": "GOOD",
                    "trade_score_reason": "mock",
                    "base_trade_score": 88,
                    "base_trade_score_label": "GOOD",
                    "base_trade_score_reason": "mock",
                    "late_policy_level": "CLEAR",
                    "late_policy_margin_z": 2.0,
                    "policy_override": False,
                    "safety_components": {"mock": 1.0},
                    "vol_30m_bp_1m": 5.0,
                    "vol_1h_bp_1m": 5.0,
                    "jump_10s_10m_rate": 0.0,
                    "jump_10s_10m_count": 0,
                    "jump_30s_15m_rate": 0.0,
                    "jump_30s_15m_count": 0,
                    "jump_sweep_10s_10m": {},
                    "jump_sweep_30s_15m": {},
                    "synthetic_count": 0,
                    "footer": "mock",
                }
            )
        return {
            "title": "mock",
            "bucket_seconds": bucket_seconds,
            "bucket_end": bucket_end,
            "time_left_s": round(time_left, 1),
            "progress": round(time_left / bucket_seconds, 4),
            "refresh_seconds": 1.0,
            "assets": assets,
            "updated_at": int(now_ts),
        }


def make_handler(state: MockSnapshotState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/api/snapshot":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            raw = json.dumps(state.payload()).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    return Handler


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def run_command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd), text=True, capture_output=True, timeout=30)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test the live capture pipeline")
    parser.add_argument("--keep-output", action="store_true", help="Keep temporary output directory after the run")
    parser.add_argument("--runtime-seconds", type=float, default=4.5, help="Capture runtime for both sidecars")
    parser.add_argument("--port", type=int, default=8137, help="Mock snapshot server port")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = Path(__file__).resolve().parents[1]
    state = MockSnapshotState()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    tmp_ctx = tempfile.TemporaryDirectory(prefix="kou_pipeline_smoke_")
    output_root = Path(tmp_ctx.name)
    session_id = "pipeline-smoke"
    snapshot_url = f"http://127.0.0.1:{args.port}/api/snapshot"

    try:
        live_cmd = [
            sys.executable,
            "kou_live_capture.py",
            "--url",
            snapshot_url,
            "--output-root",
            str(output_root),
            "--session-id",
            session_id,
            "--fine-window-seconds",
            "999",
            "--fine-seconds",
            "1",
            "--coarse-seconds",
            "1",
            "--max-runtime-seconds",
            str(args.runtime_seconds),
        ]
        poly_cmd = [
            sys.executable,
            "kou_polymarket_live_capture.py",
            "--url",
            snapshot_url,
            "--output-root",
            str(output_root),
            "--session-id",
            session_id,
            "--mock-polymarket",
            "--fine-window-seconds",
            "999",
            "--fine-seconds",
            "1",
            "--coarse-seconds",
            "1",
            "--discover-seconds",
            "1",
            "--grid-thresholds",
            "0.90,0.94",
            "--grid-hold-seconds",
            "2",
            "--grid-window-seconds",
            "999",
            "--max-runtime-seconds",
            str(args.runtime_seconds),
        ]

        live_proc = subprocess.Popen(live_cmd, cwd=str(repo), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(0.25)
        poly_proc = subprocess.Popen(poly_cmd, cwd=str(repo), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        live_out, live_err = live_proc.communicate(timeout=30)
        poly_out, poly_err = poly_proc.communicate(timeout=30)
        assert_true(live_proc.returncode == 0, f"kou_live_capture failed: {live_err or live_out}")
        assert_true(poly_proc.returncode == 0, f"kou_polymarket_live_capture failed: {poly_err or poly_out}")

        session_dir = output_root / session_id
        snapshots = read_jsonl(session_dir / "snapshots.jsonl")
        poly_quotes = read_jsonl(session_dir / "polymarket_quotes.jsonl")
        grid = read_jsonl(session_dir / "polymarket_grid_signals.jsonl")
        outcomes = read_jsonl(session_dir / "bucket_outcomes.jsonl")
        assert_true(len(snapshots) >= 4, "expected live snapshots from both assets")
        assert_true(len(poly_quotes) >= 4, "expected Polymarket quote rows from both assets")
        assert_true(len(grid) >= 2, "expected grid trigger rows")
        assert_true(len(outcomes) >= 1, "expected bucket outcome rows")

        quote = poly_quotes[0]
        assert_true(quote["kou_ref"]["signal"] in {"BUY_YES", "BUY_NO"}, "Kou signal did not reach Polymarket quote rows")
        assert_true(quote["token_prices"]["yes"]["buy_price"] is not None, "YES buy price missing")
        assert_true(quote["token_prices"]["no"]["buy_price"] is not None, "NO buy price missing")
        assert_true("bid" not in quote["book"]["yes"], "compact book still contains YES bid")
        assert_true("quote_fetch" in quote, "quote fetch timing missing")
        assert_true("safety_components" not in quote["kou_ref"], "compact Kou ref still contains safety_components")

        grid_event = grid[0]
        assert_true(grid_event["observed_token"]["entry_price"] is not None, "grid entry price missing")
        assert_true("kou_ref" not in grid_event, "grid event still contains repeated kou_ref blob")

        analyzer_cmd = [
            sys.executable,
            "analysis/analyze_polymarket_grid_signals.py",
            "--input-root",
            str(output_root),
            "--session-id",
            session_id,
            "--output-dir",
            str(output_root / "analysis"),
        ]
        analyzer = run_command(analyzer_cmd, repo)
        assert_true(analyzer.returncode == 0, f"grid analyzer failed: {analyzer.stderr or analyzer.stdout}")
        assert_true((output_root / "analysis" / "polymarket_grid_matrix.csv").exists(), "matrix CSV missing")
        assert_true(
            (output_root / "analysis" / "polymarket_grid_matrix_by_timeleft.csv").exists(),
            "time-left matrix CSV missing",
        )
        assert_true(
            (output_root / "analysis" / "polymarket_grid_matrix_pivot.csv").exists(),
            "pivot matrix CSV missing",
        )

        health_cmd = [
            sys.executable,
            "analysis/view_capture_health.py",
            "--input-root",
            str(output_root),
            "--session-id",
            session_id,
            "--max-quote-age-s",
            "999",
            "--max-snapshot-age-s",
            "999",
        ]
        health = run_command(health_cmd, repo)
        assert_true(health.returncode == 0, f"health view failed: {health.stderr or health.stdout}")

        print(json.dumps(
            {
                "ok": True,
                "output_root": str(output_root),
                "session_id": session_id,
                "snapshots": len(snapshots),
                "polymarket_quotes": len(poly_quotes),
                "grid_signals": len(grid),
                "bucket_outcomes": len(outcomes),
                "first_signal": quote["kou_ref"]["signal"],
                "first_yes_buy_price": quote["token_prices"]["yes"]["buy_price"],
            },
            indent=2,
            sort_keys=True,
        ))
        return 0
    finally:
        server.shutdown()
        server.server_close()
        if args.keep_output:
            print(f"Kept output root: {output_root}")
        else:
            tmp_ctx.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
