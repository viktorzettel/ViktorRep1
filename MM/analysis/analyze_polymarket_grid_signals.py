#!/usr/bin/env python3
"""
Analyze Polymarket grid-trigger captures.

This joins `polymarket_grid_signals.jsonl` with `bucket_outcomes.jsonl` and
produces a threshold x hold-seconds matrix for observed entry prices, paper
fillability, success rate, and estimated gain.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze Polymarket grid signal captures")
    parser.add_argument("--input-root", default="data/live_capture", help="Live capture root")
    parser.add_argument(
        "--output-dir",
        default="data/live_capture/polymarket_grid_analysis",
        help="Directory for matrix outputs",
    )
    parser.add_argument("--session-id", default=None, help="Analyze only one session id")
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
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


def safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def bucket_key(value: Any) -> Optional[str]:
    number = safe_float(value)
    if number is None:
        return None
    return f"{number:.3f}"


def time_left_bucket(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "unknown"
    if number <= 10.0:
        return "00-10s"
    if number <= 30.0:
        return "10-30s"
    if number <= 60.0:
        return "30-60s"
    if number <= 90.0:
        return "60-90s"
    return "90s+"


def discover_sessions(input_root: Path, session_id: Optional[str]) -> list[Path]:
    if session_id:
        path = input_root / session_id
        return [path] if path.exists() else []
    sessions = []
    for path in sorted(input_root.iterdir() if input_root.exists() else []):
        if path.is_dir() and (path / "polymarket_grid_signals.jsonl").exists():
            sessions.append(path)
    return sessions


def load_outcomes(session_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in read_jsonl(session_dir / "bucket_outcomes.jsonl"):
        key = bucket_key(row.get("bucket_end"))
        symbol = str(row.get("symbol") or "")
        if not key or not symbol:
            continue
        out[(symbol, key)] = row
    return out


def enrich_event(event: dict[str, Any], outcome: Optional[dict[str, Any]]) -> dict[str, Any]:
    observed = event.get("observed_token") or {}
    trigger = event.get("trigger") or {}
    rule = event.get("rule") or {}
    session = event.get("session") or {}
    decision_context = event.get("decision_context") or {}
    safety_context = decision_context.get("safety") or {}
    policy_context = decision_context.get("policy") or {}
    path_context = event.get("pre_trigger_path") or {}
    side = trigger.get("side")
    entry_price = safe_float(observed.get("entry_price"))
    fillable_size = safe_float(observed.get("fillable_size"))
    estimated_cost = safe_float(observed.get("estimated_cost"))
    settled_side = None if outcome is None else outcome.get("settled_side")
    known_outcome = outcome is not None and bool(outcome.get("complete")) and settled_side in {"yes", "no", "flat"}
    win = known_outcome and side in {"yes", "no"} and side == settled_side

    pnl_per_share = None
    roi = None
    estimated_pnl = None
    if known_outcome and entry_price is not None and side in {"yes", "no"}:
        pnl_per_share = (1.0 - entry_price) if win else -entry_price
        if entry_price > 0.0:
            roi = pnl_per_share / entry_price
        if fillable_size is not None and fillable_size > 0.0:
            estimated_pnl = fillable_size * pnl_per_share

    return {
        "session_id": session.get("id"),
        "captured_at_iso": session.get("captured_at_iso"),
        "asset": event.get("asset"),
        "symbol": event.get("symbol"),
        "bucket_end": event.get("bucket_end"),
        "bucket_end_iso": event.get("bucket_end_iso"),
        "time_left_s": event.get("time_left_s"),
        "market_slug": event.get("market_slug"),
        "threshold": rule.get("threshold"),
        "hold_seconds": rule.get("hold_seconds"),
        "side": side,
        "side_probability": trigger.get("side_probability"),
        "kou_yes": trigger.get("kou_yes"),
        "entry_price": entry_price,
        "entry_price_source": observed.get("entry_price_source"),
        "fill_status": observed.get("fill_status"),
        "fillable_size": fillable_size,
        "estimated_cost": estimated_cost,
        "book_ask_size": observed.get("book_ask_size"),
        "endpoint_buy_price": observed.get("endpoint_buy_price"),
        "time_left_bucket": time_left_bucket(event.get("time_left_s")),
        "safety_label": safety_context.get("final_label"),
        "safety_score": safety_context.get("final_score"),
        "safety_weakest_component": safety_context.get("weakest_component"),
        "policy_level": policy_context.get("level"),
        "policy_margin_z": policy_context.get("margin_z"),
        "policy_override": policy_context.get("override"),
        "path_15s_cross_count": (path_context.get("last_15s") or {}).get("cross_count"),
        "path_15s_adverse_share": (path_context.get("last_15s") or {}).get("adverse_sample_share"),
        "path_15s_margin_z_change": (path_context.get("last_15s") or {}).get("margin_z_change"),
        "path_30s_cross_count": (path_context.get("last_30s") or {}).get("cross_count"),
        "path_30s_adverse_share": (path_context.get("last_30s") or {}).get("adverse_sample_share"),
        "path_30s_margin_z_change": (path_context.get("last_30s") or {}).get("margin_z_change"),
        "path_60s_cross_count": (path_context.get("last_60s") or {}).get("cross_count"),
        "path_60s_adverse_share": (path_context.get("last_60s") or {}).get("adverse_sample_share"),
        "path_60s_margin_z_change": (path_context.get("last_60s") or {}).get("margin_z_change"),
        "settled_side": settled_side,
        "known_outcome": known_outcome,
        "win": win if known_outcome else None,
        "pnl_per_share": pnl_per_share,
        "roi": roi,
        "estimated_pnl": estimated_pnl,
    }


def summarize(rows: list[dict[str, Any]], *, by_time_left: bool = False) -> list[dict[str, Any]]:
    groups: dict[tuple[str, float, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        asset = str(row.get("asset") or "unknown")
        threshold = safe_float(row.get("threshold"))
        hold = row.get("hold_seconds")
        if threshold is None or hold is None:
            continue
        bucket = str(row.get("time_left_bucket") or "unknown") if by_time_left else "all"
        groups[(asset, threshold, int(hold), bucket)].append(row)

    out: list[dict[str, Any]] = []
    for (asset, threshold, hold, bucket), items in sorted(groups.items()):
        trades = len(items)
        known = [row for row in items if row.get("known_outcome")]
        wins = [row for row in known if row.get("win")]
        price_rows = [row for row in known if safe_float(row.get("entry_price")) is not None]
        filled = [row for row in known if row.get("fill_status") in {"full", "partial"}]
        full = [row for row in items if row.get("fill_status") == "full"]
        partial = [row for row in items if row.get("fill_status") == "partial"]
        unknown_size = [row for row in items if row.get("fill_status") == "unknown_size"]
        no_fill = [row for row in items if row.get("fill_status") == "none"]

        total_cost = sum(safe_float(row.get("estimated_cost")) or 0.0 for row in filled)
        total_pnl = sum(safe_float(row.get("estimated_pnl")) or 0.0 for row in filled)
        entry_prices = [safe_float(row.get("entry_price")) for row in items]
        entry_prices = [value for value in entry_prices if value is not None]
        fillable_sizes = [safe_float(row.get("fillable_size")) for row in items]
        fillable_sizes = [value for value in fillable_sizes if value is not None]
        rois = [safe_float(row.get("roi")) for row in price_rows]
        rois = [value for value in rois if value is not None]

        out.append(
            {
                "asset": asset,
                "threshold": threshold,
                "hold_seconds": hold,
                "time_left_bucket": bucket,
                "trades": trades,
                "known_outcomes": len(known),
                "wins": len(wins),
                "success_rate": None if not known else len(wins) / len(known),
                "full_fills": len(full),
                "partial_fills": len(partial),
                "any_size_fills": len(full) + len(partial),
                "unknown_size": len(unknown_size),
                "no_fill": len(no_fill),
                "full_fill_rate": None if not items else len(full) / len(items),
                "partial_fill_rate": None if not items else len(partial) / len(items),
                "any_size_fill_rate": None if not items else (len(full) + len(partial)) / len(items),
                "unknown_size_rate": None if not items else len(unknown_size) / len(items),
                "no_fill_rate": None if not items else len(no_fill) / len(items),
                "avg_entry_price": None if not entry_prices else sum(entry_prices) / len(entry_prices),
                "avg_fillable_size": None if not fillable_sizes else sum(fillable_sizes) / len(fillable_sizes),
                "avg_roi": None if not rois else sum(rois) / len(rois),
                "avg_roi_pct": None if not rois else 100.0 * sum(rois) / len(rois),
                "filled_known_trades": len(filled),
                "total_cost_filled": total_cost,
                "total_pnl_filled": total_pnl,
                "total_roi_filled": None if total_cost <= 0.0 else total_pnl / total_cost,
                "total_roi_filled_pct": None if total_cost <= 0.0 else 100.0 * total_pnl / total_cost,
            }
        )
    return out


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def pivot_win_fill_matrix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, float], dict[int, dict[str, Any]]] = defaultdict(dict)
    holds = sorted({int(row["hold_seconds"]) for row in rows if row.get("time_left_bucket") == "all"})
    for row in rows:
        if row.get("time_left_bucket") != "all":
            continue
        asset = str(row.get("asset") or "unknown")
        threshold = safe_float(row.get("threshold"))
        hold = row.get("hold_seconds")
        if threshold is None or hold is None:
            continue
        grouped[(asset, threshold)][int(hold)] = row

    out: list[dict[str, Any]] = []
    for (asset, threshold), by_hold in sorted(grouped.items()):
        item: dict[str, Any] = {
            "asset": asset,
            "threshold": threshold,
            "confidence_pct": round(threshold * 100.0, 2),
        }
        for hold in holds:
            source = by_hold.get(hold) or {}
            prefix = f"h{hold}s"
            item[f"{prefix}_trades"] = source.get("trades")
            item[f"{prefix}_win_rate"] = source.get("success_rate")
            item[f"{prefix}_fill_rate"] = source.get("any_size_fill_rate")
            item[f"{prefix}_full_fill_rate"] = source.get("full_fill_rate")
            item[f"{prefix}_avg_entry"] = source.get("avg_entry_price")
            item[f"{prefix}_roi_pct"] = source.get("total_roi_filled_pct")
            item[f"{prefix}_pnl"] = source.get("total_pnl_filled")
        out.append(item)
    return out


def main() -> int:
    args = parse_args()
    input_root = Path(args.input_root)
    output_dir = Path(args.output_dir)
    sessions = discover_sessions(input_root, args.session_id)

    enriched: list[dict[str, Any]] = []
    for session_dir in sessions:
        outcomes = load_outcomes(session_dir)
        for event in read_jsonl(session_dir / "polymarket_grid_signals.jsonl"):
            symbol = str(event.get("symbol") or "")
            key = bucket_key(event.get("bucket_end"))
            outcome = outcomes.get((symbol, key)) if key else None
            enriched.append(enrich_event(event, outcome))

    matrix = summarize(enriched)
    matrix_by_time_left = summarize(enriched, by_time_left=True)
    pivot_matrix = pivot_win_fill_matrix(matrix)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "polymarket_grid_events_enriched.csv", enriched)
    write_csv(output_dir / "polymarket_grid_matrix.csv", matrix)
    write_csv(output_dir / "polymarket_grid_matrix_by_timeleft.csv", matrix_by_time_left)
    write_csv(output_dir / "polymarket_grid_matrix_pivot.csv", pivot_matrix)
    (output_dir / "analysis_summary.json").write_text(
        json.dumps(
            {
                "sessions": [path.name for path in sessions],
                "grid_events": len(enriched),
                "matrix_rows": len(matrix),
                "matrix_by_time_left_rows": len(matrix_by_time_left),
                "pivot_matrix_rows": len(pivot_matrix),
                "outputs": [
                    "polymarket_grid_events_enriched.csv",
                    "polymarket_grid_matrix.csv",
                    "polymarket_grid_matrix_by_timeleft.csv",
                    "polymarket_grid_matrix_pivot.csv",
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {len(enriched)} enriched grid events, {len(matrix)} matrix rows, "
        f"{len(matrix_by_time_left)} time-left matrix rows, and {len(pivot_matrix)} pivot rows to {output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
