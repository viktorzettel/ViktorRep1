#!/usr/bin/env python3
"""
Read-only quick health view for a live Kou + Polymarket capture session.

Use while a capture is running to verify that snapshots, Polymarket quotes,
market slugs, buy prices, ask sizes, and grid rows are flowing.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show quick health for the latest live capture session")
    parser.add_argument("--input-root", default="data/live_capture", help="Live capture root")
    parser.add_argument("--session-id", default=None, help="Session id to inspect; defaults to active/newest")
    parser.add_argument("--tail", type=int, default=300, help="Recent JSONL rows to scan per file")
    parser.add_argument("--max-quote-age-s", type=float, default=5.0, help="Warn when latest Polymarket quote is older")
    parser.add_argument("--max-snapshot-age-s", type=float, default=5.0, help="Warn when latest Kou snapshot is older")
    return parser.parse_args()


def safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def read_tail_jsonl(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines: deque[str] = deque(maxlen=max(1, limit))
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                lines.append(line)
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def resolve_session(root: Path, session_id: Optional[str]) -> tuple[Optional[Path], list[str]]:
    warnings: list[str] = []
    if session_id:
        session_dir = root / session_id
        if not session_dir.exists():
            warnings.append(f"session folder not found: {session_dir}")
        return session_dir, warnings

    candidates: list[tuple[float, Path, dict[str, Any]]] = []
    for meta_path in root.glob("*/session_meta.json"):
        meta = read_json(meta_path)
        started = safe_float(meta.get("started_at_ts")) or meta_path.stat().st_mtime
        candidates.append((started, meta_path.parent, meta))
    if not candidates:
        return None, ["no sessions found"]

    active = [
        item
        for item in candidates
        if item[2].get("stopped_at_ts") is None and item[2].get("stopped_at_iso") is None
    ]
    if len(active) > 1:
        warnings.append("multiple active sessions found; using newest active")
    chosen_pool = active or candidates
    chosen_pool.sort(key=lambda item: item[0])
    return chosen_pool[-1][1], warnings


def latest_by_asset(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        market = row.get("polymarket_market") or {}
        asset = str(market.get("asset") or "")
        if not asset:
            symbol = str((row.get("kou_ref") or {}).get("symbol") or "")
            asset = "eth" if "eth" in symbol.lower() else "xrp" if "xrp" in symbol.lower() else ""
        if not asset:
            continue
        ts = safe_float((row.get("session") or {}).get("captured_at_ts")) or 0.0
        old_ts = safe_float(((out.get(asset) or {}).get("session") or {}).get("captured_at_ts")) or -1.0
        if ts >= old_ts:
            out[asset] = row
    return out


def status_word(ok: bool) -> str:
    return "OK" if ok else "WARN"


def fmt_age(now_ts: float, ts: Optional[float]) -> str:
    if ts is None:
        return "-"
    return f"{max(0.0, now_ts - ts):.1f}s"


def main() -> int:
    args = parse_args()
    now_ts = time.time()
    root = Path(args.input_root)
    session_dir, warnings = resolve_session(root, args.session_id)
    if session_dir is None:
        print("Capture health: WARN")
        for warning in warnings:
            print(f"- {warning}")
        return 1

    meta = read_json(session_dir / "session_meta.json")
    snapshots = read_tail_jsonl(session_dir / "snapshots.jsonl", args.tail)
    quotes = read_tail_jsonl(session_dir / "polymarket_quotes.jsonl", args.tail)
    grid = read_tail_jsonl(session_dir / "polymarket_grid_signals.jsonl", args.tail)
    outcomes = read_tail_jsonl(session_dir / "bucket_outcomes.jsonl", args.tail)
    shadow_orders = read_tail_jsonl(session_dir / "shadow_orders.jsonl", args.tail)
    shadow_settlements = read_tail_jsonl(session_dir / "shadow_order_settlements.jsonl", args.tail)
    events = read_tail_jsonl(session_dir / "polymarket_events.jsonl", args.tail)

    latest_snapshot_ts = safe_float((snapshots[-1].get("session") or {}).get("captured_at_ts")) if snapshots else None
    if latest_snapshot_ts is None and snapshots:
        latest_snapshot_ts = safe_float(snapshots[-1].get("captured_at_ts")) or safe_float(snapshots[-1].get("ts"))
    latest_quote_ts = safe_float((quotes[-1].get("session") or {}).get("captured_at_ts")) if quotes else None

    snapshot_age = None if latest_snapshot_ts is None else now_ts - latest_snapshot_ts
    quote_age = None if latest_quote_ts is None else now_ts - latest_quote_ts
    ok = bool(snapshots and quotes)
    ok = ok and (snapshot_age is None or snapshot_age <= float(args.max_snapshot_age_s))
    ok = ok and (quote_age is None or quote_age <= float(args.max_quote_age_s))

    print(f"Capture health: {status_word(ok)}")
    print(f"Session: {session_dir.name}")
    if meta:
        stopped = meta.get("stopped_at_iso") or meta.get("stopped_at_ts")
        print(f"Mode: {'stopped' if stopped else 'active'}")
    for warning in warnings:
        print(f"Warning: {warning}")
    print(
        "Rows: "
        f"snapshots={count_jsonl(session_dir / 'snapshots.jsonl')} "
        f"quotes={count_jsonl(session_dir / 'polymarket_quotes.jsonl')} "
        f"grid={count_jsonl(session_dir / 'polymarket_grid_signals.jsonl')} "
        f"outcomes={count_jsonl(session_dir / 'bucket_outcomes.jsonl')} "
        f"shadow_orders={count_jsonl(session_dir / 'shadow_orders.jsonl')} "
        f"shadow_settlements={count_jsonl(session_dir / 'shadow_order_settlements.jsonl')}"
    )
    print(f"Latest ages: snapshot={fmt_age(now_ts, latest_snapshot_ts)} quote={fmt_age(now_ts, latest_quote_ts)}")

    for asset, row in sorted(latest_by_asset(quotes).items()):
        market = row.get("polymarket_market") or {}
        token_prices = row.get("token_prices") or {}
        book = row.get("book") or {}
        quote_fetch = row.get("quote_fetch") or {}
        yes_buy = ((token_prices.get("yes") or {}).get("buy_price"))
        no_buy = ((token_prices.get("no") or {}).get("buy_price"))
        yes_ask_size = ((book.get("yes") or {}).get("ask_size"))
        no_ask_size = ((book.get("no") or {}).get("ask_size"))
        slug = market.get("slug")
        status = market.get("status")
        end_ts = safe_float(market.get("end_ts"))
        time_left = "-" if end_ts is None else f"{max(0.0, end_ts - now_ts):.1f}s"
        missing = []
        if yes_buy is None:
            missing.append("yes_buy")
        if no_buy is None:
            missing.append("no_buy")
        if yes_ask_size is None:
            missing.append("yes_ask_size")
        if no_ask_size is None:
            missing.append("no_ask_size")
        print(
            f"{asset.upper()}: {status} {slug} left={time_left} "
            f"yes_buy={yes_buy} no_buy={no_buy} "
            f"yes_ask_size={yes_ask_size} no_ask_size={no_ask_size} "
            f"latency={quote_fetch.get('latency_s', '-')}"
            f"{' missing=' + ','.join(missing) if missing else ''}"
        )

    recent_errors = [
        row for row in events[-20:]
        if str(row.get("event_type") or "").endswith("_error")
    ]
    if recent_errors:
        print("Recent Polymarket errors:")
        for row in recent_errors[-5:]:
            print(f"- {row.get('event_type')} {row.get('asset', '')} {row.get('side', '')}: {row.get('error')}")
    if shadow_orders:
        latest = shadow_orders[-1]
        source = latest.get("source_grid_event") or {}
        order = latest.get("order") or {}
        print(
            "Latest shadow order: "
            f"asset={source.get('asset', '-')} prob={source.get('side_probability', '-')} "
            f"side={order.get('side', '-')} entry={order.get('entry_price', '-')} "
            f"fill={order.get('fill_status', '-')}"
        )
    if shadow_settlements:
        latest = shadow_settlements[-1]
        result = latest.get("result") or {}
        outcome = latest.get("outcome") or {}
        print(
            "Latest shadow settlement: "
            f"settled={outcome.get('settled_side', '-')} win={result.get('win', '-')} "
            f"pnl_per_share={result.get('pnl_per_share', '-')}"
        )
    print(f"Recent grid triggers scanned: {len(grid)}")
    print(f"Recent outcomes scanned: {len(outcomes)}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
