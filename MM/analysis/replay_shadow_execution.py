#!/usr/bin/env python3
"""
Replay the live shadow-execution policy on historical capture sessions.

This is intentionally close to the live shadow logger in
kou_polymarket_live_capture.py: load one candidate, scan historical grid
triggers in timestamp order, write the first candidate-approved paper order per
symbol/bucket, then settle that order from bucket_outcomes.jsonl.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import kou_polymarket_live_capture as live

DEFAULT_INPUT_ROOT = ROOT / "data/live_capture"
DEFAULT_CANDIDATE = ROOT / "analysis/autoresearch_kou/candidate.py"
DEFAULT_OUTPUT_DIR = ROOT / "data/live_capture/forensic_analysis/shadow_replay"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay shadow execution on historical Polymarket grid captures")
    parser.add_argument("--input-root", default=str(DEFAULT_INPUT_ROOT), help="Live capture root")
    parser.add_argument("--candidate", default=str(DEFAULT_CANDIDATE), help="Candidate module path")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory")
    parser.add_argument("--paper-size", type=float, default=5.0, help="Requested paper size")
    parser.add_argument("--session-id", default=None, help="Optional single session id")
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


def append_jsonl(handle, payload: dict[str, Any]) -> None:
    handle.write(json.dumps(payload, sort_keys=True) + "\n")


def safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def discover_sessions(input_root: Path, session_id: Optional[str]) -> list[Path]:
    if session_id:
        session_dir = input_root / session_id
        return [session_dir] if (session_dir / "polymarket_grid_signals.jsonl").exists() else []
    return [
        path
        for path in sorted(input_root.iterdir() if input_root.exists() else [])
        if path.is_dir()
        and (path / "polymarket_grid_signals.jsonl").exists()
        and (path / "bucket_outcomes.jsonl").exists()
    ]


def sort_key(event: dict[str, Any]) -> tuple[float, float, float, int]:
    session = event.get("session") or {}
    rule = event.get("rule") or {}
    return (
        safe_float(session.get("captured_at_ts")) or 0.0,
        safe_float(event.get("bucket_end")) or 0.0,
        safe_float(rule.get("threshold")) or 0.0,
        int(rule.get("hold_seconds") or 0),
    )


def outcome_key(row: dict[str, Any]) -> tuple[str, str] | None:
    symbol = str(row.get("symbol") or "")
    key = live.bucket_key(row.get("bucket_end"))
    if not symbol or not key:
        return None
    return symbol, key


def session_outcomes(session_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in read_jsonl(session_dir / "bucket_outcomes.jsonl"):
        key = outcome_key(row)
        if key is not None:
            out[key] = row
    return out


def session_snapshots(session_dir: Path) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_jsonl(session_dir / "snapshots.jsonl"):
        asset = row.get("asset") or {}
        market = row.get("market") or {}
        session = row.get("session") or {}
        symbol = str(asset.get("symbol") or "").lower()
        ts = safe_float(session.get("captured_at_ts"))
        if not symbol or ts is None:
            continue
        out[symbol].append(
            {
                "ts": ts,
                "price": market.get("price"),
                "strike": market.get("strike"),
                "delta_bps": market.get("delta_bps"),
                "source_age_s": asset.get("age_s"),
                "model_age_s": asset.get("model_age_s"),
                "display_source": asset.get("display_source"),
                "model_source": asset.get("model_source"),
            }
        )
    for rows in out.values():
        rows.sort(key=lambda item: float(item["ts"]))
    return dict(out)


def enrich_event_from_snapshots(
    event: dict[str, Any],
    snapshots_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    max_age_s: float = 3.0,
) -> dict[str, Any]:
    enrichment_keys = (
        "price",
        "strike",
        "delta_bps",
        "source_age_s",
        "model_age_s",
        "display_source",
        "model_source",
    )
    if all(event.get(key) is not None for key in enrichment_keys):
        return event
    symbol = str(event.get("symbol") or "").lower()
    rows = snapshots_by_symbol.get(symbol) or []
    ts = safe_float((event.get("session") or {}).get("captured_at_ts"))
    if not rows or ts is None:
        return event

    times = [float(row["ts"]) for row in rows]
    pos = bisect.bisect_left(times, ts)
    candidates = []
    if pos < len(rows):
        candidates.append(rows[pos])
    if pos > 0:
        candidates.append(rows[pos - 1])
    if not candidates:
        return event

    nearest = min(candidates, key=lambda row: abs(float(row["ts"]) - ts))
    if abs(float(nearest["ts"]) - ts) > max_age_s:
        return event

    enriched = dict(event)
    for key in enrichment_keys:
        if enriched.get(key) is None:
            enriched[key] = nearest.get(key)
    return enriched


def completed_xrp_markets(outcomes: dict[tuple[str, str], dict[str, Any]]) -> int:
    return sum(
        1
        for (symbol, _key), row in outcomes.items()
        if "xrp" in symbol.lower() and bool(row.get("complete")) and row.get("settled_side") in {"yes", "no", "flat"}
    )


def utc_hour_from_iso(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value)
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text).astimezone(timezone.utc)
    except ValueError:
        return None
    return dt.hour + dt.minute / 60.0 + dt.second / 3600.0


def time_window_utc(value: Any) -> str:
    hour = utc_hour_from_iso(value)
    if hour is None:
        return "unknown"
    if 13.5 <= hour < 20.0:
        return "us_regular"
    if 20.0 <= hour < 24.0:
        return "us_after_hours"
    if 0.0 <= hour < 6.0:
        return "late_us_asia"
    return "europe_pre_us"


def settlement_flat_row(settlement: dict[str, Any]) -> dict[str, Any]:
    source = settlement.get("source_grid_event") or {}
    order = settlement.get("order") or {}
    outcome = settlement.get("outcome") or {}
    result = settlement.get("result") or {}
    candidate = settlement.get("candidate") or {}
    decision = candidate.get("decision") or {}
    return {
        "session_id": settlement.get("session_id"),
        "shadow_order_id": settlement.get("shadow_order_id"),
        "captured_at_iso": source.get("captured_at_iso") or settlement.get("iso_utc"),
        "time_window_utc": time_window_utc(source.get("captured_at_iso") or settlement.get("iso_utc")),
        "asset": source.get("asset"),
        "symbol": source.get("symbol"),
        "bucket_end": source.get("bucket_end"),
        "bucket_end_iso": source.get("bucket_end_iso"),
        "market_slug": source.get("market_slug"),
        "time_left_s": source.get("time_left_s"),
        "threshold": source.get("threshold"),
        "hold_seconds": source.get("hold_seconds"),
        "side": order.get("side"),
        "side_probability": source.get("side_probability"),
        "entry_price": order.get("entry_price"),
        "entry_price_source": order.get("entry_price_source"),
        "fill_status": order.get("fill_status"),
        "hypothetical_fill_size": order.get("hypothetical_fill_size"),
        "book_ask_size": order.get("book_ask_size"),
        "candidate_reason": decision.get("reason"),
        "settled_side": outcome.get("settled_side"),
        "known_outcome": result.get("known_outcome"),
        "win": result.get("win"),
        "pnl_per_share": result.get("pnl_per_share"),
        "roi": result.get("roi"),
        "paper_pnl_requested_size": result.get("paper_pnl_requested_size"),
        "paper_pnl_visible_size": result.get("paper_pnl_visible_size"),
    }


def summarize_rows(rows: list[dict[str, Any]], completed_markets_by_session: dict[str, int]) -> list[dict[str, Any]]:
    session_ids = sorted(set(completed_markets_by_session) | {str(row.get("session_id")) for row in rows})
    summary: list[dict[str, Any]] = []
    for session_id in session_ids:
        items = [row for row in rows if str(row.get("session_id")) == session_id]
        known = [row for row in items if row.get("known_outcome") is True]
        wins = [row for row in known if row.get("win") is True]
        losses = [row for row in known if row.get("win") is False]
        entries = [safe_float(row.get("entry_price")) for row in items]
        entries = [value for value in entries if value is not None]
        cost = sum((safe_float(row.get("entry_price")) or 0.0) * 5.0 for row in known)
        pnl = sum(safe_float(row.get("paper_pnl_requested_size")) or 0.0 for row in known)
        completed = completed_markets_by_session.get(session_id, 0)
        summary.append(
            {
                "session_id": session_id,
                "completed_xrp_markets": completed,
                "shadow_orders": len(items),
                "known_orders": len(known),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": None if not known else len(wins) / len(known),
                "skip_rate_xrp_markets": None if completed <= 0 else 1.0 - (len(items) / completed),
                "avg_entry": None if not entries else sum(entries) / len(entries),
                "paper_cost_requested_size": cost,
                "paper_pnl_requested_size": pnl,
                "paper_roi_requested_size": None if cost <= 0 else pnl / cost,
            }
        )
    return summary


def summarize_group(rows: list[dict[str, Any]], group_key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(group_key) or "unknown")].append(row)
    out: list[dict[str, Any]] = []
    for group, items in sorted(groups.items()):
        known = [row for row in items if row.get("known_outcome") is True]
        wins = [row for row in known if row.get("win") is True]
        losses = [row for row in known if row.get("win") is False]
        entries = [safe_float(row.get("entry_price")) for row in items]
        entries = [value for value in entries if value is not None]
        cost = sum((safe_float(row.get("entry_price")) or 0.0) * 5.0 for row in known)
        pnl = sum(safe_float(row.get("paper_pnl_requested_size")) or 0.0 for row in known)
        out.append(
            {
                group_key: group,
                "orders": len(items),
                "known_orders": len(known),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": None if not known else len(wins) / len(known),
                "avg_entry": None if not entries else sum(entries) / len(entries),
                "paper_cost_requested_size": cost,
                "paper_pnl_requested_size": pnl,
                "paper_roi_requested_size": None if cost <= 0 else pnl / cost,
            }
        )
    return out


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def fmt_pct(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return f"{100.0 * number:.2f}%"


def fmt_num(value: Any, digits: int = 4) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return f"{number:.{digits}f}"


def markdown_table(rows: list[dict[str, Any]], fields: list[str]) -> list[str]:
    if not rows:
        return ["_No rows._"]
    lines = [
        "| " + " | ".join(fields) + " |",
        "| " + " | ".join("---" for _ in fields) + " |",
    ]
    for row in rows:
        values = []
        for field in fields:
            value = row.get(field)
            if field.endswith("rate") or field.startswith("win_rate") or field.startswith("skip_rate") or "roi" in field:
                values.append(fmt_pct(value))
            elif isinstance(value, float):
                values.append(fmt_num(value, 4))
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return lines


def write_report(
    *,
    path: Path,
    candidate: dict[str, Any],
    sessions: list[Path],
    settlement_rows: list[dict[str, Any]],
    session_summary: list[dict[str, Any]],
    time_summary: list[dict[str, Any]],
) -> None:
    known = [row for row in settlement_rows if row.get("known_outcome") is True]
    wins = [row for row in known if row.get("win") is True]
    losses = [row for row in known if row.get("win") is False]
    entries = [safe_float(row.get("entry_price")) for row in settlement_rows]
    entries = [value for value in entries if value is not None]
    total_cost = sum((safe_float(row.get("entry_price")) or 0.0) * 5.0 for row in known)
    total_pnl = sum(safe_float(row.get("paper_pnl_requested_size")) or 0.0 for row in known)
    completed_xrp = sum(int(row.get("completed_xrp_markets") or 0) for row in session_summary)
    skip_rate = None if completed_xrp <= 0 else 1.0 - (len(settlement_rows) / completed_xrp)

    lines = [
        "# Shadow Replay Report",
        "",
        f"Candidate: `{candidate['name']}`",
        f"Candidate path: `{candidate['path']}`",
        f"Sessions scanned: `{len(sessions)}`",
        "",
        "## Headline",
        "",
        f"- Completed XRP markets in scanned sessions: `{completed_xrp}`",
        f"- Shadow orders: `{len(settlement_rows)}`",
        f"- Known settled orders: `{len(known)}`",
        f"- Wins/losses: `{len(wins)}/{len(losses)}`",
        f"- Win rate: `{fmt_pct(None if not known else len(wins) / len(known))}`",
        f"- Average entry: `{fmt_num(None if not entries else sum(entries) / len(entries), 4)}`",
        f"- Replay skip rate across completed XRP markets: `{fmt_pct(skip_rate)}`",
        f"- Paper ROI at requested 5-share size: `{fmt_pct(None if total_cost <= 0 else total_pnl / total_cost)}`",
        "",
        "Important: this is a replay on data that helped discover the rule, so it is not unbiased proof. It is still useful because it checks whether the live shadow logger's exact one-order-per-market behavior matches the candidate story before the next fresh capture.",
        "",
        "## By Session",
        "",
        *markdown_table(
            session_summary,
            [
                "session_id",
                "completed_xrp_markets",
                "shadow_orders",
                "wins",
                "losses",
                "win_rate",
                "skip_rate_xrp_markets",
                "avg_entry",
                "paper_roi_requested_size",
            ],
        ),
        "",
        "## By UTC Time Window",
        "",
        *markdown_table(
            time_summary,
            [
                "time_window_utc",
                "orders",
                "wins",
                "losses",
                "win_rate",
                "avg_entry",
                "paper_roi_requested_size",
            ],
        ),
        "",
        "## Output Files",
        "",
        "- `shadow_replay_orders.jsonl`",
        "- `shadow_replay_settlements.jsonl`",
        "- `shadow_replay_settlements.csv`",
        "- `shadow_replay_session_summary.csv`",
        "- `shadow_replay_time_window_summary.csv`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    input_root = Path(args.input_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    candidate = live.load_shadow_candidate(args.candidate)
    if candidate is None:
        raise RuntimeError("A shadow candidate is required for replay")

    sessions = discover_sessions(input_root, args.session_id)
    orders_path = output_dir / "shadow_replay_orders.jsonl"
    settlements_path = output_dir / "shadow_replay_settlements.jsonl"
    completed_markets_by_session: dict[str, int] = {}
    settlement_rows: list[dict[str, Any]] = []

    with (
        orders_path.open("w", encoding="utf-8") as orders_handle,
        settlements_path.open("w", encoding="utf-8") as settlements_handle,
    ):
        for session_dir in sessions:
            outcomes = session_outcomes(session_dir)
            snapshots = session_snapshots(session_dir)
            completed_markets_by_session[session_dir.name] = completed_xrp_markets(outcomes)
            ordered: set[tuple[str, str]] = set()
            events = sorted(read_jsonl(session_dir / "polymarket_grid_signals.jsonl"), key=sort_key)

            for event in events:
                event = enrich_event_from_snapshots(event, snapshots)
                symbol = str(event.get("symbol") or "")
                key = live.bucket_key(event.get("bucket_end"))
                if not symbol or not key or (symbol, key) in ordered:
                    continue

                row = live.grid_event_candidate_row(event)
                decision = candidate["score_grid_event"](row)
                if not isinstance(decision, dict) or not bool(decision.get("allow_trade")):
                    continue

                order = live.build_shadow_order(
                    event=event,
                    candidate=candidate,
                    candidate_row=row,
                    decision=decision,
                    paper_size=float(args.paper_size),
                )
                if order is None:
                    continue

                ordered.add((symbol, key))
                append_jsonl(orders_handle, order)
                outcome = outcomes.get((symbol, key))
                if outcome is None or not outcome.get("complete"):
                    continue
                settlement = live.build_shadow_settlement(order, outcome)
                append_jsonl(settlements_handle, settlement)
                settlement_rows.append(settlement_flat_row(settlement))

    session_summary = summarize_rows(settlement_rows, completed_markets_by_session)
    time_summary = summarize_group(settlement_rows, "time_window_utc")
    write_csv(output_dir / "shadow_replay_settlements.csv", settlement_rows)
    write_csv(output_dir / "shadow_replay_session_summary.csv", session_summary)
    write_csv(output_dir / "shadow_replay_time_window_summary.csv", time_summary)
    write_report(
        path=output_dir / "shadow_replay_report.md",
        candidate=candidate,
        sessions=sessions,
        settlement_rows=settlement_rows,
        session_summary=session_summary,
        time_summary=time_summary,
    )

    known = [row for row in settlement_rows if row.get("known_outcome") is True]
    wins = [row for row in known if row.get("win") is True]
    losses = [row for row in known if row.get("win") is False]
    print(
        json.dumps(
            {
                "candidate": candidate["name"],
                "sessions": len(sessions),
                "completed_xrp_markets": sum(completed_markets_by_session.values()),
                "shadow_orders_settled": len(settlement_rows),
                "wins": len(wins),
                "losses": len(losses),
                "report": str(output_dir / "shadow_replay_report.md"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
