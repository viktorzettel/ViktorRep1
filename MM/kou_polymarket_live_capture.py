#!/usr/bin/env python3
"""
Read-only Polymarket quote capture sidecar for kou_live_capture.py.

This script runs beside the Kou live capture process and writes Polymarket
top-of-book telemetry into the same session folder. It never creates, posts, or
cancels orders. "Fill" fields are paper estimates based on available ask
liquidity at the moment a Kou signal is observed.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import logging
import math
import os
import re
import signal
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Origin": "https://polymarket.com",
}

ASSET_ALIASES = {
    "eth": ("eth", "ethereum"),
    "xrp": ("xrp", "ripple"),
    "sol": ("sol", "solana"),
}

SYMBOL_ASSET_MAP = {
    "eth": "eth",
    "ethusd": "eth",
    "ethusdt": "eth",
    "ethereum": "eth",
    "xrp": "xrp",
    "xrpusd": "xrp",
    "xrpusdt": "xrp",
    "ripple": "xrp",
}

MARKET_ALIGNMENT_TOLERANCE_S = 2.0
SNAPSHOT_HISTORY_MAXLEN = 360
SNAPSHOT_HISTORY_WINDOWS_S = (15.0, 30.0, 60.0, 90.0)


@dataclass(frozen=True)
class PolyEnvSettings:
    poly_private_key: str
    poly_proxy_address: Optional[str]
    poly_api_key: Optional[str]
    poly_api_secret: Optional[str]
    poly_api_passphrase: Optional[str]
    poly_host: str = "https://clob.polymarket.com"
    poly_chain_id: int = 137

    def has_saved_credentials(self) -> bool:
        return bool(self.poly_api_key and self.poly_api_secret and self.poly_api_passphrase)


@dataclass(frozen=True)
class MarketCandidate:
    slug: str
    question: str
    asset: str
    interval_minutes: int
    token_yes: str
    token_no: str
    yes_label: str
    no_label: str
    start_ts: float
    end_ts: float
    accepting_orders: bool
    active: bool
    closed: bool
    liquidity: float


@dataclass(frozen=True)
class BookTop:
    bid: float
    ask: float
    bid_size: float
    ask_size: float


@dataclass
class GridThresholdState:
    bucket_key: str
    side: Optional[str] = None
    streak_start_ts: Optional[float] = None
    last_seen_ts: Optional[float] = None


@dataclass
class AssetMarketState:
    current_market: Optional[MarketCandidate] = None
    next_market: Optional[MarketCandidate] = None
    last_discover_ts: float = 0.0
    last_slug_probe_ts: float = 0.0


@dataclass(frozen=True)
class SessionResolution:
    session_id: str
    output_dir: Path
    mode: str


@dataclass(frozen=True)
class DiscoveryResult:
    current: Optional[MarketCandidate]
    next_market: Optional[MarketCandidate]
    list_count: int
    probe_count: int
    used_slug_probe: bool


class _MockOrder:
    def __init__(self, price: float, size: float) -> None:
        self.price = str(price)
        self.size = str(size)


class _MockOrderBook:
    def __init__(self, yes_side: bool) -> None:
        if yes_side:
            self.bids = [_MockOrder(0.12, 8.0), _MockOrder(0.58, 12.0)]
            self.asks = [_MockOrder(0.99, 50.0), _MockOrder(0.61, 10.0)]
            self.last_trade_price = "0.60"
        else:
            self.bids = [_MockOrder(0.11, 8.0), _MockOrder(0.39, 12.0)]
            self.asks = [_MockOrder(0.99, 50.0), _MockOrder(0.42, 10.0)]
            self.last_trade_price = "0.41"


class MockPolymarketClient:
    def get_order_book(self, token_id: str) -> _MockOrderBook:
        return _MockOrderBook(yes_side="yes" in token_id)

    def get_price(self, token_id: str, side: str) -> dict[str, str]:
        is_yes = "yes" in token_id
        if side.upper() == "BUY":
            return {"price": "0.61" if is_yes else "0.42"}
        return {"price": "0.60" if is_yes else "0.41"}


def utc_iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def safe_float(value: Any, digits: int = 6) -> Optional[float]:
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return float(f"{num:.{digits}f}")


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sign_from_delta(delta_bps: Any) -> int:
    value = _optional_float(delta_bps)
    if value is None:
        return 0
    if value > 0.0:
        return 1
    if value < 0.0:
        return -1
    return 0


def _side_name(sign: int) -> Optional[str]:
    if sign > 0:
        return "yes"
    if sign < 0:
        return "no"
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture read-only Polymarket quotes beside Kou live capture")
    parser.add_argument("--url", default="http://127.0.0.1:8071/api/snapshot", help="Kou snapshot API URL")
    parser.add_argument("--output-root", default="data/live_capture", help="Live capture session root")
    parser.add_argument("--session-id", default=None, help="Existing live capture session id to attach to")
    parser.add_argument("--env-file", default=".env", help="Environment file with Polymarket credentials")
    parser.add_argument(
        "--assets",
        default="auto",
        help="Comma-separated Polymarket assets to track, or auto to follow the Kou snapshot assets",
    )
    parser.add_argument("--paper-size", type=float, default=5.0, help="Paper buy size in shares/contracts")
    parser.add_argument(
        "--grid-thresholds",
        default="0.90,0.91,0.92,0.93,0.94,0.95,0.96",
        help="Comma-separated Kou probability thresholds to evaluate as hypothetical buy rules",
    )
    parser.add_argument(
        "--grid-hold-seconds",
        default="2,3,4,5",
        help="Comma-separated persistence requirements to evaluate as hypothetical buy rules",
    )
    parser.add_argument(
        "--grid-window-seconds",
        type=float,
        default=90.0,
        help="Only evaluate hypothetical grid rules at or below this time-left value",
    )
    parser.add_argument("--fine-window-seconds", type=float, default=120.0, help="Fine capture window")
    parser.add_argument("--fine-seconds", type=float, default=0.5, help="Fine capture cadence")
    parser.add_argument("--coarse-seconds", type=float, default=1.0, help="Coarse capture cadence")
    parser.add_argument(
        "--validation-profile",
        action="store_true",
        help=(
            "Use the lean out-of-sample validation profile: thresholds 0.90-0.96, "
            "2s hold only, 90s grid window, 120s fine window, 0.5s fine cadence, 2s coarse cadence"
        ),
    )
    parser.add_argument("--discover-seconds", type=float, default=5.0, help="Retry cadence when no usable market is held")
    parser.add_argument(
        "--slug-probe-seconds",
        type=float,
        default=20.0,
        help="Fallback slug-probe cadence when list discovery is empty",
    )
    parser.add_argument("--market-limit", type=int, default=500, help="Gamma markets list limit")
    parser.add_argument("--max-runtime-seconds", type=float, default=None, help="Optional hard stop")
    parser.add_argument(
        "--shadow-candidate",
        default=None,
        help=(
            "Optional candidate module path for read-only shadow execution logging. "
            "The module must expose score_grid_event(row). No real orders are submitted."
        ),
    )
    parser.add_argument(
        "--sniper-mode",
        choices=("off", "signal", "dry-run", "live"),
        default="off",
        help=(
            "Optional handoff from approved shadow orders to the Polymarket sniper. "
            "'signal' writes sniper_signals.jsonl only; 'dry-run' also builds sniper plans; "
            "'live' can submit real orders only with --sniper-live-ack."
        ),
    )
    parser.add_argument("--sniper-order-size", type=float, default=1.0, help="Sniper intended token size")
    parser.add_argument("--sniper-max-order-cost", type=float, default=1.0, help="Sniper max pUSD cost per order")
    parser.add_argument("--sniper-max-session-cost", type=float, default=4.0, help="Sniper max pUSD cost per session")
    parser.add_argument("--sniper-max-session-orders", type=int, default=4, help="Sniper max orders per session")
    parser.add_argument("--sniper-max-entry-price", type=float, default=0.98, help="Sniper hard max entry")
    parser.add_argument("--sniper-max-source-age-s", type=float, default=3.0, help="Sniper max source age")
    parser.add_argument("--sniper-max-model-age-s", type=float, default=3.0, help="Sniper max model age")
    parser.add_argument("--sniper-min-visible-ask-size", type=float, default=1.0, help="Sniper minimum visible ask size")
    parser.add_argument(
        "--sniper-max-book-endpoint-delta",
        type=float,
        default=0.03,
        help="Sniper max visible book ask minus endpoint buy price",
    )
    parser.add_argument("--sniper-order-type", choices=("FOK", "FAK"), default="FOK", help="Sniper live order type")
    parser.add_argument("--sniper-live-ack", action="store_true", help="Required with --sniper-mode live; spends real pUSD")
    parser.add_argument(
        "--sniper-require-geoblock-clear",
        action="store_true",
        help="Require Polymarket geoblock endpoint clear before sniper dry-run/live plan",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--mock-polymarket",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def apply_validation_profile(args: argparse.Namespace) -> argparse.Namespace:
    if not getattr(args, "validation_profile", False):
        args.capture_profile = "full"
        return args
    args.capture_profile = "candidate_validation"
    args.grid_thresholds = "0.90,0.91,0.92,0.93,0.94,0.95,0.96"
    args.grid_hold_seconds = "2"
    args.grid_window_seconds = 90.0
    args.fine_window_seconds = 120.0
    args.fine_seconds = 0.5
    args.coarse_seconds = 2.0
    return args


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(handle, payload: dict[str, Any]) -> None:
    handle.write(json.dumps(payload, sort_keys=True) + "\n")
    handle.flush()


def bucket_key(value: Any) -> Optional[str]:
    number = safe_float(value, 3)
    if number is None:
        return None
    return f"{number:.3f}"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


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


def load_shadow_candidate(candidate_path_raw: Optional[str]) -> Optional[dict[str, Any]]:
    if not candidate_path_raw:
        return None

    if str(candidate_path_raw).strip().lower() in {"none", "off", "false", "disabled"}:
        return None

    candidate_path = Path(str(candidate_path_raw)).expanduser()
    if not candidate_path.is_absolute():
        candidate_path = Path.cwd() / candidate_path
    candidate_path = candidate_path.resolve()
    if not candidate_path.exists():
        raise RuntimeError(f"Shadow candidate module not found: {candidate_path}")

    module_name = f"shadow_candidate_{candidate_path.stem}_{abs(hash(str(candidate_path)))}"
    spec = importlib.util.spec_from_file_location(module_name, candidate_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import shadow candidate module: {candidate_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    scorer = getattr(module, "score_grid_event", None)
    if not callable(scorer):
        raise RuntimeError(f"Shadow candidate must expose callable score_grid_event(row): {candidate_path}")

    return {
        "module": module,
        "score_grid_event": scorer,
        "name": str(getattr(module, "CANDIDATE_NAME", candidate_path.stem)),
        "description": str(getattr(module, "CANDIDATE_DESCRIPTION", "")),
        "path": str(candidate_path),
    }


def _path_window_value(event: dict[str, Any], window_name: str, key: str) -> Any:
    path = event.get("pre_trigger_path") or {}
    window = path.get(window_name) or {}
    return window.get(key)


def grid_event_candidate_row(event: dict[str, Any]) -> dict[str, Any]:
    observed = event.get("observed_token") or {}
    trigger = event.get("trigger") or {}
    rule = event.get("rule") or {}
    session = event.get("session") or {}
    decision_context = event.get("decision_context") or {}
    safety_context = decision_context.get("safety") or {}
    policy_context = decision_context.get("policy") or {}

    return {
        "session_id": session.get("id"),
        "captured_at_iso": session.get("captured_at_iso"),
        "asset": event.get("asset"),
        "symbol": event.get("symbol"),
        "bucket_end": event.get("bucket_end"),
        "bucket_end_iso": event.get("bucket_end_iso"),
        "time_left_s": event.get("time_left_s"),
        "source_age_s": event.get("source_age_s"),
        "model_age_s": event.get("model_age_s"),
        "display_source": event.get("display_source"),
        "model_source": event.get("model_source"),
        "price": event.get("price"),
        "strike": event.get("strike"),
        "delta_bps": event.get("delta_bps"),
        "market_slug": event.get("market_slug"),
        "threshold": rule.get("threshold"),
        "hold_seconds": rule.get("hold_seconds"),
        "side": trigger.get("side"),
        "side_probability": trigger.get("side_probability"),
        "kou_yes": trigger.get("kou_yes"),
        "entry_price": observed.get("entry_price"),
        "entry_price_source": observed.get("entry_price_source"),
        "fill_status": observed.get("fill_status"),
        "fillable_size": observed.get("fillable_size"),
        "requested_size": observed.get("requested_size"),
        "estimated_cost": observed.get("estimated_cost"),
        "book_ask_price": observed.get("book_ask_price"),
        "book_ask_size": observed.get("book_ask_size"),
        "endpoint_buy_price": observed.get("endpoint_buy_price"),
        "book_endpoint_delta": observed.get("book_endpoint_delta"),
        "safety_label": safety_context.get("final_label"),
        "safety_score": safety_context.get("final_score"),
        "safety_weakest_component": safety_context.get("weakest_component"),
        "policy_level": policy_context.get("level"),
        "policy_margin_z": policy_context.get("margin_z"),
        "policy_override": policy_context.get("override"),
        "path_15s_cross_count": _path_window_value(event, "last_15s", "cross_count"),
        "path_15s_adverse_share": _path_window_value(event, "last_15s", "adverse_sample_share"),
        "path_15s_margin_z_change": _path_window_value(event, "last_15s", "margin_z_change"),
        "path_30s_cross_count": _path_window_value(event, "last_30s", "cross_count"),
        "path_30s_adverse_share": _path_window_value(event, "last_30s", "adverse_sample_share"),
        "path_30s_margin_z_change": _path_window_value(event, "last_30s", "margin_z_change"),
        "path_60s_cross_count": _path_window_value(event, "last_60s", "cross_count"),
        "path_60s_adverse_share": _path_window_value(event, "last_60s", "adverse_sample_share"),
        "path_60s_margin_z_change": _path_window_value(event, "last_60s", "margin_z_change"),
    }


def _shadow_order_id(session_id: str, symbol: str, key: str, side: str) -> str:
    return f"{session_id}:{symbol}:{key}:{side}"


def build_shadow_order(
    *,
    event: dict[str, Any],
    candidate: dict[str, Any],
    candidate_row: dict[str, Any],
    decision: dict[str, Any],
    paper_size: float,
) -> Optional[dict[str, Any]]:
    observed = event.get("observed_token") or {}
    trigger = event.get("trigger") or {}
    rule = event.get("rule") or {}
    session = event.get("session") or {}
    side = str(trigger.get("side") or "").lower()
    symbol = str(event.get("symbol") or "")
    key = bucket_key(event.get("bucket_end"))
    decision_entry_price = safe_float(decision.get("execution_entry_price"), 6)
    entry_price = decision_entry_price if decision_entry_price is not None else safe_float(observed.get("entry_price"), 6)
    entry_price_source = decision.get("execution_entry_price_source") or observed.get("entry_price_source")
    fill_status = str(observed.get("fill_status") or "none")

    if not key or side not in {"yes", "no"} or entry_price is None or fill_status == "none":
        return None

    order_id = _shadow_order_id(str(session.get("id") or ""), symbol, key, side)
    fillable_size = safe_float(observed.get("fillable_size"), 6)
    requested_size = safe_float(paper_size, 6)
    requested_cost = None if requested_size is None else safe_float(requested_size * entry_price, 6)
    visible_cost = None if fillable_size is None else safe_float(fillable_size * entry_price, 6)

    return {
        "event_type": "shadow_order",
        "session_id": session.get("id"),
        "ts": session.get("captured_at_ts"),
        "iso_utc": session.get("captured_at_iso"),
        "shadow_order_id": order_id,
        "mode": "read_only_shadow_no_order",
        "real_order_submitted": False,
        "candidate": {
            "name": candidate["name"],
            "path": candidate["path"],
            "decision": {
                "allow_trade": bool(decision.get("allow_trade")),
                "reason": decision.get("reason"),
                "execution_entry_price": decision.get("execution_entry_price"),
                "execution_entry_price_source": decision.get("execution_entry_price_source"),
            },
        },
        "source_grid_event": {
            "asset": event.get("asset"),
            "symbol": symbol,
            "captured_at_ts": session.get("captured_at_ts"),
            "captured_at_iso": session.get("captured_at_iso"),
            "bucket_end": event.get("bucket_end"),
            "bucket_end_iso": event.get("bucket_end_iso"),
            "market_slug": event.get("market_slug"),
            "time_left_s": event.get("time_left_s"),
            "source_age_s": event.get("source_age_s"),
            "model_age_s": event.get("model_age_s"),
            "display_source": event.get("display_source"),
            "model_source": event.get("model_source"),
            "threshold": rule.get("threshold"),
            "hold_seconds": rule.get("hold_seconds"),
            "side_probability": trigger.get("side_probability"),
            "kou_yes": trigger.get("kou_yes"),
        },
        "order": {
            "side": side,
            "requested_size": requested_size,
            "entry_price": entry_price,
            "entry_price_source": entry_price_source,
            "book_ask_price": observed.get("book_ask_price"),
            "book_ask_size": observed.get("book_ask_size"),
            "endpoint_buy_price": observed.get("endpoint_buy_price"),
            "book_endpoint_delta": observed.get("book_endpoint_delta"),
            "fill_status": fill_status,
            "hypothetical_fill_size": fillable_size,
            "estimated_cost": visible_cost,
            "requested_size_cost": requested_cost,
            "visible_size_cost": visible_cost,
        },
        "risk": {
            "pnl_per_share_if_win": observed.get("pnl_per_share_if_win"),
            "pnl_per_share_if_loss": observed.get("pnl_per_share_if_loss"),
        },
        "candidate_row": candidate_row,
    }


def build_sniper_signal_from_shadow_order(order: dict[str, Any], *, ttl_s: float = 8.0) -> Optional[dict[str, Any]]:
    source = order.get("source_grid_event") or {}
    order_payload = order.get("order") or {}
    candidate = order.get("candidate") or {}
    decision = candidate.get("decision") or {}
    candidate_row = order.get("candidate_row") or {}
    side = str(order_payload.get("side") or "").lower()
    symbol = str(source.get("symbol") or candidate_row.get("symbol") or "").lower()
    entry_price = safe_float(order_payload.get("entry_price"), 6)
    captured_at_ts = safe_float(source.get("captured_at_ts") or order.get("ts"), 3)
    time_left_s = safe_float(source.get("time_left_s"), 3)
    if side not in {"yes", "no"} or not symbol or entry_price is None or captured_at_ts is None:
        return None

    live_ttl_s = max(1.0, min(float(ttl_s), (time_left_s - 5.0) if time_left_s is not None else float(ttl_s)))
    return {
        "signal_id": order.get("shadow_order_id"),
        "symbol": symbol,
        "side": side,
        "max_entry_price": entry_price,
        "market_slug": source.get("market_slug") or candidate_row.get("market_slug"),
        "bucket_end": source.get("bucket_end") or candidate_row.get("bucket_end"),
        "reason": decision.get("reason"),
        "expires_at": safe_float(captured_at_ts + live_ttl_s, 3),
        "source_age_s": source.get("source_age_s") if source.get("source_age_s") is not None else candidate_row.get("source_age_s"),
        "model_age_s": source.get("model_age_s") if source.get("model_age_s") is not None else candidate_row.get("model_age_s"),
        "time_left_s": time_left_s,
        "price": candidate_row.get("price"),
        "strike": candidate_row.get("strike"),
        "entry_price": entry_price,
        "entry_price_source": order_payload.get("entry_price_source"),
        "book_ask_price": order_payload.get("book_ask_price"),
        "book_ask_size": order_payload.get("book_ask_size"),
        "endpoint_buy_price": order_payload.get("endpoint_buy_price"),
        "book_endpoint_delta": order_payload.get("book_endpoint_delta"),
        "candidate_name": candidate.get("name"),
        "captured_at_ts": captured_at_ts,
        "captured_at_iso": source.get("captured_at_iso") or order.get("iso_utc"),
    }


def load_bucket_outcomes(outcomes_path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    outcomes: dict[tuple[str, str], dict[str, Any]] = {}
    for row in read_jsonl(outcomes_path):
        key = bucket_key(row.get("bucket_end"))
        symbol = str(row.get("symbol") or "")
        if key and symbol:
            outcomes[(symbol, key)] = row
    return outcomes


def build_shadow_settlement(order: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    now_ts = time.time()
    source = order.get("source_grid_event") or {}
    order_payload = order.get("order") or {}
    side = str(order_payload.get("side") or "").lower()
    settled_side = outcome.get("settled_side")
    known_outcome = bool(outcome.get("complete")) and settled_side in {"yes", "no", "flat"}
    win = known_outcome and side in {"yes", "no"} and side == settled_side
    entry_price = safe_float(order_payload.get("entry_price"), 6)
    requested_size = safe_float(order_payload.get("requested_size"), 6)
    fill_size = safe_float(order_payload.get("hypothetical_fill_size"), 6)

    pnl_per_share = None
    roi = None
    if known_outcome and entry_price is not None and side in {"yes", "no"}:
        pnl_per_share = (1.0 - entry_price) if win else -entry_price
        roi = None if entry_price <= 0.0 else pnl_per_share / entry_price

    return {
        "event_type": "shadow_settlement",
        "session_id": order.get("session_id"),
        "ts": safe_float(now_ts, 3),
        "iso_utc": utc_iso(now_ts),
        "shadow_order_id": order.get("shadow_order_id"),
        "mode": "read_only_shadow_no_order",
        "candidate": order.get("candidate"),
        "source_grid_event": source,
        "order": order_payload,
        "outcome": {
            "complete": outcome.get("complete"),
            "settled_side": settled_side,
            "settled_yes": outcome.get("settled_yes"),
            "settled_delta_bps": outcome.get("settled_delta_bps"),
            "bucket_end": outcome.get("bucket_end"),
            "bucket_end_iso": outcome.get("bucket_end_iso"),
            "sample_count": outcome.get("sample_count"),
        },
        "result": {
            "known_outcome": known_outcome,
            "win": win if known_outcome else None,
            "pnl_per_share": None if pnl_per_share is None else safe_float(pnl_per_share, 6),
            "roi": None if roi is None else safe_float(roi, 6),
            "paper_pnl_requested_size": None
            if pnl_per_share is None or requested_size is None
            else safe_float(pnl_per_share * requested_size, 6),
            "paper_pnl_visible_size": None
            if pnl_per_share is None or fill_size is None
            else safe_float(pnl_per_share * fill_size, 6),
        },
    }


def choose_session_id(now_ts: Optional[float] = None) -> str:
    ts = time.time() if now_ts is None else now_ts
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(ts))


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _is_active_session_meta(payload: dict[str, Any]) -> bool:
    return payload.get("stopped_at_ts") is None and payload.get("stopped_at_iso") is None


def resolve_output_session(
    output_root: Path,
    session_id: Optional[str],
    *,
    now_ts: Optional[float] = None,
) -> SessionResolution:
    if session_id:
        output_dir = output_root / session_id
        output_dir.mkdir(parents=True, exist_ok=True)
        return SessionResolution(session_id=session_id, output_dir=output_dir, mode="explicit")

    active: list[tuple[float, str, Path]] = []
    if output_root.exists():
        for meta_path in output_root.glob("*/session_meta.json"):
            payload = _read_json(meta_path)
            if not payload or not _is_active_session_meta(payload):
                continue
            started = payload.get("started_at_ts")
            try:
                started_ts = float(started)
            except (TypeError, ValueError):
                started_ts = meta_path.stat().st_mtime
            active.append((started_ts, meta_path.parent.name, meta_path.parent))

    if len(active) == 1:
        _started_ts, active_id, active_dir = active[0]
        return SessionResolution(session_id=active_id, output_dir=active_dir, mode="attached_active")

    if len(active) > 1:
        ids = ", ".join(session_id for _started, session_id, _path in sorted(active))
        raise RuntimeError(f"Multiple active live capture sessions found ({ids}); pass --session-id explicitly.")

    new_id = choose_session_id(now_ts)
    output_dir = output_root / new_id
    suffix = 1
    while output_dir.exists():
        output_dir = output_root / f"{new_id}-{suffix}"
        suffix += 1
    output_dir.mkdir(parents=True, exist_ok=False)
    return SessionResolution(session_id=output_dir.name, output_dir=output_dir, mode="standalone_new")


def load_poly_settings(env_file: str) -> PolyEnvSettings:
    env_path = Path(env_file)
    if env_path.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_path, override=False)
        except Exception:
            _load_dotenv_fallback(env_path)

    private_key = os.getenv("POLY_PRIVATE_KEY", "").strip()
    if not private_key:
        raise RuntimeError(f"POLY_PRIVATE_KEY is missing; checked env file {env_file!r} and process env.")

    chain_id_raw = os.getenv("POLY_CHAIN_ID", "137").strip()
    try:
        chain_id = int(chain_id_raw)
    except ValueError:
        chain_id = 137

    return PolyEnvSettings(
        poly_private_key=private_key,
        poly_proxy_address=_blank_to_none(os.getenv("POLY_PROXY_ADDRESS")),
        poly_api_key=_blank_to_none(os.getenv("POLY_API_KEY")),
        poly_api_secret=_blank_to_none(os.getenv("POLY_API_SECRET")),
        poly_api_passphrase=_blank_to_none(os.getenv("POLY_API_PASSPHRASE")),
        poly_host=os.getenv("POLY_HOST", "https://clob.polymarket.com").strip() or "https://clob.polymarket.com",
        poly_chain_id=chain_id,
    )


def _blank_to_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _load_dotenv_fallback(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _patch_clob_headers() -> None:
    import py_clob_client.http_helpers.helpers as helpers

    def _patched(_method: str, headers: Optional[dict[str, str]]) -> dict[str, str]:
        out = dict(headers or {})
        out.setdefault("User-Agent", BROWSER_HEADERS["User-Agent"])
        out.setdefault("Accept", "*/*")
        out.setdefault("Content-Type", "application/json")
        out.setdefault("Origin", "https://polymarket.com")
        return out

    helpers.overloadHeaders = _patched


def login_clob_client(settings: PolyEnvSettings) -> tuple[Any, str]:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds

    _patch_clob_headers()

    signature_type = 2 if settings.poly_proxy_address else None
    client = ClobClient(
        host=settings.poly_host,
        key=settings.poly_private_key,
        chain_id=settings.poly_chain_id,
        funder=settings.poly_proxy_address,
        signature_type=signature_type,
    )

    if settings.has_saved_credentials():
        creds = ApiCreds(
            api_key=settings.poly_api_key or "",
            api_secret=settings.poly_api_secret or "",
            api_passphrase=settings.poly_api_passphrase or "",
        )
    else:
        creds = client.create_or_derive_api_creds()
        if creds is None:
            raise RuntimeError("Failed to derive Polymarket API credentials")

    client.set_api_creds(creds)
    client.get_api_keys()
    return client, client.get_address() or "-"


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _parse_json_field(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _parse_iso_ts(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() and len(text) >= 10:
        return float(int(text[:10]))

    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _slug_epoch_ts(slug: str) -> Optional[float]:
    match = re.search(r"-(\d{10})(?:$|[^0-9])", slug)
    if not match:
        return None
    try:
        return float(int(match.group(1)))
    except ValueError:
        return None


def _http_get_json(url: str, params: Optional[dict[str, Any]] = None, timeout: float = 8.0) -> Any:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    req = urllib.request.Request(f"{url}{query}", headers=BROWSER_HEADERS)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def _detect_interval_minutes(market: dict[str, Any]) -> Optional[int]:
    recurrence = str(market.get("recurrence", "")).strip().lower()
    if recurrence in {"5m", "5min", "5-minute", "5 minute"}:
        return 5

    events = market.get("events")
    if isinstance(events, list) and events and isinstance(events[0], dict):
        series = events[0].get("series")
        if isinstance(series, list) and series and isinstance(series[0], dict):
            recurrence = str(series[0].get("recurrence", "")).strip().lower()
            if recurrence in {"5m", "5min", "5-minute", "5 minute"}:
                return 5

    blob = " ".join(
        [
            str(market.get("slug", "")),
            str(market.get("question", "")),
            str(market.get("title", "")),
        ]
    ).lower()
    if re.search(r"(^|[^0-9])5\s*[- ]?\s*(m|min|minute)([^a-z0-9]|$)", blob):
        return 5
    return None


def _detect_asset(market: dict[str, Any]) -> Optional[str]:
    blob = " ".join(
        [
            str(market.get("slug", "")),
            str(market.get("question", "")),
            str(market.get("title", "")),
            str(market.get("resolutionSource", "")),
        ]
    ).lower()
    for asset, aliases in ASSET_ALIASES.items():
        if any(alias in blob for alias in aliases):
            return asset
    return None


def _extract_yes_no_tokens(market: dict[str, Any]) -> tuple[str, str, str, str]:
    token_ids = _parse_json_field(market.get("clobTokenIds", []))
    if len(token_ids) < 2:
        return "", "", "YES", "NO"

    outcomes_raw = _parse_json_field(market.get("outcomes", []))
    outcomes = [str(outcome).strip().lower() for outcome in outcomes_raw]

    yes_idx = next((i for i, outcome in enumerate(outcomes) if outcome in {"yes", "up"}), None)
    no_idx = next((i for i, outcome in enumerate(outcomes) if outcome in {"no", "down"}), None)
    if yes_idx is not None and no_idx is not None:
        return (
            str(token_ids[yes_idx]),
            str(token_ids[no_idx]),
            str(outcomes_raw[yes_idx]).upper(),
            str(outcomes_raw[no_idx]).upper(),
        )

    return str(token_ids[0]), str(token_ids[1]), "YES", "NO"


def _extract_start_end_ts(market: dict[str, Any], interval_minutes: int) -> tuple[Optional[float], Optional[float]]:
    def _first_ts(keys: list[str], payload: dict[str, Any]) -> Optional[float]:
        for key in keys:
            if key in payload:
                ts = _parse_iso_ts(payload.get(key))
                if ts is not None:
                    return ts
        return None

    start_keys = ["eventStartTime", "startTime", "startDate", "acceptingOrdersTimestamp"]
    end_keys = ["endDate", "endTime"]

    start_ts = _first_ts(start_keys, market)
    end_ts = _first_ts(end_keys, market)

    events = market.get("events")
    if isinstance(events, list) and events and isinstance(events[0], dict):
        event0 = events[0]
        if start_ts is None:
            start_ts = _first_ts(start_keys, event0)
        if end_ts is None:
            end_ts = _first_ts(end_keys, event0)

    slug_ts = _slug_epoch_ts(str(market.get("slug", "")))
    if start_ts is None and slug_ts is not None:
        start_ts = slug_ts
    if start_ts is None and end_ts is not None:
        start_ts = end_ts - (interval_minutes * 60)
    if end_ts is None and start_ts is not None:
        end_ts = start_ts + (interval_minutes * 60)

    return start_ts, end_ts


def _market_to_candidate(market: dict[str, Any], asset: str) -> Optional[MarketCandidate]:
    if not _to_bool(market.get("enableOrderBook", False)):
        return None
    if _detect_interval_minutes(market) != 5:
        return None
    if _detect_asset(market) != asset:
        return None

    slug = str(market.get("slug", "")).strip()
    if not slug:
        return None

    token_yes, token_no, yes_label, no_label = _extract_yes_no_tokens(market)
    if not token_yes or not token_no:
        return None

    start_ts, end_ts = _extract_start_end_ts(market, interval_minutes=5)
    if start_ts is None or end_ts is None:
        return None

    return MarketCandidate(
        slug=slug,
        question=str(market.get("question", "")),
        asset=asset,
        interval_minutes=5,
        token_yes=token_yes,
        token_no=token_no,
        yes_label=yes_label,
        no_label=no_label,
        start_ts=start_ts,
        end_ts=end_ts,
        accepting_orders=_to_bool(market.get("acceptingOrders", False)),
        active=_to_bool(market.get("active", False)),
        closed=_to_bool(market.get("closed", False)),
        liquidity=_to_float(market.get("liquidityNum", market.get("liquidity", 0.0))),
    )


def discover_5m_markets(asset: str, limit: int, *, timeout: float = 10.0) -> list[MarketCandidate]:
    payload = _http_get_json(GAMMA_MARKETS_URL, {"closed": "false", "limit": limit}, timeout=timeout)
    markets = payload if isinstance(payload, list) else []

    out: list[MarketCandidate] = []
    for market in markets:
        candidate = _market_to_candidate(market, asset)
        if candidate is not None:
            out.append(candidate)
    out.sort(key=lambda item: (item.start_ts, item.end_ts))
    return out


def probe_5m_markets_by_slug(
    asset: str,
    now_ts: float,
    *,
    lookback_slots: int = 2,
    lookahead_slots: int = 4,
    patterns: Optional[list[str]] = None,
    timeout: float = 5.0,
) -> list[MarketCandidate]:
    step_seconds = 5 * 60
    slot_base = int(now_ts // step_seconds) * step_seconds
    slots = [slot_base + (i * step_seconds) for i in range(-lookback_slots, lookahead_slots + 1)]
    slug_patterns = patterns or [
        f"{asset}-updown-5m-{{ts}}",
        f"{asset}-up-or-down-5m-{{ts}}",
        f"{asset}-updown-{{ts}}",
        f"{asset}-up-or-down-{{ts}}",
    ]

    out: list[MarketCandidate] = []
    seen: set[str] = set()
    for slot_ts in slots:
        for pattern in slug_patterns:
            slug = pattern.format(ts=slot_ts)
            if slug in seen:
                continue
            seen.add(slug)
            payload = _http_get_json(GAMMA_MARKETS_URL, {"slug": slug}, timeout=timeout)
            rows = payload if isinstance(payload, list) else []
            for market in rows:
                candidate = _market_to_candidate(market, asset)
                if candidate is not None:
                    out.append(candidate)

    unique = {market.slug: market for market in out}
    results = list(unique.values())
    results.sort(key=lambda item: (item.start_ts, item.end_ts))
    return results


def probe_current_5m_markets_by_slug(
    asset: str,
    now_ts: float,
    *,
    timeout: float = 5.0,
    broad_fallback: bool = True,
) -> list[MarketCandidate]:
    fast = probe_5m_markets_by_slug(
        asset,
        now_ts,
        lookback_slots=0,
        lookahead_slots=1,
        patterns=[f"{asset}-updown-5m-{{ts}}"],
        timeout=timeout,
    )
    current, _next_market = select_current_and_next(fast, now_ts)
    if not broad_fallback or (current is not None and market_status(current, now_ts) == "LIVE"):
        return fast

    return probe_5m_markets_by_slug(
        asset,
        now_ts,
        lookback_slots=1,
        lookahead_slots=2,
        timeout=timeout,
    )


def select_current_and_next(
    markets: list[MarketCandidate],
    now_ts: float,
) -> tuple[Optional[MarketCandidate], Optional[MarketCandidate]]:
    live = [market for market in markets if market.start_ts <= now_ts < market.end_ts and not market.closed]
    if live:
        live.sort(key=lambda item: (item.end_ts, -item.start_ts))
        current = live[0]
    else:
        upcoming = [market for market in markets if now_ts < market.start_ts and not market.closed]
        upcoming.sort(key=lambda item: item.start_ts)
        current = upcoming[0] if upcoming else None

    if current is None:
        return None, None

    next_candidates = [market for market in markets if market.start_ts > current.start_ts and not market.closed]
    next_candidates.sort(key=lambda item: item.start_ts)
    return current, (next_candidates[0] if next_candidates else None)


def discover_current_and_next_5m_markets(
    asset: str,
    now_ts: float,
    *,
    market_limit: int,
    allow_slug_probe: bool = True,
    force_slug_probe: bool = False,
    gamma_timeout: float = 10.0,
    slug_timeout: float = 5.0,
    broad_slug_probe: bool = True,
) -> DiscoveryResult:
    markets = discover_5m_markets(asset, max(50, int(market_limit)), timeout=gamma_timeout)
    current, next_market = select_current_and_next(markets, now_ts)
    needs_probe = current is None or market_status(current, now_ts) != "LIVE"
    should_probe = allow_slug_probe and (force_slug_probe or needs_probe)

    probed: list[MarketCandidate] = []
    if should_probe:
        probed = probe_current_5m_markets_by_slug(asset, now_ts, timeout=slug_timeout, broad_fallback=broad_slug_probe)
        combined_by_slug = {market.slug: market for market in markets}
        combined_by_slug.update({market.slug: market for market in probed})
        combined = list(combined_by_slug.values())
        combined.sort(key=lambda item: (item.start_ts, item.end_ts))
        current, next_market = select_current_and_next(combined, now_ts)

    return DiscoveryResult(
        current=current,
        next_market=next_market,
        list_count=len(markets),
        probe_count=len(probed),
        used_slug_probe=bool(probed) or should_probe,
    )


def best_effort_next_5m_market(
    asset: str,
    current: MarketCandidate,
    *,
    slug_timeout: float = 1.0,
) -> Optional[MarketCandidate]:
    try:
        next_rows = probe_5m_markets_by_slug(
            asset,
            current.end_ts,
            lookback_slots=0,
            lookahead_slots=0,
            patterns=[f"{asset}-updown-5m-{{ts}}"],
            timeout=slug_timeout,
        )
    except Exception:
        next_rows = []
    if next_rows:
        return next_rows[0]
    return MarketCandidate(
        slug=f"{asset}-updown-5m-{int(current.end_ts)}",
        question=f"{asset.upper()} next 5m market",
        asset=asset,
        interval_minutes=5,
        token_yes="",
        token_no="",
        yes_label="UP",
        no_label="DOWN",
        start_ts=current.end_ts,
        end_ts=current.end_ts + 300.0,
        accepting_orders=False,
        active=False,
        closed=False,
        liquidity=0.0,
    )


def discover_slug_first_current_and_next_5m_markets(
    asset: str,
    now_ts: float,
    *,
    market_limit: int,
    gamma_timeout: float = 10.0,
    slug_timeout: float = 5.0,
    fallback_to_list: bool = True,
    broad_slug_probe: bool = True,
) -> DiscoveryResult:
    probed = probe_5m_markets_by_slug(
        asset,
        now_ts,
        lookback_slots=0,
        lookahead_slots=0,
        patterns=[f"{asset}-updown-5m-{{ts}}"],
        timeout=slug_timeout,
    )
    current, next_market = select_current_and_next(probed, now_ts)
    if current is not None and market_status(current, now_ts) == "LIVE":
        next_market = best_effort_next_5m_market(
            asset,
            current,
            slug_timeout=min(1.0, float(slug_timeout)),
        )
        return DiscoveryResult(
            current=current,
            next_market=next_market,
            list_count=0,
            probe_count=len(probed),
            used_slug_probe=True,
        )

    if not fallback_to_list:
        return DiscoveryResult(
            current=current,
            next_market=next_market,
            list_count=0,
            probe_count=len(probed),
            used_slug_probe=True,
        )

    return discover_current_and_next_5m_markets(
        asset,
        now_ts,
        market_limit=market_limit,
        allow_slug_probe=True,
        force_slug_probe=False,
        gamma_timeout=gamma_timeout,
        slug_timeout=slug_timeout,
        broad_slug_probe=broad_slug_probe,
    )


def mock_current_and_next_5m_markets(asset: str, now_ts: float) -> DiscoveryResult:
    step_seconds = 5 * 60
    start_ts = float(int(now_ts // step_seconds) * step_seconds)
    end_ts = start_ts + step_seconds
    next_start = end_ts
    next_end = next_start + step_seconds
    current = MarketCandidate(
        slug=f"{asset}-mock-updown-5m-{int(start_ts)}",
        question=f"{asset.upper()} mock Up or Down",
        asset=asset,
        interval_minutes=5,
        token_yes=f"{asset}-mock-yes-{int(start_ts)}",
        token_no=f"{asset}-mock-no-{int(start_ts)}",
        yes_label="UP",
        no_label="DOWN",
        start_ts=start_ts,
        end_ts=end_ts,
        accepting_orders=True,
        active=True,
        closed=False,
        liquidity=10000.0,
    )
    next_market = MarketCandidate(
        slug=f"{asset}-mock-updown-5m-{int(next_start)}",
        question=f"{asset.upper()} mock Up or Down next",
        asset=asset,
        interval_minutes=5,
        token_yes=f"{asset}-mock-yes-{int(next_start)}",
        token_no=f"{asset}-mock-no-{int(next_start)}",
        yes_label="UP",
        no_label="DOWN",
        start_ts=next_start,
        end_ts=next_end,
        accepting_orders=True,
        active=True,
        closed=False,
        liquidity=10000.0,
    )
    return DiscoveryResult(
        current=current,
        next_market=next_market,
        list_count=0,
        probe_count=0,
        used_slug_probe=False,
    )


def fetch_snapshot(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
    with urllib.request.urlopen(req, timeout=10.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def fetch_book_top(
    client: Any,
    token_id: str,
    *,
    attempts: int = 2,
    retry_delay_s: float = 0.05,
) -> Optional[BookTop]:
    last_exc: Optional[Exception] = None
    for attempt in range(max(1, int(attempts))):
        try:
            book = client.get_order_book(token_id)
            break
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= max(1, int(attempts)):
                raise
            time.sleep(max(0.0, float(retry_delay_s)))
    else:
        if last_exc is not None:
            raise last_exc
        return None

    bids = getattr(book, "bids", None) or []
    asks = getattr(book, "asks", None) or []
    if not asks:
        return None

    valid_bids = [
        (price, order)
        for order in bids
        if (price := _to_float(getattr(order, "price", None))) > 0.0
    ]
    valid_asks = [
        (price, order)
        for order in asks
        if (price := _to_float(getattr(order, "price", None))) > 0.0
    ]
    if not valid_asks:
        return None

    b0 = max(valid_bids, key=lambda item: item[0])[1] if valid_bids else None
    _ask_price, a0 = min(valid_asks, key=lambda item: item[0])
    bid = _to_float(getattr(b0, "price", None)) if b0 is not None else 0.0
    ask = _to_float(getattr(a0, "price", None))
    if ask <= 0.0:
        return None

    return BookTop(
        bid=bid,
        ask=ask,
        bid_size=_to_float(getattr(b0, "size", None)) if b0 is not None else 0.0,
        ask_size=_to_float(getattr(a0, "size", None)),
    )


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


def _safe_price_call(
    client: Any,
    method: str,
    *args: Any,
    attempts: int = 2,
    retry_delay_s: float = 0.05,
) -> Optional[float]:
    for attempt in range(max(1, int(attempts))):
        try:
            payload = getattr(client, method)(*args)
            return _extract_price_value(payload)
        except Exception:
            if attempt + 1 >= max(1, int(attempts)):
                return None
            time.sleep(max(0.0, float(retry_delay_s)))
    return None


def fetch_token_buy_price(client: Any, token_id: str) -> dict[str, Optional[float]]:
    return {"buy_price": _safe_price_call(client, "get_price", token_id, "BUY")}


def asset_from_symbol(symbol: Any) -> Optional[str]:
    normalized = str(symbol or "").strip().lower().replace("-", "").replace("/", "")
    return SYMBOL_ASSET_MAP.get(normalized)


def parse_assets(value: str) -> Optional[set[str]]:
    if value.strip().lower() == "auto":
        return None
    assets = {item.strip().lower() for item in value.split(",") if item.strip()}
    invalid = sorted(asset for asset in assets if asset not in ASSET_ALIASES)
    if invalid:
        raise ValueError(f"Unsupported asset(s): {', '.join(invalid)}")
    return assets


def parse_float_list(value: str, *, min_value: Optional[float] = None, max_value: Optional[float] = None) -> list[float]:
    out: list[float] = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        number = float(raw)
        if min_value is not None and number < min_value:
            raise ValueError(f"{number} is below minimum {min_value}")
        if max_value is not None and number > max_value:
            raise ValueError(f"{number} is above maximum {max_value}")
        out.append(number)
    if not out:
        raise ValueError("Expected at least one numeric value")
    return sorted(set(out))


def parse_int_list(value: str, *, min_value: Optional[int] = None, max_value: Optional[int] = None) -> list[int]:
    out: list[int] = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        number = int(raw)
        if min_value is not None and number < min_value:
            raise ValueError(f"{number} is below minimum {min_value}")
        if max_value is not None and number > max_value:
            raise ValueError(f"{number} is above maximum {max_value}")
        out.append(number)
    if not out:
        raise ValueError("Expected at least one integer value")
    return sorted(set(out))


def selected_snapshot_assets(payload: dict[str, Any], requested_assets: Optional[set[str]]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for asset_snapshot in payload.get("assets", []):
        if not isinstance(asset_snapshot, dict):
            continue
        asset = asset_from_symbol(asset_snapshot.get("symbol"))
        if asset is None:
            continue
        if requested_assets is not None and asset not in requested_assets:
            continue
        out.append((asset, asset_snapshot))
    return out


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


def market_status(market: Optional[MarketCandidate], now_ts: float) -> str:
    if market is None:
        return "NO_MARKET"
    if market.closed:
        return "CLOSED"
    if now_ts < market.start_ts:
        return "UPCOMING"
    if now_ts >= market.end_ts:
        return "ENDED"
    if not market.active:
        return "INACTIVE"
    return "LIVE"


def market_end_delta_s(kou_bucket_end: Any, market: Optional[MarketCandidate]) -> Optional[float]:
    if kou_bucket_end is None or market is None:
        return None
    try:
        return float(kou_bucket_end) - float(market.end_ts)
    except (TypeError, ValueError):
        return None


def market_alignment_status(delta_s: Optional[float], tolerance_s: float = MARKET_ALIGNMENT_TOLERANCE_S) -> str:
    if delta_s is None:
        return "unknown"
    if abs(delta_s) <= tolerance_s:
        return "aligned"
    return "warning"


def _top_to_payload(top: Optional[BookTop]) -> dict[str, Optional[float]]:
    if top is None:
        return {
            "ask": None,
            "ask_size": None,
        }
    return {
        "ask": safe_float(top.ask, 6),
        "ask_size": safe_float(top.ask_size, 6),
    }


def build_book_payload(yes_top: Optional[BookTop], no_top: Optional[BookTop]) -> dict[str, Any]:
    return {
        "yes": _top_to_payload(yes_top),
        "no": _top_to_payload(no_top),
    }


def build_token_prices_payload(
    yes_prices: Optional[dict[str, Optional[float]]],
    no_prices: Optional[dict[str, Optional[float]]],
) -> dict[str, Any]:
    yes_buy = None if yes_prices is None else yes_prices.get("buy_price")
    no_buy = None if no_prices is None else no_prices.get("buy_price")
    buy_sum = None
    if yes_buy is not None and no_buy is not None:
        buy_sum = float(yes_buy) + float(no_buy)
    return {
        "yes": {"buy_price": safe_float(yes_buy, 6)},
        "no": {"buy_price": safe_float(no_buy, 6)},
        "buy_price_sum": safe_float(buy_sum, 6),
    }


def classify_paper_fill(
    *,
    signal_state: Any,
    safety_label: Any,
    status: str,
    accepting_orders: bool,
    yes_ask: Optional[float],
    yes_ask_size: Optional[float],
    no_ask: Optional[float],
    no_ask_size: Optional[float],
    paper_size: float,
    yes_buy_price: Optional[float] = None,
    no_buy_price: Optional[float] = None,
) -> dict[str, Any]:
    signal = str(signal_state or "")
    raw_side = "yes" if signal == "BUY_YES" else "no" if signal == "BUY_NO" else None
    requested_size = max(0.0, float(paper_size))
    policy_would_buy = raw_side is not None and str(safety_label or "") == "GOOD"

    base = {
        "signal_state": signal or None,
        "raw_signal_side": raw_side,
        "policy_would_buy": policy_would_buy,
        "requested_size": safe_float(requested_size, 6),
        "fill_status": "none",
        "fillable_size": 0.0,
        "entry_price": None,
        "entry_price_source": None,
        "estimated_cost": None,
        "reason": None,
    }

    if raw_side is None:
        return {**base, "reason": "no_signal"}
    if requested_size <= 0.0:
        return {**base, "reason": "invalid_paper_size"}
    if status != "LIVE":
        return {**base, "reason": "no_live_market"}
    if not accepting_orders:
        return {**base, "reason": "market_not_accepting_orders"}

    ask = yes_ask if raw_side == "yes" else no_ask
    ask_size = yes_ask_size if raw_side == "yes" else no_ask_size
    endpoint_buy = yes_buy_price if raw_side == "yes" else no_buy_price
    entry_price = endpoint_buy if endpoint_buy is not None and endpoint_buy > 0.0 else ask
    entry_source = "clob_price_buy" if endpoint_buy is not None and endpoint_buy > 0.0 else "book_ask"

    if entry_price is None or entry_price <= 0.0:
        return {**base, "reason": "no_ask"}

    available = 0.0 if ask_size is None else max(0.0, float(ask_size))
    book_endpoint_delta = None
    if ask is not None and endpoint_buy is not None:
        book_endpoint_delta = safe_float(float(ask) - float(endpoint_buy), 6)
    common = {
        **base,
        "entry_price": safe_float(entry_price, 6),
        "entry_price_source": entry_source,
        "book_ask_price": safe_float(ask, 6),
        "book_ask_size": safe_float(ask_size, 6),
        "book_endpoint_delta": book_endpoint_delta,
    }

    if available <= 0.0:
        return {**common, "fill_status": "unknown_size", "fillable_size": None, "reason": "price_observed_size_unknown"}

    fillable = min(requested_size, available)
    fill_status = "full" if fillable >= requested_size else "partial"
    return {
        **common,
        "fill_status": fill_status,
        "fillable_size": safe_float(fillable, 6),
        "estimated_cost": safe_float(fillable * entry_price, 6),
        "reason": fill_status,
    }


def _probability_side(kou_yes: Any, threshold: float) -> tuple[Optional[str], Optional[float]]:
    try:
        yes_prob = float(kou_yes)
    except (TypeError, ValueError):
        return None, None
    if yes_prob >= threshold:
        return "yes", yes_prob
    no_prob = 1.0 - yes_prob
    if no_prob >= threshold:
        return "no", no_prob
    return None, max(yes_prob, no_prob)


def _side_price_and_size(
    *,
    side: str,
    book: dict[str, Any],
    token_prices: dict[str, Any],
) -> dict[str, Any]:
    side_book = book.get(side, {}) if isinstance(book, dict) else {}
    side_prices = token_prices.get(side, {}) if isinstance(token_prices, dict) else {}
    buy_price = side_prices.get("buy_price")
    book_ask = side_book.get("ask")
    entry_price = buy_price if buy_price is not None else book_ask
    source = "clob_price_buy" if buy_price is not None else "book_ask"
    buy_price_f = safe_float(buy_price, 6)
    book_ask_f = safe_float(book_ask, 6)
    book_endpoint_delta = None
    if book_ask_f is not None and buy_price_f is not None:
        book_endpoint_delta = book_ask_f - buy_price_f
    return {
        "entry_price": safe_float(entry_price, 6),
        "entry_price_source": source if entry_price is not None else None,
        "book_ask_price": book_ask_f,
        "book_ask_size": safe_float(side_book.get("ask_size"), 6),
        "endpoint_buy_price": buy_price_f,
        "book_endpoint_delta": safe_float(book_endpoint_delta, 6),
    }


def _fill_status_from_size(entry_price: Optional[float], ask_size: Optional[float], paper_size: float) -> dict[str, Any]:
    requested_size = max(0.0, float(paper_size))
    if entry_price is None or entry_price <= 0.0:
        return {
            "fill_status": "none",
            "fillable_size": 0.0,
            "requested_size": safe_float(requested_size, 6),
            "estimated_cost": None,
            "reason": "no_buy_price",
        }
    if ask_size is None:
        return {
            "fill_status": "unknown_size",
            "fillable_size": None,
            "requested_size": safe_float(requested_size, 6),
            "estimated_cost": None,
            "reason": "price_observed_size_unknown",
        }
    available = max(0.0, float(ask_size))
    if available <= 0.0:
        return {
            "fill_status": "none",
            "fillable_size": 0.0,
            "requested_size": safe_float(requested_size, 6),
            "estimated_cost": None,
            "reason": "no_ask_size",
        }
    fillable = min(requested_size, available)
    status = "full" if fillable >= requested_size else "partial"
    return {
        "fill_status": status,
        "fillable_size": safe_float(fillable, 6),
        "requested_size": safe_float(requested_size, 6),
        "estimated_cost": safe_float(fillable * float(entry_price), 6),
        "reason": status,
    }


def extract_grid_context(asset_snapshot: dict[str, Any]) -> dict[str, Any]:
    components = asset_snapshot.get("safety_components") or {}
    if not isinstance(components, dict):
        components = {}
    return {
        "safety": {
            "final_score": asset_snapshot.get("trade_score"),
            "final_label": asset_snapshot.get("trade_score_label"),
            "final_reason": asset_snapshot.get("trade_score_reason"),
            "heuristic_score": asset_snapshot.get("base_trade_score"),
            "heuristic_label": asset_snapshot.get("base_trade_score_label"),
            "heuristic_reason": asset_snapshot.get("base_trade_score_reason"),
            "weakest_component": components.get("weakest_component"),
            "components": {
                "margin_safety": components.get("margin_safety"),
                "jump_calm": components.get("jump_calm"),
                "flip_calm": components.get("flip_calm"),
                "reversal_safety": components.get("reversal_safety"),
                "trend_clean": components.get("trend_clean"),
            },
        },
        "policy": {
            "level": asset_snapshot.get("late_policy_level"),
            "reason": asset_snapshot.get("late_policy_reason"),
            "bucket_s": asset_snapshot.get("late_policy_bucket_s"),
            "margin_z": asset_snapshot.get("late_policy_margin_z"),
            "override": bool(asset_snapshot.get("policy_override")),
        },
        "model": {
            "model": asset_snapshot.get("model"),
            "kou_phase": asset_snapshot.get("kou_phase"),
            "raw_kou_yes": asset_snapshot.get("raw_kou_yes"),
            "bs_yes": asset_snapshot.get("bs_yes"),
            "kou_weight": asset_snapshot.get("kou_weight"),
            "edge_pp": asset_snapshot.get("edge_pp"),
            "lam": asset_snapshot.get("lam"),
            "p_up": asset_snapshot.get("p_up"),
            "sigma_model_bp_1m": asset_snapshot.get("sigma_model_bp_1m"),
        },
        "microstructure": {
            "vol_30m_bp_1m": asset_snapshot.get("vol_30m_bp_1m"),
            "vol_1h_bp_1m": asset_snapshot.get("vol_1h_bp_1m"),
            "jump_10s_10m_rate": asset_snapshot.get("jump_10s_10m_rate"),
            "jump_10s_10m_count": asset_snapshot.get("jump_10s_10m_count"),
            "jump_30s_15m_rate": asset_snapshot.get("jump_30s_15m_rate"),
            "jump_30s_15m_count": asset_snapshot.get("jump_30s_15m_count"),
        },
    }


def compact_snapshot_for_path(captured_at_ts: float, asset_snapshot: dict[str, Any]) -> dict[str, Any]:
    delta_bps = asset_snapshot.get("delta_bps")
    sign = _sign_from_delta(delta_bps)
    return {
        "ts": float(captured_at_ts),
        "time_left_s": _optional_float(asset_snapshot.get("time_left_s")),
        "price": _optional_float(asset_snapshot.get("price")),
        "strike": _optional_float(asset_snapshot.get("strike")),
        "delta_bps": _optional_float(delta_bps),
        "side_sign": sign,
        "side": _side_name(sign),
        "signal": asset_snapshot.get("signal"),
        "kou_yes": _optional_float(asset_snapshot.get("kou_yes")),
        "margin_z": _optional_float(asset_snapshot.get("late_policy_margin_z")),
    }


def summarize_recent_path(
    history: deque[dict[str, Any]],
    *,
    now_ts: float,
    current_side: Optional[str],
    windows_s: tuple[float, ...] = SNAPSHOT_HISTORY_WINDOWS_S,
) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    current_sign = 1 if current_side == "yes" else -1 if current_side == "no" else 0
    for window_s in windows_s:
        recent = [row for row in history if now_ts - float(row.get("ts", now_ts)) <= float(window_s)]
        signs = [int(row.get("side_sign") or 0) for row in recent if int(row.get("side_sign") or 0) != 0]
        deltas = [float(row["delta_bps"]) for row in recent if row.get("delta_bps") is not None]
        margins = [float(row["margin_z"]) for row in recent if row.get("margin_z") is not None]
        kou_values = [float(row["kou_yes"]) for row in recent if row.get("kou_yes") is not None]

        cross_count = 0
        prev = None
        for sign in signs:
            if prev is not None and sign != prev:
                cross_count += 1
            prev = sign

        adverse_samples = 0
        if current_sign:
            adverse_samples = sum(1 for sign in signs if sign == -current_sign)

        label = f"last_{int(window_s)}s"
        summaries[label] = {
            "samples": len(recent),
            "cross_count": cross_count,
            "side_flip_rate": safe_float(cross_count / max(1, len(signs) - 1), 6) if len(signs) > 1 else 0.0,
            "adverse_sample_share": safe_float(adverse_samples / len(signs), 6) if signs else None,
            "min_abs_delta_bps": safe_float(min((abs(v) for v in deltas), default=math.nan), 3) if deltas else None,
            "max_abs_delta_bps": safe_float(max((abs(v) for v in deltas), default=math.nan), 3) if deltas else None,
            "delta_bps_change": safe_float(deltas[-1] - deltas[0], 3) if len(deltas) >= 2 else None,
            "margin_z_change": safe_float(margins[-1] - margins[0], 3) if len(margins) >= 2 else None,
            "max_kou_side_probability": safe_float(
                max((p if current_side == "yes" else 1.0 - p for p in kou_values), default=math.nan),
                6,
            )
            if current_side in {"yes", "no"} and kou_values
            else None,
        }
    return summaries


def extract_kou_ref(payload: dict[str, Any], asset_snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": asset_snapshot.get("symbol"),
        "bucket_end": asset_snapshot.get("bucket_end", payload.get("bucket_end")),
        "time_left_s": asset_snapshot.get("time_left_s", payload.get("time_left_s")),
        "source_age_s": asset_snapshot.get("age_s"),
        "model_age_s": asset_snapshot.get("model_age_s"),
        "display_source": asset_snapshot.get("display_source"),
        "model_source": asset_snapshot.get("model_source"),
        "price": asset_snapshot.get("price"),
        "strike": asset_snapshot.get("strike"),
        "delta_bps": asset_snapshot.get("delta_bps"),
        "signal": asset_snapshot.get("signal"),
        "signal_hold_s": asset_snapshot.get("signal_hold_s"),
        "kou_yes": asset_snapshot.get("kou_yes"),
        "bs_yes": asset_snapshot.get("bs_yes"),
        "trade_score": asset_snapshot.get("trade_score"),
        "trade_score_label": asset_snapshot.get("trade_score_label"),
        "late_policy_level": asset_snapshot.get("late_policy_level"),
        "late_policy_margin_z": asset_snapshot.get("late_policy_margin_z"),
        "model": asset_snapshot.get("model"),
        "kou_phase": asset_snapshot.get("kou_phase"),
    }


def build_market_payload(
    *,
    asset: str,
    market: Optional[MarketCandidate],
    next_market: Optional[MarketCandidate],
    kou_bucket_end: Any,
    now_ts: float,
) -> dict[str, Any]:
    status = market_status(market, now_ts)
    delta_s = market_end_delta_s(kou_bucket_end, market)
    if market is None:
        return {
            "asset": asset,
            "status": status,
            "slug": None,
            "question": None,
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
            "next_slug": None if next_market is None else next_market.slug,
            "kou_market_end_delta_s": None,
            "alignment_status": "unknown",
        }

    return {
        "asset": asset,
        "status": status,
        "slug": market.slug,
        "question": market.question,
        "start_ts": safe_float(market.start_ts, 3),
        "start_iso": utc_iso(market.start_ts),
        "end_ts": safe_float(market.end_ts, 3),
        "end_iso": utc_iso(market.end_ts),
        "token_yes": market.token_yes,
        "token_no": market.token_no,
        "yes_label": market.yes_label,
        "no_label": market.no_label,
        "accepting_orders": market.accepting_orders,
        "active": market.active,
        "closed": market.closed,
        "liquidity": safe_float(market.liquidity, 6),
        "next_slug": None if next_market is None else next_market.slug,
        "kou_market_end_delta_s": safe_float(delta_s, 3),
        "alignment_status": market_alignment_status(delta_s),
    }


def _market_event_payload(market: Optional[MarketCandidate]) -> Optional[dict[str, Any]]:
    if market is None:
        return None
    return {
        "asset": market.asset,
        "slug": market.slug,
        "question": market.question,
        "start_ts": safe_float(market.start_ts, 3),
        "start_iso": utc_iso(market.start_ts),
        "end_ts": safe_float(market.end_ts, 3),
        "end_iso": utc_iso(market.end_ts),
        "token_yes": market.token_yes,
        "token_no": market.token_no,
        "yes_label": market.yes_label,
        "no_label": market.no_label,
        "accepting_orders": market.accepting_orders,
        "active": market.active,
        "closed": market.closed,
        "liquidity": safe_float(market.liquidity, 6),
    }


class PolymarketQuoteCapture:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.requested_assets = parse_assets(args.assets)
        self.grid_thresholds = parse_float_list(args.grid_thresholds, min_value=0.0, max_value=1.0)
        self.grid_hold_seconds = parse_int_list(args.grid_hold_seconds, min_value=1)
        self.session = resolve_output_session(Path(args.output_root), args.session_id)
        self.meta_path = self.session.output_dir / "polymarket_meta.json"
        self.quotes_path = self.session.output_dir / "polymarket_quotes.jsonl"
        self.events_path = self.session.output_dir / "polymarket_events.jsonl"
        self.markets_path = self.session.output_dir / "polymarket_markets.jsonl"
        self.grid_signals_path = self.session.output_dir / "polymarket_grid_signals.jsonl"
        self.shadow_candidate = load_shadow_candidate(getattr(args, "shadow_candidate", None))
        self.shadow_orders_path = self.session.output_dir / "shadow_orders.jsonl"
        self.shadow_settlements_path = self.session.output_dir / "shadow_order_settlements.jsonl"
        self.sniper_mode = str(getattr(args, "sniper_mode", "off") or "off")
        self.sniper_signals_path = self.session.output_dir / "sniper_signals.jsonl"
        self.sniper_plans_path = self.session.output_dir / "sniper_plans.jsonl"
        self.sniper_results_path = self.session.output_dir / "sniper_results.jsonl"
        self.sniper_ledger_path = self.session.output_dir / "sniper_live_ledger.jsonl"
        self.bucket_outcomes_path = self.session.output_dir / "bucket_outcomes.jsonl"
        self.market_states: dict[str, AssetMarketState] = {}
        self.grid_states: dict[tuple[str, str, float], GridThresholdState] = {}
        self.grid_triggered: set[tuple[str, str, float, int]] = set()
        self.shadow_ordered_buckets: set[tuple[str, str]] = set()
        self.shadow_pending_orders: dict[str, dict[str, Any]] = {}
        self.shadow_settled_orders: set[str] = set()
        self.snapshot_history: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=SNAPSHOT_HISTORY_MAXLEN))
        self.client: Optional[Any] = None
        self.client_address: Optional[str] = None
        self.next_login_attempt_ts = 0.0
        self.started_at_ts = time.time()
        self._running = True

    def request_stop(self, *_args: Any) -> None:
        self._running = False

    def write_meta(self, stopped_at_ts: Optional[float] = None) -> None:
        payload = {
            "session_id": self.session.session_id,
            "session_mode": self.session.mode,
            "started_at_ts": safe_float(self.started_at_ts, 3),
            "started_at_iso": utc_iso(self.started_at_ts),
            "url": self.args.url,
            "env_file": self.args.env_file,
            "assets": self.args.assets,
            "paper_size": self.args.paper_size,
            "capture_profile": getattr(self.args, "capture_profile", "full"),
            "validation_profile": bool(getattr(self.args, "validation_profile", False)),
            "grid_thresholds": self.grid_thresholds,
            "grid_hold_seconds": self.grid_hold_seconds,
            "grid_window_seconds": self.args.grid_window_seconds,
            "fine_window_seconds": self.args.fine_window_seconds,
            "fine_seconds": self.args.fine_seconds,
            "coarse_seconds": self.args.coarse_seconds,
            "discover_seconds": self.args.discover_seconds,
            "slug_probe_seconds": self.args.slug_probe_seconds,
            "market_limit": self.args.market_limit,
            "max_runtime_seconds": self.args.max_runtime_seconds,
            "mode": "read_only_paper_taker_ask",
            "mock_polymarket": bool(self.args.mock_polymarket),
            "schema_note": (
                "polymarket quotes store compact Kou references; grid signals include decision context and recent path summaries"
            ),
            "output_files": {
                "quotes": self.quotes_path.name,
                "events": self.events_path.name,
                "markets": self.markets_path.name,
                "grid_signals": self.grid_signals_path.name,
            },
            "shadow_execution": {
                "enabled": self.shadow_candidate is not None,
                "mode": "read_only_shadow_no_order",
                "candidate_name": None if self.shadow_candidate is None else self.shadow_candidate["name"],
                "candidate_path": None if self.shadow_candidate is None else self.shadow_candidate["path"],
                "orders_file": self.shadow_orders_path.name if self.shadow_candidate is not None else None,
                "settlements_file": self.shadow_settlements_path.name if self.shadow_candidate is not None else None,
                "de_dupe": "one_shadow_order_per_symbol_bucket",
            },
            "sniper_handoff": {
                "mode": self.sniper_mode,
                "signals_file": self.sniper_signals_path.name if self.sniper_mode != "off" else None,
                "plans_file": self.sniper_plans_path.name if self.sniper_mode in {"dry-run", "live"} else None,
                "results_file": self.sniper_results_path.name if self.sniper_mode == "live" else None,
                "ledger_file": self.sniper_ledger_path.name if self.sniper_mode in {"dry-run", "live"} else None,
                "order_size": getattr(self.args, "sniper_order_size", None),
                "max_order_cost": getattr(self.args, "sniper_max_order_cost", None),
                "max_session_cost": getattr(self.args, "sniper_max_session_cost", None),
                "max_session_orders": getattr(self.args, "sniper_max_session_orders", None),
                "live_ack": bool(getattr(self.args, "sniper_live_ack", False)),
            },
            "cwd": str(Path.cwd()),
            "git_revision": git_revision(Path.cwd()),
            "python": sys.version,
        }
        if self.shadow_candidate is not None:
            payload["output_files"]["shadow_orders"] = self.shadow_orders_path.name
            payload["output_files"]["shadow_settlements"] = self.shadow_settlements_path.name
        if self.sniper_mode != "off":
            payload["output_files"]["sniper_signals"] = self.sniper_signals_path.name
        if self.sniper_mode in {"dry-run", "live"}:
            payload["output_files"]["sniper_plans"] = self.sniper_plans_path.name
            payload["output_files"]["sniper_ledger"] = self.sniper_ledger_path.name
        if self.sniper_mode == "live":
            payload["output_files"]["sniper_results"] = self.sniper_results_path.name
        if stopped_at_ts is not None:
            payload["stopped_at_ts"] = safe_float(stopped_at_ts, 3)
            payload["stopped_at_iso"] = utc_iso(stopped_at_ts)
        write_json(self.meta_path, payload)

    def emit_event(self, handle, event_type: str, **payload: Any) -> None:
        now_ts = time.time()
        append_jsonl(
            handle,
            {
                "session_id": self.session.session_id,
                "ts": safe_float(now_ts, 3),
                "iso_utc": utc_iso(now_ts),
                "event_type": event_type,
                **payload,
            },
        )

    def ensure_client(self, events_handle) -> None:
        now_ts = time.time()
        if self.client is not None or now_ts < self.next_login_attempt_ts:
            return

        if self.args.mock_polymarket:
            self.client = MockPolymarketClient()
            self.client_address = "mock"
            self.emit_event(events_handle, "login_success", address="mock", mock=True)
            return

        try:
            settings = load_poly_settings(self.args.env_file)
            self.client, self.client_address = login_clob_client(settings)
        except Exception as exc:
            self.next_login_attempt_ts = now_ts + 30.0
            self.emit_event(events_handle, "login_error", error=str(exc), retry_after_s=30.0)
            return

        self.emit_event(events_handle, "login_success", address=self.client_address)

    def discover_asset(self, asset: str, now_ts: float, events_handle, markets_handle) -> None:
        state = self.market_states.setdefault(asset, AssetMarketState())
        current = state.current_market

        # Hold the same Polymarket 5m market for its whole window. Discovery is
        # only needed on startup, after rollover, or when the stored market is not usable.
        if current is not None and current.start_ts <= now_ts < current.end_ts and not current.closed:
            return

        if current is not None and now_ts < current.start_ts:
            return

        discovery_due = current is None or now_ts >= current.end_ts or current.closed
        if not discovery_due and (now_ts - state.last_discover_ts) < max(0.5, float(self.args.discover_seconds)):
            return

        try:
            if self.args.mock_polymarket:
                discovery = mock_current_and_next_5m_markets(asset, now_ts)
            else:
                force_slug_probe = current is None or current.closed or now_ts >= current.end_ts
                allow_slug_probe = force_slug_probe or (
                    (now_ts - state.last_slug_probe_ts) >= max(2.0, float(self.args.slug_probe_seconds))
                )
                if allow_slug_probe:
                    discovery = discover_slug_first_current_and_next_5m_markets(
                        asset,
                        now_ts,
                        market_limit=max(50, int(self.args.market_limit)),
                        slug_timeout=5.0,
                    )
                else:
                    discovery = discover_current_and_next_5m_markets(
                        asset,
                        now_ts,
                        market_limit=max(50, int(self.args.market_limit)),
                        allow_slug_probe=False,
                    )
                if discovery.used_slug_probe:
                    state.last_slug_probe_ts = now_ts
        except Exception as exc:
            state.last_discover_ts = now_ts
            self.emit_event(events_handle, "market_discovery_error", asset=asset, error=str(exc))
            return

        old_slug = state.current_market.slug if state.current_market else None
        new_slug = discovery.current.slug if discovery.current else None
        state.current_market = discovery.current
        state.next_market = discovery.next_market
        state.last_discover_ts = now_ts

        if old_slug != new_slug:
            payload = {
                "session_id": self.session.session_id,
                "ts": safe_float(now_ts, 3),
                "iso_utc": utc_iso(now_ts),
                "event_type": "market_switch",
                "asset": asset,
                "previous_slug": old_slug,
                "current": _market_event_payload(discovery.current),
                "next": _market_event_payload(discovery.next_market),
                "list_count": discovery.list_count,
                "probe_count": discovery.probe_count,
                "used_slug_probe": discovery.used_slug_probe,
            }
            append_jsonl(markets_handle, payload)
            self.emit_event(events_handle, "market_switch", asset=asset, previous_slug=old_slug, current_slug=new_slug)

    def fetch_quote_inputs(
        self,
        *,
        asset: str,
        market: Optional[MarketCandidate],
        status: str,
        events_handle,
    ) -> tuple[
        Optional[BookTop],
        Optional[BookTop],
        Optional[dict[str, Optional[float]]],
        Optional[dict[str, Optional[float]]],
        dict[str, Any],
    ]:
        started_ts = time.time()
        timing = {
            "started_at_ts": safe_float(started_ts, 3),
            "started_at_iso": utc_iso(started_ts),
            "completed_at_ts": safe_float(started_ts, 3),
            "completed_at_iso": utc_iso(started_ts),
            "latency_s": 0.0,
        }
        if self.client is None or market is None or status != "LIVE":
            return None, None, None, None, timing

        results: dict[str, Any] = {
            "yes_top": None,
            "no_top": None,
            "yes_prices": None,
            "no_prices": None,
        }

        tasks = {
            "yes_top": (fetch_book_top, (self.client, market.token_yes), "book_fetch_error", "yes"),
            "no_top": (fetch_book_top, (self.client, market.token_no), "book_fetch_error", "no"),
            "yes_prices": (fetch_token_buy_price, (self.client, market.token_yes), "token_price_error", "yes"),
            "no_prices": (fetch_token_buy_price, (self.client, market.token_no), "token_price_error", "no"),
        }

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(func, *call_args): (name, event_type, side)
                for name, (func, call_args, event_type, side) in tasks.items()
            }
            for future in concurrent.futures.as_completed(futures):
                name, event_type, side = futures[future]
                try:
                    results[name] = future.result()
                except Exception as exc:
                    self.emit_event(events_handle, event_type, asset=asset, slug=market.slug, side=side, error=str(exc))

        completed_ts = time.time()
        timing = {
            "started_at_ts": safe_float(started_ts, 3),
            "started_at_iso": utc_iso(started_ts),
            "completed_at_ts": safe_float(completed_ts, 3),
            "completed_at_iso": utc_iso(completed_ts),
            "latency_s": safe_float(completed_ts - started_ts, 6),
        }

        return results["yes_top"], results["no_top"], results["yes_prices"], results["no_prices"], timing

    def evaluate_grid(
        self,
        *,
        record: dict[str, Any],
        asset: str,
        market: Optional[MarketCandidate],
        book: dict[str, Any],
        token_prices: dict[str, Any],
    ) -> list[dict[str, Any]]:
        kou_ref = record["kou_ref"]
        bucket_end = kou_ref.get("bucket_end")
        if bucket_end is None:
            return []

        try:
            time_left_s = float(kou_ref.get("time_left_s"))
        except (TypeError, ValueError):
            return []
        if time_left_s > float(self.args.grid_window_seconds):
            return []

        captured_at_ts = float(record["session"]["captured_at_ts"])
        symbol = str(kou_ref.get("symbol") or asset)
        bucket_key = str(bucket_end)
        triggered_events: list[dict[str, Any]] = []

        for threshold in self.grid_thresholds:
            side, side_probability = _probability_side(kou_ref.get("kou_yes"), threshold)
            state_key = (symbol, bucket_key, threshold)
            state = self.grid_states.get(state_key)
            if state is None:
                state = GridThresholdState(bucket_key=bucket_key)
                self.grid_states[state_key] = state

            if side is None:
                state.side = None
                state.streak_start_ts = None
                state.last_seen_ts = None
                continue

            if state.side != side or state.streak_start_ts is None:
                state.side = side
                state.streak_start_ts = captured_at_ts
            state.last_seen_ts = captured_at_ts
            hold_elapsed_s = max(0.0, captured_at_ts - float(state.streak_start_ts))

            for hold_s in self.grid_hold_seconds:
                trigger_key = (symbol, bucket_key, threshold, hold_s)
                if trigger_key in self.grid_triggered or hold_elapsed_s < float(hold_s):
                    continue

                price_size = _side_price_and_size(side=side, book=book, token_prices=token_prices)
                fill = _fill_status_from_size(
                    entry_price=price_size["entry_price"],
                    ask_size=price_size["book_ask_size"],
                    paper_size=float(self.args.paper_size),
                )
                self.grid_triggered.add(trigger_key)
                event = {
                    "session": record["session"],
                    "asset": asset,
                    "symbol": symbol,
                    "bucket_end": bucket_end,
                    "bucket_end_iso": None if bucket_end is None else utc_iso(float(bucket_end)),
                    "time_left_s": kou_ref.get("time_left_s"),
                    "source_age_s": kou_ref.get("source_age_s"),
                    "model_age_s": kou_ref.get("model_age_s"),
                    "display_source": kou_ref.get("display_source"),
                    "model_source": kou_ref.get("model_source"),
                    "price": kou_ref.get("price"),
                    "strike": kou_ref.get("strike"),
                    "delta_bps": kou_ref.get("delta_bps"),
                    "market_slug": None if market is None else market.slug,
                    "market_end_ts": None if market is None else safe_float(market.end_ts, 3),
                    "rule": {
                        "threshold": safe_float(threshold, 4),
                        "hold_seconds": hold_s,
                        "window_seconds": safe_float(self.args.grid_window_seconds, 3),
                    },
                    "trigger": {
                        "side": side,
                        "side_probability": safe_float(side_probability, 6),
                        "kou_yes": kou_ref.get("kou_yes"),
                        "hold_elapsed_s": safe_float(hold_elapsed_s, 3),
                        "streak_start_ts": safe_float(state.streak_start_ts, 3),
                        "streak_start_iso": None if state.streak_start_ts is None else utc_iso(float(state.streak_start_ts)),
                    },
                    "decision_context": record.get("grid_context", {}),
                    "pre_trigger_path": summarize_recent_path(
                        self.snapshot_history.get(symbol, deque()),
                        now_ts=captured_at_ts,
                        current_side=side,
                    ),
                    "observed_token": {
                        **price_size,
                        **fill,
                        "pnl_per_share_if_win": None
                        if price_size["entry_price"] is None
                        else safe_float(1.0 - float(price_size["entry_price"]), 6),
                        "pnl_per_share_if_loss": None
                        if price_size["entry_price"] is None
                        else safe_float(-float(price_size["entry_price"]), 6),
                    },
                }
                triggered_events.append(event)

        return triggered_events

    def maybe_emit_sniper_handoff(
        self,
        *,
        order: dict[str, Any],
        sniper_signals_handle,
        sniper_plans_handle,
        sniper_results_handle,
        events_handle,
    ) -> None:
        if self.sniper_mode == "off":
            return

        sniper_signal = build_sniper_signal_from_shadow_order(order)
        if sniper_signal is None:
            self.emit_event(events_handle, "sniper_signal_build_failed", shadow_order_id=order.get("shadow_order_id"))
            return

        append_jsonl(
            sniper_signals_handle,
            {
                "event_type": "sniper_signal",
                "session_id": self.session.session_id,
                "ts": safe_float(time.time(), 3),
                "iso_utc": utc_iso(time.time()),
                "shadow_order_id": order.get("shadow_order_id"),
                "signal": sniper_signal,
            },
        )

        if self.sniper_mode == "signal":
            return

        try:
            import polymarket_token_sniper as sniper

            signal_obj = sniper.KouBuySignal.from_mapping(sniper_signal)
            limits = sniper.SniperLimits(
                order_size=max(0.0, float(getattr(self.args, "sniper_order_size", 1.0))),
                max_order_cost=max(0.0, float(getattr(self.args, "sniper_max_order_cost", 1.0))),
                max_session_cost=max(0.0, float(getattr(self.args, "sniper_max_session_cost", 4.0))),
                max_session_orders=max(0, int(getattr(self.args, "sniper_max_session_orders", 4))),
                max_entry_price=max(0.0, min(1.0, float(getattr(self.args, "sniper_max_entry_price", 0.98)))),
                max_source_age_s=max(0.0, float(getattr(self.args, "sniper_max_source_age_s", 3.0))),
                max_model_age_s=max(0.0, float(getattr(self.args, "sniper_max_model_age_s", 3.0))),
                max_book_endpoint_delta=max(0.0, float(getattr(self.args, "sniper_max_book_endpoint_delta", 0.03))),
                min_visible_ask_size=max(0.0, float(getattr(self.args, "sniper_min_visible_ask_size", 1.0))),
            )
            plan = sniper.build_dry_run_plan(
                signal_obj,
                limits,
                env_file=self.args.env_file,
                ledger_path=str(self.sniper_ledger_path),
                require_geoblock_clear=bool(
                    getattr(self.args, "sniper_require_geoblock_clear", False) or self.sniper_mode == "live"
                ),
            )
            append_jsonl(
                sniper_plans_handle,
                {
                    "event_type": "sniper_plan",
                    "session_id": self.session.session_id,
                    "ts": safe_float(time.time(), 3),
                    "iso_utc": utc_iso(time.time()),
                    "shadow_order_id": order.get("shadow_order_id"),
                    "plan": asdict(plan),
                },
            )
            if self.sniper_mode != "live":
                return

            result = sniper.submit_live_order(
                plan,
                env_file=self.args.env_file,
                ledger_path=str(self.sniper_ledger_path),
                order_type=str(getattr(self.args, "sniper_order_type", "FOK") or "FOK"),
                live_ack=bool(getattr(self.args, "sniper_live_ack", False)),
            )
            append_jsonl(
                sniper_results_handle,
                {
                    "event_type": "sniper_live_result",
                    "session_id": self.session.session_id,
                    "ts": safe_float(time.time(), 3),
                    "iso_utc": utc_iso(time.time()),
                    "shadow_order_id": order.get("shadow_order_id"),
                    "result": asdict(result),
                },
            )
        except Exception as exc:
            logging.exception("Sniper handoff failed")
            self.emit_event(
                events_handle,
                "sniper_handoff_error",
                shadow_order_id=order.get("shadow_order_id"),
                sniper_mode=self.sniper_mode,
                error=f"{type(exc).__name__}: {exc}",
            )
            if self.sniper_mode == "live":
                self._running = False

    def maybe_emit_shadow_order(
        self,
        grid_event: dict[str, Any],
        shadow_orders_handle,
        sniper_signals_handle=None,
        sniper_plans_handle=None,
        sniper_results_handle=None,
        events_handle=None,
    ) -> None:
        if self.shadow_candidate is None or shadow_orders_handle is None:
            return

        symbol = str(grid_event.get("symbol") or "")
        key = bucket_key(grid_event.get("bucket_end"))
        if not symbol or not key:
            return

        ordered_key = (symbol, key)
        if ordered_key in self.shadow_ordered_buckets:
            return

        candidate_row = grid_event_candidate_row(grid_event)
        try:
            decision = self.shadow_candidate["score_grid_event"](candidate_row)
        except Exception:
            logging.exception("Shadow candidate scoring failed")
            return
        if not isinstance(decision, dict) or not bool(decision.get("allow_trade")):
            return

        try:
            order = build_shadow_order(
                event=grid_event,
                candidate=self.shadow_candidate,
                candidate_row=candidate_row,
                decision=decision,
                paper_size=float(self.args.paper_size),
            )
        except Exception:
            logging.exception("Shadow order build failed")
            return
        if order is None:
            return

        append_jsonl(shadow_orders_handle, order)
        self.shadow_ordered_buckets.add(ordered_key)
        self.shadow_pending_orders[str(order["shadow_order_id"])] = order
        if events_handle is not None and sniper_signals_handle is not None:
            self.maybe_emit_sniper_handoff(
                order=order,
                sniper_signals_handle=sniper_signals_handle,
                sniper_plans_handle=sniper_plans_handle,
                sniper_results_handle=sniper_results_handle,
                events_handle=events_handle,
            )

    def settle_shadow_orders(self, shadow_settlements_handle) -> None:
        if self.shadow_candidate is None or shadow_settlements_handle is None or not self.shadow_pending_orders:
            return

        outcomes = load_bucket_outcomes(self.bucket_outcomes_path)
        if not outcomes:
            return

        for order_id, order in list(self.shadow_pending_orders.items()):
            if order_id in self.shadow_settled_orders:
                continue
            source = order.get("source_grid_event") or {}
            symbol = str(source.get("symbol") or "")
            key = bucket_key(source.get("bucket_end"))
            if not symbol or not key:
                continue
            outcome = outcomes.get((symbol, key))
            if outcome is None or not outcome.get("complete"):
                continue

            append_jsonl(shadow_settlements_handle, build_shadow_settlement(order, outcome))
            self.shadow_settled_orders.add(order_id)
            self.shadow_pending_orders.pop(order_id, None)

    def remember_snapshot(self, *, symbol: str, captured_at_ts: float, asset_snapshot: dict[str, Any]) -> None:
        self.snapshot_history[symbol].append(compact_snapshot_for_path(captured_at_ts, asset_snapshot))

    def build_quote_record(
        self,
        *,
        payload: dict[str, Any],
        asset: str,
        asset_snapshot: dict[str, Any],
        captured_at_ts: float,
        capture_interval_s: float,
        market: Optional[MarketCandidate],
        next_market: Optional[MarketCandidate],
        yes_top: Optional[BookTop],
        no_top: Optional[BookTop],
        yes_prices: Optional[dict[str, Optional[float]]],
        no_prices: Optional[dict[str, Optional[float]]],
        quote_fetch: dict[str, Any],
    ) -> dict[str, Any]:
        kou_ref = extract_kou_ref(payload, asset_snapshot)
        market_payload = build_market_payload(
            asset=asset,
            market=market,
            next_market=next_market,
            kou_bucket_end=kou_ref["bucket_end"],
            now_ts=captured_at_ts,
        )
        book = build_book_payload(yes_top, no_top)
        token_prices = build_token_prices_payload(yes_prices, no_prices)
        paper_fill = classify_paper_fill(
            signal_state=kou_ref["signal"],
            safety_label=kou_ref["trade_score_label"],
            status=market_payload["status"],
            accepting_orders=bool(market_payload["accepting_orders"]),
            yes_ask=book["yes"]["ask"],
            yes_ask_size=book["yes"]["ask_size"],
            no_ask=book["no"]["ask"],
            no_ask_size=book["no"]["ask_size"],
            yes_buy_price=token_prices["yes"]["buy_price"],
            no_buy_price=token_prices["no"]["buy_price"],
            paper_size=float(self.args.paper_size),
        )
        return {
            "session": {
                "id": self.session.session_id,
                "captured_at_ts": safe_float(captured_at_ts, 3),
                "captured_at_iso": utc_iso(captured_at_ts),
                "capture_interval_s": safe_float(capture_interval_s, 3),
            },
            "kou_ref": kou_ref,
            "grid_context": extract_grid_context(asset_snapshot),
            "polymarket_market": market_payload,
            "book": book,
            "token_prices": token_prices,
            "quote_fetch": quote_fetch,
            "paper_fill": paper_fill,
        }

    def run(self) -> int:
        self.write_meta()
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)

        with (
            self.quotes_path.open("a", encoding="utf-8") as quotes_handle,
            self.events_path.open("a", encoding="utf-8") as events_handle,
            self.markets_path.open("a", encoding="utf-8") as markets_handle,
            self.grid_signals_path.open("a", encoding="utf-8") as grid_handle,
            self.shadow_orders_path.open("a", encoding="utf-8") if self.shadow_candidate is not None else open(os.devnull, "w", encoding="utf-8") as shadow_orders_handle,
            self.shadow_settlements_path.open("a", encoding="utf-8") if self.shadow_candidate is not None else open(os.devnull, "w", encoding="utf-8") as shadow_settlements_handle,
            self.sniper_signals_path.open("a", encoding="utf-8") if self.sniper_mode != "off" else open(os.devnull, "w", encoding="utf-8") as sniper_signals_handle,
            self.sniper_plans_path.open("a", encoding="utf-8") if self.sniper_mode in {"dry-run", "live"} else open(os.devnull, "w", encoding="utf-8") as sniper_plans_handle,
            self.sniper_results_path.open("a", encoding="utf-8") if self.sniper_mode == "live" else open(os.devnull, "w", encoding="utf-8") as sniper_results_handle,
        ):
            self.emit_event(
                events_handle,
                "session_start",
                session_mode=self.session.mode,
                output_dir=str(self.session.output_dir),
                shadow_execution_enabled=self.shadow_candidate is not None,
                shadow_candidate_name=None if self.shadow_candidate is None else self.shadow_candidate["name"],
                sniper_mode=self.sniper_mode,
            )

            while self._running:
                if self.args.max_runtime_seconds is not None:
                    if time.time() - self.started_at_ts >= float(self.args.max_runtime_seconds):
                        break

                self.ensure_client(events_handle)

                try:
                    payload = fetch_snapshot(self.args.url)
                except urllib.error.URLError as exc:
                    self.emit_event(events_handle, "snapshot_fetch_error", error=str(exc))
                    time.sleep(2.0)
                    continue
                except Exception as exc:
                    self.emit_event(events_handle, "snapshot_parse_error", error=str(exc))
                    time.sleep(2.0)
                    continue

                captured_at_ts = time.time()
                capture_interval_s = extract_capture_interval(
                    payload,
                    fine_window_s=float(self.args.fine_window_seconds),
                    fine_s=float(self.args.fine_seconds),
                    coarse_s=float(self.args.coarse_seconds),
                )
                selected_assets = selected_snapshot_assets(payload, self.requested_assets)
                if not selected_assets:
                    self.emit_event(events_handle, "no_selected_assets", requested_assets=self.args.assets)

                for asset, asset_snapshot in selected_assets:
                    symbol = str(asset_snapshot.get("symbol") or asset)
                    self.remember_snapshot(symbol=symbol, captured_at_ts=captured_at_ts, asset_snapshot=asset_snapshot)
                    self.discover_asset(asset, captured_at_ts, events_handle, markets_handle)
                    state = self.market_states.setdefault(asset, AssetMarketState())
                    market = state.current_market
                    next_market = state.next_market
                    status = market_status(market, captured_at_ts)
                    yes_top, no_top, yes_prices, no_prices, quote_fetch = self.fetch_quote_inputs(
                        asset=asset,
                        market=market,
                        status=status,
                        events_handle=events_handle,
                    )
                    record = self.build_quote_record(
                        payload=payload,
                        asset=asset,
                        asset_snapshot=asset_snapshot,
                        captured_at_ts=captured_at_ts,
                        capture_interval_s=capture_interval_s,
                        market=market,
                        next_market=next_market,
                        yes_top=yes_top,
                        no_top=no_top,
                        yes_prices=yes_prices,
                        no_prices=no_prices,
                        quote_fetch=quote_fetch,
                    )
                    append_jsonl(quotes_handle, record)
                    for grid_event in self.evaluate_grid(
                        record=record,
                        asset=asset,
                        market=market,
                        book=record["book"],
                        token_prices=record["token_prices"],
                    ):
                        append_jsonl(grid_handle, grid_event)
                        self.maybe_emit_shadow_order(
                            grid_event,
                            shadow_orders_handle,
                            sniper_signals_handle,
                            sniper_plans_handle,
                            sniper_results_handle,
                            events_handle,
                        )

                self.settle_shadow_orders(shadow_settlements_handle)

                time.sleep(align_sleep(capture_interval_s))

            self.settle_shadow_orders(shadow_settlements_handle)
            self.emit_event(events_handle, "session_stop")

        self.write_meta(stopped_at_ts=time.time())
        return 0


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> int:
    args = apply_validation_profile(parse_args())
    setup_logging(args.verbose)
    capture = PolymarketQuoteCapture(args)
    return capture.run()


if __name__ == "__main__":
    raise SystemExit(main())
