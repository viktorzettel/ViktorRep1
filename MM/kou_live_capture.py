#!/usr/bin/env python3
"""
Sidecar telemetry logger for kou_dual_compact_web.py.

This polls the local dashboard snapshot API and builds a structured live dataset
for later calibration and policy analysis.

Cadence:
- every 5 seconds by default
- every 1 second in the last 120 seconds of a bucket

Outputs per session:
- session_meta.json
- snapshots.jsonl
- events.jsonl
- bucket_outcomes.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture live telemetry from kou_dual_compact_web.py")
    parser.add_argument("--url", default="http://127.0.0.1:8071/api/snapshot", help="Snapshot API URL")
    parser.add_argument(
        "--output-root",
        default="data/live_capture",
        help="Directory where session folders will be created",
    )
    parser.add_argument("--session-id", default=None, help="Optional explicit session id")
    parser.add_argument(
        "--fine-window-seconds",
        type=float,
        default=120.0,
        help="Capture every fine-seconds once time_left_s is at or below this threshold",
    )
    parser.add_argument("--fine-seconds", type=float, default=1.0, help="Fine capture cadence")
    parser.add_argument("--coarse-seconds", type=float, default=5.0, help="Coarse capture cadence")
    parser.add_argument(
        "--max-runtime-seconds",
        type=float,
        default=None,
        help="Optional hard stop for unattended runs",
    )
    parser.add_argument(
        "--validation-profile",
        action="store_true",
        help="Use the lean out-of-sample validation cadence for candidate testing",
    )
    return parser.parse_args()


def apply_validation_profile(args: argparse.Namespace) -> argparse.Namespace:
    if not getattr(args, "validation_profile", False):
        args.capture_profile = "full"
        return args
    args.capture_profile = "candidate_validation"
    args.fine_window_seconds = 120.0
    args.fine_seconds = 1.0
    args.coarse_seconds = 5.0
    return args


def utc_iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def safe_float(value: Any, digits: int = 6) -> Optional[float]:
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return float(f"{num:.{digits}f}")


def sign_of_delta(delta_bps: Optional[float]) -> int:
    if delta_bps is None:
        return 0
    if delta_bps > 0.0:
        return 1
    if delta_bps < 0.0:
        return -1
    return 0


def fetch_snapshot(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(handle, payload: dict[str, Any]) -> None:
    handle.write(json.dumps(payload, sort_keys=True) + "\n")
    handle.flush()


def git_revision(cwd: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    value = result.stdout.strip()
    return value or None


def choose_session_id(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def extract_capture_interval(payload: dict[str, Any], fine_window_s: float, fine_s: float, coarse_s: float) -> float:
    time_left = payload.get("time_left_s")
    try:
        value = float(time_left)
    except (TypeError, ValueError):
        value = None
    if value is not None and value <= fine_window_s:
        return fine_s
    return coarse_s


def align_sleep(interval_s: float) -> float:
    now = time.time()
    if interval_s <= 0.0:
        return 0.25
    return max(0.05, (math.floor(now / interval_s) + 1) * interval_s - now)


def normalize_snapshot_record(
    *,
    session_id: str,
    payload: dict[str, Any],
    asset: dict[str, Any],
    captured_at_ts: float,
    capture_interval_s: float,
) -> dict[str, Any]:
    return {
        "session": {
            "id": session_id,
            "captured_at_ts": safe_float(captured_at_ts, 3),
            "captured_at_iso": utc_iso(captured_at_ts),
            "capture_interval_s": safe_float(capture_interval_s, 3),
        },
        "bucket": {
            "bucket_seconds": payload.get("bucket_seconds"),
            "bucket_end": payload.get("bucket_end"),
            "bucket_end_iso": None if payload.get("bucket_end") is None else utc_iso(float(payload["bucket_end"])),
            "time_left_s": asset.get("time_left_s", payload.get("time_left_s")),
            "progress": payload.get("progress"),
        },
        "asset": {
            "symbol": asset.get("symbol"),
            "name": asset.get("name"),
            "state": asset.get("state"),
            "display_source": asset.get("display_source"),
            "model_source": asset.get("model_source"),
            "age_s": asset.get("age_s"),
            "model_age_s": asset.get("model_age_s"),
        },
        "market": {
            "price": asset.get("price"),
            "model_price": asset.get("model_price"),
            "strike": asset.get("strike"),
            "delta_bps": asset.get("delta_bps"),
            "current_side": "yes" if (asset.get("delta_bps") or 0) > 0 else ("no" if (asset.get("delta_bps") or 0) < 0 else "flat"),
        },
        "model": {
            "model": asset.get("model"),
            "kou_phase": asset.get("kou_phase"),
            "sample_count": asset.get("sample_count"),
            "kou_yes": asset.get("kou_yes"),
            "raw_kou_yes": asset.get("raw_kou_yes"),
            "bs_yes": asset.get("bs_yes"),
            "kou_weight": asset.get("kou_weight"),
            "edge_pp": asset.get("edge_pp"),
            "lam": asset.get("lam"),
            "p_up": asset.get("p_up"),
            "sigma_model_bp_1m": asset.get("sigma_model_bp_1m"),
        },
        "signal": {
            "state": asset.get("signal"),
            "hold_s": asset.get("signal_hold_s"),
        },
        "safety": {
            "final_score": asset.get("trade_score"),
            "final_label": asset.get("trade_score_label"),
            "final_reason": asset.get("trade_score_reason"),
            "heuristic_score": asset.get("base_trade_score"),
            "heuristic_label": asset.get("base_trade_score_label"),
            "heuristic_reason": asset.get("base_trade_score_reason"),
            "components": asset.get("safety_components") or {},
        },
        "policy": {
            "level": asset.get("late_policy_level"),
            "reason": asset.get("late_policy_reason"),
            "bucket_s": asset.get("late_policy_bucket_s"),
            "margin_z": asset.get("late_policy_margin_z"),
            "override": bool(asset.get("policy_override")),
        },
        "volatility": {
            "vol_30m_bp_1m": asset.get("vol_30m_bp_1m"),
            "vol_1h_bp_1m": asset.get("vol_1h_bp_1m"),
        },
        "jumps": {
            "jump_10s_10m_rate": asset.get("jump_10s_10m_rate"),
            "jump_10s_10m_count": asset.get("jump_10s_10m_count"),
            "jump_30s_15m_rate": asset.get("jump_30s_15m_rate"),
            "jump_30s_15m_count": asset.get("jump_30s_15m_count"),
            "jump_sweep_10s_10m": asset.get("jump_sweep_10s_10m") or {},
            "jump_sweep_30s_15m": asset.get("jump_sweep_30s_15m") or {},
        },
        "meta": {
            "synthetic_count": asset.get("synthetic_count"),
            "footer": asset.get("footer"),
            "updated_at": payload.get("updated_at"),
        },
    }


def tracked_event_fields(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": record["asset"]["state"],
        "signal_state": record["signal"]["state"],
        "final_label": record["safety"]["final_label"],
        "final_reason": record["safety"]["final_reason"],
        "heuristic_label": record["safety"]["heuristic_label"],
        "policy_level": record["policy"]["level"],
        "policy_override": record["policy"]["override"],
        "bucket_end": record["bucket"]["bucket_end"],
    }


@dataclass
class BucketTracker:
    session_id: str
    symbol: str
    bucket_end: Optional[float]
    bucket_seconds: Optional[int]
    strike: Optional[float]
    sample_count: int = 0
    first_capture_ts: Optional[float] = None
    last_capture_ts: Optional[float] = None
    first_price: Optional[float] = None
    last_price: Optional[float] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    min_delta_bps: Optional[float] = None
    max_delta_bps: Optional[float] = None
    cross_count: int = 0
    prev_side_sign: int = 0
    signal_yes_samples: int = 0
    signal_no_samples: int = 0
    policy_override_samples: int = 0
    hard_no_go_samples: int = 0
    caution_samples: int = 0
    late_policy_levels: set[str] = field(default_factory=set)
    final_labels_seen: set[str] = field(default_factory=set)
    heuristic_labels_seen: set[str] = field(default_factory=set)
    max_kou_yes: Optional[float] = None
    min_kou_yes: Optional[float] = None
    max_trade_score: Optional[float] = None
    min_trade_score: Optional[float] = None

    def update(self, record: dict[str, Any]) -> None:
        self.sample_count += 1
        captured_at = record["session"]["captured_at_ts"]
        price = record["market"]["price"]
        delta_bps = record["market"]["delta_bps"]
        kou_yes = record["model"]["kou_yes"]
        final_score = record["safety"]["final_score"]
        final_label = record["safety"]["final_label"]
        heuristic_label = record["safety"]["heuristic_label"]
        policy_level = record["policy"]["level"]
        signal_state = record["signal"]["state"]

        if self.first_capture_ts is None:
            self.first_capture_ts = captured_at
        self.last_capture_ts = captured_at

        if self.first_price is None:
            self.first_price = price
        self.last_price = price

        if price is not None:
            self.min_price = price if self.min_price is None else min(self.min_price, price)
            self.max_price = price if self.max_price is None else max(self.max_price, price)

        if delta_bps is not None:
            self.min_delta_bps = delta_bps if self.min_delta_bps is None else min(self.min_delta_bps, delta_bps)
            self.max_delta_bps = delta_bps if self.max_delta_bps is None else max(self.max_delta_bps, delta_bps)
            side_sign = sign_of_delta(delta_bps)
            if self.prev_side_sign and side_sign and side_sign != self.prev_side_sign:
                self.cross_count += 1
            if side_sign:
                self.prev_side_sign = side_sign

        if signal_state == "BUY_YES":
            self.signal_yes_samples += 1
        elif signal_state == "BUY_NO":
            self.signal_no_samples += 1

        if record["policy"]["override"]:
            self.policy_override_samples += 1
        if policy_level == "HARD_NO_GO":
            self.hard_no_go_samples += 1
        elif policy_level == "CAUTION":
            self.caution_samples += 1

        if policy_level:
            self.late_policy_levels.add(str(policy_level))
        if final_label:
            self.final_labels_seen.add(str(final_label))
        if heuristic_label:
            self.heuristic_labels_seen.add(str(heuristic_label))

        if kou_yes is not None:
            self.max_kou_yes = kou_yes if self.max_kou_yes is None else max(self.max_kou_yes, kou_yes)
            self.min_kou_yes = kou_yes if self.min_kou_yes is None else min(self.min_kou_yes, kou_yes)

        if final_score is not None:
            self.max_trade_score = final_score if self.max_trade_score is None else max(self.max_trade_score, final_score)
            self.min_trade_score = final_score if self.min_trade_score is None else min(self.min_trade_score, final_score)

    def finalize(self, complete: bool) -> dict[str, Any]:
        settled_side = None
        settled_yes = None
        settled_delta_bps = self.last_delta_bps
        if complete and self.last_price is not None and self.strike is not None:
            if self.last_price > self.strike:
                settled_side = "yes"
                settled_yes = True
            elif self.last_price < self.strike:
                settled_side = "no"
                settled_yes = False
            else:
                settled_side = "flat"
                settled_yes = None

        bucket_start = None
        if self.bucket_end is not None and self.bucket_seconds is not None:
            bucket_start = float(self.bucket_end) - float(self.bucket_seconds)

        return {
            "session_id": self.session_id,
            "symbol": self.symbol,
            "bucket_start": bucket_start,
            "bucket_start_iso": None if bucket_start is None else utc_iso(bucket_start),
            "bucket_end": self.bucket_end,
            "bucket_end_iso": None if self.bucket_end is None else utc_iso(float(self.bucket_end)),
            "bucket_seconds": self.bucket_seconds,
            "complete": complete,
            "first_capture_ts": self.first_capture_ts,
            "first_capture_iso": None if self.first_capture_ts is None else utc_iso(float(self.first_capture_ts)),
            "last_capture_ts": self.last_capture_ts,
            "last_capture_iso": None if self.last_capture_ts is None else utc_iso(float(self.last_capture_ts)),
            "strike": self.strike,
            "first_price": self.first_price,
            "last_price": self.last_price,
            "settled_yes": settled_yes,
            "settled_side": settled_side,
            "settled_delta_bps": settled_delta_bps,
            "sample_count": self.sample_count,
            "sampled_min_price": self.min_price,
            "sampled_max_price": self.max_price,
            "sampled_min_delta_bps": self.min_delta_bps,
            "sampled_max_delta_bps": self.max_delta_bps,
            "sampled_cross_count": self.cross_count,
            "signal_yes_samples": self.signal_yes_samples,
            "signal_no_samples": self.signal_no_samples,
            "policy_override_samples": self.policy_override_samples,
            "hard_no_go_samples": self.hard_no_go_samples,
            "caution_samples": self.caution_samples,
            "late_policy_levels_seen": sorted(self.late_policy_levels),
            "final_labels_seen": sorted(self.final_labels_seen),
            "heuristic_labels_seen": sorted(self.heuristic_labels_seen),
            "max_kou_yes": self.max_kou_yes,
            "min_kou_yes": self.min_kou_yes,
            "max_trade_score": self.max_trade_score,
            "min_trade_score": self.min_trade_score,
        }

    @property
    def last_delta_bps(self) -> Optional[float]:
        return self.max_delta_bps if self.sample_count == 1 else None if self.last_price is None or self.strike is None else safe_float((self.last_price - self.strike) / self.strike * 10000.0, 4)


class LiveCaptureSession:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.session_id = choose_session_id(args.session_id)
        self.output_dir = Path(args.output_root) / self.session_id
        # Allow an explicit session id to attach to a folder already created by
        # the Polymarket sidecar. Auto-generated ids remain collision-checked.
        self.output_dir.mkdir(parents=True, exist_ok=bool(args.session_id))
        self.snapshots_path = self.output_dir / "snapshots.jsonl"
        self.events_path = self.output_dir / "events.jsonl"
        self.bucket_outcomes_path = self.output_dir / "bucket_outcomes.jsonl"
        self.session_meta_path = self.output_dir / "session_meta.json"
        self._running = True
        self.prev_by_symbol: dict[str, dict[str, Any]] = {}
        self.bucket_by_symbol: dict[str, BucketTracker] = {}
        self.started_at_ts = time.time()

    def write_meta(self) -> None:
        payload = {
            "session_id": self.session_id,
            "started_at_ts": safe_float(self.started_at_ts, 3),
            "started_at_iso": utc_iso(self.started_at_ts),
            "url": self.args.url,
            "fine_window_seconds": self.args.fine_window_seconds,
            "fine_seconds": self.args.fine_seconds,
            "coarse_seconds": self.args.coarse_seconds,
            "max_runtime_seconds": self.args.max_runtime_seconds,
            "capture_profile": getattr(self.args, "capture_profile", "full"),
            "validation_profile": bool(getattr(self.args, "validation_profile", False)),
            "cwd": str(Path.cwd()),
            "git_revision": git_revision(Path.cwd()),
            "python": sys.version,
        }
        write_json(self.session_meta_path, payload)

    def request_stop(self, *_args: Any) -> None:
        self._running = False

    def emit_field_events(self, events_handle, record: dict[str, Any], previous: Optional[dict[str, Any]]) -> None:
        current = tracked_event_fields(record)
        if previous is None:
            append_jsonl(
                events_handle,
                {
                    "session_id": self.session_id,
                    "ts": record["session"]["captured_at_ts"],
                    "iso_utc": record["session"]["captured_at_iso"],
                    "symbol": record["asset"]["symbol"],
                    "event_type": "first_seen",
                    "current": current,
                },
            )
            return

        prev = tracked_event_fields(previous)
        for field, current_value in current.items():
            prev_value = prev.get(field)
            if prev_value == current_value:
                continue
            append_jsonl(
                events_handle,
                {
                    "session_id": self.session_id,
                    "ts": record["session"]["captured_at_ts"],
                    "iso_utc": record["session"]["captured_at_iso"],
                    "symbol": record["asset"]["symbol"],
                    "event_type": f"{field}_changed",
                    "field": field,
                    "previous": prev_value,
                    "current": current_value,
                    "time_left_s": record["bucket"]["time_left_s"],
                    "price": record["market"]["price"],
                    "strike": record["market"]["strike"],
                },
            )

    def advance_bucket_tracker(self, bucket_handle, record: dict[str, Any]) -> None:
        symbol = str(record["asset"]["symbol"])
        bucket_end = record["bucket"]["bucket_end"]
        bucket_seconds = record["bucket"]["bucket_seconds"]
        strike = record["market"]["strike"]
        tracker = self.bucket_by_symbol.get(symbol)
        if tracker is None:
            tracker = BucketTracker(
                session_id=self.session_id,
                symbol=symbol,
                bucket_end=bucket_end,
                bucket_seconds=bucket_seconds,
                strike=strike,
            )
            self.bucket_by_symbol[symbol] = tracker
        elif tracker.bucket_end != bucket_end:
            append_jsonl(bucket_handle, tracker.finalize(complete=True))
            tracker = BucketTracker(
                session_id=self.session_id,
                symbol=symbol,
                bucket_end=bucket_end,
                bucket_seconds=bucket_seconds,
                strike=strike,
            )
            self.bucket_by_symbol[symbol] = tracker

        if tracker.strike is None and strike is not None:
            tracker.strike = strike
        tracker.update(record)

    def finalize_open_buckets(self, bucket_handle) -> None:
        for tracker in self.bucket_by_symbol.values():
            append_jsonl(bucket_handle, tracker.finalize(complete=False))

    def run(self) -> int:
        self.write_meta()
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)

        with (
            self.snapshots_path.open("a", encoding="utf-8") as snapshots_handle,
            self.events_path.open("a", encoding="utf-8") as events_handle,
            self.bucket_outcomes_path.open("a", encoding="utf-8") as buckets_handle,
        ):
            while self._running:
                if self.args.max_runtime_seconds is not None:
                    if time.time() - self.started_at_ts >= float(self.args.max_runtime_seconds):
                        break

                try:
                    payload = fetch_snapshot(self.args.url)
                except urllib.error.URLError as exc:
                    append_jsonl(
                        events_handle,
                        {
                            "session_id": self.session_id,
                            "ts": safe_float(time.time(), 3),
                            "iso_utc": utc_iso(time.time()),
                            "event_type": "fetch_error",
                            "error": str(exc),
                        },
                    )
                    time.sleep(2.0)
                    continue

                captured_at_ts = time.time()
                capture_interval_s = extract_capture_interval(
                    payload,
                    fine_window_s=float(self.args.fine_window_seconds),
                    fine_s=float(self.args.fine_seconds),
                    coarse_s=float(self.args.coarse_seconds),
                )

                for asset in payload.get("assets", []):
                    record = normalize_snapshot_record(
                        session_id=self.session_id,
                        payload=payload,
                        asset=asset,
                        captured_at_ts=captured_at_ts,
                        capture_interval_s=capture_interval_s,
                    )
                    append_jsonl(snapshots_handle, record)

                    symbol = str(record["asset"]["symbol"])
                    previous = self.prev_by_symbol.get(symbol)
                    self.emit_field_events(events_handle, record, previous)
                    self.advance_bucket_tracker(buckets_handle, record)
                    self.prev_by_symbol[symbol] = record

                time.sleep(align_sleep(capture_interval_s))

            self.finalize_open_buckets(buckets_handle)

        finished_at_ts = time.time()
        write_json(
            self.session_meta_path,
            {
                **json.loads(self.session_meta_path.read_text(encoding="utf-8")),
                "stopped_at_ts": safe_float(finished_at_ts, 3),
                "stopped_at_iso": utc_iso(finished_at_ts),
            },
        )
        return 0


def main() -> int:
    args = apply_validation_profile(parse_args())
    session = LiveCaptureSession(args)
    return session.run()


if __name__ == "__main__":
    raise SystemExit(main())
